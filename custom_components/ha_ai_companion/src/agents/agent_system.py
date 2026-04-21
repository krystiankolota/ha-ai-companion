import asyncio
import logging
import os
import re
from typing import Dict, Any, Optional, List
from openai import AsyncOpenAI
from ..agents.tools import AgentTools
from ..config import ConfigurationManager
from ..memory.manager import MemoryManager
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, asdict


logger = logging.getLogger(__name__)

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


@dataclass
class Changeset:
    """Represents a proposed set of configuration changes."""
    changeset_id: str
    file_changes: List[Dict[str, str]]  # List of {file_path, new_content}
    created_at: str
    expires_at: str

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
        memory_manager: Optional[MemoryManager] = None,
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
            memory_manager: Optional MemoryManager for persistent cross-session memories
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

        # Suggestion model — independent provider, all 3 vars must be set to activate.
        # No fallback mixing: if any of the 3 are missing, main client+model is used.
        _s_model = os.getenv('SUGGESTION_MODEL')
        _s_url   = os.getenv('SUGGESTION_API_URL')
        _s_key   = os.getenv('SUGGESTION_API_KEY')
        if _s_model and _s_url and _s_key:
            self.suggestion_model = _s_model
            self.suggestion_client = AsyncOpenAI(api_key=_s_key, base_url=_s_url)
        else:
            self.suggestion_model = self.model
            self.suggestion_client = self.client

        # Config model — independent provider, all 3 vars must be set to activate.
        _c_model = os.getenv('CONFIG_MODEL')
        _c_url   = os.getenv('CONFIG_API_URL')
        _c_key   = os.getenv('CONFIG_API_KEY')
        if _c_model and _c_url and _c_key:
            self.config_model = _c_model
            self.config_client = AsyncOpenAI(api_key=_c_key, base_url=_c_url)
        else:
            self.config_model = self.model
            self.config_client = self.client

        # Get temperature from environment variable, use None if not specified
        temperature_str = os.getenv('TEMPERATURE')
        self.temperature = float(temperature_str) if temperature_str else None

        # Suggestion calls use a lower temperature by default (0.2) to reduce hallucination.
        # Can be overridden via SUGGESTION_TEMPERATURE env var.
        suggestion_temp_str = os.getenv('SUGGESTION_TEMPERATURE', '0.2')
        self.suggestion_temperature = float(suggestion_temp_str)

        # Pricing (cost display in UI) — default 0 means cost won't be shown
        self.input_price_per_1m = float(os.getenv('INPUT_PRICE_PER_1M', '0') or '0')
        self.output_price_per_1m = float(os.getenv('OUTPUT_PRICE_PER_1M', '0') or '0')

        # Per-agent max_tokens limits (0 = no limit / model default)
        def _int_env(key: str) -> Optional[int]:
            val = os.getenv(key, '').strip()
            return int(val) if val and val.isdigit() and int(val) > 0 else None

        self.max_tokens = _int_env('MAX_TOKENS')
        self.suggestion_max_tokens = _int_env('SUGGESTION_MAX_TOKENS') or self.max_tokens
        self.config_max_tokens = _int_env('CONFIG_MAX_TOKENS') or self.max_tokens

        # Per-client cache: tracks whether usage-tracking params were rejected (BUG-8)
        # Keys: 'main', 'suggestion', 'config' — value: True = params accepted, False = rejected
        self._client_usage_ok: Dict[str, Optional[bool]] = {
            'main': None, 'suggestion': None, 'config': None
        }

        # Store cache control setting
        self.enable_cache_control = enable_cache_control

        # Store usage tracking mode (global) and per-phase overrides
        self.usage_tracking = usage_tracking
        _su = os.getenv('SUGGESTION_USAGE_TRACKING', 'default').strip().lower()
        self.suggestion_usage_tracking = usage_tracking if _su == 'default' else _su
        _cu = os.getenv('CONFIG_USAGE_TRACKING', 'default').strip().lower()
        self.config_usage_tracking = usage_tracking if _cu == 'default' else _cu

        logger.info(f"AgentSystem initialized with model: {self.model}")
        if self.suggestion_model != self.model:
            logger.info(f"Suggestion model override: {self.suggestion_model}")
        if self.config_model != self.model:
            logger.info(f"Config model override: {self.config_model}")
        if self.temperature is not None:
            logger.info(f"Temperature: {self.temperature}")
        logger.info(f"Cache control: {'enabled' if self.enable_cache_control else 'disabled'}")
        logger.info(f"Usage tracking: {self.usage_tracking} (suggestion: {self.suggestion_usage_tracking}, config: {self.config_usage_tracking})")

        # In-memory storage for pending changesets
        self.pending_changesets: Dict[str, Changeset] = {}

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
        """Get the default system prompt for the configuration agent."""
        return """You are a Home Assistant Configuration Assistant with persistent memory.

Your role is to help users manage their Home Assistant configuration files safely and effectively, suggest useful automations, and remember important facts about their setup across sessions.

Key Responsibilities:
1. **Understanding Requests**: Interpret user requests about Home Assistant configuration
2. **Reading Configuration**: Use tools to examine current configuration files
3. **Proposing Changes**: Suggest configuration changes with clear explanations using the propose_config_changes tool without requesting confirmation
4. **Safety First**: Always explain the impact of changes before proposing them
5. **Best Practices**: Guide users toward Home Assistant best practices
6. **Automation Suggestions**: Proactively suggest useful automations based on the user's devices and current states
7. **Memory Management**: Remember important facts about the user's setup across sessions

Available Tools:
- search_config_files: Search for terms in configuration files (use first when reading config). Includes all Lovelace dashboards as lovelace.yaml (default) and lovelace/{url_path}.yaml (custom).
- propose_config_changes: Propose file changes for user approval. Supports lovelace.yaml and lovelace/{url_path}.yaml for dashboard edits.
- list_dashboards: List all Lovelace dashboards with their url_path and virtual file path
- create_dashboard: Create a new Lovelace dashboard (returns url_path for editing)
- delete_dashboard: Delete a Lovelace dashboard by url_path
- get_entity_states: Get live current states of all entities (use for automation suggestions)
- get_ha_issues: Get Watchman missing entity/service references + Spook repair issues. Call when user mentions issues, Watchman, Spook, broken references, or asks to fix missing entities in config.
- get_nodered_flows: Read existing Node-RED flows via API or backup file (always call before add_nodered_flow or edit_nodered_tab)
- add_nodered_flow: Stage a NEW flow tab for approval (non-destructive, never touches existing flows)
- edit_nodered_tab: Stage an update to an EXISTING flow tab for approval (only that tab is changed; requires tab_id from get_nodered_flows)
- read_memories: Read persistent memory files from previous sessions
- save_memory: Save a memory file to persist knowledge across sessions
- delete_memory: Delete an outdated memory file
- list_memory_stats: Audit memory files — sizes, ages, stale flags
- consolidate_memories: Review all memory files and propose a MERGE/DELETE/KEEP plan (user must confirm before applying)
- search_past_sessions: Keyword search across past conversation sessions — use when user references prior work or before starting a topic with likely history
- reload_config: Reload HA configuration after approved YAML changes (activates new entities without restart)

Dashboard Guidelines:
- Call list_dashboards first to discover what dashboards exist and their url_path values
- The default dashboard is always available as lovelace.yaml
- Custom dashboards are available as lovelace/{url_path}.yaml (e.g. lovelace/kitchen.yaml)
- To review a dashboard: use search_config_files with 'lovelace' or call list_dashboards then search_config_files
- To edit a dashboard: read it via search_config_files, then propose_config_changes with the correct path
- To create a dashboard: call create_dashboard (returns url_path), then populate it via propose_config_changes
- To delete a dashboard: call delete_dashboard with the url_path (cannot delete the default dashboard)
- Dashboard YAML structure: must include at minimum 'title' and 'views' keys

Helper Entities in YAML:
- Define input_number, input_boolean, input_text, input_select helpers directly in configuration.yaml as YAML blocks (e.g. input_number: / entity_id: / ...). Do NOT tell the user to create them manually in the UI — add them to the YAML and reload.
- After the user approves changes that add new helpers or template sensors, call reload_config immediately to activate them. No restart needed.

Important Guidelines:
- NEVER suggest changes directly - always use propose_config_changes
- To intentionally delete automations/scripts/scenes, pass confirm_delete=true — only do this when the user explicitly asks to delete them
- Always read the current configuration before proposing changes using search_config_files
- Briefly explain the change in text, then call propose_config_changes immediately — do NOT reproduce the full file content in your text response before calling the tool
- The user can accept or reject your proposed config changes through their own UI
- Preserve all existing code, comments and structure when possible
- Only change what's needed to complete the request of the user
- Validate that changes align with Home Assistant documentation
- Warn users about potential breaking changes
- Remember when searching for files that terms are case-insensitive

Language:
- Look at the entity friendly_names, automation names, and notification text in the Home Layout section below.
- Detect the primary language used there (e.g. Polish, German, French, English).
- Respond in that same language. Generate all new content — automation names, descriptions, notifications, comments — in that language.
- If the user writes in a different language than the home config, follow the user's message language for your replies but keep generated HA content in the home config language.

Automation safety rules (CRITICAL):
- Before proposing changes to any automation/script/scene file, ALWAYS read the file first with search_config_files.
- This applies to: automations.yaml, scripts.yaml, scenes.yaml, AND any files inside automations/, scripts/, scenes/ directories (split-file setups like automations/heating.yaml, automations/lights.yaml, etc.)
- Your proposed content for each file MUST include ALL existing automations/scripts/scenes in that file PLUS any new ones.
- NEVER submit a partial list — removing existing automations is permanent data loss.
- If you only want to add one automation, copy ALL existing ones from that file first, then append the new one at the end.
- When the user has a split-file setup (multiple files per category), edit ONLY the relevant file — do NOT merge all files into one.

Entity ID rules (CRITICAL — prevents broken automations):
- NEVER invent or guess entity IDs. Only use entity_ids you have seen in this conversation — either from get_entity_states results, from configuration files you read this turn, or from the Home Layout section.
- If unsure which entity_id to use, call get_entity_states first and search for the entity by friendly_name or domain before writing any YAML.
- When proposing YAML with entity_ids: double-check each one against what you actually received — do NOT substitute with ids that "sound right".
- If propose_config_changes returns entity_warnings: the entity_ids listed were NOT found in the registry. For each: either (a) use a suggestion from the list to fix the entity_id and re-propose, or (b) if no suitable entity exists, remove or comment out that automation and inform the user. Do NOT leave broken entity_ids in the config.

Memory Guidelines:
- Categories: preference_, identity_, device_, baseline_, pattern_, correction_ (use as filename prefix)
- Examples: preference_notifications.md (who gets notified), identity_residents.md, preference_language.md

SAVE only when ALL are true: (1) user explicitly stated it, (2) it is durable across sessions, (3) it is NOT derivable from HA config or entity states:
- User preferences ("always notify mobile_phone_a AND mobile_phone_b", "prefer 22°C at night")
- Residents / occupants and their devices ("Krystian's phone = mobile_krystian")
- Device purpose when the name is genuinely unclear ("pompa = water pressure pump in basement")
- Recurring routines stated by the user ("we go to bed around 23:00")
- Corrections to previously stored facts

NEVER save (not even if unsure):
- What you did this session ("created automation X", "we edited file Y")
- Current or recent sensor readings / states
- Automations or scripts that were created (they're in the config files)
- Device specs, model names, or integration details
- Anything the AI can figure out by reading the HA config or entity list
- Inferred or assumed facts the user never explicitly stated

Anti-bloat rules (enforced by the system, also your responsibility):
- Max 25 files total — merge related facts into one file rather than creating many small ones
- Max 800 chars per file — be terse; bullet points only, no prose
- When updating a memory, overwrite the whole file — never append stale info
- Use the `replaces` field when correcting a previous memory to delete the old file atomically
- Call list_memory_stats periodically and proactively delete/merge stale or oversized files

Context injection:
- Memory is already injected into this prompt at session start — NEVER reproduce or quote memory content in your responses
- Call read_memories only for a specific file not shown in the injected context
- Home layout (areas → entities) is injected below — use it to answer location/entity questions without tool calls
- Only call get_entity_states when you need live state values or attributes not shown in the layout
- Call search_past_sessions when the user references a prior conversation ("that thing we did last week", "remember when...") or before tackling a topic that may have prior history (automations, dashboards, routines)

Automation Suggestion Guidelines:
- When asked to suggest automations, first call get_entity_states to see what devices exist
- Also call search_config_files to see what automations already exist (avoid duplicates)
- If Node-RED is configured, call get_nodered_flows to see existing flows — do NOT suggest automations that are already implemented in Node-RED
- To create a new Node-RED flow tab: call get_nodered_flows first (check for duplicates), generate valid Node-RED JSON (array with tab node + its nodes), then call add_nodered_flow.
- To modify nodes in an EXISTING flow tab: call get_nodered_flows to get the tab's id and current nodes, build the updated array (tab node + all nodes with your changes applied), then call edit_nodered_tab with the tab_id. NEVER replace all flows — that operation is not available.
- Node-RED flow JSON format: array containing one {type:"tab", id, label} node plus the flow's functional nodes, each with {id, type, name, wires, x, y, ...}. Use common node types: inject, debug, function, change, switch, delay, http request, mqtt in/out, ha-api, ha-entity, ha-state-changed, ha-call-service, ha-events-all, ha-webhook.
- Suggest practical, common-sense automations based on the devices present
- Group suggestions by area/domain and explain the benefit of each
- When Node-RED flows are available, mention whether a suggestion is best done in HA automations or Node-RED
- Offer to implement any suggestion via propose_config_changes

Safety & Reversibility:
- Every file change creates an automatic timestamped backup before writing.
- If the user asks to undo or revert a change: call list_backups to find the right backup, then restore_backup — this is always available and safe.
- Prefer targeted edits over full-file rewrites to minimise diff size and revert risk.
- When making multiple related changes, propose them together in one changeset so they can be approved or rejected as a unit.

Response Style:
- Be concise but thorough
- Use technical terms appropriately
- Provide examples when helpful
- Format code blocks with YAML syntax
- Ask clarifying questions if request is ambiguous

Remember: You're helping manage a production Home Assistant system. Safety and clarity are paramount."""

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

    @staticmethod
    def _prune_old_tool_messages(messages: list, keep_blocks: int = 6) -> list:
        """Remove oldest tool call+result blocks beyond keep_blocks to keep context lean.

        keep_blocks=6 allows up to 6 read/write iterations before pruning starts.
        Pruning only applies when there are already more blocks than keep_blocks —
        short single-turn operations (3–4 tool calls) are never affected.
        """
        blocks = []  # [(assistant_idx, [tool_indices...])]
        i = 0
        while i < len(messages):
            msg = messages[i]
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                block_start = i
                tool_idxs = []
                j = i + 1
                while j < len(messages) and messages[j].get("role") == "tool":
                    tool_idxs.append(j)
                    j += 1
                if tool_idxs:
                    blocks.append((block_start, tool_idxs))
                i = j
            else:
                i += 1
        if len(blocks) <= keep_blocks:
            return messages
        to_remove: set = set()
        for block_start, tool_idxs in blocks[:-keep_blocks]:
            to_remove.add(block_start)
            to_remove.update(tool_idxs)
        pruned = [m for k, m in enumerate(messages) if k not in to_remove]
        logger.debug(f"Pruned {len(messages) - len(pruned)} old tool messages (kept last {keep_blocks} blocks)")
        return pruned

    async def _dispatch_tool(self, function_name: str, function_args: dict, turn_state: Optional[dict] = None) -> dict:
        """Execute a named tool and return its result dict.

        Args:
            turn_state: Per-request mutable dict with keys like 'has_read'. When None,
                        the read-before-write guard is skipped (used by consolidation).
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
                    "You MUST first read files with search_config_files, then provide all modified content. "
                    f"Received args: {function_args}"
                )
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
            if turn_state is not None and not turn_state.get("has_read", False):
                error_msg = (
                    "ERROR: You must read the current file content before proposing changes. "
                    "Call search_config_files (for YAML/Lovelace files) or get_nodered_flows (for Node-RED) first "
                    "to get the current state of the files you want to modify. "
                    "This ensures your changes preserve all existing content and structure."
                )
                logger.warning("propose_config_changes blocked: no read_config call this turn")
                return {"success": False, "error": error_msg}
            return await self.tools.propose_config_changes(**function_args)
        elif function_name == "patch_config_key":
            if turn_state is not None and not turn_state.get("has_read", False):
                return {
                    "success": False,
                    "error": (
                        "ERROR: You must read the current file content before patching. "
                        "Call search_config_files first to see the exact key path."
                    ),
                }
            return await self.tools.patch_config_key(**function_args)
        elif function_name == "patch_config_block":
            if turn_state is not None and not turn_state.get("has_read", False):
                return {
                    "success": False,
                    "error": (
                        "ERROR: You must read the current file content before patching. "
                        "Call search_config_files first to confirm the anchor path exists."
                    ),
                }
            return await self.tools.patch_config_block(**function_args)
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
        elif function_name == "add_nodered_flow":
            if turn_state is not None and not turn_state.get("has_read", False):
                return {
                    "success": False,
                    "error": (
                        "ERROR: Call get_nodered_flows first to confirm the flow doesn't already exist "
                        "and to understand the existing flow structure."
                    ),
                }
            return await self.tools.add_nodered_flow(**function_args)
        elif function_name == "edit_nodered_tab":
            if turn_state is not None and not turn_state.get("has_read", False):
                return {
                    "success": False,
                    "error": (
                        "ERROR: Call get_nodered_flows first to get the tab_id and current node content "
                        "before editing a tab."
                    ),
                }
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
        elif function_name == "read_memories":
            return await self.tools.read_memories(**function_args)
        elif function_name == "save_memory":
            return await self.tools.save_memory(**function_args)
        elif function_name == "delete_memory":
            return await self.tools.delete_memory(**function_args)
        elif function_name == "list_memory_stats":
            return await self.tools.list_memory_stats()
        elif function_name == "consolidate_memories":
            return await self.tools.consolidate_memories()
        elif function_name == "search_past_sessions":
            return await self.tools.search_past_sessions(**function_args)
        elif function_name == "reload_config":
            return await self.tools.reload_config()
        elif function_name == "set_ha_text_entity":
            return await self.tools.set_ha_text_entity(**function_args)
        elif function_name == "schedule_ai_task":
            return await self.tools.schedule_ai_task(**function_args)
        else:
            logger.error(f"Unknown tool requested: {function_name}")
            return {"success": False, "error": f"Unknown tool: {function_name}"}

    async def chat_stream(
        self,
        user_message: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None
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

        try:
            logger.info(f"Agent streaming user message: {user_message[:100]}...")

            # Build messages list with prompt caching support
            # Inject datetime + memory context into system prompt
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            system_content = self.system_prompt + f"\n\nCurrent date/time: {now_str}"
            if self.memory_manager:
                try:
                    memory_context = await self.memory_manager.get_context()
                    if memory_context:
                        system_content = system_content + "\n\n" + memory_context
                except Exception as mem_err:
                    logger.warning(f"Failed to load memory context: {mem_err}")

            # Inject home topology (areas → entities) — cached, fails silently
            topology = await self._build_home_topology()
            if topology:
                system_content = system_content + "\n\n" + topology

            system_message = {
                "role": "system",
                "content": system_content
            }
            if self.enable_cache_control:
                system_message["cache_control"] = {"type": "ephemeral"}
            messages = [system_message]

            # Track the starting point for new messages (after history)
            history_length = 1  # system message
            if conversation_history:
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

            # BUG-6: Summarize very long conversations to avoid context overflow.
            # If history has > 30 messages (15 exchanges), summarise the oldest half.
            if conversation_history and len(conversation_history) > 30:
                try:
                    messages = await self._summarize_old_history(messages)
                except Exception as sum_err:
                    logger.warning(f"History summarization failed (continuing without): {sum_err}")

            # Add current user message
            messages.append({"role": "user", "content": user_message})

            # Define available tools for function calling with cache control
            # Mark tools for caching to reduce repeated processing
            propose_tool = {
                "type": "function",
                "function": {
                    "name": "propose_config_changes",
                    "description": "Propose changes to one or more configuration files for user approval. Use for new files, structural rewrites, or multi-file changes. For changing a single existing key use patch_config_key instead. For replacing one named section (e.g. one automation, the logger block) use patch_config_block instead.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "changes": {
                                "type": "array",
                                "description": "Array of file changes. Each change must include file_path and new_content.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "file_path": {
                                            "type": "string",
                                            "description": "Relative path to config file (e.g., 'configuration.yaml', 'switches.yaml'). New areas can be specified with 'areas/{area_id}.json' and must include the 'name'"
                                        },
                                        "new_content": {
                                            "type": "string",
                                            "description": "The complete new content of the file as a valid YAML string. Include all lines - both changed and unchanged."
                                        }
                                    },
                                    "required": ["file_path", "new_content"]
                                }
                            },
                            "confirm_delete": {
                                "type": "boolean",
                                "description": "Set to true only when the user explicitly wants to delete automations/scripts/scenes. Bypasses the deletion safety guard. Default false."
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
                        "Surgical patch: change a single YAML key without rewriting the whole file. "
                        "Use this for changing one known value (e.g. logger.default, a timeout, a URL). "
                        "All comments, ordering, and surrounding keys are preserved. "
                        "Do NOT use for adding new top-level sections or restructuring — use propose_config_changes for those."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": "Relative path to the config file, e.g. 'configuration.yaml'."
                            },
                            "key_path": {
                                "type": "string",
                                "description": (
                                    "Dot-notation path to the key. Examples: "
                                    "'logger.default', "
                                    "'homeassistant.name', "
                                    "'automations[0].trigger', "
                                    "'automations[alias=Morning light].action[0].service'."
                                )
                            },
                            "new_value": {
                                "description": "New value to set. Can be a string, number, boolean, list, or object."
                            },
                            "description": {
                                "type": "string",
                                "description": "Brief human-readable description of the change for the approval UI."
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
                        "Replace an entire YAML block (subtree) without rewriting the whole file. "
                        "Use this to replace a named section such as the logger config, one automation entry, "
                        "or a homeassistant block. All other parts of the file are preserved. "
                        "Do NOT use for simple scalar changes — use patch_config_key for those. "
                        "Do NOT use for adding a new top-level key that doesn't exist yet — use propose_config_changes."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": "Relative path to the config file, e.g. 'automations.yaml'."
                            },
                            "anchor": {
                                "type": "string",
                                "description": (
                                    "Dot-notation path identifying the block to replace. Examples: "
                                    "'logger' (top-level key), "
                                    "'automations[alias=Morning light]' (list item by alias), "
                                    "'homeassistant' (replace the homeassistant section)."
                                )
                            },
                            "new_block": {
                                "type": "string",
                                "description": "Valid YAML string for the replacement. Must match the structure of the block being replaced."
                            },
                            "description": {
                                "type": "string",
                                "description": "Brief human-readable description of the change for the approval UI."
                            }
                        },
                        "required": ["file_path", "anchor", "new_block"]
                    }
                }
            }

            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "search_config_files",
                        "description": "Search configuration files (all YAML files + all Lovelace dashboards, plus device/entity/area virtual files). The default dashboard is returned as 'lovelace.yaml'; custom dashboards as 'lovelace/{url_path}.yaml'. Devices/entities/areas are ONLY included when search_pattern is provided.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "search_pattern": {
                                    "type": "string",
                                    "description": "Optional text to search for in file contents (case-insensitive). Only files containing this text will be returned. Omit to return all files."
                                }
                            },
                            "required": []
                        }
                    }
                },
                propose_tool,
                patch_key_tool,
                patch_block_tool,
                *dashboard_tools,
                {
                    "type": "function",
                    "function": {
                        "name": "get_nodered_flows",
                        "description": "Fetch Node-RED flows via the Node-RED Admin API (or file fallback). Always call this before add_nodered_flow or edit_nodered_tab — you need the tab IDs and existing node content. Also use to check what flows already exist before suggesting automations.",
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
                        "name": "add_nodered_flow",
                        "description": (
                            "Stage a NEW Node-RED flow tab for user approval. Non-destructive — existing flows are never touched. "
                            "Always call get_nodered_flows first to confirm the flow doesn't already exist. "
                            "Do NOT use this to modify an existing flow — use edit_nodered_tab for that."
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
                            "Stage an update to an EXISTING Node-RED flow tab for user approval. Only the specified tab is changed. "
                            "Always call get_nodered_flows first to get the tab_id and the current nodes. "
                            "Include ALL nodes for the tab in flows_json (not just the changed ones). "
                            "Do NOT use this to create a new flow — use add_nodered_flow for that. "
                            "NEVER use this to replace all flows — that operation is not available."
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
                {
                    "type": "function",
                    "function": {
                        "name": "get_entity_states",
                        "description": (
                            "Get the current live states of Home Assistant entities. "
                            "For specific questions about a subset of entities (e.g. 'lights in the bedroom', 'all sensors', 'is the door locked'), "
                            "pass a *query* — semantic search returns the ~40 most relevant entities rather than flooding context with all of them. "
                            "Pass *domain_filter* to limit to a single HA domain (e.g. 'light', 'switch', 'sensor'). "
                            "Omit both arguments only when a broad overview of ALL entities is genuinely needed."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "domain_filter": {
                                    "type": "string",
                                    "description": "HA domain to limit results (e.g. 'light', 'switch', 'sensor', 'binary_sensor', 'climate', 'media_player'). When set, *query* is ignored."
                                },
                                "query": {
                                    "type": "string",
                                    "description": "Natural language description of which entities you need (e.g. 'bedroom lights', 'door and window sensors', 'heating climate devices'). Triggers semantic search — returns ~40 most relevant entities. Ignored when domain_filter is set."
                                }
                            },
                            "required": []
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "read_memories",
                        "description": "Read persistent memory files saved from previous sessions. Always call this at the start of a session when context about the user's setup might help. Returns all memory files or a specific one.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "filename": {
                                    "type": "string",
                                    "description": "Specific memory file to read (e.g. 'home_structure.md'). Omit to read all memory files."
                                }
                            },
                            "required": []
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "save_memory",
                        "description": "Save or update a persistent memory file. Use category prefixes: preference_, device_, identity_, baseline_, pattern_, correction_. Only save persistent facts, not current states or command echoes. Use 'replaces' when correcting a previous memory.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "filename": {
                                    "type": "string",
                                    "description": "Filename with category prefix (e.g. 'preference_lighting.md', 'device_nicknames.md', 'pattern_morning_routine.md'). Only letters, numbers, hyphens and underscores kept; .md forced."
                                },
                                "content": {
                                    "type": "string",
                                    "description": "Full markdown content to store. Concise factual bullet points preferred."
                                },
                                "replaces": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Optional list of memory filenames this new entry supersedes (e.g. when the user corrects a preference). Those files are deleted atomically."
                                }
                            },
                            "required": ["filename", "content"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "delete_memory",
                        "description": "Delete a memory file that is no longer accurate or relevant.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "filename": {
                                    "type": "string",
                                    "description": "Name of the memory file to delete."
                                }
                            },
                            "required": ["filename"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "list_memory_stats",
                        "description": "Audit memory health: returns each file's name, size in chars, age in days, and a stale flag (true when age > 90 days). Call this periodically to identify files to prune, merge, or delete.",
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
                        "name": "consolidate_memories",
                        "description": (
                            "Audit all memory files and produce a consolidation plan. "
                            "Call this when the user asks to clean up, review, or consolidate memories, "
                            "or when list_memory_stats shows many stale/tiny files. "
                            "Returns file contents for analysis — you then present a MERGE/DELETE/KEEP plan "
                            "to the user before taking any action."
                        ),
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
                        "name": "search_past_sessions",
                        "description": "Keyword search across past conversation sessions. Use this when the user references something discussed before (e.g. 'that automation we made last week', 'remember when we fixed...'), or before starting work on a topic that likely has prior history. Returns matching sessions with excerpts.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "Search query, e.g. 'morning routine automation', 'boiler heating schedule', 'dashboard lights card'."
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
                            "Write a plain-text value directly to an existing input_text helper in Home Assistant. "
                            "Use this to deliver AI-generated content (morning briefings, summaries, status reports, advice) "
                            "so it can be consumed by automations, dashboards, or TTS without going through the chat. "
                            "The entity must already exist — create it via Settings → Helpers if needed. "
                            "Max 255 characters (HA hard limit). No approval needed — the write happens immediately."
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
                            "Create a recurring AI task: runs a prompt on a schedule and writes the result to an input_text entity. "
                            "Use this when the user wants automated periodic content — e.g. 'every morning write a briefing to input_text.briefing'. "
                            "The task is saved persistently and survives restarts. "
                            "The entity must already exist as an input_text helper. "
                            "Call get_entity_states(domain_filter='input_text') first to confirm the entity exists."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description": "Short human-readable label for this task, e.g. 'Morning briefing'."
                                },
                                "prompt": {
                                    "type": "string",
                                    "description": "The prompt the AI runs each time. Should produce a self-contained plain-text result (max 255 chars)."
                                },
                                "entity_id": {
                                    "type": "string",
                                    "description": "input_text entity to write the result to, e.g. 'input_text.morning_briefing'."
                                },
                                "schedule": {
                                    "type": "string",
                                    "description": "When to run. Format: 'daily HH:MM' (24-hour local time). Example: 'daily 08:00'."
                                }
                            },
                            "required": ["name", "prompt", "entity_id", "schedule"]
                        }
                    }
                },
            ]

            # Track tool calls and results
            new_messages = []

            # Loop to handle multiple rounds of tool calls
            max_iterations = 10
            iteration = 0

            # Track cumulative token usage across all iterations
            total_input_tokens = 0
            total_output_tokens = 0
            total_cached_tokens = 0

            # Initialize status queue for streaming tool progress events
            self._tool_status_queue = asyncio.Queue()

            # Per-turn state (local — safe under concurrent WS connections)
            turn_state = {"has_read": False, "retry_counts": {}}
            self.tools.clear_turn_cache()

            # Pre-declare so outer except can access partial content on stream errors
            accumulated_content = ""

            while iteration < max_iterations:
                iteration += 1
                logger.info(f"[ITERATION {iteration}] Calling OpenAI streaming API")

                # Prune old tool exchange blocks only in long conversations to keep context lean.
                # Don't prune in short single-turn operations — AI needs file content it just read.
                if iteration > 1 and len(messages) > 30:
                    messages = self._prune_old_tool_messages(messages)

                # Select client+model: config once tool results exist, suggestion before that.
                has_tool_results = any(m.get("role") == "tool" for m in messages)
                active_model  = self.config_model  if has_tool_results else self.suggestion_model
                active_client = self.config_client if has_tool_results else self.suggestion_client
                logger.debug(f"[ITERATION {iteration}] Using {'config' if has_tool_results else 'suggestion'} model: {active_model}")

                # Strip excess cache_control blocks before each call (Anthropic limit: 4 total,
                # we reserve 1 for system prompt so allow 3 in the messages list)
                safe_messages = self._strip_excess_cache_control(messages) if self.enable_cache_control else messages

                # Call OpenAI API with streaming
                api_params = {
                    "model": active_model,
                    "messages": safe_messages,
                    "tools": tools,
                    "tool_choice": "auto",
                    "stream": True
                }

                # Add usage tracking based on per-phase mode
                active_usage_tracking = self.config_usage_tracking if has_tool_results else self.suggestion_usage_tracking
                if active_usage_tracking == 'stream_options':
                    api_params["stream_options"] = {"include_usage": True}
                elif active_usage_tracking == 'usage':
                    api_params["usage"] = {"include": True}
                # If disabled, don't add any usage tracking parameters

                # Add temperature if specified
                if self.temperature is not None:
                    api_params["temperature"] = self.temperature

                # Add per-phase max_tokens if configured (FEAT-7)
                phase_max_tokens = self.config_max_tokens if has_tool_results else self.suggestion_max_tokens
                if phase_max_tokens:
                    api_params["max_tokens"] = phase_max_tokens

                # Determine which client slot we're using for BUG-8 per-client caching
                client_slot = 'config' if has_tool_results else 'suggestion'

                # Strip usage-tracking params if we already know this client rejects them
                if self._client_usage_ok.get(client_slot) is False:
                    api_params = {k: v for k, v in api_params.items() if k not in ('stream_options', 'usage')}

                try:
                    stream = await active_client.chat.completions.create(**api_params)
                    # Mark as accepted only if we haven't already confirmed it
                    if self._client_usage_ok.get(client_slot) is None:
                        self._client_usage_ok[client_slot] = True
                except Exception as api_err:
                    # Some providers (e.g. Anthropic/Haiku) reject stream_options or
                    # usage params — retry without them to keep things working.
                    err_str = str(api_err).lower()
                    if 'stream_options' in err_str or 'usage' in err_str or 'extra' in err_str or 'unknown' in err_str:
                        logger.warning(f"API rejected usage-tracking params, retrying without them: {api_err}")
                        # Propagate to all slots — if one slot rejects, the same endpoint will reject all.
                        for slot in self._client_usage_ok:
                            if self._client_usage_ok[slot] is None:
                                self._client_usage_ok[slot] = False
                        self._client_usage_ok[client_slot] = False
                        retry_params = {k: v for k, v in api_params.items() if k not in ('stream_options', 'usage')}
                        stream = await active_client.chat.completions.create(**retry_params)
                    else:
                        raise

                # Accumulate the streaming response
                accumulated_content = ""
                accumulated_tool_calls = []
                current_tool_call = None
                tool_calls_announced = False
                tool_calls_pending_announced = False
                # Track token usage
                input_tokens = 0
                output_tokens = 0
                cached_tokens = 0

                async for chunk in stream:
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

                        logger.debug(f"[USAGE] Parsed - Input: {input_tokens}, Output: {output_tokens}, Cached: {cached_tokens}")

                        # Accumulate totals
                        total_input_tokens += input_tokens
                        total_output_tokens += output_tokens
                        total_cached_tokens += cached_tokens

                    # Stream content tokens
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

                        # Announce tool calls to UI as soon as we know them (may have partial arguments)
                        if not tool_calls_announced and any(tc.get("function", {}).get("name") for tc in accumulated_tool_calls):
                            yield {
                                "event": "tool_call",
                                "data": json.dumps({
                                    "tool_calls": accumulated_tool_calls,
                                    "iteration": iteration
                                })
                            }
                            tool_calls_announced = True

                    # Check for finish reason
                    if chunk.choices[0].finish_reason:
                        break

                # Check if we have tool calls
                if not accumulated_tool_calls:
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
                    function_args = json.loads(tool_call["function"]["arguments"] or "{}")

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

                    # Execute the tool via a task, yielding status events while waiting
                    tool_task = asyncio.create_task(self._dispatch_tool(function_name, function_args, turn_state))
                    while not tool_task.done():
                        while not self._tool_status_queue.empty():
                            yield {"event": "tool_status", "data": json.dumps(self._tool_status_queue.get_nowait())}
                        await asyncio.sleep(0.05)
                    # Drain any remaining status events before announcing the result
                    while not self._tool_status_queue.empty():
                        yield {"event": "tool_status", "data": json.dumps(self._tool_status_queue.get_nowait())}
                    try:
                        result = tool_task.result()
                    except Exception as e:
                        result = {"success": False, "error": str(e)}
                    logger.info(f"[ITERATION {iteration}] Tool '{function_name}' done: success={result.get('success')}")

                    # Update read-before-write guard (local per-turn state)
                    if function_name in ("search_config_files", "get_nodered_flows") and result.get("success"):
                        turn_state["has_read"] = True

                    # Error recovery: inject a retry directive for correctable failures.
                    # Skip for: successes, approval-gated results (changeset_id), timed-out calls.
                    tool_result_content = json.dumps(result)
                    _is_failure = not result.get("success", True)
                    _is_approval_gated = "changeset_id" in result
                    _is_infra_error = "timed out" in result.get("error", "").lower()
                    if _is_failure and not _is_approval_gated and not _is_infra_error:
                        _tool_key = f"{tool_call['id']}:{function_name}"
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

            billable_input = max(0, total_input_tokens - total_cached_tokens)
            cost_usd = (
                billable_input * self.input_price_per_1m +
                total_output_tokens * self.output_price_per_1m
            ) / 1_000_000 if (self.input_price_per_1m or self.output_price_per_1m) else None

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
            # Skip if the conversation was too short to contain durable facts.
            all_messages = messages + new_messages
            user_turns = sum(1 for m in all_messages if m.get("role") == "user")
            has_tool_activity = any(m.get("role") == "tool" for m in all_messages)
            if user_turns >= 2 or has_tool_activity:
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
            for eid, (state_val, fname) in entity_state.items():
                if eid.split(".")[0] in SKIP_DOMAINS:
                    continue
                aid = entity_area.get(eid)
                if aid and aid in area_buckets:
                    label = fname if fname and fname != eid else eid
                    area_buckets[aid].append(f"{label} [{state_val}]")
                else:
                    unassigned_count += 1

            # Format each area (max 8 entities shown per area to keep it compact)
            MAX_CHARS = 2000
            for aid, aname in areas.items():
                bucket = area_buckets.get(aid, [])
                if not bucket:
                    continue
                shown = bucket[:8]
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

    async def _summarize_old_history(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        BUG-6: When conversation history is very long (>30 messages / 15 exchanges),
        summarise the oldest half into a single system message so the context stays manageable.
        Keeps the system prompt + summarised block + recent messages intact.
        """
        import json
        # messages[0] is the system prompt; the rest are history + current user msg
        # (current user msg is NOT yet added when this is called — it's added right after)
        # Identify the history portion (everything after system message)
        system_msg = messages[0]
        history = messages[1:]

        # Keep the newest 20 messages verbatim; summarize the rest
        keep_recent = 20
        to_summarize = history[:-keep_recent] if len(history) > keep_recent else []
        keep = history[-keep_recent:] if len(history) > keep_recent else history

        if not to_summarize:
            return messages

        # Build a slim text representation of the messages to summarise
        summary_input = "\n".join(
            f"{m['role'].upper()}: {m.get('content', '') if isinstance(m.get('content'), str) else ''}"
            for m in to_summarize
            if m.get('role') in ('user', 'assistant')
        )

        summary_prompt = [
            {"role": "system", "content": "Summarise the following conversation in concise bullet points, capturing all important facts, decisions, and context. Be brief — max 400 words."},
            {"role": "user", "content": summary_input}
        ]

        try:
            resp = await self.client.chat.completions.create(
                model=self.model,
                messages=summary_prompt,
                stream=False,
                max_tokens=600,
            )
            summary_text = resp.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"Summarization API call failed: {e}")
            return messages

        summary_msg = {
            "role": "system",
            "content": f"[Earlier conversation summary]\n{summary_text}"
        }
        logger.info(f"Summarized {len(to_summarize)} old messages into a single block ({len(summary_text)} chars)")
        return [system_msg, summary_msg] + keep

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
            memory_context = await self.memory_manager.get_context()
            system_content = (
                "You are a memory consolidation assistant for a Home Assistant AI companion.\n"
                "Your job: extract ONLY high-value, durable facts from the conversation — facts the AI\n"
                "cannot figure out by reading HA config files or entity states.\n\n"
                "SAVE only when ALL of these are true:\n"
                "  1. The user explicitly stated it (never infer or assume)\n"
                "  2. It is durable — remains true across many future sessions\n"
                "  3. It cannot be derived from HA config/entity list\n"
                "  4. Confidence ≥ 0.9 — when in doubt, save NOTHING\n\n"
                "Good examples to save:\n"
                "  - preference_notifications.md: 'always notify mobile_krystian AND mobile_marta'\n"
                "  - identity_residents.md: 'Krystian and Marta live here; Krystian = primary user'\n"
                "  - device_purpose.md: 'pompa = water pressure pump; cwu = hot water boiler'\n"
                "  - preference_language.md: 'respond in Polish; HA content in Polish'\n\n"
                "NEVER save (automatic disqualifiers):\n"
                "  - What was done this session ('created automation X', 'edited file Y', 'we configured Z')\n"
                "  - Any automation, script, or flow that was created (it is already in config files)\n"
                "  - Current sensor readings, device states, or live values\n"
                "  - Device model names, specs, or integration details\n"
                "  - Anything the AI can find by searching config files or entity states\n"
                "  - Inferred or assumed facts the user never said out loud\n\n"
                "If nothing from the conversation qualifies: call NO tools at all.\n"
                "If existing memory is wrong or stale: overwrite with save_memory (use 'replaces' field).\n"
                "Max 800 chars per file (bullet points only). Max 25 files — merge before creating new.\n"
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
                if isinstance(m.get("content"), str):
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
            memory_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "read_memories",
                        "description": "Read persistent memory files saved from previous sessions.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "filename": {"type": "string", "description": "Specific memory file to read. Omit to read all."}
                            },
                            "required": []
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "save_memory",
                        "description": "Save or update a persistent memory file. Use category prefixes: preference_, device_, identity_, baseline_, pattern_, correction_, ecosystem_. Only save persistent facts.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "filename": {"type": "string", "description": "Filename with category prefix (e.g. 'preference_lighting.md'). Only letters, numbers, hyphens and underscores kept; .md forced."},
                                "content": {"type": "string", "description": "Full markdown content to store. Concise factual bullet points preferred."},
                                "replaces": {"type": "array", "items": {"type": "string"}, "description": "Optional list of memory filenames this entry supersedes."}
                            },
                            "required": ["filename", "content"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "delete_memory",
                        "description": "Delete a memory file that is no longer accurate or relevant.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "filename": {"type": "string", "description": "Name of the memory file to delete."}
                            },
                            "required": ["filename"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "list_memory_stats",
                        "description": "Audit memory health: returns each file's name, size, age in days, and stale flag.",
                        "parameters": {"type": "object", "properties": {}, "required": []}
                    }
                },
            ]

            # Non-streaming, single pass, no retry loop.
            # Use suggestion model (cheaper) for background consolidation work.
            consolidation_client = self.suggestion_client
            consolidation_model = self.suggestion_model
            response = await consolidation_client.chat.completions.create(
                model=consolidation_model,
                messages=messages,
                tools=memory_tools,
                tool_choice="auto",
                max_tokens=1024,
            )

            _CONSOLIDATION_TOOLS = {"read_memories", "save_memory", "delete_memory", "list_memory_stats"}
            choice = response.choices[0]
            if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
                for tc in choice.message.tool_calls:
                    fn = tc.function.name
                    if fn not in _CONSOLIDATION_TOOLS:
                        logger.warning(f"[memory consolidation] Ignoring unexpected tool call: {fn}")
                        continue
                    args = json.loads(tc.function.arguments or "{}")
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
        changeset = Changeset(
            changeset_id=changeset_id,
            file_changes=changeset_data['file_changes'],
            created_at=now.isoformat(),
            expires_at=(now + timedelta(hours=1)).isoformat()
        )

        self.pending_changesets[changeset_id] = changeset
        logger.info(f"Stored changeset {changeset_id} with {len(changeset.file_changes)} file(s)")
        return changeset_id

    async def process_approval(
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
            return {
                "success": False,
                "applied": False,
                "message": f"Changeset {change_id} not found or has expired"
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
            del self.pending_changesets[change_id]
            return {
                "success": False,
                "applied": False,
                "message": "Changeset has expired. Please re-propose the changes."
            }

        # Apply changes
        try:
            applied_files = []
            failed_files = []

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
            if validate and applied_files:
                try:
                    logger.info("Validating configuration after writing all files...")
                    await self.config_manager.validate_config()
                    logger.info("Configuration validation passed")
                except Exception as e:
                    logger.error(f"Configuration validation failed: {e}")
                    validation_failed = True
                    # Note: We don't rollback here because backups were created
                    # Users can manually restore from backups if needed
                    failed_files.append({
                        "file_path": "validation",
                        "error": f"Configuration validation failed: {str(e)}"
                    })

            # Remove changeset from pending
            del self.pending_changesets[change_id]

            # Reload Home Assistant configuration after successful changes (only if validation passed)
            reload_success = False
            if applied_files and not validation_failed:
                try:
                    from ..ha.ha_websocket import reload_homeassistant_config

                    supervisor_token = os.getenv('SUPERVISOR_TOKEN')
                    if supervisor_token:
                        ws_url = "ws://supervisor/core/websocket"
                        await reload_homeassistant_config(ws_url, supervisor_token)
                        reload_success = True
                        logger.info("Home Assistant configuration reloaded successfully")
                    else:
                        logger.warning("SUPERVISOR_TOKEN not available, skipping config reload")
                except Exception as e:
                    logger.warning(f"Failed to reload Home Assistant config: {e}")

            if failed_files:
                return {
                    "success": True,
                    "applied": True,
                    "message": f"Partially applied: {len(applied_files)} succeeded, {len(failed_files)} failed",
                    "applied_files": applied_files,
                    "failed_files": failed_files,
                    "config_reloaded": reload_success
                }
            else:
                message = f"Successfully applied changes to {len(applied_files)} file(s)"
                if reload_success:
                    message += " and reloaded Home Assistant configuration"
                return {
                    "success": True,
                    "applied": True,
                    "message": message,
                    "applied_files": applied_files,
                    "config_reloaded": reload_success
                }

        except Exception as e:
            logger.error(f"Error applying changeset: {e}", exc_info=True)
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
            active_types = set(resource_types) if resource_types else set(ALL_RESOURCE_TYPES)
            context_summary = []

            # 1. Gather context based on selected resource types
            context_sections = []
            # Track included file paths to avoid sending the same file content in multiple sections
            included_paths: set = set()

            if 'entity_states' in active_types:
                await _emit("Fetching entity states…")
                entity_states_result = await self.tools.get_entity_states()
                states = entity_states_result.get("states", [])
                entity_states_text = self._format_entity_states_compact(states)
                context_sections.append(f"## Current entity states\n{entity_states_text}")
                context_summary.append({"type": "entity_states", "count": len(states), "chars": len(entity_states_text)})
                await _emit(f"✓ Entity states: {len(states)} entities")

            if 'automations' in active_types:
                await _emit("Loading automations…")
                # Read all automation files (supports single-file and split-file setups).
                # Search for 'alias' which is present in every YAML automation block.
                automations_result = await self.tools.search_config_files(search_pattern="alias")
                automations_files = automations_result.get("files", [])
                automation_contents = []
                for f in automations_files:
                    p = f.get("path", "")
                    if "automation" in p.lower() and p not in included_paths:
                        included_paths.add(p)
                        automation_contents.append(f"# {p}\n{f.get('content', '')}")
                automations_text = "\n\n".join(automation_contents) if automation_contents else json.dumps(automations_files, indent=2)
                context_sections.append(f"## Existing automations\n{automations_text}")
                context_summary.append({"type": "automations", "files": len(automation_contents), "chars": len(automations_text)})
                await _emit(f"✓ Automations: {len(automation_contents)} file(s)")

            if 'scenes' in active_types:
                try:
                    await _emit("Loading scenes…")
                    scenes_result = await self.tools.search_config_files(search_pattern="scene:")
                    scenes_files = [
                        f for f in scenes_result.get("files", [])
                        if "scene" in f.get("path", "").lower() and f.get("path") not in included_paths
                    ]
                    for f in scenes_files:
                        included_paths.add(f.get("path", ""))
                    if scenes_files:
                        scenes_text = "\n\n".join(f"# {f['path']}\n{f.get('content', '')}" for f in scenes_files)
                        context_sections.append(f"## Existing scenes\n{scenes_text}")
                        context_summary.append({"type": "scenes", "files": len(scenes_files), "chars": len(scenes_text)})
                        await _emit(f"✓ Scenes: {len(scenes_files)} file(s)")
                    else:
                        await _emit("✓ Scenes: none found")
                except Exception:
                    pass

            if 'scripts' in active_types:
                try:
                    await _emit("Loading scripts…")
                    scripts_result = await self.tools.search_config_files(search_pattern="sequence:")
                    scripts_files = [
                        f for f in scripts_result.get("files", [])
                        if "script" in f.get("path", "").lower() and f.get("path") not in included_paths
                    ]
                    for f in scripts_files:
                        included_paths.add(f.get("path", ""))
                    if scripts_files:
                        scripts_text = "\n\n".join(f"# {f['path']}\n{f.get('content', '')}" for f in scripts_files)
                        context_sections.append(f"## Existing scripts\n{scripts_text}")
                        context_summary.append({"type": "scripts", "files": len(scripts_files), "chars": len(scripts_text)})
                        await _emit(f"✓ Scripts: {len(scripts_files)} file(s)")
                    else:
                        await _emit("✓ Scripts: none found")
                except Exception:
                    pass

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
                except Exception:
                    pass

            nodered_text = ""
            if 'nodered' in active_types:
                try:
                    await _emit("Loading Node-RED flows…")
                    nodered_result = await self.tools.get_nodered_flows()
                    if nodered_result.get("success"):
                        nodered_text = json.dumps(nodered_result.get("flows", []), indent=2)
                except Exception:
                    pass
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
                    memory_context = await self.memory_manager.get_context()
                    if memory_context:
                        context_sections.append(f"## Home context from memory\n{memory_context}")
                        context_summary.append({"type": "memory", "chars": len(memory_context)})
                        await _emit(f"✓ Memory: {len(memory_context)} chars")
                    else:
                        await _emit("✓ Memory: empty")
                except Exception:
                    pass

            # 3. Build remaining context
            suggestion_prompt_env = os.getenv("SUGGESTION_PROMPT", "").strip()

            # Inject dismissed suggestions so the AI won't re-suggest them
            dismissed = []
            try:
                config_dir = os.getenv("HA_CONFIG_DIR", "/config")
                dismissed_path = os.path.join(config_dir, ".ai_agent_suggestions_dismissed.json")
                if os.path.exists(dismissed_path):
                    with open(dismissed_path, "r") as f:
                        dismissed = json.load(f)
            except Exception:
                pass
            if dismissed:
                dismissed_list = "\n".join(f"- {t}" for t in dismissed)
                context_sections.append(f"## User has dismissed these suggestions — do NOT suggest them again\n{dismissed_list}")

            # Inject applied suggestions so the AI won't re-suggest them
            applied = []
            try:
                applied_path = os.path.join(config_dir, ".ai_agent_suggestions_applied.json")
                if os.path.exists(applied_path):
                    with open(applied_path, "r") as f:
                        applied = json.load(f)
            except Exception:
                pass
            if applied:
                applied_list = "\n".join(f"- {t}" for t in applied)
                context_sections.append(f"## User has already applied these suggestions — do NOT suggest them again\n{applied_list}")

            # Per-request extra_prompt (from UI textarea) takes priority over env var
            active_extra = (extra_prompt or "").strip() or suggestion_prompt_env
            if active_extra:
                context_sections.append(f"## Additional focus from user\n{active_extra}")

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

            naming_instruction = ""
            if 'entity_states' in active_types:
                naming_instruction = (
                    "Also review entity friendly_names and flag ONLY names where the device's purpose or function "
                    "is genuinely ambiguous — i.e. a knowledgeable person could not determine what the device does "
                    "from the name alone (e.g. 'pompa', 'sensor_1', 'device_003', 'nowy_guzik'). "
                    "Do NOT flag cosmetic issues: punctuation, capitalisation, hyphenation, word order, "
                    "abbreviations, or names that are simply short. "
                    "Keep naming_issues minimal — fewer is better. "
                )

            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a Home Assistant expert. "
                        f"{suggest_instruction} "
                        f"{entity_grounding}"
                        f"{naming_instruction}"
                        "Return ONLY valid JSON — an object with: "
                        "'suggestions' (array of suggestions) and "
                        "'naming_issues' (array of unclear names, may be empty). "
                        "Each suggestion must have: title (string), description (string), "
                        "type (either 'new' for new automations/scripts or 'improvement' for improving existing ones), "
                        "category (one of: lighting, climate, security, energy, comfort, other), "
                        "entities (array of entity_id strings that actually exist), "
                        "implementation_hint (string with brief YAML hint or description of what to change). "
                        "Each naming_issue must have: entity_id (string), current_name (string), "
                        "suggested_name (string), reason (string)."
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
            response = await self.suggestion_client.chat.completions.create(**api_params)
            raw = response.choices[0].message.content.strip()

            # Strip markdown code fences if present
            if raw.startswith("```"):
                fence_match = re.search(r'^```(?:json)?\s*\n?([\s\S]*?)\n?```', raw)
                if fence_match:
                    raw = fence_match.group(1).strip()
                else:
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                    raw = raw.strip()

            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                # LLM sometimes wraps JSON in extra text or produces minor syntax errors.
                # Try to extract the outermost JSON object as a fallback.
                m = re.search(r'\{[\s\S]*\}', raw)
                if m:
                    try:
                        parsed = json.loads(m.group())
                    except json.JSONDecodeError as inner_err:
                        logger.warning(f"Suggestions JSON unrecoverable after extraction ({inner_err}); returning empty result")
                        return {"success": True, "suggestions": [], "naming_issues": [], "context_summary": context_summary}
                else:
                    logger.warning("Suggestions JSON parse failed, no JSON block found; returning empty result")
                    return {"success": True, "suggestions": [], "naming_issues": [], "context_summary": context_summary}
            if isinstance(parsed, dict):
                suggestions = parsed.get("suggestions", [])
                naming_issues = parsed.get("naming_issues", [])
            else:
                suggestions = parsed
                naming_issues = []

            logger.info(f"Generated {len(suggestions)} automation suggestions, {len(naming_issues)} naming issues")
            await _emit(f"✓ Done: {len(suggestions)} suggestion(s), {len(naming_issues)} naming issue(s)")
            return {"success": True, "suggestions": suggestions, "naming_issues": naming_issues, "context_summary": context_summary}

        except Exception as e:
            logger.error(f"Failed to generate suggestions: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


