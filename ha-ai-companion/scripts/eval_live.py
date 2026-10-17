"""Live end-to-end eval: run real HA tasks against the installed add-on.

Connects through HA ingress to the add-on's /ws/chat, sends each task from
eval_tasks.yaml as a fresh anonymous chat (not saved as a session), and grades
tools used, errors, staged changesets, iterations, cost and latency.
Never approves a changeset, so HA config is not changed.

Usage (from ha-ai-companion/):
    set HA_URL=http://homeassistant.local:8123   (default)
    set HA_TOKEN=<long-lived token>
    python scripts/eval_live.py [--only id1,id2] [--json out.json]
"""
import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import yaml

ADDON_NAME_HINT = "ai companion"


def grade(task: Dict[str, Any], events: List[Dict[str, Any]], elapsed_s: float,
          first_event_s: float) -> Dict[str, Any]:
    """Grade one task from its event stream. Pure; unit-tested."""
    tools, tool_errors, full_reads, changesets = [], 0, 0, 0
    answer, usage, iterations, error = "", {}, 0, None
    for ev in events:
        kind, data = ev.get("event"), ev.get("data") or {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                data = {}
        if kind == "tool_start":
            tools.append(data.get("function"))
            if (data.get("arguments") or {}).get("full_content") is True:
                full_reads += 1
        elif kind == "tool_result":
            result = data.get("result") or {}
            if result.get("changeset_id"):
                changesets += 1
            elif result.get("success") is False and not result.get("blocked"):
                tool_errors += 1
        elif kind == "message_complete":
            answer = ((data.get("message") or {}).get("content") or answer)
        elif kind == "complete":
            usage = data.get("usage") or {}
            iterations = data.get("iterations") or 0
        elif kind == "error":
            error = data.get("error") or "error"

    fails = []
    if error:
        fails.append(f"error: {error[:120]}")
    if not any(e.get("event") == "complete" for e in events):
        fails.append("no complete event")
    expect = task.get("expect_tools") or []
    if expect and not set(expect) & set(tools):
        fails.append(f"none of {expect} called")
    if task.get("forbid_full_read") and full_reads:
        fails.append(f"{full_reads} full_content read(s)")
    if task.get("expect_changeset") and not changesets:
        fails.append("no changeset staged")
    words = [w.lower() for w in task.get("answer_any") or []]
    if words and not any(w in answer.lower() for w in words):
        fails.append(f"answer lacks any of {words}")
    if task.get("max_iterations") and iterations > task["max_iterations"]:
        fails.append(f"{iterations} iterations > {task['max_iterations']}")
    return {
        "id": task["id"], "passed": not fails, "fails": fails, "tools": tools,
        "tool_errors": tool_errors, "iterations": iterations,
        "cost_usd": usage.get("cost_usd"), "input_tokens": usage.get("input_tokens"),
        "seconds": round(elapsed_s, 1), "first_event_s": round(first_event_s, 1),
    }


class Ingress:
    """HA WebSocket for supervisor/api, then an ingress session cookie."""

    def __init__(self, session, base: str, token: str):
        self.s, self.base, self.token, self._id = session, base.rstrip("/"), token, 0

    async def _supervisor(self, ws, endpoint: str, method: str = "get") -> Any:
        self._id += 1
        await ws.send_json({"id": self._id, "type": "supervisor/api", "endpoint": endpoint, "method": method})
        while True:
            msg = await ws.receive_json()
            if msg.get("id") == self._id:
                if not msg.get("success"):
                    raise RuntimeError(f"{endpoint}: {msg.get('error')}")
                return msg.get("result")

    async def open(self):
        ws_url = self.base.replace("http", "ws", 1) + "/api/websocket"
        async with self.s.ws_connect(ws_url) as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": self.token})
            if (await ws.receive_json()).get("type") != "auth_ok":
                raise RuntimeError("HA auth failed - check HA_TOKEN")
            addons = (await self._supervisor(ws, "/addons")).get("addons", [])
            addon = next((a for a in addons if ADDON_NAME_HINT in a.get("name", "").lower()), None)
            if not addon:
                raise RuntimeError("AI Companion add-on not found")
            info = await self._supervisor(ws, f"/addons/{addon['slug']}/info")
            session = (await self._supervisor(ws, "/ingress/session", "post"))["session"]
        return info, session


async def run_task(http, chat_url: str, cookie: str, task: Dict[str, Any], timeout_s: float) -> Dict[str, Any]:
    events, start, first = [], time.monotonic(), None
    async with http.ws_connect(chat_url, headers={"Cookie": f"ingress_session={cookie}"},
                               heartbeat=30) as ws:
        await ws.send_json({"type": "chat", "message": task["prompt"], "conversation_history": []})
        wait = timeout_s
        try:
            while True:
                msg = await asyncio.wait_for(ws.receive_json(), wait)
                first = first if first is not None else time.monotonic() - start
                events.append(msg)
                if msg.get("event") == "complete":
                    break
                if msg.get("event") == "error":
                    wait = 5  # max-iteration errors are followed by complete
        except asyncio.TimeoutError:
            if wait == timeout_s:
                events.append({"event": "error", "data": {"error": f"no event for {timeout_s}s"}})
    return grade(task, events, time.monotonic() - start, first or 0.0)


async def main() -> int:
    import aiohttp

    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated task ids")
    ap.add_argument("--json", help="write results to this file")
    ap.add_argument("--timeout", type=float, default=180, help="max seconds between events")
    args = ap.parse_args()

    token = os.getenv("HA_TOKEN")
    if not token:
        print("Set HA_TOKEN to a long-lived access token.")
        return 2
    base = os.getenv("HA_URL", "http://homeassistant.local:8123")
    tasks = yaml.safe_load((Path(__file__).parent / "eval_tasks.yaml").read_text(encoding="utf-8"))
    if args.only:
        wanted = set(args.only.split(","))
        tasks = [t for t in tasks if t["id"] in wanted]

    async with aiohttp.ClientSession() as http:
        info, cookie = await Ingress(http, base, token).open()
        chat_url = base.replace("http", "ws", 1) + info["ingress_url"].rstrip("/") + "/ws/chat"
        print(f"Add-on {info.get('name')} {info.get('version')} - {len(tasks)} task(s)\n")
        results = []
        for task in tasks:
            r = await run_task(http, chat_url, cookie, task, args.timeout)
            results.append(r)
            cost = f"${r['cost_usd']:.4f}" if r["cost_usd"] is not None else "$?"
            print(f"{'PASS' if r['passed'] else 'FAIL'} {r['id']:<22} {r['seconds']:>6}s it={r['iterations']} "
                  f"tools={len(r['tools'])} err={r['tool_errors']} {cost}"
                  + ("" if r["passed"] else f"  <- {'; '.join(r['fails'])}"))

    passed = sum(r["passed"] for r in results)
    cost = sum(r["cost_usd"] or 0 for r in results)
    print(f"\n{passed}/{len(results)} passed, total ${cost:.4f}, "
          f"{sum(r['seconds'] for r in results):.0f}s")
    if args.json:
        Path(args.json).write_text(json.dumps({"addon_version": info.get("version"), "results": results},
                                              indent=2), encoding="utf-8")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
