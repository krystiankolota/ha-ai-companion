"""Navigation + automation tools: read only what is needed, edit one item at a time.

Mixed into AgentTools. Relies on: self.config_manager, self._store_changeset_with_diffs,
self._validate_entity_ids_in_yaml, self._get_all_entities/_get_all_devices/_get_all_areas.
"""
import logging
import os
import re
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..ha.layout import default_target, detect_layout
from ..ha.yaml_blocks import (
    find_item, item_text, load_tolerant, normalize_item_text, outline, read_range, remove_item, split_blocks,
    upsert_item,
)

logger = logging.getLogger(__name__)

SMALL_FILE_LINES = 60
_ENTITY_ID_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")


def _short_ts(iso: Optional[str]) -> str:
    if not iso:
        return "never"
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso[:16]


def summarize_trace(trace: Dict[str, Any]) -> Dict[str, Any]:
    """Compact view of one trace/get result: per-step result and errors, no variable dumps."""
    steps = []
    for path, entries in (trace.get("trace") or {}).items():
        for e in entries or []:
            step = {"path": path}
            res = e.get("result")
            if isinstance(res, dict):
                step["result"] = {k: v for k, v in res.items() if k in ("result", "enabled", "delay", "done", "params", "response", "conditions", "wait", "choice", "running_script")}
            if e.get("error"):
                step["error"] = e["error"]
            steps.append(step)
    out = {
        "run_id": trace.get("run_id"),
        "start": trace.get("timestamp", {}).get("start"),
        "trigger": trace.get("trigger"),
        "state": trace.get("state"),
        "script_execution": trace.get("script_execution"),
        "last_step": trace.get("last_step"),
        "steps": steps,
    }
    if trace.get("error"):
        out["error"] = trace["error"]
    return out


def score_entity(query_terms: List[str], text: str) -> int:
    """Count of query terms found; all terms must match or score is 0."""
    hits = sum(1 for t in query_terms if t in text)
    return hits if hits == len(query_terms) else 0


class NavigationToolsMixin:
    # ------------------------------------------------------------------
    # Low-level HA access (add-on: Supervisor WS; dev: HA_URL + HA_TOKEN)
    # ------------------------------------------------------------------

    async def _ws_call(self, msg_type: str, **kwargs) -> Any:
        from ..ha.ha_websocket import HomeAssistantWebSocket

        token = os.getenv("SUPERVISOR_TOKEN")
        url = "ws://supervisor/core/websocket"
        if not token and os.getenv("HA_TOKEN") and os.getenv("HA_URL"):
            token = os.getenv("HA_TOKEN")
            url = os.getenv("HA_URL").rstrip("/").replace("http", "ws", 1) + "/api/websocket"
        if not token:
            raise RuntimeError("Home Assistant WebSocket is not available (no SUPERVISOR_TOKEN)")
        ws = HomeAssistantWebSocket(url, token)
        await ws.connect()
        try:
            return await ws.call(msg_type, **kwargs)
        finally:
            await ws.close()

    async def _all_states(self) -> List[Dict[str, Any]]:
        cache = getattr(self, "_turn_registry_cache", None)
        if cache is not None and "states" in cache:
            return cache["states"]
        hass = getattr(self.config_manager, "hass", None)
        if hass is not None:
            states = [s.as_dict() for s in hass.states.async_all()]
        else:
            states = await self._ws_call("get_states")
        if cache is not None:
            cache["states"] = states
        return states

    def _layout(self) -> Dict[str, List[dict]]:
        return detect_layout(self.config_manager.config_dir)

    async def _read_text(self, file_path: str) -> Optional[str]:
        if os.path.basename(file_path) == "secrets.yaml":
            raise PermissionError("secrets.yaml is not readable")
        return await self.config_manager.read_file_raw(file_path, allow_missing=True)

    # ------------------------------------------------------------------
    # Generic file navigation
    # ------------------------------------------------------------------

    async def outline_file(self, file_path: str, depth: int = 1) -> Dict[str, Any]:
        """Top-level items of a YAML file with line ranges. Small files come back whole."""
        from .tools import _redact_secrets
        try:
            text = await self._read_text(file_path)
        except Exception as e:
            return {"success": False, "error": str(e)}
        if text is None:
            return {"success": False, "error": f"File not found: {file_path}"}
        total = len(text.splitlines())
        result: Dict[str, Any] = {"success": True, "file_path": file_path, "total_lines": total}
        if total <= SMALL_FILE_LINES:
            result["content"] = _redact_secrets(text)
            return result
        result["items"] = outline(text, depth=2 if int(depth or 1) >= 2 else 1)
        result["hint"] = "Use read_range(file_path, start_line, end_line) for the lines you need."
        return result

    async def read_range(self, file_path: str, start_line: int, end_line: int) -> Dict[str, Any]:
        """Numbered lines start..end (1-based, inclusive, max 400)."""
        from .tools import _redact_secrets
        try:
            text = await self._read_text(file_path)
        except Exception as e:
            return {"success": False, "error": str(e)}
        if text is None:
            return {"success": False, "error": f"File not found: {file_path}"}
        # Redact before numbering: the redactor's patterns are anchored at line start.
        r = read_range(_redact_secrets(text), start_line, end_line)
        return {"success": True, "file_path": file_path, **r}

    # ------------------------------------------------------------------
    # Entities
    # ------------------------------------------------------------------

    async def search_entities(self, query: str = "", domain: Optional[str] = None,
                              area: Optional[str] = None, limit: int = 30) -> Dict[str, Any]:
        """Lexical entity search over entity_id, friendly name, and area. Compact output."""
        try:
            states = await self._all_states()
        except Exception as e:
            return {"success": False, "error": str(e)}
        entities = {e.get("entity_id"): e for e in await self._get_all_entities()}
        devices = {d.get("id"): d for d in await self._get_all_devices()}
        areas = {a.get("area_id"): a.get("name") or a.get("area_id") for a in await self._get_all_areas()}

        def area_of(eid):
            reg = entities.get(eid) or {}
            aid = reg.get("area_id") or (devices.get(reg.get("device_id")) or {}).get("area_id")
            return areas.get(aid) if aid else None

        terms = [t for t in (query or "").lower().split() if t]
        area_l = (area or "").lower()
        rows = []
        for s in states:
            eid = s.get("entity_id", "")
            if domain and not eid.startswith(f"{domain}."):
                continue
            name = (s.get("attributes") or {}).get("friendly_name") or ""
            a = area_of(eid) or ""
            if area_l and area_l not in a.lower():
                continue
            score = score_entity(terms, f"{eid} {name} {a}".lower()) if terms else 1
            if score:
                rows.append((score, eid, name, a, s.get("state")))
        rows.sort(key=lambda r: (-r[0], r[1]))
        limit = max(1, min(int(limit or 30), 100))
        lines = [f'"{n}"[{e}]={st}' + (f" @{a}" if a else "") for _, e, n, a, st in rows[:limit]]
        return {"success": True, "count": len(rows), "returned": len(lines), "entities": "\n".join(lines)}

    # ------------------------------------------------------------------
    # Automations
    # ------------------------------------------------------------------

    async def _automation_index(self) -> List[Dict[str, Any]]:
        """Every automation item found in the layout's files: id, alias, file, lines."""
        idx = []
        for entry in self._layout().get("automation", []):
            if entry.get("empty_dir"):
                continue
            text = await self._read_text(entry["path"]) or ""
            if entry["format"] == "list":
                _kind, blocks = split_blocks(text)
                for b in blocks:
                    if isinstance(b.value, dict):
                        idx.append({"id": b.value.get("id"), "alias": b.value.get("alias"),
                                    "file": entry["path"], "lines": f"{b.start + 1}-{b.end}", "editable": True})
            elif entry["format"] == "item":
                data = load_tolerant(text)
                if isinstance(data, dict):
                    idx.append({"id": data.get("id"), "alias": data.get("alias"),
                                "file": entry["path"], "editable": False, "single_item": True})
            elif entry["format"] in ("package", "inline"):
                data = load_tolerant(text) or {}
                items = data.get(entry.get("key") or "automation") if isinstance(data, dict) else None
                for it in items if isinstance(items, list) else []:
                    if isinstance(it, dict):
                        idx.append({"id": it.get("id"), "alias": it.get("alias"),
                                    "file": entry["path"], "editable": False})
        return idx

    async def _resolve_automation(self, ref: str):
        """ref = id, entity_id, or alias → (state or None, index entry or None)."""
        ref = (ref or "").strip()
        states = [s for s in await self._all_states() if s.get("entity_id", "").startswith("automation.")]
        state = next((s for s in states if s["entity_id"] == ref), None) \
            or next((s for s in states if str((s.get("attributes") or {}).get("id")) == ref), None) \
            or next((s for s in states if (s.get("attributes") or {}).get("friendly_name") == ref), None)
        item_id = str((state or {}).get("attributes", {}).get("id") or ref)
        alias = (state or {}).get("attributes", {}).get("friendly_name") or ref
        idx = await self._automation_index()
        entry = next((e for e in idx if e.get("id") is not None and str(e["id"]) == item_id), None)
        if entry is None:
            # Alias is a fallback only; duplicates (id-less items) must not pick an arbitrary one.
            by_alias = [e for e in idx if e["alias"] == alias and e.get("id") is None]
            entry = by_alias[0] if len(by_alias) == 1 else None
        return state, entry

    async def list_automations(self, query: Optional[str] = None) -> Dict[str, Any]:
        """One line per automation: id | alias | state | last triggered | file:lines."""
        try:
            states = [s for s in await self._all_states() if s.get("entity_id", "").startswith("automation.")]
            idx = await self._automation_index()
        except Exception as e:
            return {"success": False, "error": str(e)}
        by_id = {str(e["id"]): e for e in idx if e.get("id") is not None}
        q = (query or "").lower()
        lines, seen = [], set()
        for s in sorted(states, key=lambda s: s["entity_id"]):
            attrs = s.get("attributes") or {}
            aid = str(attrs.get("id")) if attrs.get("id") is not None else None
            entry = by_id.get(aid) if aid else None
            seen.add(aid)
            alias = attrs.get("friendly_name") or s["entity_id"]
            if q and q not in f"{aid} {alias} {s['entity_id']}".lower():
                continue
            where = f"{entry['file']}:{entry.get('lines', '?')}" if entry else "location unknown (UI/other)"
            lines.append(f"{aid or '-'} | {alias} | {s.get('state')} | last: {_short_ts(attrs.get('last_triggered'))} | {where}")
        for e in idx:
            if str(e.get("id")) not in seen and (not q or q in f"{e.get('id')} {e.get('alias')}".lower()):
                lines.append(f"{e.get('id') or '-'} | {e.get('alias')} | NOT LOADED in HA | {e['file']}:{e.get('lines', '?')}")
        return {"success": True, "count": len(lines), "automations": "\n".join(lines[:300]),
                "format": "id | alias | state | last triggered | file:lines"}

    async def get_automation(self, automation: str) -> Dict[str, Any]:
        """Exact YAML of one automation (by id, entity_id, or alias) and where it lives."""
        try:
            state, entry = await self._resolve_automation(automation)
        except Exception as e:
            return {"success": False, "error": str(e)}
        from .tools import _redact_secrets
        if entry and entry.get("editable"):
            text = await self._read_text(entry["file"]) or ""
            b = find_item(text, item_id=entry.get("id"), alias=entry.get("alias"))
            if b is not None:
                return {"success": True, "id": entry.get("id"), "file_path": entry["file"],
                        "lines": f"{b.start + 1}-{b.end}", "yaml": _redact_secrets(item_text(text, b)),
                        "entity_id": (state or {}).get("entity_id")}
        if state is not None:
            try:
                cfg = (await self._ws_call("automation/config", entity_id=state["entity_id"])).get("config")
            except Exception as e:
                return {"success": False, "error": f"Automation found but its config could not be read: {e}"}
            import yaml
            return {"success": True, "id": (cfg or {}).get("id"), "entity_id": state["entity_id"],
                    "file_path": entry["file"] if entry else None,
                    "yaml": _redact_secrets(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True)),
                    "note": ("Single-item file — edit with propose_config_changes on that file."
                             if entry and entry.get("single_item") else
                             "Lives in a package/inline block — edit with patch_config_block on that file."
                             if entry else "Not found in any automation file; read-only view from HA.")}
        return {"success": False, "error": f"No automation matches {automation!r}. Call list_automations."}

    def _stage(self, file_path: str, original: str, new_text: str, message: str,
               warnings: Optional[list] = None) -> Dict[str, Any]:
        changeset_id, diff_stats = self._store_changeset_with_diffs(str(uuid.uuid4())[:8], [(file_path, original, new_text)])
        result = {"success": True, "changeset_id": changeset_id, "files": [file_path], "total_files": 1,
                  "diff_stats": diff_stats, "reason": message, "message": message + " Awaiting user approval."}
        if warnings:
            result["entity_warnings"] = warnings
        return result

    def _target_allowed(self, layout: Dict[str, List[dict]], file_path: str) -> bool:
        for e in layout.get("automation", []):
            if e["format"] != "list":
                continue
            if e["path"] == file_path and not e.get("empty_dir"):
                return True
            d = e.get("dir")
            if d and file_path.startswith(d.rstrip("/") + "/") and file_path.endswith(".yaml") and ".." not in file_path:
                return True
        return False

    async def upsert_automation(self, automation_yaml: str, file_path: Optional[str] = None) -> Dict[str, Any]:
        """Add or replace ONE automation. Other automations in the file stay byte-identical."""
        try:
            block, item = normalize_item_text(automation_yaml or "", str(int(time.time() * 1000)))
        except Exception as e:
            return {"success": False, "error": f"Invalid automation YAML: {e}"}
        layout = self._layout()
        idx = await self._automation_index()
        existing = next((e for e in idx if e.get("id") is not None and str(e["id"]) == str(item["id"])), None)
        if existing and not existing.get("editable"):
            how = "propose_config_changes (single-item file)" if existing.get("single_item") else "patch_config_block (package/inline)"
            return {"success": False, "error": f"Automation {item['id']} lives in {existing['file']}. Edit it with {how}."}
        target = existing["file"] if existing else (file_path or default_target(layout, "automation"))
        if not target:
            return {"success": False, "error": "configuration.yaml has no automation include (e.g. 'automation: !include automations.yaml'). Use propose_config_changes to add one."}
        if not self._target_allowed(layout, target):
            allowed = [e["path"] for e in layout.get("automation", []) if e["format"] == "list"]
            return {"success": False, "error": f"{target} is not an automation list file. Use one of: {allowed}"}
        original = await self._read_text(target) or ""
        try:
            new_text, mode = upsert_item(original, block, str(item["id"]), item.get("alias"))
        except ValueError as e:
            return {"success": False, "error": str(e)}
        warnings = await self._validate_entity_ids_in_yaml(block)
        verb = "Replaced" if mode == "replaced" else "Added"
        return self._stage(target, original, new_text,
                           f"{verb} automation '{item.get('alias') or item['id']}' (id {item['id']}) in {target}.", warnings)

    async def remove_automation(self, automation: str) -> Dict[str, Any]:
        """Remove ONE automation (by id, entity_id, or alias) from its file."""
        try:
            _state, entry = await self._resolve_automation(automation)
        except Exception as e:
            return {"success": False, "error": str(e)}
        if not entry:
            return {"success": False, "error": f"No unique automation file entry matches {automation!r} "
                                               "(not found, or several id-less automations share that alias)."}
        if not entry.get("editable"):
            how = "propose_config_changes (single-item file)" if entry.get("single_item") else "patch_config_block (package/inline)"
            return {"success": False, "error": f"It lives in {entry['file']}. Use {how}."}
        original = await self._read_text(entry["file"]) or ""
        new_text = remove_item(original, item_id=entry.get("id"), alias=entry.get("alias"))
        if new_text is None:
            return {"success": False, "error": "Automation not found in file (it may have just changed)."}
        return self._stage(entry["file"], original, new_text,
                           f"Removed automation '{entry.get('alias')}' (id {entry.get('id')}) from {entry['file']}.")

    async def get_automation_traces(self, automation: str, run_id: Optional[str] = None) -> Dict[str, Any]:
        """Recent runs of an automation, plus step-by-step detail for one run (latest by default)."""
        try:
            state, entry = await self._resolve_automation(automation)
            item_id = (state or {}).get("attributes", {}).get("id") or (entry or {}).get("id")
            if not item_id:
                return {"success": False, "error": f"No automation with an id matches {automation!r}."}
            runs = await self._ws_call("trace/list", domain="automation", item_id=str(item_id)) or []
            if not runs:
                return {"success": True, "id": item_id, "runs": [], "message": "No stored traces (not triggered since restart, or tracing disabled)."}
            runs = sorted(runs, key=lambda r: (r.get("timestamp") or {}).get("start") or "", reverse=True)
            pick = run_id or runs[0]["run_id"]
            detail = await self._ws_call("trace/get", domain="automation", item_id=str(item_id), run_id=pick)
        except Exception as e:
            return {"success": False, "error": str(e)}
        brief = [{"run_id": r.get("run_id"), "start": _short_ts((r.get("timestamp") or {}).get("start")),
                  "trigger": r.get("trigger"), "state": r.get("state"),
                  "script_execution": r.get("script_execution"), "last_step": r.get("last_step"),
                  **({"error": r["error"]} if r.get("error") else {})} for r in runs[:10]]
        return {"success": True, "id": item_id, "runs": brief, "detail": summarize_trace(detail or {})}
