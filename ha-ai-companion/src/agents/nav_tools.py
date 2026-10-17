"""Navigation + automation tools: read only what is needed, edit one item at a time.

Mixed into AgentTools. Relies on: self.config_manager, self._store_changeset_with_diffs,
self._validate_entity_ids_in_yaml, self._get_all_entities/_get_all_devices/_get_all_areas.
"""
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..ha.layout import default_target, detect_layout
from ..ha.yaml_blocks import (
    find_item, item_text, load_tolerant, dump_tolerant, normalize_item_text, outline, read_range, remove_item, split_blocks,
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
    """Compact view of a trace/get result without variable dumps."""
    steps = []
    for path, entries in (trace.get("trace") or {}).items():
        for e in entries or []:
            step = {"path": str(path)[:160]}
            res = e.get("result")
            if isinstance(res, dict):
                brief = {k: v for k, v in res.items()
                         if k in ("result", "enabled", "done", "conditions", "wait", "choice", "running_script")}
                if brief:
                    step["result"] = brief
            if e.get("error"):
                step["error"] = str(e["error"])[:300]
            steps.append(step)
    out = {
        "run_id": trace.get("run_id"),
        "start": trace.get("timestamp", {}).get("start"),
        "trigger": str(trace.get("trigger") or "")[:300],
        "state": trace.get("state"),
        "script_execution": trace.get("script_execution"),
        "last_step": trace.get("last_step"),
        "steps": steps,
    }
    if trace.get("error"):
        out["error"] = str(trace["error"])[:300]
    import json
    while len(json.dumps(out, ensure_ascii=False, default=str)) > 3000 and steps:
        steps.pop()
    return out


def format_ha_health(repairs, config_entries, states, logs, include_ignored=False) -> Dict[str, Any]:
    """Build a small health report from HA WebSocket results."""
    issues = [i for i in (repairs or []) if include_ignored or not i.get("ignored")]
    failed = [e for e in (config_entries or [])
              if str(e.get("state", "")).lower() not in ("loaded", "not_loaded", "")]
    updates = []
    for state in states or []:
        if not state.get("entity_id", "").startswith("update.") or state.get("state") != "on":
            continue
        attrs = state.get("attributes") or {}
        updates.append({"name": str(attrs.get("friendly_name") or state["entity_id"])[:180],
                        "installed": str(attrs.get("installed_version") or "")[:80],
                        "latest": str(attrs.get("latest_version") or "")[:80]})
    grouped = {}
    for entry in logs or []:
        level = str(entry.get("level") or "ERROR").upper()
        source = entry.get("name") or entry.get("source") or "unknown"
        messages = entry.get("message") or []
        if isinstance(messages, str):
            messages = messages.splitlines()
        first = str(messages[0] if messages else entry.get("message", ""))[:220]
        key = (level, source, first)
        grouped[key] = grouped.get(key, 0) + int(entry.get("count") or 1)
    top_logs = [{"level": level, "source": source, "count": count, "message": message}
                for (level, source, message), count in sorted(
                    grouped.items(), key=lambda row: (-row[1], row[0][0], row[0][1]))[:10]]
    report = {"repairs": [
                  {key: (str(issue[key])[:240] if isinstance(issue.get(key), str) else issue.get(key))
                   for key in ("issue_id", "domain", "title", "severity", "is_fixable")}
                  for issue in issues[:30]],
              "failed_config_entries": [{"domain": str(e.get("domain", ""))[:120],
                                         "title": str(e.get("title", ""))[:240],
                                         "state": e.get("state"),
                                         "reason": str(e.get("reason", ""))[:240]}
                                        for e in failed[:30]],
              "pending_updates": updates[:30], "logs": top_logs}
    import json
    while len(json.dumps(report, ensure_ascii=False, default=str)) > 4000 and top_logs:
        top_logs.pop()
    return report


def _age_hours(iso: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    if not iso:
        return None
    try:
        changed = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if changed.tzinfo is None:
            changed = changed.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return max(0.0, (current - changed).total_seconds() / 3600)
    except (TypeError, ValueError):
        return None


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

    async def _read_for_write(self, file_path: str, allow_missing: bool = True) -> Optional[str]:
        """Base content for a new edit: the pending (unapproved) version if one exists, else disk.

        Building on the pending version lets the store merge both edits into one changeset,
        so the user never gets two cards for one file where approving one stales the other.
        """
        pending_file = getattr(self.agent_system, "pending_file", None)
        pending = pending_file(file_path) if pending_file else None
        if pending:
            return pending[1]["new_content"]
        if os.path.basename(file_path) == "secrets.yaml":
            raise PermissionError("secrets.yaml is not readable")
        return await self.config_manager.read_file_raw(file_path, allow_missing=allow_missing)

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

    async def get_ha_health(self, include_ignored: bool = False) -> Dict[str, Any]:
        """Summarize repairs, failed config entries, pending updates, and top logs."""
        try:
            repairs = await self._ws_call("repairs/list_issues")
            entries = await self._ws_call("config_entries/get")
            states = await self._all_states()
            logs = await self._ws_call("system_log/list")
            report = format_ha_health(
                repairs.get("issues", []) if isinstance(repairs, dict) else repairs,
                entries.get("config_entries", []) if isinstance(entries, dict) else entries,
                states, logs, include_ignored,
            )
            report["success"] = True
            import json
            while len(json.dumps(report, ensure_ascii=False, default=str)) > 4000:
                if report["logs"]:
                    report["logs"].pop()
                elif report["pending_updates"]:
                    report["pending_updates"].pop()
                elif report["failed_config_entries"]:
                    report["failed_config_entries"].pop()
                elif report["repairs"]:
                    report["repairs"].pop()
                else:
                    break
            return report
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_automation_trace(self, automation: str, run_index: int = 0) -> Dict[str, Any]:
        """Return one compact trace; run_index 0 is the most recent run."""
        try:
            state, entry = await self._resolve_automation(automation)
            item_id = ((state or {}).get("attributes") or {}).get("id") or (entry or {}).get("id") or automation
            runs = await self._ws_call("trace/list", domain="automation", item_id=str(item_id)) or []
            runs = sorted(runs, key=lambda r: (r.get("timestamp") or {}).get("start") or "", reverse=True)
            if not runs:
                return {"success": True, "id": item_id, "message": "No stored traces."}
            index = int(run_index or 0)
            if index < 0 or index >= len(runs):
                return {"success": False, "error": f"run_index must be from 0 to {len(runs) - 1}."}
            run = runs[index]
            trace = await self._ws_call("trace/get", domain="automation", item_id=str(item_id),
                                        run_id=run["run_id"])
            result = summarize_trace(trace or {})
            result["success"] = True
            import json
            while len(json.dumps(result, ensure_ascii=False, default=str)) > 3000 and result.get("steps"):
                result["steps"].pop()
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def find_unavailable_entities(self, min_age_hours: float = 24) -> Dict[str, Any]:
        """List old unavailable/unknown entities grouped by integration."""
        try:
            states = await self._all_states()
            registry = {e.get("entity_id"): e for e in await self._get_all_entities()}
            rows = {}
            for state in states:
                if state.get("state") not in ("unavailable", "unknown"):
                    continue
                age = _age_hours(state.get("last_changed"))
                if age is None or age < max(0, float(min_age_hours)):
                    continue
                eid = state.get("entity_id", "")
                integration = (registry.get(eid) or {}).get("platform") or eid.split(".", 1)[0]
                rows.setdefault(integration, []).append({
                    "entity_id": eid,
                    "name": (state.get("attributes") or {}).get("friendly_name") or eid,
                    "state": state.get("state"),
                    "age_hours": round(age, 1),
                })
            for values in rows.values():
                values.sort(key=lambda row: -row["age_hours"])
            total = sum(map(len, rows.values()))
            while sum(len(str(k)) + len(str(v)) for k, v in rows.items()) > 3800:
                largest = max(rows, key=lambda key: len(rows[key]), default=None)
                if not largest:
                    break
                rows[largest].pop()
                if not rows[largest]:
                    del rows[largest]
            return {"success": True, "count": total, "min_age_hours": min_age_hours,
                    "by_integration": rows}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def find_cleanup_candidates(self) -> Dict[str, Any]:
        """Report possible stale automation/script entities, helpers, and mobile notify services."""
        try:
            states = await self._all_states()
            entity_registry = await self._get_all_entities()
            layout = self._layout()
            auto_items = await self._automation_index()
            script_ids = set()
            reference_text = []
            for kind in ("automation", "script"):
                for entry in layout.get(kind, []):
                    if entry.get("empty_dir"):
                        continue
                    text = await self._read_text(entry["path"]) or ""
                    reference_text.append(text)
                    if kind == "script":
                        fmt, blocks = split_blocks(text)
                        for block in blocks:
                            ident = block.key if fmt == "mapping" else (
                                block.value.get("id") if isinstance(block.value, dict) else None)
                            if ident is not None:
                                script_ids.add(str(ident))

            flows = []
            try:
                flow_result = await self.get_nodered_flows()
                if flow_result.get("success"):
                    flows = flow_result.get("flows", [])
            except Exception:
                pass
            reference_text.extend([str(flows)])
            references = "\n".join(reference_text)
            auto_ids = {str(e["id"]) for e in auto_items if e.get("id") is not None}
            stale_items = []
            states_by_id = {state.get("entity_id"): state for state in states}
            registry_entities = [e for e in entity_registry
                                 if e.get("entity_id", "").startswith(("automation.", "script."))]
            for entity in registry_entities:
                entity_id = entity["entity_id"]
                domain = entity_id.split(".", 1)[0]
                if domain not in ("automation", "script"):
                    continue
                attrs = (states_by_id.get(entity_id) or {}).get("attributes") or {}
                item_id = str(attrs.get("id") or entity_id.split(".", 1)[-1])
                present = item_id in (auto_ids if domain == "automation" else script_ids)
                if present:
                    continue
                command = "automation/config" if domain == "automation" else "script/config"
                try:
                    await self._ws_call(command, entity_id=entity_id)
                except Exception as e:
                    if "404" in str(e).lower() or "not found" in str(e).lower():
                        stale_items.append({"entity_id": entity_id, "id": item_id, "reason": "config not found"})

            helpers = []
            for state in states:
                eid = state.get("entity_id", "")
                if eid.startswith(("input_boolean.", "input_number.", "input_select.", "input_text.", "input_datetime.", "input_button.")) \
                        and eid not in references:
                    helpers.append(eid)

            recent_mobile = set()
            registry_by_id = {e.get("entity_id"): e for e in entity_registry}
            for state in states:
                if not state.get("entity_id", "").startswith("device_tracker."):
                    continue
                platform = (registry_by_id.get(state["entity_id"]) or {}).get("platform", "")
                if platform == "mobile_app" and (_age_hours(state.get("last_updated")) or 0) < 30 * 24:
                    recent_mobile.add(str((state.get("attributes") or {}).get("device_tracker") or
                                          state["entity_id"].split(".", 1)[-1]))
            services = await self._ws_call("get_services")
            notify = services.get("notify", []) if isinstance(services, dict) else []
            notify_services = (list(notify) if isinstance(notify, dict) else
                               [s.get("service") for s in notify if isinstance(s, dict)])
            notify_services = [name for name in notify_services
                               if isinstance(name, str) and name.startswith("mobile_app_")]
            old_notify = [name for name in notify_services
                          if not any(name.removeprefix("mobile_app_") in recent or
                                     recent in name.removeprefix("mobile_app_")
                                     for recent in recent_mobile)]
            return {"success": True, "report_only": True,
                    "stale_automation_script_entities": stale_items[:50],
                    "unreferenced_helpers": helpers[:100],
                    "mobile_app_notify_without_recent_device": old_notify[:50]}
        except Exception as e:
            return {"success": False, "error": str(e)}

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

    async def get_automation(self, automation: str, kind: str = "automation") -> Dict[str, Any]:
        """Exact YAML of one automation (by id, entity_id, or alias) and where it lives."""
        if kind in ("script", "scene"):
            return await self._get_script_scene_item(kind, automation)
        if kind != "automation":
            return {"success": False, "error": "kind must be automation, script, or scene."}
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

    async def _get_script_scene_item(self, kind: str, ref: str) -> Dict[str, Any]:
        """Read one script (mapping key) or scene (list item) from its config file."""
        from .tools import _redact_secrets
        from ..ha.yaml_blocks import split_blocks

        for entry in self._layout().get(kind, []):
            if entry.get("empty_dir"):
                continue
            text = await self._read_text(entry["path"]) or ""
            fmt, blocks = split_blocks(text)
            for block in blocks:
                value = block.value if isinstance(block.value, dict) else {}
                mapped = value.get(block.key) if fmt == "mapping" and block.key else value
                mapped = mapped if isinstance(mapped, dict) else {}
                ident = block.key if fmt == "mapping" else value.get("id")
                alias = mapped.get("alias") or mapped.get("name")
                if str(ident or "") == ref or alias == ref:
                    return {"success": True, "id": ident, "kind": kind, "file_path": entry["path"],
                            "lines": f"{block.start + 1}-{block.end}",
                            "yaml": _redact_secrets(item_text(text, block))}
        return {"success": False, "error": f"No {kind} matches {ref!r}. Use get_automation(kind='{kind}') with its id or alias."}

    def _target_item_file(self, layout: Dict[str, List[dict]], kind: str, file_path: Optional[str]) -> Optional[dict]:
        """Choose a writable single list/mapping file for a script or scene."""
        candidates = [e for e in layout.get(kind, [])
                      if not e.get("empty_dir") and e.get("format") in ("list", "mapping")]
        if file_path:
            return next((e for e in candidates if e["path"] == file_path), None)
        preferred = default_target(layout, kind)
        if preferred:
            return next((e for e in candidates if e["path"] == preferred), None)
        return next(iter(candidates), None)

    async def _upsert_script_scene_item(self, kind: str, item_yaml: str,
                                        file_path: Optional[str] = None) -> Dict[str, Any]:
        """Stage one script/scene item while leaving other file blocks untouched."""
        import yaml
        from ..ha.yaml_blocks import split_blocks

        try:
            parsed = load_tolerant(item_yaml or "")
            if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], dict):
                parsed = parsed[0]
            if not isinstance(parsed, dict):
                raise ValueError("Provide one YAML mapping.")
            if kind == "script" and len(parsed) == 1:
                only_key, only_value = next(iter(parsed.items()))
                if isinstance(only_value, dict) and "id" not in only_value:
                    item_id, item = str(only_key), only_value
                else:
                    item_id, item = str(parsed.get("id") or ""), parsed
            else:
                item_id, item = str(parsed.get("id") or ""), parsed
            if not item_id:
                raise ValueError("Include the item id in the YAML (scripts use their mapping key).")
            item.pop("id", None)
            alias = item.get("alias") or item.get("name")
        except Exception as e:
            return {"success": False, "error": f"Invalid {kind} YAML: {e}"}

        layout = self._layout()
        existing = None
        for entry in layout.get(kind, []):
            if entry.get("empty_dir"):
                continue
            text = await self._read_text(entry["path"]) or ""
            fmt, blocks = split_blocks(text)
            for block in blocks:
                ident = block.key if fmt == "mapping" else (
                    block.value.get("id") if isinstance(block.value, dict) else None)
                if str(ident or "") == item_id:
                    existing = entry
                    break
            if existing:
                break

        target = existing or self._target_item_file(layout, kind, file_path)
        if not target:
            return {"success": False, "error": f"No writable {kind} list/mapping file was found."}
        if file_path and target["path"] != file_path:
            return {"success": False, "error": f"{file_path} is not a writable {kind} item file."}
        original = await self._read_for_write(target["path"]) or ""
        fmt, blocks = split_blocks(original)
        nl = "\r\n" if "\r\n" in original else "\n"
        if kind == "script":
            if fmt != "mapping":
                return {"success": False, "error": "Script files must be YAML mappings keyed by script id."}
            new_block = dump_tolerant({item_id: item})
            if nl == "\r\n":
                new_block = new_block.replace("\n", "\r\n")
            current = next((b for b in blocks if b.key == item_id), None)
            if current:
                lines = original.splitlines(keepends=True)
                new_text = "".join(lines[:current.start]) + new_block + "".join(lines[current.end:])
            else:
                try:
                    empty_mapping = not load_tolerant(original or "{}")
                except Exception:
                    empty_mapping = False
                prefix = re.sub(r"^\s*\{\s*\}\s*$", "", original, flags=re.M) if empty_mapping else original
                prefix = prefix if prefix.strip() else ""
                if prefix and not prefix.endswith(("\n", "\r")):
                    prefix += nl
                new_text = prefix + new_block
            try:
                load_tolerant(new_text)
            except Exception as e:
                return {"success": False, "error": f"Resulting {target['path']} would be invalid YAML: {e}"}
        else:
            if fmt not in ("list", "other"):
                return {"success": False, "error": "Scene files must be YAML lists of items."}
            try:
                block, _ = normalize_item_text(item_yaml, item_id)
                new_text, mode = upsert_item(original, block, item_id, alias)
            except Exception as e:
                return {"success": False, "error": f"Invalid scene YAML: {e}"}
            return self._stage(target["path"], original, new_text,
                               f"{'Replaced' if mode == 'replaced' else 'Added'} scene '{alias or item_id}' (id {item_id}) in {target['path']}.")
        replaced = any(b.key == item_id for b in blocks) if kind == "script" else False
        return self._stage(target["path"], original, new_text,
                           f"{'Replaced' if replaced else 'Added'} script '{alias or item_id}' (id {item_id}) in {target['path']}.")

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

    async def upsert_automation(self, automation_yaml: str, file_path: Optional[str] = None,
                                kind: str = "automation") -> Dict[str, Any]:
        """Add or replace ONE automation. Other automations in the file stay byte-identical."""
        if kind in ("script", "scene"):
            return await self._upsert_script_scene_item(kind, automation_yaml, file_path)
        if kind != "automation":
            return {"success": False, "error": "kind must be automation, script, or scene."}
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
        original = await self._read_for_write(target) or ""
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
        original = await self._read_for_write(entry["file"]) or ""
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
