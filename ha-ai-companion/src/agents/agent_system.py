import asyncio
import hashlib
import json
import logging
import os
import re
import time
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Dict, Any, Optional, List
from openai import AsyncOpenAI
from ..agents.tools import AgentTools, _compact_states
from .diffing import content_hash, is_disk_file
from .changeset_history import ChangesetHistory
from ..env_utils import env_positive_int
from ..config import ConfigurationManager
from ..memory.store import MemoryStore
from ..usage.manager import UsageManager
from ..ha.yaml_blocks import load_tolerant, split_blocks
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, asdict


logger = logging.getLogger(__name__)

_TRIGGER_KEYS = {"trigger", "triggers"}


def _yaml_platforms(text: str) -> Counter:
    """Counts of `platform:` list entries (sensor:, notify:, included list files), not triggers."""
    try:
        root = load_tolerant(text) if text else None
    except Exception:
        return Counter()
    found, stack = Counter(), [root]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            stack.extend(v for k, v in node.items() if k not in _TRIGGER_KEYS)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, dict) and isinstance(item.get("platform"), str):
                    found[item["platform"]] += 1
                else:
                    stack.append(item)
    return found


def _platforms_added(file_changes: List[Dict[str, Any]], applied: List[str]) -> set:
    """Integration platforms with more entries in applied new content than in the original."""
    added = set()
    for fc in file_changes:
        if fc.get("file_path") not in applied or fc["file_path"].startswith("nodered/"):
            continue
        added |= set(_yaml_platforms(fc.get("new_content") or "") - _yaml_platforms(fc.get("original_content") or ""))
    return added


def _parse_suggestion_response(raw: str) -> Any:
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    text = fenced.group(1).strip() if fenced else raw.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    for start, opener in enumerate(text):
        if opener not in "[{":
            continue
        stack = []
        in_string = escaped = False
        for end in range(start, len(text)):
            char = text[end]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char in "[{":
                stack.append("]" if char == "[" else "}")
            elif char in "]}":
                if not stack or stack.pop() != char:
                    break
                if not stack:
                    try:
                        return json.loads(text[start:end + 1])
                    except json.JSONDecodeError:
                        break
    raise ValueError("The suggestion model returned invalid JSON. Try generating again.")


def _normalize_suggestion_text(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _collect_yaml_values(node: Any, keys: set) -> List[str]:
    values = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in keys:
                values.extend(value if isinstance(value, list) else [value])
            else:
                values.extend(_collect_yaml_values(value, keys))
    elif isinstance(node, list):
        for value in node:
            values.extend(_collect_yaml_values(value, keys))
    return [str(value) for value in values if value is not None]


def _suggestion_outline_items(text: str, resource: str) -> List[Dict[str, Any]]:
    try:
        kind, blocks = split_blocks(text)
        if kind == "list":
            data = [block.value for block in blocks if isinstance(block.value, dict)]
        else:
            data = load_tolerant(text)
        if isinstance(data, dict) and resource[:-1] in data:
            data = data[resource[:-1]]
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            config_keys = {"id", "alias", "name", "trigger", "triggers", "action", "actions", "sequence", "entities"}
            if config_keys.intersection(data):
                return [data]
            return [dict(value, alias=value.get("alias") or value.get("name") or name)
                    for name, value in data.items() if isinstance(value, dict)]
    except Exception:
        logger.debug("Could not parse %s for suggestion outline", resource, exc_info=True)
    return []


def _build_suggestion_outlines(resource_files: Dict[str, List[Dict[str, Any]]],
                               max_chars: int = 20000) -> tuple:
    outlines = {}
    aliases = set()
    remaining = max_chars
    for resource in ("automations", "scenes", "scripts"):
        rows = []
        for file_entry in sorted(resource_files.get(resource, []), key=lambda f: f.get("path", "")):
            for item in _suggestion_outline_items(file_entry.get("content") or "", resource):
                alias = str(item.get("alias") or item.get("name") or item.get("id") or "unnamed")
                if resource == "automations":
                    aliases.add(alias)
                if resource == "automations":
                    trigger_nodes = item.get("triggers", item.get("trigger", []))
                    triggers = sorted(set(_collect_yaml_values(trigger_nodes, {"platform", "trigger"})))
                    trigger_entities = sorted(set(_collect_yaml_values(trigger_nodes, {"entity_id"})))
                    services = sorted(set(_collect_yaml_values(item.get("actions", item.get("action", [])), {"action", "service"})))
                    targets = sorted(set(_collect_yaml_values(item.get("actions", item.get("action", [])), {"entity_id", "device_id", "area_id"})))
                    row = (f"{item.get('id', '-')} | {alias} | trigger: "
                           f"{'+'.join(triggers + trigger_entities) or '-'} | action: "
                           f"{'+'.join(services + targets) or '-'}")
                elif resource == "scenes":
                    entities = sorted((item.get("entities") or {}).keys()) if isinstance(item.get("entities"), dict) else []
                    row = f"{alias} | entities: {', '.join(entities) or '-'}"
                else:
                    actions = item.get("sequence", item.get("actions", item.get("action", [])))
                    services = sorted(set(_collect_yaml_values(actions, {"action", "service"})))
                    targets = sorted(set(_collect_yaml_values(actions, {"entity_id", "device_id", "area_id"})))
                    row = f"{alias} | action: {'+'.join(services + targets) or '-'}"
                if len(row) + 1 > remaining:
                    break
                rows.append(row)
                remaining -= len(row) + 1
            if remaining <= 0:
                break
        outlines[resource] = "\n".join(rows)
        if remaining <= 0:
            break
    return outlines, sorted(aliases)


def _is_duplicate_suggestion(suggestion: Dict[str, Any], aliases: List[str]) -> bool:
    title_text = _normalize_suggestion_text(suggestion.get("title"))
    title_tokens = set(title_text.split())
    candidate_tokens = set(title_tokens)
    for entity in suggestion.get("entities", []) or []:
        candidate_tokens.update(_normalize_suggestion_text(str(entity).split(".")[-1]).split())
    if not candidate_tokens:
        return False
    for alias in aliases:
        alias_text = _normalize_suggestion_text(alias)
        alias_tokens = set(alias_text.split())
        if alias_text and alias_text == title_text:
            return True
        if len(alias_tokens) == 1:
            continue
        if alias_tokens and len(candidate_tokens & alias_tokens) / min(len(candidate_tokens), len(alias_tokens)) >= 0.8:
            return True
    return False


_NAMING_DOMAINS = {
    "light", "switch", "sensor", "binary_sensor", "climate", "cover", "fan", "lock",
    "media_player", "camera", "vacuum", "valve", "water_heater", "humidifier", "lawn_mower",
}
_RAW_NAME_PATTERNS = [
    (re.compile(r"\d{9,}"), "contains a long numeric device ID"),
    (re.compile(r"\b(?:[0-9a-f]{2}[:_-]){5}[0-9a-f]{2}\b|\b[0-9a-f]{12,}\b", re.I), "contains a MAC/hex address"),
    (re.compile(r"_?tz[e0-9]\d{3}_[a-z0-9]{8}", re.I), "contains a Tuya manufacturer code"),
    (re.compile(r"\bts\d{4}[a-z]?\b", re.I), "contains a Zigbee model code"),
]


def _detect_naming_issues(states: List[Dict[str, Any]], limit: int = 50) -> List[Dict[str, Any]]:
    """Flag names that are provably raw: missing, slug-like, device codes, or duplicated."""
    issues: List[Dict[str, Any]] = []
    by_name: Dict[str, List[tuple]] = {}
    for state in states:
        entity_id = state.get("entity_id", "")
        if entity_id.split(".")[0] not in _NAMING_DOMAINS:
            continue
        name = str((state.get("attributes") or {}).get("friendly_name") or "").strip()
        reason = None
        if not name:
            reason = "has no friendly name"
        elif re.fullmatch(r"[a-z0-9_]+", name):
            reason = "name is a raw slug"
        else:
            reason = next((why for rx, why in _RAW_NAME_PATTERNS if rx.search(name)), None)
        if reason:
            issues.append({"entity_id": entity_id, "current_name": name, "reason": reason})
        else:
            by_name.setdefault(name.lower(), []).append((entity_id, name))
    for group in by_name.values():
        if len(group) > 1:
            for entity_id, name in group:
                others = ", ".join(other for other, _ in group if other != entity_id)
                issues.append({"entity_id": entity_id, "current_name": name, "reason": f"same name as {others}"})
    return issues[:limit]


# Domains that never drive an automation idea (firmware, one-shot buttons, platform plumbing).
_SUGGESTION_SKIP_DOMAINS = {
    "update", "button", "image", "tts", "stt", "conversation", "ai_task", "notify", "event", "wake_word",
}


def _suggestion_states(states: List[Dict[str, Any]], registry: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """User-facing entities only: drop diagnostic/config/hidden entities and plumbing domains."""
    reg = {e.get("entity_id"): e for e in registry or []}
    kept = []
    for state in states:
        entity_id = state.get("entity_id", "")
        entry = reg.get(entity_id) or {}
        if entity_id.split(".")[0] in _SUGGESTION_SKIP_DOMAINS:
            continue
        if entry.get("entity_category") or entry.get("hidden_by") or entry.get("disabled_by"):
            continue
        kept.append(state)
    return kept

# Maps deprecated tool names to their current equivalents.
# Applied when replaying conversation_history so old sessions don't break.
TOOL_ALIASES: Dict[str, str] = {
    "call_config_files": "search_config_files",
}


def _normalize_history_msg(msg: Dict[str, Any]) -> Dict[str, Any]:
    """Remap deprecated tool names in a single history message."""
    if not TOOL_ALIASES:
        return msg
    # Assistant messages carry tool_calls
    if msg.get("role") == "assistant" and msg.get("tool_calls"):
        new_tcs = []
        changed = False
        for tc in msg["tool_calls"]:
            fn = tc.get("function", {})
            old_name = fn.get("name", "")
            new_name = TOOL_ALIASES.get(old_name, old_name)
            if new_name != old_name:
                changed = True
                tc = dict(tc)
                tc["function"] = dict(fn)
                tc["function"]["name"] = new_name
                logger.debug(f"Remapped tool alias: {old_name} → {new_name}")
            new_tcs.append(tc)
        if changed:
            msg = dict(msg)
            msg["tool_calls"] = new_tcs
    return msg


def _is_text_first_family(model: str) -> bool:
    """Models that must emit a plan BEFORE any tools are bound (Tier B plan-before-act).

    Substring match on purpose - providers prefix/suffix freely
    ("google/gemini-2.5-flash", "gemini-2.0-flash-exp").

    NEVER add generic tier words like "flash"/"pro"/"mini" here. "flash" matched
    "deepseek/deepseek-v4-flash-0731" and forced Tier B onto a model with working
    native tool calling; with tools omitted it emitted DSML tool-call markup as
    plain text and the run died at iteration 1 having executed nothing (v1.18.5).
    """
    return any(x in (model or "").lower() for x in ("gemini", "google"))


# Markers that mean the model tried to call a tool as TEXT instead of using the
# tool-calling API. Deliberately call-shaped: prose that merely mentions "DSML"
# or "tool calls" must NOT match, or a legitimate answer gets retried.
_PSEUDO_TOOL_CALL_MARKERS = (
    "｜dsml｜",   # DeepSeek, e.g. <|DSML|invoke name="...">
    "<tool_calls>",
    "<invoke name=",
    "<function_calls>",
)


def _looks_like_pseudo_tool_call(text: str) -> bool:
    """True when content contains tool-call markup instead of real tool_calls."""
    low = (text or "").lower()
    return any(m in low for m in _PSEUDO_TOOL_CALL_MARKERS)


def _fold(text: str) -> str:
    """Lowercase and strip diacritics ("Zrób" -> "zrob"); ł has no decomposition."""
    decomposed = unicodedata.normalize("NFKD", (text or "").lower().replace("ł", "l"))
    return "".join(c for c in decomposed if not unicodedata.combining(c))


_WRITE_INTENT_KEYWORDS = tuple(_fold(k) for k in (
    "create", "add", "fix", "change", "modify", "update", "edit", "make",
    "automate", "repair", "delete", "remove", "build", "configure", "set",
    "enable", "disable", "adjust", "rewrite", "improve", "turn on", "turn off",
    "restore", "rollback", "revert",
    # Polish
    "stwórz", "dodaj", "zmień", "napraw", "zmodyfikuj", "zaktualizuj",
    "edytuj", "zrób", "skonfiguruj", "usuń", "wyłącz", "włącz", "popraw",
    "przywróć", "cofnij",
))


def _has_write_intent(message: str) -> bool:
    folded = _fold(message)
    return any(kw in folded for kw in _WRITE_INTENT_KEYWORDS)


def _complexity_routing_enabled() -> bool:
    # Opt-in: when on, short read-only turns never reach the main model, which
    # hid the user's chosen main model (log 2026-10-16, gpt-6.1-sol never ran).
    return os.getenv("COMPLEXITY_ROUTING", "off").strip().lower() in ("on", "1", "true", "yes")


_REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh"})
_SUMMARY_KEEP_RECENT = 10
_SUMMARY_STEP = 10

# Listing/state/log tools only. File-content tools are never capped: a cut read
# could be written back as a truncated file.
_CAPPED_TOOLS = frozenset({
    "get_entity_states", "search_entities", "list_automations", "get_automation_traces",
    "get_ha_error_log", "get_ha_issues", "get_lovelace_resources", "search_past_sessions",
    "list_changesets", "list_dashboards", "search_memory", "get_ha_health",
    "get_automation_trace", "find_unavailable_entities", "find_cleanup_candidates",
    "nodered_get_tab",
})


def _cap_tool_result(name: str, content: str) -> str:
    """Cap once at insertion so the in-turn prefix stays stable for the provider cache."""
    limit = env_positive_int("TOOL_RESULT_MAX_CHARS", 12000)
    if name not in _CAPPED_TOOLS or len(content) <= limit:
        return content
    return (content[:limit] + f"\n\n[Truncated: showed {limit} of {len(content)} chars. "
            "Narrow the query (filter, domain, area) to see the rest.]")


# Tools whose dispatch maps alternative argument names; validate after that mapping.
_ARG_ALIAS_TOOLS = {"add_nodered_flow", "edit_nodered_tab"}
_JSON_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool,
               "array": list, "object": dict}


def _coerce_arg(value: Any, spec: Dict[str, Any]) -> Any:
    """Coerce one argument to its JSON-schema type. Raises ValueError when impossible."""
    t = spec.get("type")
    expected = _JSON_TYPES.get(t)
    if expected is None or (isinstance(value, expected) and (t == "boolean" or not isinstance(value, bool))):
        return value
    if t == "string" and isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if t == "string" and isinstance(value, (int, float)):
        return str(value)
    if t == "integer" and isinstance(value, float) and value.is_integer():
        return int(value)
    if t == "integer" and isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    if t == "number" and isinstance(value, str):
        return float(value.strip())
    if t == "boolean" and isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    if t in ("array", "object") and isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, expected):
            return parsed
    raise ValueError(f"expected {t}, got {type(value).__name__}")


def _validate_tool_args(name: str, args: Any, schema: Optional[Dict[str, Any]]) -> tuple:
    """Check tool arguments against the tool's JSON schema before dispatch.

    Returns (args, error). Safe coercions ("5" -> 5, "true" -> True, list -> JSON
    string) are applied silently; anything else returns one precise error so the
    model can fix the call instead of hitting a Python TypeError."""
    if schema is None or name in _ARG_ALIAS_TOOLS:
        return args, None
    if not isinstance(args, dict):
        return args, f"Arguments for {name} must be a JSON object."
    props = schema.get("properties") or {}
    problems = []
    missing = [k for k in schema.get("required") or [] if k not in args]
    if missing:
        problems.append(f"missing required {', '.join(missing)}")
    unknown = [k for k in args if k not in props]
    if unknown:
        problems.append(f"unknown argument(s) {', '.join(unknown)}")
    out = dict(args)
    for key, value in args.items():
        spec = props.get(key)
        if not spec or value is None:
            continue
        try:
            out[key] = _coerce_arg(value, spec)
        except (ValueError, TypeError) as e:
            problems.append(f"{key} {e}")
            continue
        if spec.get("enum") and out[key] not in spec["enum"]:
            problems.append(f"{key} must be one of {spec['enum']}")
    if problems:
        expected = ", ".join(f"{k}: {v.get('type', 'any')}" for k, v in props.items()) or "none"
        return args, f"Invalid arguments for {name}: {'; '.join(problems)}. Expected: {expected}."
    return out, None


def _merge_reasoning_details(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge streamed reasoning_details deltas into whole blocks, in order.

    OpenRouter streams one delta per text fragment with the same (type, index).
    Blocks must be passed back unchanged and in order on the next tool turn.
    """
    merged: Dict[Any, Dict[str, Any]] = {}
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        key = (chunk.get("type"), str(chunk.get("index", 0)))
        block = merged.get(key)
        if block is None:
            merged[key] = dict(chunk)
            continue
        for field, value in chunk.items():
            if field in ("text", "summary") and isinstance(value, str):
                block[field] = (block.get(field) or "") + value
            elif value not in (None, ""):
                block[field] = value
    return list(merged.values())


HEARTBEAT_INTERVAL_S = 5.0


async def _await_with_heartbeat(awaitable, timeout: Optional[float], interval: float = HEARTBEAT_INTERVAL_S):
    """Await `awaitable`, yielding ("heartbeat", elapsed_s) every `interval`.

    Ends with ("done", result). Raises asyncio.TimeoutError after `timeout`
    seconds (None = no limit). The inner task is cancelled if the consumer
    stops early (run stopped) so no orphan provider read keeps running.
    """
    task = asyncio.ensure_future(awaitable)
    start = time.monotonic()
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=interval)
            if done:
                yield "done", task.result()
                return
            elapsed = time.monotonic() - start
            if timeout is not None and elapsed >= timeout:
                raise asyncio.TimeoutError()
            yield "heartbeat", elapsed
    finally:
        if not task.done():
            task.cancel()


async def _next_chunk(iterator):
    """__anext__ wrapper: StopAsyncIteration can't cross an async generator (PEP 479)."""
    try:
        return True, await iterator.__anext__()
    except StopAsyncIteration:
        return False, None


@dataclass
class Changeset:
    """Represents a proposed set of configuration changes."""
    changeset_id: str
    file_changes: List[Dict[str, Any]]  # {file_path, new_content, original_content, base_hash}
    created_at: str
    expires_at: str
    restored_from: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AgentSystem:
    """
    Multi-agent system for Home Assistant configuration management.

    Uses OpenAI's GPT models to provide intelligent configuration assistance:
    - Understanding user requests
    - Reading and analyzing configuration
    - Proposing safe changes
    - Explaining configuration decisions
    """

    def __init__(
        self,
        config_manager: ConfigurationManager,
        system_prompt: Optional[str] = None,
        enable_cache_control: bool = False,
        usage_tracking: str = 'stream_options',
        memory_manager: Optional[MemoryStore] = None,
        conversation_manager=None,
    ):
        """
        Initialize the agent system.

        Args:
            config_manager: ConfigurationManager for file operations
            system_prompt: Optional custom system prompt. If not provided, uses default.
            enable_cache_control: Whether to enable cache control for API calls (default: False)
            usage_tracking: How to request usage tracking from the API:
                - 'stream_options': Use stream_options.include_usage (OpenAI format)
                - 'usage': Use usage.include (alternative format)
                - 'disabled': Don't request usage tracking
            memory_manager: Optional MemoryStore for persistent cross-session memories
        """
        self.config_manager = config_manager
        self.memory_manager = memory_manager
        self.conversation_manager = conversation_manager
        self.tools = AgentTools(config_manager, agent_system=self, memory_manager=memory_manager, conversation_manager=conversation_manager)

        # Initialize main OpenAI client
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            logger.warning("No OpenAI API key configured")
            self.client = None
        else:
            self.client = AsyncOpenAI(
                api_key=api_key,
                base_url=os.getenv('OPENAI_API_URL', 'https://api.openai.com/v1')
            )

        self.model = os.getenv('OPENAI_MODEL', 'gpt-4o')

        # Fast model — side tasks only (suggestions, summaries, memory extraction).
        # Same provider/key as main. The chat loop never switches models mid-turn:
        # each model has its own provider cache, so a switch re-bills the whole prefix.
        # Legacy RESEARCH_*/SUGGESTION_* env names still work; a separate URL+key is
        # honoured only when both are set. Internal names stay `suggestion_*` (usage slot key).
        _f_model = (os.getenv('FAST_MODEL') or os.getenv('RESEARCH_MODEL') or os.getenv('SUGGESTION_MODEL') or '').strip()
        _f_url   = os.getenv('RESEARCH_API_URL') or os.getenv('SUGGESTION_API_URL')
        _f_key   = os.getenv('RESEARCH_API_KEY') or os.getenv('SUGGESTION_API_KEY')
        self.suggestion_model = _f_model or self.model
        self.suggestion_client = (AsyncOpenAI(api_key=_f_key, base_url=_f_url)
                                  if _f_model and _f_url and _f_key else self.client)

        # The chat/tool loop always uses the main model.
        self.config_model = self.model
        self.config_client = self.client

        # Get temperature from environment variable, use None if not specified
        temperature_str = os.getenv('TEMPERATURE')
        self.temperature = float(temperature_str) if temperature_str else None

        # Suggestion calls use a lower temperature by default (0.2) to reduce hallucination.
        # Can be overridden via SUGGESTION_TEMPERATURE env var.
        suggestion_temp_str = os.getenv('RESEARCH_TEMPERATURE') or os.getenv('SUGGESTION_TEMPERATURE', '0.2')
        self.suggestion_temperature = float(suggestion_temp_str)

        # Per-agent max_tokens limits (0 = no limit / model default)
        def _int_env(key: str) -> Optional[int]:
            val = os.getenv(key, '').strip()
            return int(val) if val and val.isdigit() and int(val) > 0 else None

        self.max_tokens = _int_env('MAX_TOKENS')
        self.suggestion_max_tokens = _int_env('RESEARCH_MAX_TOKENS') or _int_env('SUGGESTION_MAX_TOKENS') or self.max_tokens
        self.config_max_tokens = _int_env('REASONING_MAX_TOKENS') or _int_env('CONFIG_MAX_TOKENS') or self.max_tokens

        # Per-client cache: tracks whether usage-tracking params were rejected (BUG-8)
        # Keys: 'main', 'suggestion', 'config' — value: True = params accepted, False = rejected
        # session_id -> (messages covered, prefix digest, summary text); see _summarize_old_history.
        self._summary_cache: Dict[str, tuple] = {}
        self._client_usage_ok: Dict[str, Optional[bool]] = {
            'main': None, 'suggestion': None, 'config': None
        }

        # Store cache control setting. Auto-enable for Anthropic models and for
        # OpenRouter even when the option is left off: the agent loop (stable
        # system+schema prefix re-sent every iteration) is the ideal prompt-caching
        # pattern. OpenRouter maps block-level markers per provider and drops them
        # where unsupported, so this is safe. DISABLE_CACHE_AUTO opts out.
        self.enable_cache_control = enable_cache_control
        if not self.enable_cache_control and os.getenv('DISABLE_CACHE_AUTO', '').strip().lower() not in ('1', 'true', 'yes'):
            _m = (self.model or '').lower()
            if 'claude' in _m or 'anthropic' in _m or 'openrouter' in os.getenv('OPENAI_API_URL', '').lower():
                self.enable_cache_control = True
                logger.info("Cache control auto-enabled for %s", self.model)

        # Store usage tracking mode (global) and per-phase overrides
        self.usage_tracking = usage_tracking
        self.suggestion_usage_tracking = usage_tracking
        self.config_usage_tracking = usage_tracking

        logger.info(f"AgentSystem initialized with model: {self.model}")
        if self.suggestion_model != self.model:
            logger.info(f"Fast model (side tasks): {self.suggestion_model}")
        if self.temperature is not None:
            logger.info(f"Temperature: {self.temperature}")
        logger.info(f"Cache control: {'enabled' if self.enable_cache_control else 'disabled'}")
        # OpenRouter reasoning.effort; empty = not sent (provider/model default).
        _effort = os.getenv('REASONING_EFFORT', '').strip().lower()
        if _effort == 'null':  # bashio::config output for an unset optional key
            _effort = ''
        if _effort and _effort not in _REASONING_EFFORTS:
            logger.warning(f"Invalid REASONING_EFFORT '{_effort}' — ignored")
            _effort = ''
        self.reasoning_effort = _effort
        logger.info(f"Usage tracking: {self.usage_tracking} (suggestion: {self.suggestion_usage_tracking}, config: {self.config_usage_tracking})")

        # Per-call usage/cost log (Track 1). Derive the dir from the memory dir
        # (sibling of .ai_agent_memories) so it lives alongside the other state.
        try:
            if os.getenv('USAGE_DIR'):
                _usage_dir = os.getenv('USAGE_DIR')
            elif memory_manager is not None:
                _usage_dir = str(Path(memory_manager.memory_dir).parent / ".ai_agent_usage")
            else:
                _usage_dir = ".ai_agent_usage"
            self.usage_manager: Optional[UsageManager] = UsageManager(_usage_dir)
        except Exception as exc:
            logger.error("Failed to init UsageManager: %s", exc)
            self.usage_manager = None

        # In-memory storage for pending changesets
        self.pending_changesets: Dict[str, Changeset] = {}
        try:
            self.changeset_history: Optional[ChangesetHistory] = ChangesetHistory(
                os.getenv('CHANGESET_HISTORY_DIR') or str(Path(config_manager.config_dir) / ".ai_agent_changesets")
            )
        except Exception as exc:
            logger.error("Failed to init ChangesetHistory: %s", exc)
            self.changeset_history = None

        # Home topology cache (areas → entities) — refreshed every 10 minutes
        self._topology_cache: Optional[str] = None
        self._topology_cache_time: Optional[datetime] = None

        # Status queue for streaming tool execution progress events to the frontend
        self._tool_status_queue: Optional[asyncio.Queue] = None

        # System prompt for the configuration agent
        base_prompt = system_prompt or self._get_default_system_prompt()

        # Append user-defined suggestion prompt if configured
        suggestion_prompt = os.getenv('SUGGESTION_PROMPT', '').strip()
        if suggestion_prompt:
            base_prompt = base_prompt + "\n\nAdditional instructions for automation suggestions:\n" + suggestion_prompt
            logger.info(f"Appended custom suggestion prompt ({len(suggestion_prompt)} characters)")

        self.system_prompt = base_prompt
        if system_prompt:
            logger.info(f"Using custom system prompt ({len(system_prompt)} characters)")
        else:
            logger.info("Using default system prompt")

    def _get_default_system_prompt(self) -> str:
        """Get the default system prompt for the configuration agent.

        Core rules only — dashboard/HACS and automation-suggestion procedures
        live in _DASHBOARD_TOPIC_PROMPT / _AUTOMATION_SUGGESTIONS_TOPIC_PROMPT
        and are injected per-turn only when the topic is actually relevant
        (see _select_topic_fragments). This keeps the always-loaded prompt
        under Anthropic's ~200-line guidance instead of paying for the full
        rulebook — including Node-RED JSON schemas and HACS fetch workflow —
        on every single turn regardless of what the user asked.
        """
        return """HA Configuration Assistant with persistent memory. You read and change a production Home Assistant config. Every write is staged for user approval, backed up, and can be restored.

How to work:
- State your plan in one short sentence, then act. Batch independent tool calls in one response.
- Read only what you need. Never load whole big files: use outline_file then read_range, or the automation tools below.
- If the answer is already in Memory or Home Layout, answer without tools.
- When stuck after a few lookups, ask the user one specific question.

Automations (preferred path — never rewrite automations.yaml as a whole):
- list_automations: one line per automation (id, alias, state, last run, file:lines).
- get_automation: exact YAML of one automation, script, or scene (set kind for scripts/scenes).
- upsert_automation: add/replace ONE automation, script, or scene (set kind); other items are untouched.
- remove_automation: only when the user asked to delete.
- get_automation_traces: why an automation did / did not run (step results, errors).

Other config files:
- outline_file + read_range: navigate any YAML file by line ranges.
- search_config_files: text search across config (snippets; full_content only for small files).
- patch_config_key / patch_config_block: targeted edits of mapping files (configuration.yaml etc.).
- propose_config_changes: full new content of a file — use for new files or when the targeted tools cannot express the edit. Preserve every existing item, comment, and !secret/!include tag. Deleting automations/scripts/scenes needs confirm_delete=true and an explicit user request.
- For scripts.yaml/scenes.yaml, prefer get_automation(kind='script'|'scene') and upsert_automation(kind='script'|'scene').
- File layout: match the user's existing layout (single file vs split files). Never move items between files unless asked.
- Never reproduce file content in text — call the tool directly.
- Several edits to one file in a turn are fine: each write tool builds on the still-pending version and merges into one approval card. Never rewrite a whole file just to combine edits.
- Only tell the user a change is ready to approve after a write tool returned a changeset_id in this turn.

Entities:
- search_entities: find entities by name/id/area words. Use it before writing YAML with entity_ids you have not seen.
- get_entity_states: live states/attributes (use domain_filter or query; avoid full dumps).
- NEVER invent entity_ids. If a write tool returns entity_warnings, fix the ids from the suggestions or ask.

Diagnostics: get_ha_issues (Watchman/Spook/repairs), get_ha_error_log (core errors — check after changes), reload_config (after approved helper/template changes; no restart needed).

Undo: list_changesets shows applied changes; restore_changeset stages the previous content for approval.

Helpers: define input_number / input_boolean / input_text / input_select in YAML (configuration.yaml or the user's packages), never tell the user to create them in the UI.

Dashboards, custom HACS cards, and Node-RED have detailed rules that load automatically when on-topic. Dashboard tools: list_dashboards, create_dashboard, delete_dashboard, get_lovelace_resources. Node-RED: get_nodered_flows, add_nodered_flow (new tab), edit_nodered_tab (existing tab, only that tab).

Language:
- Detect the home language from entity names and automation aliases in Home Layout.
- Reply in the user's language. Write new HA content (aliases, descriptions, notifications) in the home language.

Generation minimalism (least config that works):
- Ask in order, stop at the first yes: does it need to exist? native HA feature (sun/time/numeric_state/template trigger, existing helper)? one simple automation? Node-RED only for genuinely complex flows.
- No speculative helpers or options. Never trim safety conditions, validation, or notifications the user relies on.
- Validate against current HA syntax (triggers:/conditions:/actions:, trigger: state). Warn about breaking changes.
- Integration YAML (sensor platforms, template, etc.): use only option names you are sure exist, with every required key. On a config-check error, fix exactly the reported key (HA's "did you mean" is right) in the same structure; do not restructure.

Memory (save_memory / search_memory / delete_memory):
- Save ONLY durable facts the user stated that HA cannot tell you: preferences, residents and their devices, unclear device purposes, routines, decisions, corrections.
- NEVER save session actions, sensor readings, created automations, device specs, or anything in HA config/registry.
- One short fact per memory. Correct with replaces=[id]; retire wrong ones with delete_memory.
- Relevant memories and the current time come in a <context> block at the end of the user's message; HA state wins if they conflict. search_memory for anything not shown.
- search_past_sessions when the user refers to earlier work.

Production system: safety and clarity first."""

    # Topic fragments — injected into system_content only when relevant (see
    # _select_topic_fragments), instead of being part of the always-loaded
    # core prompt above. Text is unchanged from the prior always-on version.
    _NAV_TOOLS = frozenset({
        "outline_file", "read_range", "search_entities", "list_automations", "get_automation",
        "upsert_automation", "remove_automation", "get_automation_traces", "get_ha_health",
        "get_automation_trace", "find_unavailable_entities", "find_cleanup_candidates",
    })

    @staticmethod
    def _memory_tool_schemas() -> List[Dict[str, Any]]:
        def fn(name, desc, props, required=()):
            return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
                "type": "object", "properties": props, "required": list(required)}}}
        return [
            fn("save_memory",
               "Remember ONE durable fact the user stated that HA cannot tell you (preference, resident/device "
               "mapping, unclear device purpose, routine, decision). Never session actions, states, or config contents.",
               {"kind": {"type": "string", "enum": ["preference", "fact", "decision", "procedure"],
                         "description": "preference = how the user wants things; fact = household knowledge; "
                                        "decision = agreed design choice; procedure = learned HACS card syntax."},
                "text": {"type": "string", "description": "One short fact, max 800 chars, in the user's words."},
                "key": {"type": "string", "description": "Optional stable topic key, e.g. pattern_bubble-card_syntax. "
                                                         "Saving with the same key replaces the old record."},
                "replaces": {"type": "array", "items": {"type": "integer"},
                             "description": "Memory ids this corrects; they are marked superseded."}},
               ("kind", "text")),
            fn("search_memory",
               "Search stored memories. Relevant ones are already injected; use this for anything not shown.",
               {"query": {"type": "string", "description": "Words to match. Empty lists recent memories."},
                "kind": {"type": "string", "enum": ["preference", "fact", "decision", "procedure"]},
                "include_candidates": {"type": "boolean", "description": "Also return unreviewed candidates."}}),
            fn("delete_memory", "Retire a wrong or outdated memory by id.",
               {"id": {"type": "integer", "description": "Memory id (shown as #id)."}}, ("id",)),
            fn("memory_doctor",
               "Check memory health against live HA: retire records that cite missing entities, report "
               "duplicates and unreviewed candidates. Use when the user asks to clean up memory.", {}),
        ]

    @staticmethod
    def _nav_tool_schemas() -> List[Dict[str, Any]]:
        def fn(name, description, properties=None, required=()):
            return {"type": "function", "function": {
                "name": name, "description": description,
                "parameters": {"type": "object", "properties": properties or {}, "required": list(required)},
            }}
        auto_ref = {"automation": {"type": "string", "description": "Item id, entity_id, or exact alias."}}
        return [
            fn("outline_file",
               "Map of a config file: top-level items with line ranges (files ≤60 lines come back whole). "
               "Use before read_range instead of reading a big file.",
               {"file_path": {"type": "string"},
                "depth": {"type": "integer", "description": "2 = also list children of big blocks (e.g. template:, sensor:)."}},
               ["file_path"]),
            fn("read_range", "Numbered lines start_line..end_line (1-based, inclusive, max 400) of a config file.",
               {"file_path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}},
               ["file_path", "start_line", "end_line"]),
            fn("search_entities",
               "Find entities by words in entity_id / friendly name / area (all words must match). "
               "Returns '\"name\"[entity_id]=state @area' lines. Use instead of get_entity_states dumps.",
               {"query": {"type": "string"}, "domain": {"type": "string", "description": "e.g. light, sensor"},
                "area": {"type": "string"}, "limit": {"type": "integer", "description": "Default 30, max 100."}}),
            fn("list_automations",
               "All automations, one line each: id | alias | state | last triggered | file:lines. Optional text filter.",
               {"query": {"type": "string"}}),
            fn("get_automation", "Exact YAML of one automation, script, or scene.", {
                **auto_ref, "kind": {"type": "string", "enum": ["automation", "script", "scene"]}},
               ["automation"]),
            fn("upsert_automation",
               "Add or replace ONE automation, script, or scene (staged for approval). Give only that item's YAML. "
               "Keep its id to replace it; use a new id to add. Other items stay untouched.",
               {"automation_yaml": {"type": "string"},
                "kind": {"type": "string", "enum": ["automation", "script", "scene"]},
                "file_path": {"type": "string", "description": "Only for a new item in a split-file setup."}},
               ["automation_yaml"]),
            fn("remove_automation", "Remove ONE automation (staged for approval). Only when the user asked.", auto_ref, ["automation"]),
            fn("get_automation_traces",
               "Recent runs of an automation and step-by-step results/errors of one run (latest by default). "
               "Use to debug 'why did/didn't it run'.",
               {**auto_ref, "run_id": {"type": "string"}}, ["automation"]),
            fn("get_ha_health", "Compact HA repairs, failed integrations, pending updates, and top logs.",
               {"include_ignored": {"type": "boolean"} }),
            fn("get_automation_trace", "Compact trace of one automation run; index 0 is the latest.",
               {**auto_ref, "run_index": {"type": "integer", "description": "0 = latest run."}}, ["automation"]),
            fn("find_unavailable_entities", "List old unavailable/unknown entities grouped by integration.",
               {"min_age_hours": {"type": "number", "description": "Default 24."}}),
            fn("find_cleanup_candidates", "Report possible stale entities, unused helpers, and old mobile notify services; no removals.",
               {}),
        ]

    _DASHBOARD_TOPIC_PROMPT = """Dashboard rules:
- Call list_dashboards first to discover url_path values.
- Default: lovelace.yaml. Custom: lovelace/{url_path}.yaml (e.g. lovelace/kitchen.yaml).
- Review: search_config_files with 'lovelace' or list_dashboards then search_config_files.
- Edit existing: read via search_config_files, then propose_config_changes with correct path.
- Create new: call create_dashboard (returns url_path), then call propose_config_changes immediately with file_path='lovelace/{url_path}.yaml'. Do NOT call search_config_files first — new dashboard has no file to read. The system handles empty new dashboards automatically.
- Delete: call delete_dashboard with url_path (cannot delete default dashboard).
- Dashboard YAML must include at minimum 'title' and 'views' keys.
- STORAGE MODE ONLY: never add 'lovelace:' or 'dashboards:' entries to configuration.yaml — YAML-mode dashboards are not UI-editable and break the save flow. Always use create_dashboard.
- YAML-mode dashboard detected (list_dashboards entry without 'id')? Offer migration: create_dashboard storage twin, copy config via propose_config_changes, then tell user to remove the configuration.yaml entry and restart HA.
- Custom card configs are schema-checked on propose. On CARD SCHEMA ERROR: fix exactly per the hint, re-propose. Do not retry the same structure.
- Prefer 'sections' view layout (type: sections, heading cards) for new dashboards — responsive on desktop + mobile.

Custom card components (HACS):
This rule applies to every HACS custom card or integration (Bubble-Card, Mushroom, mini-graph-card,
custom-button-card, ApexCharts, or any other). Never assume you know the current YAML syntax from
training data — card APIs change between major versions.

Slug: component name lowercased, spaces/special chars replaced with hyphens.
  Examples: "Bubble-Card" → bubble-card, "Mini Graph Card" → mini-graph-card.

Before writing YAML for any custom card:
1. search_memory(query="<SLUG>", kind="procedure") for keys pattern_<SLUG>_syntax, pattern_<SLUG>_examples, etc.
2. If none found: tell user "I don't have docs for [component name] saved — want me to fetch them?"
   then call learn_hacs_component(name="[component name]").
3. If learn_hacs_component returns status=cached: the pattern memories are fresh; read them with search_memory.
4. If user says "refresh docs for [component]": call learn_hacs_component again ignoring cache.
5. If user provides a GitHub URL for the component: pass it as github_url to learn_hacs_component.

After a successful fetch, distill the returned readme, changelog, and examples into memory:
save_memory(kind="procedure", key=...) with keys:
- pattern_<SLUG>_syntax  — card types, required fields, type values, config keys
- pattern_<SLUG>_examples — 2-3 minimal working YAML blocks
- pattern_<SLUG>_changelog — breaking changes, removed fields, migration notes (skip if no changelog)
Each: ≤800 chars, bullet points only, no filler.

fetch_url rules (CRITICAL — prevents infinite loops):
- Call fetch_url at most 2 times per topic per turn (once for README, once for a second file if needed).
- If fetch_url returns truncated=true: do NOT retry with higher max_chars. Use the content you have.
- If you searched for a term (e.g. "pop-up") and the README doesn't contain it in the fetched portion: STOP fetching. Tell the user the term wasn't found in the documentation retrieved and offer to proceed with what is available."""

    _AUTOMATION_SUGGESTIONS_TOPIC_PROMPT = """Automation suggestions:
- Call list_automations for existing automations (avoid duplicates); search_entities / get_entity_states(domain_filter=...) for devices.
- If Node-RED configured, call get_nodered_flows — do NOT suggest automations already in Node-RED.
- New Node-RED tab: call get_nodered_flows first (check duplicates), generate valid JSON (array: tab node + nodes), call add_nodered_flow.
- Modify existing tab: call get_nodered_flows for tab id + current nodes, build updated array (tab node + all nodes with changes applied), call edit_nodered_tab with tab_id. NEVER replace all flows — that operation is not available.
- When editing an existing tab: PRESERVE existing node ids, x/y positions, names and the tab node (with its label) exactly as returned by get_nodered_flows. Change only the properties that need changing; add new nodes with new ids only where needed. Inventing new ids for existing nodes is rejected.
- Node-RED JSON format: array with one {type:"tab", id, label} node + functional nodes, each {id, type, name, wires, x, y, ...}. Node types: inject, debug, function, change, switch, delay, http request, mqtt in/out, ha-api, ha-entity, ha-state-changed, ha-call-service, ha-events-all, ha-webhook.
- Group suggestions by area/domain. Explain benefit of each.
- When Node-RED flows available, note whether suggestion fits HA automations or Node-RED.
- Offer to implement via upsert_automation (one automation per call)."""

    _MAINTENANCE_TOPIC_PROMPT = """HA maintenance:
- Use get_ha_health for repairs, failed integrations, updates, and top errors.
- Use get_automation_trace for one automation run; use find_unavailable_entities for old unavailable entities.
- Use find_cleanup_candidates for a report only. Never remove registry entries or helpers without explicit user action."""

    # Keyword triggers (checked against the current user message) and tool
    # names (checked against tool calls already made this session) that turn
    # a topic fragment on. Tool-name triggers keep the fragment loaded across
    # a multi-turn flow even if the topic word isn't repeated every message.
    _DASHBOARD_TRIGGER_KEYWORDS = (
        "dashboard", "lovelace", "card", "mushroom", "bubble-card", "bubble card",
        "mini-graph", "apexchart", "button-card", "view", "panel", "tile", "hacs",
    )
    _DASHBOARD_TRIGGER_TOOLS = frozenset({
        "list_dashboards", "create_dashboard", "delete_dashboard",
        "get_lovelace_resources", "learn_hacs_component",
    })
    _SUGGESTIONS_TRIGGER_KEYWORDS = (
        "suggest", "suggestion", "recommend", "automation idea", "node-red", "nodered", "flow",
    )
    _SUGGESTIONS_TRIGGER_TOOLS = frozenset({
        "get_nodered_flows", "add_nodered_flow", "edit_nodered_tab",
    })
    _MAINTENANCE_TRIGGER_KEYWORDS = (
        "repair", "unavailable", "error", "log", "cleanup", "clean up", "update", "trace",
        "naprawa", "niedostępn", "niedostepn", "błąd", "blad", "dziennik",
    )
    _MAINTENANCE_TRIGGER_TOOLS = frozenset({
        "get_ha_health", "get_automation_trace", "find_unavailable_entities", "find_cleanup_candidates",
    })

    @staticmethod
    def _history_tool_names(conversation_history: Optional[List[Dict[str, Any]]]) -> set:
        """Return the set of tool function names already called this session."""
        names: set = set()
        for msg in conversation_history or []:
            for tc in (msg.get("tool_calls") or []):
                fn = tc.get("function") or {}
                if fn.get("name"):
                    names.add(fn["name"])
        return names

    @classmethod
    def _select_topic_fragments(
        cls,
        user_message: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
    ) -> List[str]:
        """Return topic prompt fragments relevant to this turn, or [] if none.

        Cheap substring/tool-name check, mirroring MemoryManager's relevance
        gating — avoids paying for dashboard/HACS/Node-RED procedures on
        every turn when the user is doing something unrelated (e.g. asking
        about an automation or a memory fact). Classmethod (no instance
        state needed) so it's testable without constructing a full AgentSystem.
        """
        message_lower = (user_message or "").lower()
        used_tools = cls._history_tool_names(conversation_history)
        fragments = []

        if (any(kw in message_lower for kw in cls._DASHBOARD_TRIGGER_KEYWORDS)
                or used_tools & cls._DASHBOARD_TRIGGER_TOOLS):
            fragments.append(cls._DASHBOARD_TOPIC_PROMPT)

        if (any(kw in message_lower for kw in cls._SUGGESTIONS_TRIGGER_KEYWORDS)
                or used_tools & cls._SUGGESTIONS_TRIGGER_TOOLS):
            fragments.append(cls._AUTOMATION_SUGGESTIONS_TOPIC_PROMPT)

        if (any(kw in message_lower for kw in cls._MAINTENANCE_TRIGGER_KEYWORDS)
                or used_tools & cls._MAINTENANCE_TRIGGER_TOOLS):
            fragments.append(cls._MAINTENANCE_TOPIC_PROMPT)

        return fragments

    @staticmethod
    def _format_entity_states_compact(states: list) -> str:
        """Compact entity state format: one line per domain, entity_id[friendly_name]=state pairs."""
        from collections import defaultdict
        by_domain: Dict[str, list] = defaultdict(list)
        for s in states:
            eid = s.get("entity_id", "")
            domain = eid.split(".")[0] if "." in eid else "other"
            state = s.get("state", "?")
            fname = s.get("friendly_name")
            # Friendly name first so LLM reads purpose before the technical slug
            entry = f'"{fname}"[{eid}]={state}' if fname else f"{eid}={state}"
            by_domain[domain].append(entry)
        return "\n".join(
            f"{d}: " + ", ".join(entries)
            for d, entries in sorted(by_domain.items())
        )

    async def _dispatch_tool(self, function_name: str, function_args: dict, turn_state: Optional[dict] = None) -> dict:
        """Execute a named tool and return its result dict.

        Args:
            turn_state: Optional per-request mutable dict (retry/loop counters).
        """
        if function_name == "search_config_files":
            try:
                return await asyncio.wait_for(self.tools.search_config_files(**function_args), timeout=60.0)
            except asyncio.TimeoutError:
                return {"success": False, "error": "Tool execution timed out after 60 seconds"}
        elif function_name == "propose_config_changes":
            if "changes" not in function_args or not isinstance(function_args["changes"], list):
                error_msg = (
                    "ERROR: propose_config_changes requires a 'changes' parameter with a list of file changes. "
                    "Each change must have 'file_path' and 'new_content'. "
                    f"Received args: {function_args}"
                )
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            return await self.tools.propose_config_changes(**function_args)
        elif function_name == "patch_config_key":
            return await self.tools.patch_config_key(**function_args)
        elif function_name == "patch_config_block":
            return await self.tools.patch_config_block(**function_args)
        elif function_name in self._NAV_TOOLS:
            return await getattr(self.tools, function_name)(**function_args)
        elif function_name == "list_changesets":
            return await self.tools.list_changesets(**function_args)
        elif function_name == "restore_changeset":
            return await self.tools.restore_changeset(**function_args)
        elif function_name == "list_dashboards":
            return await self.tools.list_dashboards()
        elif function_name == "create_dashboard":
            return await self.tools.create_dashboard(**function_args)
        elif function_name == "delete_dashboard":
            return await self.tools.delete_dashboard(**function_args)
        elif function_name == "get_nodered_flows":
            try:
                return await asyncio.wait_for(self.tools.get_nodered_flows(), timeout=60.0)
            except asyncio.TimeoutError:
                return {"success": False, "error": "Tool execution timed out after 60 seconds"}
        elif function_name == "nodered_get_tab":
            return await self.tools.nodered_get_tab(**function_args)
        elif function_name == "nodered_patch_nodes":
            return await self.tools.nodered_patch_nodes(**function_args)
        elif function_name == "add_nodered_flow":
            # Normalize alternative param names Gemini sometimes uses
            import json as _json
            for alt in ("nodes", "flow_nodes", "new_nodes", "flow"):
                if alt in function_args and "flows_json" not in function_args:
                    function_args["flows_json"] = function_args.pop(alt)
                    logger.debug(f"add_nodered_flow: normalized '{alt}' -> 'flows_json'")
            if isinstance(function_args.get("flows_json"), list):
                function_args["flows_json"] = _json.dumps(function_args["flows_json"])
                logger.debug("add_nodered_flow: serialized list flows_json to JSON string")
            return await self.tools.add_nodered_flow(**function_args)
        elif function_name == "edit_nodered_tab":
            # Normalize alternative param names Gemini sometimes uses
            import json as _json
            for alt in ("new_nodes", "nodes", "updated_nodes", "flow_nodes", "new_flow", "flow", "flows"):
                if alt in function_args and "flows_json" not in function_args:
                    function_args["flows_json"] = function_args.pop(alt)
                    logger.debug(f"edit_nodered_tab: normalized '{alt}' -> 'flows_json'")
            # If LLM passed a list instead of JSON string, serialize it
            if isinstance(function_args.get("flows_json"), list):
                function_args["flows_json"] = _json.dumps(function_args["flows_json"])
                logger.debug("edit_nodered_tab: serialized list flows_json to JSON string")
            return await self.tools.edit_nodered_tab(**function_args)
        elif function_name == "get_entity_states":
            try:
                return await asyncio.wait_for(self.tools.get_entity_states(**function_args), timeout=60.0)
            except asyncio.TimeoutError:
                return {"success": False, "error": "Tool execution timed out after 60 seconds"}
        elif function_name == "get_ha_issues":
            try:
                return await asyncio.wait_for(self.tools.get_ha_issues(), timeout=30.0)
            except asyncio.TimeoutError:
                return {"success": False, "error": "Tool execution timed out after 30 seconds"}
        elif function_name == "get_ha_error_log":
            try:
                return await asyncio.wait_for(self.tools.get_ha_error_log(), timeout=30.0)
            except asyncio.TimeoutError:
                return {"success": False, "error": "Tool execution timed out after 30 seconds"}
        elif function_name == "get_lovelace_resources":
            try:
                return await asyncio.wait_for(self.tools.get_lovelace_resources(), timeout=30.0)
            except asyncio.TimeoutError:
                return {"success": False, "error": "Tool execution timed out after 30 seconds"}
        elif function_name in ("save_memory", "search_memory", "delete_memory", "memory_doctor"):
            return await getattr(self.tools, function_name)(**function_args)
        elif function_name == "search_past_sessions":
            return await self.tools.search_past_sessions(**function_args)
        elif function_name == "reload_config":
            return await self.tools.reload_config()
        elif function_name == "set_ha_text_entity":
            return await self.tools.set_ha_text_entity(**function_args)
        elif function_name == "schedule_ai_task":
            return await self.tools.schedule_ai_task(**function_args)
        elif function_name == "fetch_url":
            try:
                return await asyncio.wait_for(self.tools.fetch_url(**function_args), timeout=30.0)
            except asyncio.TimeoutError:
                return {"success": False, "error": "fetch_url timed out after 30 seconds"}
        elif function_name == "learn_hacs_component":
            try:
                return await asyncio.wait_for(self.tools.learn_hacs_component(**function_args), timeout=90.0)
            except asyncio.TimeoutError:
                return {"success": False, "error": "learn_hacs_component timed out after 90 seconds"}
        else:
            logger.error(f"Unknown tool requested: {function_name}")
            return {"success": False, "error": f"Unknown tool: {function_name}"}

    async def chat_stream(
        self,
        user_message: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        session_id: Optional[str] = None
    ):
        """
        Process a user message and stream response events in real-time.

        Args:
            user_message: The user's message/request
            conversation_history: Optional list of previous messages
                                Format: [{"role": "user"|"assistant", "content": "..."}]

        Yields:
            Dict events with:
                - event: "token" | "tool_call" | "tool_result" | "message_complete" | "complete" | "error"
                - data: JSON string with event-specific data

        Example:
            >>> async for event in agent_system.chat_stream("Enable debug logging"):
            ...     print(event)
        """
        import json

        if not self.client:
            yield {
                "event": "error",
                "data": json.dumps({
                    "error": "OpenAI API not configured. Please set OPENAI_API_KEY environment variable."
                })
            }
            return

        iteration = 0
        turn_tool_errors = 0
        turn_ok = False
        turn_model = self.model
        try:
            logger.info(f"Agent streaming user message: {user_message[:100]}...")

            # Build messages list with prompt caching support
            # Inject datetime + memory context into system prompt
            # Static parts first (core prompt, topology, topic fragments) so the
            # provider's prefix cache survives; per-turn parts (time, memory) last.
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            system_content = self.system_prompt

            # Inject home topology (areas → entities) — cached, fails silently
            topology = await self._build_home_topology()
            if topology:
                system_content = system_content + "\n\n" + topology

            # Inject topic fragments (dashboard/HACS, automation suggestions)
            # only when this turn is actually on-topic — see _select_topic_fragments.
            topic_fragments = self._select_topic_fragments(user_message, conversation_history)
            if topic_fragments:
                system_content = system_content + "\n\n" + "\n\n".join(topic_fragments)

            # Time and recalled memory change every turn. They ride on the CURRENT
            # user message (never the system prompt) so system + tools + history stay
            # a byte-identical prefix and the provider cache survives across turns.
            dynamic_content = f"Current date/time: {now_str}"
            if self.memory_manager:
                try:
                    memory_context = self.memory_manager.recall(query=user_message)
                    if memory_context:
                        dynamic_content += "\n\n" + memory_context
                except Exception as mem_err:
                    logger.warning(f"Failed to load memory context: {mem_err}")

            if self.enable_cache_control:
                # OpenRouter honours block-level markers (Anthropic, Alibaba, Google, OpenAI GPT-5.6+).
                system_message = {"role": "system", "content": [
                    {"type": "text", "text": system_content, "cache_control": {"type": "ephemeral"}},
                ]}
            else:
                system_message = {"role": "system", "content": system_content}
            messages = [system_message]

            # Track the starting point for new messages (after history)
            history_length = 1  # system message
            if conversation_history:
                # Token diet: truncate tool payloads from turns older than the
                # last completed one — they're re-billed every iteration otherwise.
                from .history_diet import truncate_old_tool_content, drop_unpaired_tool_messages
                conversation_history = truncate_old_tool_content(
                    drop_unpaired_tool_messages(conversation_history)
                )
                # Add conversation history
                # Mark the last message in history for caching if there's substantial history
                for idx, msg in enumerate(conversation_history):
                    # Remap any deprecated tool names before replaying history
                    msg = _normalize_history_msg(msg)
                    is_last_history_msg = (idx == len(conversation_history) - 1)
                    if self.enable_cache_control and is_last_history_msg and len(conversation_history) >= 3:
                        # Cache the conversation history at this breakpoint
                        msg_with_cache = dict(msg)
                        msg_with_cache["cache_control"] = {"type": "ephemeral"}
                        messages.append(msg_with_cache)
                    else:
                        messages.append(msg)
                history_length += len(conversation_history)

            # Summarize long conversations; the summary is reused per session so the
            # prefix stays stable (see _summarize_old_history).
            if conversation_history and len(conversation_history) >= _SUMMARY_KEEP_RECENT + _SUMMARY_STEP:
                try:
                    messages = await self._summarize_old_history(messages, session_id=session_id)
                except Exception as sum_err:
                    logger.warning(f"History summarization failed (continuing without): {sum_err}")

            turn_start = len(messages)
            user_msg = {"role": "user", "content": f"{user_message}\n\n<context>\n{dynamic_content}\n</context>"}
            messages.append(user_msg)

            # Define available tools for function calling with cache control
            # Mark tools for caching to reduce repeated processing
            propose_tool = {
                "type": "function",
                "function": {
                    "name": "propose_config_changes",
                    "description": "Stage whole-file changes for user approval: new files, restructuring, multi-file edits. Prefer patch_config_key (one key) or patch_config_block (one section).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "changes": {
                                "type": "array",
                                "description": "File changes.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "file_path": {
                                            "type": "string",
                                            "description": "Relative path, e.g. 'switches.yaml'. New area: 'areas/{area_id}.json' with a 'name'."
                                        },
                                        "new_content": {
                                            "type": "string",
                                            "description": "Complete new file content, unchanged lines included."
                                        }
                                    },
                                    "required": ["file_path", "new_content"]
                                }
                            },
                            "confirm_delete": {
                                "type": "boolean",
                                "description": "True only when the user explicitly asked to delete automations/scripts/scenes."
                            },
                        },
                        "required": ["changes"]
                    }
                }
            }
            if self.enable_cache_control:
                propose_tool["cache_control"] = {"type": "ephemeral"}

            dashboard_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "list_dashboards",
                        "description": "List all Lovelace dashboards. Returns url_path, title, icon, and virtual_file path for each. Call this before reading or editing a specific dashboard.",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "required": []
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "create_dashboard",
                        "description": "Create a new Lovelace dashboard. After creation, populate it with propose_config_changes using 'lovelace/{url_path}.yaml' as the file_path.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "title": {
                                    "type": "string",
                                    "description": "Human-readable dashboard title (e.g. 'Kitchen Dashboard')"
                                },
                                "url_path": {
                                    "type": "string",
                                    "description": "URL slug for the dashboard (e.g. 'kitchen'). Auto-generated from title if omitted."
                                },
                                "icon": {
                                    "type": "string",
                                    "description": "Material Design icon (e.g. 'mdi:silverware-fork-knife')"
                                }
                            },
                            "required": ["title"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "delete_dashboard",
                        "description": "Delete a Lovelace dashboard. Cannot delete the default dashboard.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "url_path": {
                                    "type": "string",
                                    "description": "Dashboard URL slug to delete (e.g. 'kitchen')"
                                }
                            },
                            "required": ["url_path"]
                        }
                    }
                },
            ]

            patch_key_tool = {
                "type": "function",
                "function": {
                    "name": "patch_config_key",
                    "description": (
                        "Change a single YAML key in place (comments/ordering preserved). "
                        "For new sections or restructuring use propose_config_changes."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": "Relative path, e.g. 'configuration.yaml'."
                            },
                            "key_path": {
                                "type": "string",
                                "description": (
                                    "Dot path, e.g. 'logger.default' or "
                                    "'automations[alias=Morning light].action[0].service'."
                                )
                            },
                            "new_value": {
                                "description": "Any JSON value. Pass mappings/lists as JSON objects/arrays, not as quoted strings."
                            },
                            "description": {
                                "type": "string",
                                "description": "Short summary for the approval card."
                            }
                        },
                        "required": ["file_path", "key_path", "new_value"]
                    }
                }
            }

            patch_block_tool = {
                "type": "function",
                "function": {
                    "name": "patch_config_block",
                    "description": (
                        "Replace one existing YAML block (e.g. one automation, the logger section); the rest of "
                        "the file is kept. Scalars: patch_config_key. New top-level keys: propose_config_changes."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": "Relative path, e.g. 'automations.yaml'."
                            },
                            "anchor": {
                                "type": "string",
                                "description": (
                                    "Dot path of the block, e.g. 'logger' or 'automations[alias=Morning light]'."
                                )
                            },
                            "new_block": {
                                "type": "string",
                                "description": "Replacement YAML for that block."
                            },
                            "description": {
                                "type": "string",
                                "description": "Short summary for the approval card."
                            }
                        },
                        "required": ["file_path", "anchor", "new_block"]
                    }
                }
            }

            # Node-RED tools only available when NODERED_URL is configured.
            # (__init__.py sets NODERED_URL from the nodered_url option; the tool
            # impls in tools.py read NODERED_URL too — must match or the schema is
            # never exposed and the agent can't see get_nodered_flows.)
            # Avoids sending ~3000 chars of NR tool schemas to users who don't use it.
            _has_nodered = bool(os.getenv("NODERED_URL"))

            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "search_config_files",
                        "description": (
                            "Search config files (YAML plus device/entity/area virtual files; the virtual files "
                            "only when search_pattern is set). Dashboards only with include_lovelace=true."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "search_pattern": {
                                    "type": "string",
                                    "description": "Case-insensitive text to match. Omit to list all files (files over 60 lines come back as an outline)."
                                },
                                "include_lovelace": {
                                    "type": "boolean",
                                    "description": "Also search Lovelace dashboards. Default false."
                                },
                                "context_lines": {
                                    "type": "integer",
                                    "description": "Context lines per match (default 12)."
                                },
                                "full_content": {
                                    "type": "boolean",
                                    "description": "Return whole files instead of snippets. Default false."
                                },
                                "force": {
                                    "type": "boolean",
                                    "description": "Allow large structured full reads only when necessary."
                                }
                            },
                            "required": []
                        }
                    }
                },
                propose_tool,
                patch_key_tool,
                patch_block_tool,
                *self._nav_tool_schemas(),
                {
                    "type": "function",
                    "function": {
                        "name": "list_changesets",
                        "description": (
                            "List recently APPLIED changesets (newest first): id, time, files, and which "
                            "automations/scripts each added/changed/removed. Use when the user wants to undo "
                            "or review earlier changes."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {"limit": {"type": "integer", "description": "Max entries (default 10)."}},
                            "required": [],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "restore_changeset",
                        "description": (
                            "Undo an applied changeset: stages the files' previous content for user approval "
                            "(same approval card and diff as any other change). Get the id from list_changesets."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {"changeset_id": {"type": "string"}},
                            "required": ["changeset_id"],
                        },
                    },
                },
                *dashboard_tools,
                {
                    "type": "function",
                    "function": {
                        "name": "get_entity_states",
                        "description": (
                            "Live entity states, one line each: domain: \"name\"[entity_id]=state. "
                            "Pass query (semantic, ~40 best matches) or domain_filter; omit both only for a full overview."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "domain_filter": {
                                    "type": "string",
                                    "description": "One HA domain, e.g. 'light'. Overrides query."
                                },
                                "query": {
                                    "type": "string",
                                    "description": "What you need, e.g. 'bedroom lights'."
                                }
                            },
                            "required": []
                        }
                    }
                },
                *self._memory_tool_schemas(),
                {
                    "type": "function",
                    "function": {
                        "name": "search_past_sessions",
                        "description": "Keyword search of past sessions, with excerpts. Use when the user refers to earlier work.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "Keywords, e.g. 'boiler schedule'."
                                },
                                "limit": {
                                    "type": "integer",
                                    "description": "Max sessions to return (default 3, max 5).",
                                    "default": 3
                                }
                            },
                            "required": ["query"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "fetch_url",
                        "description": "Fetch raw text content from a GitHub URL. Restricted to github.com, raw.githubusercontent.com, api.github.com, data.home-assistant.io. Use for fetching README files, changelogs, or YAML examples from GitHub repos.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "url": {
                                    "type": "string",
                                    "description": "Full HTTPS URL to fetch. Must be a GitHub domain."
                                },
                                "max_chars": {
                                    "type": "integer",
                                    "description": "Max chars to return (default 4000, max 8000). Use higher for full READMEs."
                                }
                            },
                            "required": ["url"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "learn_hacs_component",
                        "description": "Fetch GitHub docs (README/CHANGELOG/examples) for a HACS component. Call before writing YAML for any custom card not in memory.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description": "Component display name (e.g. 'Bubble-Card', 'Mushroom'). Used for HACS store lookup."
                                },
                                "github_url": {
                                    "type": "string",
                                    "description": "Direct GitHub repo URL (e.g. 'https://github.com/Clooos/Bubble-Card'). Use when name lookup fails or user provides URL directly."
                                }
                            },
                            "required": []
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "get_ha_issues",
                        "description": "Get all current Home Assistant issues: Watchman missing entity/service references detected in config files, and Spook/repair issues. Call this when the user asks to fix issues, mentions Watchman, Spook, missing entities, or broken references in config files.",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "required": []
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "get_ha_error_log",
                        "description": "Get Home Assistant core errors and warnings (system_log). Structured + deduplicated: logger name, message, occurrence count. Call when user reports something broken, asks about errors, or after config changes to verify nothing failed.",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "required": []
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "get_lovelace_resources",
                        "description": "List registered Lovelace frontend resources (custom card JS modules) with derived card slugs like 'bubble-card'. Call before writing dashboard YAML with custom: card types to confirm the card is installed.",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "required": []
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "reload_config",
                        "description": "Reload Home Assistant configuration (homeassistant.reload_all) without restarting. Call this after the user approves YAML changes that add new entities — input_number helpers, template sensors, scripts, automations, etc. — to activate them immediately.",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "required": []
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "set_ha_text_entity",
                        "description": (
                            "Write text to an existing input_text helper (max 255 chars, immediate, no approval). "
                            "Use for AI-generated content consumed by automations/dashboards/TTS."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "entity_id": {
                                    "type": "string",
                                    "description": "input_text entity to update, e.g. 'input_text.morning_briefing'."
                                },
                                "value": {
                                    "type": "string",
                                    "description": "Text to write. Max 255 chars — longer values are truncated."
                                }
                            },
                            "required": ["entity_id", "value"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "schedule_ai_task",
                        "description": (
                            "Create a persistent recurring AI task: runs a prompt on a schedule, writes result to an "
                            "existing input_text entity (confirm it exists via get_entity_states first)."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description": "Short label."
                                },
                                "prompt": {
                                    "type": "string",
                                    "description": "Prompt run each time; result is plain text, max 255 chars."
                                },
                                "entity_id": {
                                    "type": "string",
                                    "description": "Target input_text entity."
                                },
                                "schedule": {
                                    "type": "string",
                                    "description": "'daily HH:MM' (24h local)."
                                }
                            },
                            "required": ["name", "prompt", "entity_id", "schedule"]
                        }
                    }
                },
            ]

            # Conditionally add Node-RED tools when NODERED_URL is configured.
            # Saves ~3000 chars of tool schema tokens per iteration for users without NR.
            if _has_nodered:
                tools.extend([
                    {
                        "type": "function",
                        "function": {
                            "name": "get_nodered_flows",
                            "description": "Fetch Node-RED flows (tab IDs + nodes). Required before add_nodered_flow or edit_nodered_tab.",
                            "parameters": {
                                "type": "object",
                                "properties": {},
                                "required": []
                            }
                        }
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "nodered_get_tab",
                            "description": "Read one Node-RED tab compactly by id.",
                            "parameters": {
                                "type": "object",
                                "properties": {"tab_id": {"type": "string"}},
                                "required": ["tab_id"]
                            }
                        }
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "nodered_patch_nodes",
                            "description": "Stage targeted edits to nodes in one tab for approval; removed ids are cleared from wires.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "tab_id": {"type": "string"},
                                    "updates": {"type": "array", "items": {"type": "object",
                                        "properties": {"id": {"type": "string"}, "set": {"type": "object"}},
                                        "required": ["id", "set"]}},
                                    "add": {"type": "array", "items": {"type": "object"}},
                                    "remove": {"type": "array", "items": {"type": "string"}},
                                    "description": {"type": "string"}
                                },
                                "required": ["tab_id"]
                            }
                        }
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "add_nodered_flow",
                            "description": (
                                "Stage a NEW Node-RED flow tab for approval (never touches existing flows; "
                                "call get_nodered_flows first; to modify existing use edit_nodered_tab)."
                            ),
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "flows_json": {
                                        "type": "string",
                                        "description": "JSON array with one tab node plus its functional nodes. Example: [{\"type\":\"tab\",\"id\":\"uuid\",\"label\":\"My Flow\"},{\"type\":\"inject\",...}]"
                                    },
                                    "description": {
                                        "type": "string",
                                        "description": "Brief description of what this flow does, for the approval UI."
                                    }
                                },
                                "required": ["flows_json"]
                            }
                        }
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "edit_nodered_tab",
                            "description": (
                                "Stage an update to ONE existing Node-RED tab for approval. Requires tab_id from "
                                "get_nodered_flows; flows_json must include ALL nodes of that tab. New flows: add_nodered_flow."
                            ),
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "tab_id": {
                                        "type": "string",
                                        "description": "The id of the tab to update. Obtained from get_nodered_flows."
                                    },
                                    "flows_json": {
                                        "type": "string",
                                        "description": "JSON array with the tab node plus ALL updated functional nodes for this tab."
                                    },
                                    "description": {
                                        "type": "string",
                                        "description": "Brief description of what changed, for the approval UI."
                                    }
                                },
                                "required": ["tab_id", "flows_json"]
                            }
                        }
                    },
                ])


            _has_write_intent_flag = _has_write_intent(user_message)
            _tools_chars = len(json.dumps(tools))
            _tool_schemas = {t["function"]["name"]: t["function"].get("parameters") or {}
                             for t in tools if isinstance(t, dict) and t.get("function")}

            # Complexity routing (opt-in, COMPLEXITY_ROUTING=on): keep simple,
            # read-only Q&A turns on the cheaper research model for the whole turn.
            route_to_cheap = (
                _complexity_routing_enabled()
                and not _has_write_intent_flag
                and len(user_message) < 240
                and self.suggestion_client is not None
                and self.suggestion_model != self.config_model
            )
            if route_to_cheap:
                logger.info("[ROUTING] Simple read-only turn -> cheaper model %s for all iterations", self.suggestion_model)

            # Track tool calls and results
            new_messages = []

            # Loop to handle multiple rounds of tool calls.
            # 0/blank/invalid → default 25 (the add-on option defaults to 0; run.sh exports
            # "0", so a bare int() would set the limit to 0 and abort every turn). See env_utils.
            max_iterations = env_positive_int('MAX_ITERATIONS', 25)
            iteration = 0

            # Track cumulative token usage across all iterations
            total_input_tokens = 0
            total_output_tokens = 0
            total_cached_tokens = 0
            total_cost_usd = 0.0  # provider-reported (OpenRouter usage.cost)

            # Initialize status queue for streaming tool progress events
            self._tool_status_queue = asyncio.Queue()

            # Per-turn state (local — safe under concurrent WS connections)
            turn_state = {"retry_counts": {}, "seen_calls": {}, "pseudo_retry_used": False}
            self.tools.clear_turn_cache()

            # Pre-declare so outer except can access partial content on stream errors
            accumulated_content = ""

            while iteration < max_iterations:
                iteration += 1
                logger.info(f"[ITERATION {iteration}] Calling OpenAI streaming API")

                # No mid-turn pruning: removing blocks changes the prefix and voids the
                # provider cache for the whole rest of the turn. Size is bounded at insertion.

                # One model per turn (cache-friendly): main, or fast for opted-in simple turns.
                has_tool_results = any(m.get("role") == "tool" for m in messages)
                _use_config = not route_to_cheap
                active_model  = self.config_model  if _use_config else self.suggestion_model
                turn_model = active_model
                active_client = self.config_client if _use_config else self.suggestion_client
                logger.info(f"[ITERATION {iteration}] Model: {active_model} ({'main' if _use_config else 'research'})")
                yield {
                    "event": "model",
                    "data": json.dumps({
                        "model": active_model,
                        "role": "main" if _use_config else "fast",
                        "iteration": iteration,
                    }),
                }

                # Strip excess cache_control blocks before each call (Anthropic limit: 4 total,
                # we reserve 1 for system prompt so allow 3 in the messages list)
                safe_messages = (self._blockify_cache_control(self._strip_excess_cache_control(messages))
                                 if self.enable_cache_control else messages)

                # Plan-before-act (P0-2): on the very first call, force Gemini-family
                # models to emit a planning text response before calling any tools.
                # Claude-family models receive the instruction via system prompt (Tier A+).
                _is_text_first = _is_text_first_family(active_model)
                _force_text_first = (
                    iteration == 1
                    and not has_tool_results
                    and _is_text_first
                    and _has_write_intent_flag
                )
                if _force_text_first:
                    logger.info("[PLAN] Tier B: tool_choice=none on iter 1 (text-first family + write intent)")

                # Call OpenAI API with streaming
                # When forcing text-first (Tier B), omit tools entirely — passing tools=[...]
                # with tool_choice="none" is redundant and may cause provider validation errors.
                _sz = [len(json.dumps(m, default=str)) for m in messages]
                logger.info(
                    "[CTX] iter=%d system=%d tools=%d history=%d turn=%d max_tool=%d",
                    iteration, _sz[0], 0 if _force_text_first else _tools_chars,
                    sum(_sz[1:turn_start]), sum(_sz[turn_start:]),
                    max((s for s, m in zip(_sz, messages) if m.get("role") == "tool"), default=0),
                )
                api_params = {
                    "model": active_model,
                    "messages": safe_messages,
                    "stream": True
                }
                if _force_text_first:
                    api_params["tool_choice"] = "none"
                else:
                    api_params["tools"] = tools
                    api_params["tool_choice"] = "auto"

                # Add usage tracking based on per-phase mode
                active_usage_tracking = self.config_usage_tracking if _use_config else self.suggestion_usage_tracking
                _is_openrouter = 'openrouter' in str(getattr(active_client, 'base_url', '')).lower()
                if active_usage_tracking == 'stream_options':
                    api_params["stream_options"] = {"include_usage": True}
                # OpenRouter returns real USD cost + native token counts ONLY when usage
                # accounting is requested via extra_body (a top-level `usage` param is
                # rejected by the OpenAI client). Harmless for other providers — ignored,
                # and stripped on rejection below. This is what fixes the usage=0 logs.
                if active_usage_tracking != 'disabled' and (_is_openrouter or active_usage_tracking == 'usage'):
                    api_params["extra_body"] = {"usage": {"include": True}}
                # Sticky provider routing keeps the prompt cache warm across iterations.
                if _is_openrouter and session_id:
                    api_params.setdefault("extra_body", {})["session_id"] = str(session_id)[:256]
                if _is_openrouter and self.reasoning_effort:
                    api_params.setdefault("extra_body", {})["reasoning"] = {"effort": self.reasoning_effort}
                # If disabled, don't add any usage tracking parameters

                # Add temperature if specified
                if self.temperature is not None:
                    api_params["temperature"] = self.temperature

                # Add per-phase max_tokens if configured (FEAT-7)
                phase_max_tokens = self.config_max_tokens if has_tool_results else self.suggestion_max_tokens
                if phase_max_tokens:
                    api_params["max_tokens"] = phase_max_tokens

                # Determine which client slot we're using for BUG-8 per-client caching
                client_slot = 'config' if _use_config else 'suggestion'

                # Strip usage-tracking params if we already know this client rejects them
                if self._client_usage_ok.get(client_slot) is False:
                    api_params = self._strip_usage_params(api_params)

                async def _create_stream(params, slot=client_slot, client=active_client):
                    try:
                        created = await client.chat.completions.create(**params)
                        if self._client_usage_ok.get(slot) is None:
                            self._client_usage_ok[slot] = True
                        return created
                    except Exception as api_err:
                        # Some providers (e.g. Anthropic/Haiku) reject stream_options or
                        # usage params — retry without them to keep things working.
                        err_str = str(api_err).lower()
                        if 'stream_options' in err_str or 'usage' in err_str or 'extra' in err_str or 'unknown' in err_str:
                            logger.warning(f"API rejected usage-tracking params, retrying without them: {api_err}")
                            # Mark ONLY this slot — slots can use different models/endpoints.
                            self._client_usage_ok[slot] = False
                            retry_params = self._strip_usage_params(params)
                            return await client.chat.completions.create(**retry_params)
                        raise

                # Idle-timeout guard: a stalled provider used to freeze the run
                # (7.5 min in create() on 2026-10-16 — the OpenAI client default is
                # 600 s). Applies to the initial request and to every chunk gap.
                # Heartbeats keep the UI informed while waiting.
                _idle_timeout = float(os.getenv("STREAM_IDLE_TIMEOUT_S", "120"))

                def _heartbeat(elapsed, phase):
                    return {
                        "event": "heartbeat",
                        "data": json.dumps({
                            "phase": phase,
                            "elapsed": int(elapsed),
                            "model": active_model,
                            "iteration": iteration,
                        }),
                    }

                def _stall_error():
                    logger.warning(
                        f"[STREAM] Idle timeout ({_idle_timeout}s) — provider stalled "
                        f"(model={active_model}, iteration {iteration}), aborting run"
                    )
                    return {
                        "event": "error",
                        "data": json.dumps({
                            "error": f"{active_model} stopped responding (no data for "
                                     f"{int(_idle_timeout)}s). Please try again or pick another model."
                        })
                    }

                stream = None
                try:
                    async for _kind, _val in _await_with_heartbeat(_create_stream(api_params), _idle_timeout):
                        if _kind == "heartbeat":
                            yield _heartbeat(_val, "waiting_model")
                        else:
                            stream = _val
                except asyncio.TimeoutError:
                    yield _stall_error()
                    return

                # Accumulate the streaming response
                accumulated_content = ""
                accumulated_tool_calls = []
                reasoning_chunks = []
                current_tool_call = None
                tool_calls_announced = False
                tool_calls_pending_announced = False
                # Track token usage
                input_tokens = 0
                output_tokens = 0
                cached_tokens = 0
                call_cost = 0.0  # OpenRouter returns actual USD cost in usage.cost
                served_model = active_model  # router slugs (openrouter/auto) report the real model per chunk

                _stream_iter = stream.__aiter__()
                while True:
                    _has_chunk, chunk = False, None
                    try:
                        async for _kind, _val in _await_with_heartbeat(_next_chunk(_stream_iter), _idle_timeout):
                            if _kind == "heartbeat":
                                yield _heartbeat(_val, "streaming")
                            else:
                                _has_chunk, chunk = _val
                    except asyncio.TimeoutError:
                        yield _stall_error()
                        return
                    if not _has_chunk:
                        break
                    if isinstance(getattr(chunk, 'model', None), str) and chunk.model:
                        served_model = chunk.model
                    # Guard: some providers (e.g. Anthropic/Haiku) send a final
                    # usage-only chunk with an empty choices array.
                    if not chunk.choices:
                        if active_usage_tracking != 'disabled' and hasattr(chunk, 'usage') and chunk.usage:
                            input_tokens = getattr(chunk.usage, 'prompt_tokens', 0) or getattr(chunk.usage, 'input_tokens', 0)
                            output_tokens = getattr(chunk.usage, 'completion_tokens', 0) or getattr(chunk.usage, 'output_tokens', 0)
                            if hasattr(chunk.usage, 'cached_tokens'):
                                cached_tokens = chunk.usage.cached_tokens or 0
                            elif hasattr(chunk.usage, 'prompt_tokens_details') and chunk.usage.prompt_tokens_details:
                                cached_tokens = getattr(chunk.usage.prompt_tokens_details, 'cached_tokens', 0)
                            call_cost = getattr(chunk.usage, 'cost', 0) or call_cost
                            total_input_tokens += input_tokens
                            total_output_tokens += output_tokens
                            total_cached_tokens += cached_tokens
                        continue

                    delta = chunk.choices[0].delta

                    # Capture token usage if available (present in final chunk)
                    # Only attempt to parse if usage tracking is not disabled
                    if active_usage_tracking != 'disabled' and hasattr(chunk, 'usage') and chunk.usage:
                        input_tokens = getattr(chunk.usage, 'prompt_tokens', 0) or getattr(chunk.usage, 'input_tokens', 0)
                        output_tokens = getattr(chunk.usage, 'completion_tokens', 0) or getattr(chunk.usage, 'output_tokens', 0)

                        # Check for cached tokens - supports multiple API formats
                        if hasattr(chunk.usage, 'cached_tokens'):
                            cached_tokens = chunk.usage.cached_tokens or 0
                        elif hasattr(chunk.usage, 'prompt_tokens_details') and chunk.usage.prompt_tokens_details:
                            cached_tokens = getattr(chunk.usage.prompt_tokens_details, 'cached_tokens', 0)
                        elif hasattr(chunk.usage, 'cached_content_token_count'):
                            cached_tokens = chunk.usage.cached_content_token_count or 0

                        call_cost = getattr(chunk.usage, 'cost', 0) or call_cost
                        logger.debug(f"[USAGE] Parsed - Input: {input_tokens}, Output: {output_tokens}, Cached: {cached_tokens}, Cost: {call_cost}")

                        # Accumulate totals
                        total_input_tokens += input_tokens
                        total_output_tokens += output_tokens
                        total_cached_tokens += cached_tokens

                    # Stream content tokens
                    _rd = (getattr(delta, "model_extra", None) or {}).get("reasoning_details")
                    if isinstance(_rd, list):
                        reasoning_chunks.extend(_rd)

                    if delta.content:
                        accumulated_content += delta.content
                        logger.debug(f"[STREAM] Yielding token: {delta.content[:50]}")
                        yield {
                            "event": "token",
                            "data": json.dumps({
                                "content": delta.content,
                                "iteration": iteration
                            })
                        }

                    # Handle tool calls
                    if delta.tool_calls:
                        for tool_call_delta in delta.tool_calls:
                            # Initialize new tool call
                            # Handle index if provided (OpenAI format), otherwise default to 0 (Google format)
                            index = tool_call_delta.index if tool_call_delta.index is not None else 0

                            # Ensure we have a slot for this tool call
                            while len(accumulated_tool_calls) <= index:
                                accumulated_tool_calls.append({
                                    "id": "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""}
                                })

                            current_tool_call = accumulated_tool_calls[index]

                            # Update tool call details
                            if tool_call_delta.id:
                                current_tool_call["id"] = tool_call_delta.id
                            if tool_call_delta.function:
                                if tool_call_delta.function.name:
                                    current_tool_call["function"]["name"] = tool_call_delta.function.name
                                if tool_call_delta.function.arguments:
                                    current_tool_call["function"]["arguments"] += tool_call_delta.function.arguments

                    # Finish reason marks end of generation, but OpenRouter/OpenAI
                    # append a trailing usage-only chunk (empty choices) AFTER this
                    # one. Breaking here skipped it, so token/cost totals logged as 0.
                    # Keep draining — the iterator ends (StopAsyncIteration) once the
                    # stream closes, which captures the trailing usage chunk above.
                    if chunk.choices[0].finish_reason:
                        continue

                # Record per-call usage/cost for this iteration (Track 1).
                if self.usage_manager is not None:
                    try:
                        self.usage_manager.record(
                            session_id=session_id,
                            # This IS the main agent loop. client_slot only picks
                            # the model (suggestion slot when a read-only turn is
                            # routed to the cheap model) — recording it as the phase
                            # mislabeled normal turns as 'suggestion' in the Usage
                            # tab. The model is already captured in `model` below.
                            phase="main_agent",
                            model=served_model,
                            iteration=iteration,
                            input_tokens=input_tokens,
                            cached_tokens=cached_tokens,
                            output_tokens=output_tokens,
                            cost_usd=call_cost,
                        )
                    except Exception as _uerr:
                        logger.debug("usage record failed: %s", _uerr)
                total_cost_usd += call_cost or 0.0
                turn_model = served_model

                # Check if we have tool calls
                if not accumulated_tool_calls:
                    # Pseudo-tool-call guard: some models emit tool-call markup as TEXT
                    # (DeepSeek DSML, Anthropic-style <invoke name=...>) instead of using
                    # the tool-calling API - typically when tools were omitted from the
                    # request. Accepting that as the final answer ends the run having
                    # executed nothing. Retry ONCE with tools bound: the retry runs at
                    # iteration >= 2, where _force_text_first (iteration == 1 only) is
                    # already False, so tools get attached with no extra logic.
                    # Bounded by a single-use flag - worst case is one wasted call, never
                    # a loop. The stale markup already streamed to the UI is overwritten
                    # by the authoritative text in the final message_complete.
                    if (_looks_like_pseudo_tool_call(accumulated_content)
                            and not turn_state["pseudo_retry_used"]):
                        turn_state["pseudo_retry_used"] = True
                        logger.warning(
                            "[PSEUDO-TOOL-CALL] Iteration %s returned tool-call markup as text "
                            "(model=%s) - retrying once with tools bound",
                            iteration, active_model,
                        )
                        messages.append({"role": "assistant", "content": accumulated_content})
                        messages.append({
                            "role": "user",
                            "content": (
                                "[SYSTEM] Your previous reply printed tool-call markup as plain "
                                "text instead of making an actual tool call. Tools ARE available "
                                "on this request. Either call the tool through the native "
                                "tool-calling API, or answer directly in prose if no tool is "
                                "needed. Never print tool-call markup as text."
                            ),
                        })
                        continue

                    # No tool calls - final response
                    logger.info(f"[ITERATION {iteration}] No tool calls, final response received")

                    # Send message complete event with full message data
                    assistant_message = {
                        "role": "assistant",
                        "content": accumulated_content
                    }
                    new_messages.append(assistant_message)

                    yield {
                        "event": "message_complete",
                        "data": json.dumps({
                            "message": assistant_message,
                            "iteration": iteration,
                            "usage": {
                                "input_tokens": input_tokens,
                                "output_tokens": output_tokens,
                                "cached_tokens": cached_tokens,
                                "total_tokens": input_tokens + output_tokens
                            }
                        })
                    }
                    break

                # We have tool calls - add assistant message to history
                logger.info(f"[ITERATION {iteration}] Processing {len(accumulated_tool_calls)} tool call(s)")

                assistant_message = {
                    "role": "assistant",
                    "content": accumulated_content,
                    "tool_calls": accumulated_tool_calls
                }
                # reasoning_details go back to the provider within this turn only;
                # new_messages is persisted, so it gets the plain message.
                if reasoning_chunks:
                    messages.append({**assistant_message, "reasoning_details": _merge_reasoning_details(reasoning_chunks)})
                else:
                    messages.append(assistant_message)
                new_messages.append(assistant_message)

                # Notify about ALL tool calls upfront before executing any (only if not already announced)
                if not tool_calls_announced:
                    yield {
                        "event": "tool_call",
                        "data": json.dumps({
                            "tool_calls": accumulated_tool_calls,
                            "iteration": iteration
                        })
                    }
                    tool_calls_announced = True

                # Execute each tool call and stream results immediately
                for tool_idx, tool_call in enumerate(accumulated_tool_calls):
                    function_name = tool_call["function"]["name"]
                    function_args, _args_parse_error = self._parse_tool_args(tool_call["function"]["arguments"])
                    _args_schema_error = None
                    if not _args_parse_error:
                        function_args, _args_schema_error = _validate_tool_args(
                            function_name, function_args, _tool_schemas.get(function_name))
                    if _args_parse_error:
                        logger.warning(
                            f"[ITERATION {iteration}] Tool '{function_name}' arguments failed to "
                            f"parse as JSON: {_args_parse_error}"
                        )

                    logger.info(f"[ITERATION {iteration}] Calling tool: {function_name}")

                    # Send individual tool execution start event
                    yield {
                        "event": "tool_start",
                        "data": json.dumps({
                            "tool_call_id": tool_call["id"],
                            "function": function_name,
                            "arguments": function_args,
                            "iteration": iteration
                        })
                    }

                    # PRE-DISPATCH GUARD: block repeated fetch calls before they execute.
                    # Appending text to results is not enough — LLM ignores it and keeps calling.
                    # Blocking before execution is the only reliable way to stop infinite fetch loops.
                    _FETCH_GUARD_TOOLS = {"fetch_url", "learn_hacs_component"}
                    _pre_call_key = (function_name, json.dumps(function_args, sort_keys=True))
                    _pre_call_count = turn_state["seen_calls"].get(_pre_call_key, 0)
                    _pre_fn_total = turn_state["seen_calls"].get(f"__fn__{function_name}", 0)
                    _blocked_error = None
                    if function_name in _FETCH_GUARD_TOOLS:
                        if _pre_call_count >= 2:
                            _blocked_error = (
                                f"BLOCKED: '{function_name}' with these exact arguments already called "
                                f"{_pre_call_count} times — result will not change. "
                                "Do NOT retry. Use content already retrieved. "
                                "Call propose_config_changes or respond to the user now."
                            )
                        elif _pre_fn_total >= 3:
                            _blocked_error = (
                                f"BLOCKED: '{function_name}' called {_pre_fn_total} times this turn — "
                                "no more fetches allowed. Use what you already have. "
                                "Call propose_config_changes or respond to the user now."
                            )

                    if _args_parse_error:
                        result = {
                            "success": False,
                            "error": (
                                f"Malformed tool call arguments: {_args_parse_error}. "
                                "The JSON generated for this tool call was invalid — likely an "
                                "unescaped quote, backslash, or literal newline inside a string "
                                "field (e.g. YAML content). Retry the same tool call now with "
                                "valid JSON: escape special characters inside any string values."
                            ),
                        }
                    elif _args_schema_error:
                        result = {"success": False, "error": _args_schema_error + " Retry with corrected arguments."}
                    elif _blocked_error:
                        result = {"success": False, "blocked": True, "error": _blocked_error}
                        logger.warning(
                            f"[LOOP] PRE-DISPATCH BLOCKED '{function_name}' "
                            f"(exact_count={_pre_call_count}, fn_total={_pre_fn_total})"
                        )
                    else:
                        # Execute the tool via a task, yielding status events while waiting
                        tool_task = asyncio.create_task(self._dispatch_tool(function_name, function_args, turn_state))
                        _tool_started = time.monotonic()
                        _last_hb = _tool_started
                        try:
                            while not tool_task.done():
                                while not self._tool_status_queue.empty():
                                    yield {"event": "tool_status", "data": json.dumps(self._tool_status_queue.get_nowait())}
                                await asyncio.sleep(0.05)
                                if time.monotonic() - _last_hb >= HEARTBEAT_INTERVAL_S:
                                    _last_hb = time.monotonic()
                                    yield {"event": "heartbeat", "data": json.dumps({
                                        "phase": "tool",
                                        "tool": function_name,
                                        "elapsed": int(_last_hb - _tool_started),
                                        "iteration": iteration,
                                    })}
                        finally:
                            if not tool_task.done():
                                tool_task.cancel()
                        # Drain any remaining status events before announcing the result
                        while not self._tool_status_queue.empty():
                            yield {"event": "tool_status", "data": json.dumps(self._tool_status_queue.get_nowait())}
                        try:
                            result = tool_task.result()
                        except Exception as e:
                            result = {"success": False, "error": str(e)}
                    logger.info(f"[ITERATION {iteration}] Tool '{function_name}' done: success={result.get('success')}, blocked={result.get('blocked', False)}")

                    if result.get("success") and result.get("changeset_id"):
                        _problems = await self._precheck_changeset(result["changeset_id"])
                        if _problems:
                            result["config_check_errors"] = _problems


                    # Loop detection: track (tool, args) pairs and per-function call counts.
                    _call_key = (function_name, json.dumps(function_args, sort_keys=True))
                    _seen_calls = turn_state["seen_calls"]
                    _call_count = _seen_calls.get(_call_key, 0) + 1
                    _seen_calls[_call_key] = _call_count
                    # Also track total calls per function name (catches alternating-arg loops).
                    _fn_count_key = f"__fn__{function_name}"
                    _fn_total = _seen_calls.get(_fn_count_key, 0) + 1
                    _seen_calls[_fn_count_key] = _fn_total

                    # Error recovery: inject a retry directive for correctable failures.
                    # Skip for: successes, approval-gated results (changeset_id), timed-out calls, blocked calls.
                    tool_result_content = json.dumps(result)
                    tool_result_content = _cap_tool_result(function_name, tool_result_content)
                    _is_failure = not result.get("success", True) and not result.get("blocked", False)
                    _is_approval_gated = "changeset_id" in result
                    _is_infra_error = "timed out" in result.get("error", "").lower()
                    if result.get("config_check_errors"):
                        tool_result_content += (
                            "\n\n[HA config check of this proposal reports NEW problems (see config_check_errors). "
                            "Fix exactly the reported keys now with the same tool — your fix merges into this "
                            "pending change. Do not tell the user it is ready until the check is clean.]"
                        )
                    if _is_failure and not _is_approval_gated and not _is_infra_error:
                        turn_tool_errors += 1
                        _err_msg = result.get("error", "")
                        if _err_msg:
                            logger.warning(f"[ITERATION {iteration}] Tool '{function_name}' error: {_err_msg[:300]}")
                        _tool_key = function_name
                        _retry_counts = turn_state["retry_counts"]
                        _retry_n = _retry_counts.get(_tool_key, 0) + 1
                        _retry_counts[_tool_key] = _retry_n
                        if _retry_n <= 2:
                            tool_result_content += (
                                f"\n\n[Auto-retry {_retry_n}/2: The above call failed. "
                                "Analyse the error carefully, correct the parameters, and call the same tool again "
                                "with fixed arguments before doing anything else.]"
                            )
                            logger.info(f"[RETRY] Injecting retry directive {_retry_n}/2 for '{function_name}'")
                        else:
                            tool_result_content += (
                                "\n\n[Max retries (2) reached for this tool call. "
                                "Stop retrying and inform the user about the error clearly.]"
                            )
                            logger.warning(f"[RETRY] Max retries reached for '{function_name}'")

                    # Exact-duplicate guard: same tool + same args called again.
                    if _call_count == 2:
                        tool_result_content += (
                            f"\n\n[DUPLICATE CALL: You already called '{function_name}' with these exact arguments "
                            "and received the same result. Do NOT call it again with the same arguments. "
                            "Use the information already retrieved — read a different file or call propose_config_changes now.]"
                        )
                        logger.warning(f"[LOOP] Duplicate call detected for '{function_name}' (exact, count=2)")
                    elif _call_count >= 3:
                        tool_result_content += (
                            f"\n\n[LOOP DETECTED: '{function_name}' called {_call_count} times with identical arguments. "
                            "You are stuck. STOP. Either call propose_config_changes with what you have, "
                            "or respond to the user explaining what you cannot find.]"
                        )
                        logger.warning(f"[LOOP] Hard-stop injected for '{function_name}' (exact, count={_call_count})")
                    _FETCH_TOOLS    = {"fetch_url", "learn_hacs_component"}
                    if _fn_total == 3 and function_name in _FETCH_TOOLS:
                        tool_result_content += (
                            f"\n\n[FETCH LIMIT: You have called '{function_name}' {_fn_total} times this turn. "
                            "Do NOT fetch more URLs. Use the content already retrieved — "
                            "distill what you have into memory files or respond to the user now.]"
                        )
                        logger.warning(f"[LOOP] Fetch volume limit reached for '{function_name}' (total={_fn_total})")
                    elif _fn_total >= 4 and function_name in _FETCH_TOOLS:
                        tool_result_content += (
                            f"\n\n[HARD FETCH LIMIT: '{function_name}' called {_fn_total} times this turn. "
                            "Further fetches are BLOCKED. You MUST save what you have via save_memory "
                            "or respond to the user. Do not call fetch_url or learn_hacs_component again.]"
                        )
                        logger.warning(f"[LOOP] Hard fetch volume limit for '{function_name}' (total={_fn_total})")

                    # Add tool result to messages with cache control on the last tool result
                    is_last_tool = (tool_idx == len(accumulated_tool_calls) - 1)
                    tool_message = {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": tool_result_content
                    }
                    # Mark the last tool result for caching to preserve full context
                    if self.enable_cache_control and is_last_tool:
                        tool_message["cache_control"] = {"type": "ephemeral"}

                    messages.append(tool_message)
                    new_messages.append(tool_message)

                    # Notify about tool result immediately after execution.
                    # For propose_config_changes, echo back the original arguments so the
                    # frontend can build the approval card without relying on tool_start lookup.
                    tool_result_data: Dict[str, Any] = {
                        "tool_call_id": tool_call["id"],
                        "function": function_name,
                        "result": result,
                        "iteration": iteration,
                    }
                    if function_name == "propose_config_changes" and result.get("success"):
                        tool_result_data["arguments"] = function_args
                    yield {
                        "event": "tool_result",
                        "data": json.dumps(tool_result_data)
                    }

                # Tier A+ retroactive nudge (Claude-family): if model called tools on
                # iter 1 without emitting any reasoning text, patch the last tool result
                # so the next iteration is prompted to state its plan.
                if (iteration == 1
                        and not _is_text_first
                        and not accumulated_content.strip()
                        and new_messages):
                    for _tm in reversed(new_messages):
                        if _tm.get("role") == "tool":
                            _tm["content"] += (
                                "\n\n[PLAN REQUIRED: You called tools without stating your intent. "
                                "Before calling more tools, briefly state in 1-2 sentences: "
                                "(1) what the user needs, (2) what you already know from Memory "
                                "and Home Layout, (3) your next action. Answer directly without "
                                "tools if the information is already available.]"
                            )
                            logger.info("[PLAN] Tier A+ retroactive nudge injected (iter 1, no plan text)")
                            break

            # Send completion event with all new messages
            if iteration >= max_iterations:
                logger.warning(f"Hit max iterations ({max_iterations}), stopping")
                yield {
                    "event": "error",
                    "data": json.dumps({
                        "error": "Maximum iteration limit reached. Please try breaking down your request."
                    })
                }

            logger.info(f"Agent completed after {iteration} iteration(s) - Total tokens: {total_input_tokens + total_output_tokens} (input: {total_input_tokens}, output: {total_output_tokens}, cached: {total_cached_tokens})")

            cost_usd = total_cost_usd or None
            turn_ok = iteration < max_iterations

            usage_data = {
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "cached_tokens": total_cached_tokens,
                "total_tokens": total_input_tokens + total_output_tokens
            }
            if cost_usd is not None:
                usage_data["cost_usd"] = round(cost_usd, 6)

            yield {
                "event": "complete",
                "data": json.dumps({
                    "messages": new_messages,
                    "iterations": iteration,
                    "usage": usage_data
                })
            }

            # Fire-and-forget memory consolidation — runs after response is delivered.
            # Gate: only run when message content signals a durable new fact, and session
            # is established enough (≥5 user turns) to contain worth-keeping information.
            # The injected <context> (recalled memories) must not be re-extracted as new facts.
            all_messages = [{"role": "user", "content": user_message} if m is user_msg else m
                            for m in messages] + new_messages
            user_turns = sum(1 for m in all_messages if m.get("role") == "user")
            if user_turns >= 5 and self._should_consolidate(user_message, accumulated_content):
                asyncio.ensure_future(self._run_memory_consolidation(all_messages))

        except Exception as e:
            from openai import APIError
            if isinstance(e, APIError) and accumulated_content:
                # Stream closed mid-response (e.g. "JSON error injected into SSE stream")
                # but we already have content — deliver what we received rather than erroring.
                logger.warning(f"Stream interrupted with partial content, delivering as-is: {e}")
                assistant_message = {"role": "assistant", "content": accumulated_content}
                yield {
                    "event": "message_complete",
                    "data": json.dumps({"message": assistant_message, "usage": {}, "truncated": True})
                }
                yield {
                    "event": "complete",
                    "data": json.dumps({"messages": [assistant_message], "iterations": iteration, "usage": {}, "truncated": True})
                }
            else:
                logger.error(f"Agent streaming error: {e}", exc_info=True)
                yield {
                    "event": "error",
                    "data": json.dumps({"error": str(e)})
                }
        finally:
            # Every terminal path (success, error, watchdog timeout, stop) counts once.
            self._record_turn(session_id, turn_ok, iteration, turn_tool_errors, turn_model)

    def _record_turn(self, session_id, ok, iterations, tool_errors, model=None) -> None:
        if self.usage_manager is not None:
            self.usage_manager.record_turn(session_id=session_id, model=model or self.model, ok=ok,
                                           iterations=iterations, tool_errors=tool_errors)

    @staticmethod
    def _parse_tool_args(arguments: Optional[str]) -> tuple:
        """
        Parse tool-call arguments JSON. LLM can emit invalid JSON (e.g. unescaped
        quote/newline inside a YAML content field) — must not crash the whole run,
        so return (args, error) and let the caller route the error through the
        retry-directive pipeline like any other tool failure.
        """
        import json
        try:
            return json.loads(arguments or "{}"), None
        except json.JSONDecodeError as e:
            return {}, str(e)

    @staticmethod
    def _strip_usage_params(params: Dict[str, Any]) -> Dict[str, Any]:
        """Drop usage-tracking params but keep other extra_body keys (e.g. session_id)."""
        out = {k: v for k, v in params.items() if k not in ('stream_options', 'usage', 'extra_body')}
        rest = {k: v for k, v in (params.get('extra_body') or {}).items() if k != 'usage'}
        if rest:
            out['extra_body'] = rest
        return out

    @staticmethod
    def _blockify_cache_control(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Move message-level cache_control onto a text content block (request copy only).

        Providers behind OpenRouter read the marker from content blocks; a top-level
        message key is ignored. Stored history keeps the simple string form.
        """
        out = []
        for m in messages:
            if "cache_control" not in m:
                out.append(m)
                continue
            m2 = {k: v for k, v in m.items() if k != "cache_control"}
            if isinstance(m2.get("content"), str) and m2["content"]:
                m2["content"] = [{"type": "text", "text": m2["content"], "cache_control": m["cache_control"]}]
            out.append(m2)
        return out

    @staticmethod
    def _strip_excess_cache_control(messages: List[Dict[str, Any]], max_blocks: int = 3) -> List[Dict[str, Any]]:
        """
        Anthropic allows at most 4 cache_control blocks total (system + tools + messages).
        We reserve 1 for the system prompt and keep at most `max_blocks` in messages.
        Strip from oldest messages first so newest context is always cached.
        """
        # Collect indices of messages that carry cache_control
        cached_indices = []
        for i, m in enumerate(messages):
            has_cc = bool(m.get("cache_control"))
            if not has_cc and isinstance(m.get("content"), list):
                has_cc = any(
                    isinstance(b, dict) and b.get("cache_control")
                    for b in m["content"]
                )
            if has_cc:
                cached_indices.append(i)

        if len(cached_indices) <= max_blocks:
            return messages

        # Strip cache_control from oldest over-limit entries
        strip_set = set(cached_indices[: len(cached_indices) - max_blocks])
        result = []
        for i, m in enumerate(messages):
            if i not in strip_set:
                result.append(m)
                continue
            m2 = dict(m)
            m2.pop("cache_control", None)
            if isinstance(m2.get("content"), list):
                m2["content"] = [
                    {k: v for k, v in b.items() if k != "cache_control"} if isinstance(b, dict) else b
                    for b in m2["content"]
                ]
            result.append(m2)
        return result

    async def _build_home_topology(self) -> str:
        """
        Build a compact home layout string (areas → entities) for system prompt injection.
        Cached for 10 minutes to avoid repeated WebSocket calls.
        Returns empty string on failure — topology is advisory, never required.
        """
        _TTL = int(os.getenv("TOPOLOGY_CACHE_TTL", "600"))  # default 10 minutes
        if (self._topology_cache is not None and
                self._topology_cache_time is not None and
                (datetime.now(timezone.utc) - self._topology_cache_time).total_seconds() < _TTL):
            return self._topology_cache

        try:
            areas: Dict[str, str] = {}        # area_id → name
            entity_area: Dict[str, str] = {}  # entity_id → area_id
            entity_state: Dict[str, tuple] = {}  # entity_id → (state, friendly_name)

            hass = self.config_manager.hass if self.config_manager else None
            if hass:
                # Custom component mode — use in-memory registries (fast, no I/O)
                area_reg = hass.area_registry.async_get()
                for area in area_reg.areas.values():
                    areas[area.id] = area.name
                ent_reg = hass.entity_registry.async_get()
                for ent in ent_reg.entities.values():
                    if ent.area_id:
                        entity_area[ent.entity_id] = ent.area_id
                for state in hass.states.async_all():
                    entity_state[state.entity_id] = (
                        state.state,
                        state.attributes.get("friendly_name", state.entity_id)
                    )
            else:
                # Add-on mode — 3 WebSocket calls (cached, so only every 10 min)
                supervisor_token = os.getenv('SUPERVISOR_TOKEN')
                if not supervisor_token:
                    return ""
                from ..ha.ha_websocket import HomeAssistantWebSocket
                ws = HomeAssistantWebSocket("ws://supervisor/core/websocket", supervisor_token)
                await ws.connect()
                try:
                    for a in await ws.list_areas():
                        areas[a["area_id"]] = a["name"]
                    for e in await ws.list_entities():
                        if e.get("area_id"):
                            entity_area[e["entity_id"]] = e["area_id"]
                    for s in await ws.get_states():
                        entity_state[s["entity_id"]] = (
                            s.get("state", ""),
                            s.get("attributes", {}).get("friendly_name", s["entity_id"])
                        )
                finally:
                    await ws.close()

            if not entity_state:
                return ""

            # Domain counts
            domain_counts: Dict[str, int] = {}
            for eid in entity_state:
                d = eid.split(".")[0]
                domain_counts[d] = domain_counts.get(d, 0) + 1

            SHOW_DOMAINS = ["light", "switch", "climate", "media_player", "cover",
                            "vacuum", "fan", "lock", "sensor", "binary_sensor",
                            "scene", "script", "automation", "input_boolean"]
            domain_parts = [f"{domain_counts[d]} {d}" for d in SHOW_DOMAINS if d in domain_counts]
            other = sum(v for k, v in domain_counts.items() if k not in SHOW_DOMAINS)
            if other:
                domain_parts.append(f"{other} other")

            lines = [f"## Home Layout — {len(entity_state)} entities: {', '.join(domain_parts)}"]

            # Group actionable entities by area (skip pure diagnostic domains)
            SKIP_DOMAINS = {"logbook", "persistent_notification", "zone", "sun", "weather",
                            "update", "person", "device_tracker"}
            area_buckets: Dict[str, List[str]] = {aid: [] for aid in areas}
            unassigned_count = 0
            for eid, (_state, fname) in entity_state.items():
                if eid.split(".")[0] in SKIP_DOMAINS:
                    continue
                aid = entity_area.get(eid)
                if aid and aid in area_buckets:
                    label = fname if fname and fname != eid else eid
                    area_buckets[aid].append(label)
                else:
                    unassigned_count += 1

            # Format each area (max 5 entities shown per area to keep it compact)
            MAX_CHARS = 1500
            for aid, aname in areas.items():
                bucket = area_buckets.get(aid, [])
                if not bucket:
                    continue
                shown = bucket[:5]
                rest = len(bucket) - len(shown)
                line = f"**{aname}**: {', '.join(shown)}"
                if rest:
                    line += f", +{rest} more"
                lines.append(line)

            if unassigned_count:
                lines.append(f"*({unassigned_count} entities not assigned to any area)*")

            result = "\n".join(lines)
            # Hard cap to stay within token budget
            if len(result) > MAX_CHARS:
                result = result[:MAX_CHARS] + "\n*(topology truncated)*"

            self._topology_cache = result
            self._topology_cache_time = datetime.now(timezone.utc)
            logger.info(f"Home topology built: {len(areas)} areas, {len(entity_state)} entities, {len(result)} chars")
            return result

        except Exception as e:
            logger.warning(f"Failed to build home topology (non-fatal): {e}")
            return ""

    def _record_response_usage(self, response, *, phase: str, model: str,
                               session_id: Optional[str] = None, iteration: int = 0) -> None:
        """Record token/cost from a NON-streaming completion response. Never raises.

        Standalone (non-agent-loop) LLM calls — summarization, consolidation, task
        runs, clear-all extraction — otherwise show $0 in the Usage tab because they
        bypass the streaming loop's record(). See generate_suggestions for the origin
        of this pattern.
        """
        if self.usage_manager is None:
            return
        try:
            u = getattr(response, "usage", None)
            if not u:
                return
            in_tok = getattr(u, 'prompt_tokens', 0) or getattr(u, 'input_tokens', 0) or 0
            out_tok = getattr(u, 'completion_tokens', 0) or getattr(u, 'output_tokens', 0) or 0
            cached = 0
            if hasattr(u, 'cached_tokens'):
                cached = u.cached_tokens or 0
            elif getattr(u, 'prompt_tokens_details', None):
                cached = getattr(u.prompt_tokens_details, 'cached_tokens', 0) or 0
            self.usage_manager.record(
                session_id=session_id,
                phase=phase,
                model=model,
                iteration=iteration,
                input_tokens=in_tok,
                cached_tokens=cached,
                output_tokens=out_tok,
                cost_usd=getattr(u, 'cost', 0) or 0.0,
            )
        except Exception as _uerr:
            logger.debug("usage record failed (%s): %s", phase, _uerr)

    async def _completion_with_usage(self, client, slot: str, *, phase: str,
                                     session_id: Optional[str] = None, **params):
        """Non-streaming chat completion that records token/cost usage.

        Requests OpenRouter real cost via extra_body, handles provider rejection of
        the usage param (marks the slot, retries clean), then records. Use for every
        standalone LLM call so it shows up in /api/usage by_phase.
        """
        tracking = self.config_usage_tracking if slot == 'config' else self.suggestion_usage_tracking
        is_or = 'openrouter' in str(getattr(client, 'base_url', '')).lower()
        want_usage = (
            tracking != 'disabled'
            and (is_or or tracking == 'usage')
            and self._client_usage_ok.get(slot) is not False
        )
        if want_usage:
            params["extra_body"] = {"usage": {"include": True}}
        try:
            response = await client.chat.completions.create(**params)
            if want_usage and self._client_usage_ok.get(slot) is None:
                self._client_usage_ok[slot] = True
        except Exception as api_err:
            err_str = str(api_err).lower()
            if want_usage and ('usage' in err_str or 'extra' in err_str or 'unknown' in err_str):
                logger.warning("%s API rejected usage param, retrying without: %s", phase, api_err)
                self._client_usage_ok[slot] = False
                params.pop("extra_body", None)
                response = await client.chat.completions.create(**params)
            else:
                raise
        self._record_response_usage(
            response, phase=phase, model=params.get("model", ""), session_id=session_id,
        )
        return response

    async def _summarize_old_history(self, messages: List[Dict[str, Any]],
                                     session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Replace old history with one summary block, keeping recent messages verbatim.

        The split point only moves every _SUMMARY_STEP messages and the summary text is
        reused per session until then, so the prompt prefix stays byte-identical across
        turns (provider cache) and the summary LLM call runs once per step, not per turn.
        When the split moves, only the new messages are folded into the previous summary.
        """
        system_msg = messages[0]
        history = messages[1:]

        k = ((len(history) - _SUMMARY_KEEP_RECENT) // _SUMMARY_STEP) * _SUMMARY_STEP
        # A kept window must not start with an orphaned 'tool' message (strict providers).
        while 0 < k < len(history) and history[k].get("role") == "tool":
            k += 1
        if k <= 0:
            return messages
        to_summarize, keep = history[:k], history[k:]

        def digest(msgs):
            return hashlib.sha1(json.dumps(
                [(m.get("role"), m.get("content") if isinstance(m.get("content"), str) else "") for m in msgs]
            ).encode("utf-8")).hexdigest()

        # Entry = (k, digest of history[:k], text). Reuse or fold only while that exact
        # prefix is unchanged; a sliding/reloaded/edited history rebuilds from scratch.
        cached = self._summary_cache.get(session_id) if session_id else None
        if cached and cached[0] == k and cached[1] == digest(to_summarize):
            summary_text = cached[2]
        else:
            prev_k, prev_text = 0, ""
            if cached and cached[0] < k and cached[1] == digest(to_summarize[:cached[0]]):
                prev_k, prev_text = cached[0], cached[2]
            summary_text = await self._summarize_messages(to_summarize[prev_k:], prev_text, to_summarize)
            if session_id:
                self._summary_cache[session_id] = (k, digest(to_summarize), summary_text)
                while len(self._summary_cache) > 100:
                    self._summary_cache.pop(next(iter(self._summary_cache)))
            logger.info(f"Summarized {len(to_summarize)} old messages into a single block ({len(summary_text)} chars)")

        summary_msg = {
            "role": "system",
            "content": f"[Earlier conversation summary]\n{summary_text}"
        }
        return [system_msg, summary_msg] + keep

    async def _summarize_messages(self, new_part, prev_summary: str, all_old) -> str:
        """LLM summary of new_part folded into prev_summary; extractive fallback."""
        summary_input = "\n".join(
            f"{m['role'].upper()}: {m.get('content') if isinstance(m.get('content'), str) else ''}"
            for m in new_part if m.get('role') in ('user', 'assistant')
        )
        if prev_summary:
            summary_input = f"PREVIOUS SUMMARY:\n{prev_summary}\n\nNEW MESSAGES:\n{summary_input}"
        summary_text = ""
        if summary_input.strip():
            summary_prompt = [
                {"role": "system", "content": "Summarise the following conversation in concise bullet points, capturing all important facts, decisions, and context. If a previous summary is given, merge it with the new messages. Be brief — max 400 words."},
                {"role": "user", "content": summary_input}
            ]
            try:
                # Cheaper suggestion model — summarization is compression, not reasoning.
                # 1500 tokens: reasoning models spent all of 600 on thinking and returned empty text.
                resp = await self._completion_with_usage(
                    self.suggestion_client, 'suggestion', phase="summarization",
                    model=self.suggestion_model,
                    messages=summary_prompt,
                    stream=False,
                    max_tokens=1500,
                )
                choice = resp.choices[0] if resp.choices else None
                summary_text = ((choice.message.content if choice else None) or "").strip()
                if not summary_text:
                    logger.warning("Summarization returned empty content — using extractive fallback")
            except Exception as e:
                logger.warning(f"Summarization API call failed: {e} — using extractive fallback")
        if not summary_text:
            # Cheap, deterministic: the user's own earlier requests.
            asks = [m["content"].strip().splitlines()[0][:200] for m in all_old
                    if m.get("role") == "user" and isinstance(m.get("content"), str) and m["content"].strip()]
            summary_text = "Earlier user requests:\n" + "\n".join(f"- {a}" for a in asks[-8:])
        return summary_text

    _CONSOLIDATION_TRIGGERS = (
        # English — explicit preference/correction/identity signals
        "i prefer", "remember that", "remember:", "from now on", "always ", "never ",
        "my name", "we always", "you should know", "note that", "keep in mind",
        "i like", "i don't like", "i hate", "i love", "i want you to",
        "correct,", "that's wrong", "you were wrong", "actually,",
        # Polish equivalents
        "pamiętaj", "zapamiętaj", "wolę", "zawsze ", "nigdy ", "moje imię",
        "od teraz", "od tej pory", "warto wiedzieć", "chcę żebyś",
        "lubię", "nie lubię", "popraw", "to nieprawda", "masz rację",
    )

    def _should_consolidate(self, user_message: str, assistant_content: str) -> bool:
        """Return True only when conversation signals a durable new fact worth saving."""
        combined = (user_message + " " + assistant_content).lower()
        return any(trigger in combined for trigger in self._CONSOLIDATION_TRIGGERS)

    def _fast_client_and_model(self):
        """Client+model for side tasks (fast slot; falls back to main)."""
        return self.suggestion_client, self.suggestion_model

    async def _run_memory_consolidation(self, conversation_messages: List[Dict[str, Any]]) -> None:
        """
        Background pass after each user turn: let the LLM review what was discussed
        and save/delete/update memories without involving the user.
        Runs silently — errors are logged but never surface to the UI.
        """
        if not self.memory_manager or not self.client:
            return
        try:
            import json
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            last_user = next((m["content"] for m in reversed(conversation_messages)
                              if m.get("role") == "user" and isinstance(m.get("content"), str)), "")
            memory_context = self.memory_manager.recall(query=last_user)
            system_content = (
                "You are a memory consolidation assistant for a Home Assistant AI companion.\n"
                "Your job: extract ONLY high-value, durable facts from the conversation — facts the AI\n"
                "cannot figure out by reading HA config files or entity states.\n\n"
                "SAVE only when ALL of these are true:\n"
                "  1. The user explicitly stated it (never infer or assume)\n"
                "  2. It is durable — remains true across many future sessions\n"
                "  3. It cannot be derived from HA config/entity list\n"
                "  4. Confidence ≥ 0.9 — when in doubt, save NOTHING\n\n"
                "Good examples to save (kind: text):\n"
                "  - preference: 'always notify mobile_krystian AND mobile_marta'\n"
                "  - fact: 'Krystian and Marta live here; Krystian = primary user'\n"
                "  - fact: 'pompa = water pressure pump; cwu = hot water boiler'\n"
                "  - preference: 'respond in Polish; HA content in Polish'\n\n"
                "NEVER save (automatic disqualifiers):\n"
                "  - What was done this session ('created automation X', 'edited file Y', 'we configured Z')\n"
                "  - Any automation, script, or flow that was created (it is already in config files)\n"
                "  - Current sensor readings, device states, or live values\n"
                "  - Device model names, specs, or integration details\n"
                "  - Anything the AI can find by searching config files or entity states\n"
                "  - Inferred or assumed facts the user never said out loud\n\n"
                "If nothing from the conversation qualifies: call NO tools at all.\n"
                "If an existing memory (shown as #id) is wrong: save the correction with replaces=[id].\n"
                "Save only user preferences, decisions with their reasons, and facts NOT derivable from Home Assistant.\n"
                "Never save entity lists, areas, devices, dashboard structure, automation contents, or IDs.\n"
                "Save one fact per record, keep each record to 200 characters or less, and do not use Markdown headings.\n"
                "Saves become candidates the user reviews.\n"
                f"Current date/time: {now_str}"
            )
            if memory_context:
                system_content += "\n\n" + memory_context

            # Include only role/content pairs — strip tool results, cache_control, large blobs
            slim_history = []
            for m in conversation_messages:
                role = m.get("role")
                # Skip tool results (often large JSON) and system_info markers
                if role in ("tool", "system_info"):
                    continue
                if isinstance(m.get("content"), str) and m.get("content"):
                    slim_history.append({"role": role, "content": m["content"]})
                elif isinstance(m.get("content"), list):
                    # Flatten content blocks to plain text
                    text = " ".join(
                        b.get("text", "") for b in m["content"] if isinstance(b, dict) and b.get("type") == "text"
                    )
                    if text:
                        slim_history.append({"role": role, "content": text})

            # Some providers (Azure, Gemini via OpenRouter) require the conversation
            # to end with a user message. Strip any trailing assistant turns.
            while slim_history and slim_history[-1]["role"] != "user":
                slim_history.pop()

            messages = [{"role": "system", "content": system_content}] + slim_history

            # Memory-only tool list (hardcoded — self.tools is the AgentTools instance, not iterable)
            memory_tools = [t for t in self._memory_tool_schemas()
                            if t["function"]["name"] in ("save_memory", "search_memory", "delete_memory")]

            # Non-streaming, single pass, no retry loop.
            # Use suggestion model (cheaper) for background consolidation work.
            consolidation_client, consolidation_model = self._fast_client_and_model()
            response = await self._completion_with_usage(
                consolidation_client, 'suggestion', phase="consolidation",
                model=consolidation_model,
                messages=messages,
                tools=memory_tools,
                tool_choice="auto",
                max_tokens=1024,
            )

            _CONSOLIDATION_TOOLS = {"save_memory", "search_memory", "delete_memory"}
            choice = response.choices[0]
            if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
                for tc in choice.message.tool_calls:
                    fn = tc.function.name
                    if fn not in _CONSOLIDATION_TOOLS:
                        logger.warning(f"[memory consolidation] Ignoring unexpected tool call: {fn}")
                        continue
                    args = json.loads(tc.function.arguments or "{}")
                    if fn == "save_memory":
                        # Background extraction is unconfirmed: never inject until the user approves it.
                        args["status"] = "candidate"
                    result = await self._dispatch_tool(fn, args)
                    logger.info(f"[memory consolidation] {fn}({list(args.keys())}) → success={result.get('success')}")
        except Exception as e:
            logger.warning(f"Memory consolidation failed (non-fatal): {e}")

    def store_changeset(self, changeset_data: Dict[str, Any]) -> str:
        """
        Store a changeset for later approval.

        Args:
            changeset_data: Dictionary with file_changes and changeset_id

        Returns:
            changeset_id
        """
        import uuid
        changeset_id = changeset_data.get('changeset_id') or str(uuid.uuid4())[:8]

        now = datetime.now(timezone.utc)
        _ttl_hours = int(os.getenv("CHANGESET_TTL_HOURS", "24"))
        changeset = Changeset(
            changeset_id=changeset_id,
            file_changes=changeset_data['file_changes'],
            created_at=now.isoformat(),
            expires_at=(now + timedelta(hours=_ttl_hours)).isoformat(),
            restored_from=changeset_data.get('restored_from'),
        )

        self.pending_changesets[changeset_id] = changeset
        logger.info(f"Stored changeset {changeset_id} with {len(changeset.file_changes)} file(s)")
        return changeset_id

    def pending_file(self, file_path: str):
        """Newest unexpired pending change for file_path as (changeset_id, file_change), else None."""
        now = datetime.now(timezone.utc)
        for cid in reversed(list(self.pending_changesets)):
            cs = self.pending_changesets[cid]
            if datetime.fromisoformat(cs.expires_at) < now:
                continue
            fc = next((f for f in cs.file_changes if f["file_path"] == file_path), None)
            if fc:
                return cid, fc
        return None

    def drop_pending_file(self, changeset_id: str, file_path: str) -> None:
        """Remove one file from a pending changeset (deleting it when empty) after a newer one absorbed it."""
        cs = self.pending_changesets.get(changeset_id)
        if not cs:
            return
        cs.file_changes = [f for f in cs.file_changes if f["file_path"] != file_path]
        if not cs.file_changes:
            del self.pending_changesets[changeset_id]

    async def _precheck_changeset(self, changeset_id: str) -> Optional[str]:
        """New HA config-check problems the pending changeset would add, or None."""
        cs = self.pending_changesets.get(changeset_id)
        if not cs:
            return None
        files = [
            (fc["file_path"], fc["new_content"]) for fc in cs.file_changes
            if is_disk_file(fc["file_path"]) and fc["file_path"].endswith((".yaml", ".yml"))
        ]
        if not files:
            return None
        try:
            problems = await self.config_manager.precheck_files(files)
        except Exception as e:
            logger.warning(f"[PRECHECK] Skipped for {changeset_id}: {e}")
            return None
        return problems[:2000] if problems else None

    async def process_approval(
        self,
        change_id: str,
        approved: bool,
        validate: bool = True
    ) -> Dict[str, Any]:
        async with self.config_manager.write_lock:
            return await self._process_approval(change_id, approved, validate)

    async def _process_approval(
        self,
        change_id: str,
        approved: bool,
        validate: bool = True
    ) -> Dict[str, Any]:
        """
        Process user's approval/rejection of proposed changes.

        Args:
            change_id: Unique identifier for the proposed change
            approved: Whether user approved the changes
            validate: Whether to validate after applying changes

        Returns:
            Dict with:
                - success: bool
                - applied: bool
                - message: str
                - error: Optional[str]
        """
        logger.info(f"Processing approval for change {change_id}: {'approved' if approved else 'rejected'}")

        # Check if changeset exists
        changeset = self.pending_changesets.get(change_id)
        if not changeset:
            logger.warning(f"Approval failed: changeset {change_id} not found (expired, already applied, or server restarted) — nothing written")
            return {
                "success": False,
                "applied": False,
                "message": f"Changeset {change_id} not found or has expired. Please re-propose the changes."
            }

        # If rejected, just remove and return
        if not approved:
            del self.pending_changesets[change_id]
            return {
                "success": True,
                "applied": False,
                "message": "Changes rejected by user"
            }

        # Check if expired
        expires_at = datetime.fromisoformat(changeset.expires_at)
        if datetime.now(timezone.utc) > expires_at:
            logger.warning(f"Approval failed: changeset {change_id} expired at {changeset.expires_at} (created {changeset.created_at}) — nothing written")
            del self.pending_changesets[change_id]
            return {
                "success": False,
                "applied": False,
                "message": "Changeset has expired. Please re-propose the changes."
            }

        # Claim it before the first await so a concurrent edit cannot merge into
        # (or a second click re-apply) a changeset that is being written.
        del self.pending_changesets[change_id]

        # Apply changes
        try:
            applied_files = []
            failed_files = []

            # Refuse to apply over a file that changed after the proposal —
            # the user approved a diff against content that no longer exists.
            conflicts = []
            for file_change in changeset.file_changes:
                base_hash = file_change.get('base_hash')
                if not base_hash:
                    continue
                current = await self.config_manager.read_file_raw(file_change['file_path'], allow_missing=True)
                if content_hash(current) != base_hash:
                    conflicts.append(file_change['file_path'])
            if conflicts:
                logger.warning(f"Approval refused: {conflicts} changed since changeset {change_id} was proposed")
                return {
                    "success": False,
                    "applied": False,
                    "conflict": True,
                    "failed_files": [{"file_path": p, "error": "changed since proposal"} for p in conflicts],
                    "message": (
                        f"Not applied: {', '.join(conflicts)} changed after this proposal was made. "
                        "Re-read the file and propose the change again."
                    ),
                }

            # Step 1: Write all files first (without validation)
            for file_change in changeset.file_changes:
                file_path = file_change['file_path']
                new_content = file_change['new_content']

                # Node-RED virtual paths — deploy via API instead of writing to disk
                if file_path.startswith("nodered/"):
                    try:
                        if file_path == "nodered/flows.json":
                            mode = "replace"
                            tab_id = ""
                        elif file_path.startswith("nodered/flow/"):
                            mode = "update_tab"
                            tab_id = file_path[len("nodered/flow/"):].removesuffix(".json")
                        else:
                            mode = "add"
                            tab_id = ""
                        result = await self.tools.deploy_nodered_flows(new_content, mode=mode, tab_id=tab_id)
                        if result.get("success"):
                            applied_files.append(file_path)
                            logger.info(f"Deployed Node-RED flows via API ({mode})")
                        else:
                            failed_files.append({"file_path": file_path, "error": result.get("error", "Unknown error")})
                    except Exception as e:
                        logger.error(f"Failed to deploy Node-RED flows: {e}")
                        failed_files.append({"file_path": file_path, "error": str(e)})
                    continue

                try:
                    # Recheck right before writing: earlier items may have awaited network I/O.
                    if file_change.get('base_hash'):
                        current = await self.config_manager.read_file_raw(file_path, allow_missing=True)
                        if content_hash(current) != file_change['base_hash']:
                            failed_files.append({"file_path": file_path, "error": "changed since proposal"})
                            continue
                    await self.config_manager.write_file_raw(
                        file_path=file_path,
                        content=new_content,
                        create_backup=True
                    )
                    applied_files.append(file_path)
                    logger.info(f"Applied changes to {file_path}")

                    # Invalidate lovelace cache so the next read gets fresh content
                    if file_path == "lovelace.yaml":
                        self.tools._lovelace_cache.pop(None, None)
                    elif file_path.startswith("lovelace/") and file_path.endswith(".yaml"):
                        _lv_url = file_path[len("lovelace/"):-len(".yaml")] or None
                        self.tools._lovelace_cache.pop(_lv_url, None)

                except Exception as e:
                    logger.error(f"Failed to apply changes to {file_path}: {e}")
                    failed_files.append({"file_path": file_path, "error": str(e)})

            # Step 2: If validation requested and files were written, validate all at once
            validation_failed = False
            validation_warnings = None
            if validate and applied_files:
                try:
                    logger.info("Validating configuration after writing all files...")
                    validation_warnings = await self.config_manager.validate_config()
                except Exception as e:
                    logger.error(f"Configuration validation failed: {e}")
                    validation_failed = True
                    # Note: We don't rollback here because backups were created
                    # Users can manually restore from backups if needed
                    failed_files.append({
                        "file_path": "validation",
                        "error": f"Configuration validation failed: {str(e)}"
                    })

            # Keep the "before" content of every written file so this changeset
            # can be restored later (restore_changeset).
            if applied_files and getattr(self, "changeset_history", None):
                try:
                    self.changeset_history.record(
                        change_id, changeset.file_changes, applied_files,
                        restored_from=changeset.restored_from,
                    )
                except Exception as e:
                    logger.warning(f"Could not record changeset {change_id} history: {e}")

            # Reload Home Assistant configuration after successful changes (only if validation passed)
            reload_success = False
            reload_error = None
            if applied_files and not validation_failed:
                try:
                    from ..ha.ha_websocket import reload_homeassistant_config

                    from ..ha.layout import detect_layout, reload_domains

                    supervisor_token = os.getenv('SUPERVISOR_TOKEN')
                    if supervisor_token:
                        ws_url = "ws://supervisor/core/websocket"
                        try:
                            domains = reload_domains(detect_layout(self.config_manager.config_dir), applied_files)
                        except Exception:
                            domains = None
                        await reload_homeassistant_config(ws_url, supervisor_token, domains)
                        reload_success = True
                        logger.info(f"Home Assistant reloaded ({', '.join(domains) if domains else 'reload_all'})")
                    else:
                        logger.warning("SUPERVISOR_TOKEN not available, skipping config reload")
                except Exception as e:
                    # This can happen even though validate_config() passed above: HA's
                    # check_config endpoint validates syntax/schema shallowly, but actual
                    # component reload runs full setup validation and can still reject the
                    # config (e.g. a bad key path silently nested under the wrong domain).
                    # Surface the real error instead of a bare False so the agent tells the
                    # user their config file may now be broken and needs a fix + retry.
                    reload_error = str(e)
                    logger.warning(f"Failed to reload Home Assistant config: {e}")

            # A YAML platform whose integration has no `<domain>.reload` service (e.g.
            # `sensor: - platform: statistics` when statistics was only set up via UI)
            # is silently skipped by reload — only a restart loads it.
            restart_required = []
            if reload_success:
                try:
                    from ..ha.ha_websocket import get_reloadable_domains_ws
                    added = _platforms_added(changeset.file_changes, applied_files)
                    if added:
                        reloadable = await get_reloadable_domains_ws(
                            "ws://supervisor/core/websocket", os.getenv('SUPERVISOR_TOKEN'))
                        restart_required = sorted(added - reloadable)
                        if restart_required:
                            logger.info(f"Restart required for new platform(s): {restart_required}")
                except Exception as e:
                    logger.warning(f"Could not check reloadable domains: {e}")
            restart_note = (
                f". RESTART REQUIRED: new YAML platform(s) {', '.join(restart_required)} cannot be "
                "reloaded — entities will not exist until Home Assistant restarts. Tell the user."
                if restart_required else ""
            )

            if failed_files:
                return {
                    "success": True,
                    "applied": True,
                    "message": f"Partially applied: {len(applied_files)} succeeded, {len(failed_files)} failed" + restart_note,
                    "applied_files": applied_files,
                    "failed_files": failed_files,
                    "config_reloaded": reload_success,
                    **({"restart_required": restart_required} if restart_required else {}),
                    **({"reload_error": reload_error} if reload_error else {}),
                    **({"validation_warnings": validation_warnings} if validation_warnings else {})
                }
            else:
                message = f"Successfully applied changes to {len(applied_files)} file(s)"
                if reload_success:
                    message += " and reloaded Home Assistant configuration"
                elif reload_error:
                    message += (". WARNING: files were written but Home Assistant rejected the "
                                f"reload with a real validation error: {reload_error}. The config on "
                                "disk may be broken — call search_config_files to inspect and fix it.")
                if validation_warnings:
                    message += (". WARNING: Home Assistant config check reported problems (an integration "
                                f"may not load): {validation_warnings[:1500]} — check whether this change "
                                "caused them and fix it.")
                message += restart_note
                return {
                    "success": True,
                    "applied": True,
                    "message": message,
                    "applied_files": applied_files,
                    "config_reloaded": reload_success,
                    **({"restart_required": restart_required} if restart_required else {}),
                    **({"reload_error": reload_error} if reload_error else {}),
                    **({"validation_warnings": validation_warnings} if validation_warnings else {})
                }

        except Exception as e:
            logger.error(f"Error applying changeset: {e}", exc_info=True)
            if not applied_files:
                self.pending_changesets.setdefault(change_id, changeset)
            return {
                "success": False,
                "applied": False,
                "message": f"Error applying changes: {str(e)}"
            }

    async def generate_suggestions(self, extra_prompt: Optional[str] = None, resource_types: Optional[List[str]] = None, progress_cb=None) -> Dict[str, Any]:
        """
        Generate automation suggestions by pre-fetching context and making a single AI call.

        Fetches entity states, existing automations, and Node-RED flows, then asks
        the suggestion_model to return structured JSON suggestions.

        Returns:
            Dict with 'suggestions' list, each item having:
              - title: str
              - description: str
              - category: str (e.g. 'lighting', 'climate', 'security', 'energy', 'comfort')
              - entities: list[str]  (entity_ids involved)
              - implementation_hint: str
        """
        import json

        if not self.client:
            return {"success": False, "error": "OpenAI API not configured"}

        async def _emit(payload):
            if progress_cb:
                if isinstance(payload, str):
                    await progress_cb({"event": "status", "message": payload})
                else:
                    await progress_cb(payload)

        try:
            logger.info("Generating automation suggestions")
            ALL_RESOURCE_TYPES = ['entity_states', 'automations', 'scenes', 'scripts', 'dashboards', 'nodered', 'memory']
            # Dashboards (full Lovelace YAML) and Node-RED (full flow JSON, often the
            # single biggest section) are large and burn a lot of tokens for marginal
            # suggestion value, so they are opt-in: excluded from the default fallback,
            # included only when the UI explicitly selects them.
            DEFAULT_RESOURCE_TYPES = [t for t in ALL_RESOURCE_TYPES if t not in ('dashboards', 'nodered')]
            active_types = set(resource_types) if resource_types else set(DEFAULT_RESOURCE_TYPES)
            context_summary = []

            # 1. Gather context based on selected resource types
            context_sections = []
            # Track included file paths to avoid sending the same file content in multiple sections
            included_paths: set = set()
            suggestion_resource_files = {"automations": [], "scenes": [], "scripts": []}

            suggestion_states: List[Dict[str, Any]] = []
            if 'entity_states' in active_types:
                await _emit("Fetching entity states…")
                self.tools.clear_turn_cache()
                all_states = await self.tools._all_states()
                suggestion_states = _suggestion_states(all_states, await self.tools._get_all_entities())
                entity_states_text = _compact_states([
                    {**s, "friendly_name": (s.get("attributes") or {}).get("friendly_name")}
                    for s in suggestion_states
                ])
                context_sections.append(f"## Current entity states\n{entity_states_text}")
                entity_count = len(suggestion_states)
                context_summary.append({"type": "entity_states", "count": entity_count, "chars": len(entity_states_text)})
                await _emit(f"✓ Entity states: {entity_count} of {len(all_states)} entities (diagnostic/config/hidden skipped)")

            if 'automations' in active_types:
                await _emit("Loading automations…")
                # Read all automation files (supports single-file and split-file setups).
                # Search for 'alias' which is present in every YAML automation block.
                automations_result = await self.tools.search_config_files(search_pattern="alias", full_content=True, force=True)
                if not automations_result.get("success", True):
                    logger.warning("Suggestions: automation read failed: %s", automations_result.get("error"))
                automations_files = automations_result.get("files", [])
                for f in automations_files:
                    p = f.get("path", "")
                    if "automation" in p.lower() and p not in included_paths:
                        included_paths.add(p)
                        suggestion_resource_files["automations"].append(f)
                await _emit(f"✓ Automations: {len(suggestion_resource_files['automations'])} file(s)")

            if 'scenes' in active_types:
                try:
                    await _emit("Loading scenes…")
                    scenes_result = await self.tools.search_config_files(search_pattern="/**/*scene*.yaml", full_content=True, force=True)
                    if not scenes_result.get("success", True):
                        raise RuntimeError(scenes_result.get("error", "scene files could not be read"))
                    scenes_files = [
                        f for f in scenes_result.get("files", [])
                        if "scene" in f.get("path", "").lower() and f.get("path") not in included_paths
                    ]
                    for f in scenes_files:
                        included_paths.add(f.get("path", ""))
                    suggestion_resource_files["scenes"] = scenes_files
                    await _emit(f"✓ Scenes: {len(scenes_files)} file(s)" if scenes_files else "✓ Scenes: none found")
                except Exception as e:
                    logger.warning("Could not load scenes for suggestions: %s", e)
                    await _emit("⚠ Scenes context is missing; suggestions may not account for existing scenes.")

            if 'scripts' in active_types:
                try:
                    await _emit("Loading scripts…")
                    scripts_result = await self.tools.search_config_files(search_pattern="/**/*script*.yaml", full_content=True, force=True)
                    if not scripts_result.get("success", True):
                        raise RuntimeError(scripts_result.get("error", "script files could not be read"))
                    scripts_files = [
                        f for f in scripts_result.get("files", [])
                        if "script" in f.get("path", "").lower() and f.get("path") not in included_paths
                    ]
                    for f in scripts_files:
                        included_paths.add(f.get("path", ""))
                    suggestion_resource_files["scripts"] = scripts_files
                    await _emit(f"✓ Scripts: {len(scripts_files)} file(s)" if scripts_files else "✓ Scripts: none found")
                except Exception as e:
                    logger.warning("Could not load scripts for suggestions: %s", e)
                    await _emit("⚠ Scripts context is missing; suggestions may not account for existing scripts.")

            suggestion_outlines, existing_automation_aliases = _build_suggestion_outlines(suggestion_resource_files)
            for resource, heading in (("automations", "automations"), ("scenes", "scenes"), ("scripts", "scripts")):
                if resource not in active_types:
                    continue
                outline_text = suggestion_outlines.get(resource, "")
                if outline_text:
                    section = f"## Existing {heading} (compact outline)\n{outline_text}"
                    context_sections.append(section)
                    context_summary.append({
                        "type": resource,
                        "files": len(suggestion_resource_files[resource]),
                        "chars": len(outline_text),
                    })

            if 'dashboards' in active_types:
                try:
                    await _emit("Loading dashboards…")
                    dashboards_result = await self.tools.list_dashboards()
                    if dashboards_result.get("success"):
                        dashboard_list = dashboards_result.get("dashboards", [])
                        dashboard_contents = []
                        for dash in dashboard_list:
                            url_path = dash.get("url_path")
                            title = dash.get("title", url_path or "default")
                            # Fetch actual YAML so LLM can see current cards/layout
                            lovelace_key = None if url_path in (None, "lovelace") else url_path
                            yaml_content = await self.tools._get_lovelace_config(lovelace_key)
                            if yaml_content:
                                dashboard_contents.append(f"### Dashboard: {title}\n{yaml_content}")
                            else:
                                dashboard_contents.append(f"### Dashboard: {title} (url_path={url_path})\n(YAML not available)")
                        if dashboard_contents:
                            context_sections.append(f"## Existing dashboards\n" + "\n\n".join(dashboard_contents))
                        context_summary.append({"type": "dashboards", "count": len(dashboard_list)})
                        await _emit(f"✓ Dashboards: {len(dashboard_list)} loaded")
                except Exception as e:
                    logger.warning("Could not load dashboards for suggestions: %s", e)
                    await _emit("⚠ Dashboard context is missing; suggestions may not account for existing dashboards.")

            nodered_text = ""
            if 'nodered' in active_types:
                try:
                    await _emit("Loading Node-RED flows…")
                    nodered_result = await self.tools.get_nodered_flows()
                    if nodered_result.get("success"):
                        nodered_text = json.dumps(nodered_result.get("flows", []))  # compact — indent=2 wastes ~30% tokens
                except Exception as e:
                    logger.warning("Could not load Node-RED context for suggestions: %s", e)
                    await _emit("⚠ Node-RED context is missing; suggestions may not account for existing flows.")
                if nodered_text:
                    context_sections.append(f"## Existing Node-RED flows\n{nodered_text}")
                    context_summary.append({"type": "nodered", "chars": len(nodered_text)})
                    await _emit(f"✓ Node-RED flows loaded")
                else:
                    await _emit("✓ Node-RED: not available")

            # 2. Inject memory context so the suggester knows device relationships etc.
            # Use the focus prompt (if any) as the relevance query; fall back to empty
            # so all non-stale memory is included when no specific focus is given.
            if 'memory' in active_types and self.memory_manager:
                try:
                    await _emit("Loading memory context…")
                    memory_context = self.memory_manager.recall(query=extra_prompt or "")
                    if memory_context:
                        context_sections.append(memory_context)
                        context_summary.append({"type": "memory", "chars": len(memory_context)})
                        await _emit(f"✓ Memory: {len(memory_context)} chars")
                    else:
                        await _emit("✓ Memory: empty")
                except Exception as e:
                    logger.warning("Could not load memory context for suggestions: %s", e)
                    await _emit("⚠ Memory context is missing; suggestions may lack saved device relationships.")
            elif 'memory' in active_types:
                logger.warning("Memory context is unavailable for suggestions")
                await _emit("⚠ Memory context is missing; suggestions may lack saved device relationships.")

            # 3. Build remaining context
            suggestion_prompt_env = os.getenv("SUGGESTION_PROMPT", "").strip()

            # Inject dismissed suggestions so the AI won't re-suggest them
            dismissed = []
            try:
                config_dir = os.getenv("HA_CONFIG_DIR", "/config")
                dismissed_path = os.path.join(config_dir, ".ai_agent_suggestions_dismissed.json")
                if os.path.exists(dismissed_path):
                    with open(dismissed_path, "r", encoding="utf-8") as f:
                        dismissed = json.load(f)
            except Exception as e:
                logger.warning("Could not load dismissed suggestions context: %s", e)
                await _emit("⚠ Dismissed suggestions context is missing; previously dismissed items may reappear.")
            if dismissed:
                dismissed_list = "\n".join(f"- {t}" for t in dismissed)
                context_sections.append(f"## User has dismissed these suggestions — do NOT suggest them again\n{dismissed_list}")

            # Inject applied suggestions so the AI won't re-suggest them
            applied = []
            try:
                applied_path = os.path.join(config_dir, ".ai_agent_suggestions_applied.json")
                if os.path.exists(applied_path):
                    with open(applied_path, "r", encoding="utf-8") as f:
                        applied = json.load(f)
            except Exception as e:
                logger.warning("Could not load applied suggestions context: %s", e)
                await _emit("⚠ Applied suggestions context is missing; previously applied items may reappear.")
            if applied:
                applied_list = "\n".join(f"- {t}" for t in applied)
                context_sections.append(f"## User has already applied these suggestions — do NOT suggest them again\n{applied_list}")

            # Per-request extra_prompt (from UI textarea) takes priority over env var
            active_extra = (extra_prompt or "").strip() or suggestion_prompt_env
            if active_extra:
                context_sections.append(f"## Additional focus from user\n{active_extra}")

            # Hard total-context budget: very large homes (hundreds of entities + full
            # automation YAML) can produce a prompt so big the model returns empty
            # content. Cap the combined section size and trim the largest sections first
            # (tail-truncate, keeping each section's header) until under budget.
            try:
                ctx_cap = int(os.getenv('SUGGESTION_CTX_CAP', '') or 80000)
            except ValueError:
                ctx_cap = 80000
            total_section_chars = sum(len(s) for s in context_sections)
            if total_section_chars > ctx_cap:
                over = total_section_chars - ctx_cap
                # Largest sections first so we shave the worst offenders, not the small ones.
                for i in sorted(range(len(context_sections)), key=lambda j: len(context_sections[j]), reverse=True):
                    if over <= 0:
                        break
                    s = context_sections[i]
                    cut = min(over, max(0, len(s) - 200))  # never shrink a section below ~200 chars
                    if cut > 0:
                        context_sections[i] = s[: len(s) - cut] + f"\n…[truncated {cut} chars to fit suggestion context budget]"
                        over -= cut
                logger.warning(
                    "Suggestion context %d chars exceeded %d cap — truncated largest sections (nodered/dashboards are opt-in to avoid this)",
                    total_section_chars, ctx_cap,
                )

            context = "\n\n".join(context_sections)

            # Determine what types of suggestions to generate based on available context.
            # If the user only selected dashboards (no automation-related types), focus on
            # dashboard improvements rather than automations to match their intent.
            automation_types = {'automations', 'scripts', 'scenes', 'nodered'}
            wants_automations = bool(automation_types & active_types)
            wants_dashboards = 'dashboards' in active_types
            # If nothing explicit was selected, default to automations
            if not wants_automations and not wants_dashboards:
                wants_automations = True

            suggestion_targets = []
            if wants_automations:
                suggestion_targets.append(
                    "5-10 suggestions that are either: "
                    "(a) new automations/scripts the user does not already have, OR "
                    "(b) meaningful improvements to existing automations, scripts, or Node-RED flows "
                    "(e.g. add missing conditions, make triggers more reliable, reduce redundancy, "
                    "add notifications, handle edge cases, improve efficiency). "
                    "Mix of new and improvements is ideal. "
                    "Do NOT re-suggest automations that are already correct and need no changes."
                )
            if wants_dashboards:
                suggestion_targets.append(
                    "3-5 Lovelace dashboard improvements — new cards, missing entity tiles, "
                    "layout improvements, or useful info the dashboards currently lack "
                    "(do NOT suggest cards/views that already exist on the dashboards)"
                )

            suggest_instruction = "Suggest " + "; and ".join(suggestion_targets) + "."

            entity_grounding = ""
            if 'entity_states' in active_types:
                entity_grounding = (
                    "CRITICAL: Only reference entity_ids that appear in the '## Current entity states' section. "
                    "Never invent or guess entity_ids. If an entity for a given location or device is not in the list, "
                    "do not include that entity in the suggestion. "
                )

            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a Home Assistant expert. "
                        f"{suggest_instruction} "
                        f"{entity_grounding}"
                        "Return ONLY valid JSON — an object with "
                        "'suggestions' (array of suggestions). "
                        "Each suggestion must have: title (string), description (string), "
                        "type (either 'new' for new automations/scripts or 'improvement' for improving existing ones), "
                        "category (one of: lighting, climate, security, energy, comfort, other), "
                        "entities (array of entity_id strings that actually exist), "
                        "implementation_hint (string with brief YAML hint or description of what to change)."
                    )
                },
                {
                    "role": "user",
                    "content": context
                }
            ]

            api_params = {
                "model": self.suggestion_model,
                "messages": messages,
                "stream": False,
                "temperature": self.suggestion_temperature,
            }
            if self.suggestion_max_tokens:
                api_params["max_tokens"] = self.suggestion_max_tokens

            # Request usage accounting so this call lands in the Usage tab. OpenRouter
            # only returns real USD cost + native token counts when asked via extra_body
            # (a top-level `usage` param is rejected by the OpenAI client). Reuse the
            # 'suggestion' slot's rejection memory — same client as the in-chat phase.
            _sugg_tracking = self.suggestion_usage_tracking
            _is_openrouter = 'openrouter' in str(getattr(self.suggestion_client, 'base_url', '')).lower()
            if _sugg_tracking != 'disabled' and (_is_openrouter or _sugg_tracking == 'usage'):
                api_params["extra_body"] = {"usage": {"include": True}}
            if self._client_usage_ok.get('suggestion') is False:
                api_params.pop("extra_body", None)

            total_chars = sum(c.get("chars", 0) for c in context_summary)

            # Emit context details for UI transparency before the AI call
            sections_preview = []
            for section in context_sections:
                lines = section.split('\n')
                header = lines[0] if lines else ''
                body_lines = lines[1:] if len(lines) > 1 else []
                preview = '\n'.join(body_lines)[:500]
                truncated = len('\n'.join(body_lines)) > 500
                sections_preview.append({
                    "header": header,
                    "chars": len(section),
                    "preview": preview,
                    "truncated": truncated,
                })
            await _emit({
                "event": "context_ready",
                "system_prompt": messages[0]["content"],
                "sections": sections_preview,
                "model": self.suggestion_model,
                "total_chars": total_chars,
            })

            await _emit(f"Calling AI model ({len(context_sections)} context sections, ~{total_chars // 1000}K chars)…")
            try:
                response = await self.suggestion_client.chat.completions.create(**api_params)
                if self._client_usage_ok.get('suggestion') is None and "extra_body" in api_params:
                    self._client_usage_ok['suggestion'] = True
            except Exception as api_err:
                # Provider rejected the usage param — mark slot + retry without it.
                err_str = str(api_err).lower()
                if "extra_body" in api_params and ('usage' in err_str or 'extra' in err_str or 'unknown' in err_str):
                    logger.warning(f"Suggestion API rejected usage param, retrying without: {api_err}")
                    self._client_usage_ok['suggestion'] = False
                    api_params.pop("extra_body", None)
                    response = await self.suggestion_client.chat.completions.create(**api_params)
                else:
                    raise

            # Record per-call usage/cost so the Suggestions tab shows up in Usage.
            if self.usage_manager is not None:
                try:
                    u = getattr(response, "usage", None)
                    if u:
                        in_tok = getattr(u, 'prompt_tokens', 0) or getattr(u, 'input_tokens', 0) or 0
                        out_tok = getattr(u, 'completion_tokens', 0) or getattr(u, 'output_tokens', 0) or 0
                        cached = 0
                        if hasattr(u, 'cached_tokens'):
                            cached = u.cached_tokens or 0
                        elif getattr(u, 'prompt_tokens_details', None):
                            cached = getattr(u.prompt_tokens_details, 'cached_tokens', 0) or 0
                        self.usage_manager.record(
                            session_id=None,
                            phase="suggestions",
                            model=self.suggestion_model,
                            iteration=0,
                            input_tokens=in_tok,
                            cached_tokens=cached,
                            output_tokens=out_tok,
                            cost_usd=getattr(u, 'cost', 0) or 0.0,
                        )
                except Exception as _uerr:
                    logger.debug("suggestions usage record failed: %s", _uerr)

            # content can be None (model hit max_tokens with reasoning-only output,
            # refused, or truncated on a very large prompt) and choices can be empty
            # on some providers — guard both before .strip() so a null response gives
            # a clear error instead of crashing the whole suggestion run.
            choice = response.choices[0] if response.choices else None
            raw = ((choice.message.content if choice else None) or "").strip()
            if not raw:
                logger.warning(
                    "Suggestions model returned empty content (model=%s, ~%dK chars in) — likely over the output token limit or truncated",
                    self.suggestion_model, total_chars // 1000,
                )
                return {
                    "success": False,
                    "error": "The suggestion model returned no content — the context may be too large. Try unchecking some resource types (e.g. nodered, dashboards) and generate again.",
                    "context_summary": context_summary,
                }

            try:
                parsed = _parse_suggestion_response(raw)
            except ValueError as parse_error:
                logger.warning("Suggestions JSON parse failed: %s", parse_error)
                await _emit({"event": "error", "message": str(parse_error)})
                return {"success": False, "error": str(parse_error), "context_summary": context_summary}
            suggestions = parsed.get("suggestions", []) if isinstance(parsed, dict) else parsed
            naming_issues = _detect_naming_issues(suggestion_states)
            if not isinstance(suggestions, list):
                message = "The suggestion model returned JSON with no suggestions array. Try generating again."
                await _emit({"event": "error", "message": message})
                return {"success": False, "error": message, "context_summary": context_summary}

            before_filter = len(suggestions)
            suggestions = [
                suggestion for suggestion in suggestions
                if not isinstance(suggestion, dict)
                or str(suggestion.get("type", "new")).lower() == "improvement"
                or not _is_duplicate_suggestion(suggestion, existing_automation_aliases)
            ]
            removed_duplicates = before_filter - len(suggestions)
            if removed_duplicates:
                logger.info("Dropped %d suggestions duplicating existing automation aliases", removed_duplicates)

            logger.info(f"Generated {len(suggestions)} automation suggestions, {len(naming_issues)} naming issues")
            await _emit(f"✓ Done: {len(suggestions)} suggestion(s), {len(naming_issues)} naming issue(s)")
            return {"success": True, "suggestions": suggestions, "naming_issues": naming_issues, "context_summary": context_summary}

        except Exception as e:
            logger.error(f"Failed to generate suggestions: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
