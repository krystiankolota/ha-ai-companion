"""
Run registry — decouples agent execution from WebSocket connections.

A "run" is one agent chat_stream execution. The run lives in an asyncio task
owned by the registry, NOT by the WebSocket handler: a client disconnect
(tab switch, HA panel change, mobile app background) only detaches the
subscriber queue — the agent keeps executing, events keep buffering, and a
reconnecting client resumes via sequence-numbered replay.

Concurrency: all registry state is guarded by one asyncio.Lock. Subscribe
atomically snapshots the replay slice and registers the live queue so no
event can fall in the gap.
"""
import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Terminal event names — a pump can close after relaying one of these.
TERMINAL_EVENTS = ("complete", "error")


class RunRegistry:
    """Tracks active and recently-finished agent runs keyed by session id."""

    MAX_EVENTS = 5000          # buffer cap; beyond this replay is degraded
    TTL_SECONDS = 600          # keep finished runs for 10 min for late resume

    def __init__(self):
        self._runs: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        key: str,
        stream_factory: Callable[[], Any],
        on_finish: Optional[Callable[[str, List[Dict]], Any]] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Start a new run. stream_factory() must return an async generator of
        {"event": str, "data": dict} messages. on_finish(status, events) is
        awaited after the stream ends (any status).

        Returns (ok, error). Rejects when a run is already active for key.
        """
        async with self._lock:
            self._evict_expired_locked()
            existing = self._runs.get(key)
            if existing and existing["status"] == "running":
                return False, "A run is already active for this session"

            run = {
                "events": [],
                "next_seq": 0,
                "subscribers": [],
                "status": "running",
                "completed_at": None,
                "degraded": False,
                "task": None,
            }
            self._runs[key] = run
            run["task"] = asyncio.create_task(
                self._consume(key, run, stream_factory, on_finish)
            )
            return True, None

    async def _consume(self, key, run, stream_factory, on_finish):
        status = "done"
        try:
            async for message in stream_factory():
                await self._publish(run, message)
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except Exception as e:
            status = "error"
            logger.error(f"Run {key} failed: {e}", exc_info=True)
            await self._publish(run, {"event": "error", "data": {"error": str(e)}})
        finally:
            async with self._lock:
                run["status"] = status
                run["completed_at"] = time.monotonic()
                for q in run["subscribers"]:
                    q.put_nowait(None)  # sentinel: stream over
                run["subscribers"] = []
            if on_finish:
                try:
                    await on_finish(status, run["events"])
                except Exception as e:
                    logger.error(f"Run {key} on_finish failed: {e}", exc_info=True)

    async def _publish(self, run, message):
        async with self._lock:
            message = dict(message)
            # Dedicated counter: must stay monotonic even after the buffer cap,
            # or the frontend seq-dedupe would drop every live event past it.
            message["seq"] = run["next_seq"]
            run["next_seq"] += 1
            if len(run["events"]) < self.MAX_EVENTS:
                run["events"].append(message)
            else:
                run["degraded"] = True
            for q in run["subscribers"]:
                q.put_nowait(message)

    async def subscribe(
        self, key: str, last_seq: int = -1
    ) -> Tuple[Optional[List[Dict]], Optional[asyncio.Queue], Optional[str]]:
        """
        Attach to a run. Returns (replay_events, live_queue, error).

        - Unknown/expired run: (None, None, "not_found")
        - Degraded buffer with missed events: (None, None, "buffer_overflow")
        - Finished run: (replay, None, None) — no live queue needed
        - Active run: (replay, queue, None)
        """
        async with self._lock:
            self._evict_expired_locked()
            run = self._runs.get(key)
            if not run:
                return None, None, "not_found"
            if run["degraded"] and last_seq + 1 < len(run["events"]):
                # events were dropped past the cap; can't guarantee gapless replay
                return None, None, "buffer_overflow"

            replay = run["events"][last_seq + 1:]
            if run["status"] != "running":
                return replay, None, None
            queue: asyncio.Queue = asyncio.Queue()
            run["subscribers"].append(queue)
            return replay, queue, None

    async def unsubscribe(self, key: str, queue: asyncio.Queue):
        async with self._lock:
            run = self._runs.get(key)
            if run and queue in run["subscribers"]:
                run["subscribers"].remove(queue)

    async def is_running(self, key: str) -> bool:
        async with self._lock:
            run = self._runs.get(key)
            return bool(run and run["status"] == "running")

    def _evict_expired_locked(self):
        now = time.monotonic()
        expired = [
            k for k, r in self._runs.items()
            if r["status"] != "running"
            and r["completed_at"] is not None
            and now - r["completed_at"] > self.TTL_SECONDS
        ]
        for k in expired:
            del self._runs[k]
