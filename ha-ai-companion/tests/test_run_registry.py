"""
Tests for RunRegistry — agent execution decoupled from WebSocket connections.

Covers:
- events buffered with increasing seq
- mid-run subscribe gets gapless replay + live events
- subscriber loss doesn't cancel the run; on_finish receives all events
- concurrent run on same key rejected; new run allowed after finish
- TTL eviction of finished runs
- buffer cap -> degraded replay refused, live forwarding still works
- stream exception -> error event published, status=error
"""
import asyncio
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.runs import RunRegistry


def make_stream(events, gate: asyncio.Event = None):
    """Async generator factory yielding given events; optionally waits on gate."""
    async def factory():
        for i, e in enumerate(events):
            if gate is not None and i == len(events) // 2:
                await gate.wait()
            yield dict(e)
            await asyncio.sleep(0)
    return factory


def msg(name, **data):
    return {"event": name, "data": data}


@pytest.mark.asyncio
async def test_events_buffered_with_increasing_seq():
    reg = RunRegistry()
    finished = {}

    async def on_finish(status, events):
        finished["status"] = status
        finished["events"] = events

    ok, err = await reg.start("s1", make_stream([msg("token", content="a"), msg("complete")]), on_finish)
    assert ok and err is None

    replay, queue, sub_err = await reg.subscribe("s1", -1)
    assert sub_err is None
    received = list(replay)
    while queue is not None:
        item = await asyncio.wait_for(queue.get(), timeout=2)
        if item is None:
            break
        received.append(item)

    assert [m["seq"] for m in received] == list(range(len(received)))
    assert received[-1]["event"] == "complete"
    await asyncio.sleep(0.05)
    assert finished["status"] == "done"
    assert len(finished["events"]) == 2


@pytest.mark.asyncio
async def test_mid_run_subscribe_replays_without_gaps():
    reg = RunRegistry()
    gate = asyncio.Event()
    events = [msg("token", content=str(i)) for i in range(6)] + [msg("complete")]
    ok, _ = await reg.start("s1", make_stream(events, gate))
    assert ok
    await asyncio.sleep(0.05)  # first half flows into the buffer

    replay, queue, err = await reg.subscribe("s1", -1)
    assert err is None and queue is not None
    gate.set()

    received = list(replay)
    while True:
        item = await asyncio.wait_for(queue.get(), timeout=2)
        if item is None:
            break
        received.append(item)

    seqs = [m["seq"] for m in received]
    assert seqs == list(range(7)), f"gapless replay expected, got {seqs}"


@pytest.mark.asyncio
async def test_run_survives_subscriber_loss():
    reg = RunRegistry()
    gate = asyncio.Event()
    finished = {}

    async def on_finish(status, events):
        finished["status"] = status

    events = [msg("token", content=str(i)) for i in range(4)] + [msg("complete")]
    await reg.start("s1", make_stream(events, gate), on_finish)

    replay, queue, _ = await reg.subscribe("s1", -1)
    await reg.unsubscribe("s1", queue)  # client gone (tab switch)
    gate.set()
    await asyncio.sleep(0.1)

    assert finished.get("status") == "done"
    # late resume still sees everything
    replay, queue, err = await reg.subscribe("s1", -1)
    assert err is None and queue is None  # finished run: replay only
    assert len(replay) == 5


@pytest.mark.asyncio
async def test_concurrent_run_rejected_then_allowed_after_finish():
    reg = RunRegistry()
    gate = asyncio.Event()
    await reg.start("s1", make_stream([msg("token"), msg("complete")], gate))

    ok, err = await reg.start("s1", make_stream([msg("complete")]))
    assert not ok and "already active" in err

    gate.set()
    await asyncio.sleep(0.05)
    ok, err = await reg.start("s1", make_stream([msg("complete")]))
    assert ok


@pytest.mark.asyncio
async def test_ttl_eviction():
    reg = RunRegistry()
    reg.TTL_SECONDS = 0  # everything finished is instantly stale
    await reg.start("s1", make_stream([msg("complete")]))
    await asyncio.sleep(0.05)

    replay, queue, err = await reg.subscribe("s1", -1)
    assert err == "not_found"


@pytest.mark.asyncio
async def test_buffer_cap_degrades_replay_but_not_live():
    reg = RunRegistry()
    reg.MAX_EVENTS = 3
    gate = asyncio.Event()
    events = [msg("token", content=str(i)) for i in range(5)] + [msg("complete")]
    await reg.start("s1", make_stream(events, gate))
    await asyncio.sleep(0.05)

    # live subscriber attached before overflow keeps receiving
    replay, queue, err = await reg.subscribe("s1", -1)
    assert err is None
    gate.set()
    live = list(replay)
    while True:
        item = await asyncio.wait_for(queue.get(), timeout=2)
        if item is None:
            break
        live.append(item)
    assert live[-1]["event"] == "complete"
    assert len(live) == 6  # everything arrived live despite buffer cap
    # seq must stay monotonic past the cap or client-side dedupe drops live events
    assert [m["seq"] for m in live] == list(range(6))

    # but a fresh resume from scratch is refused (gapless replay impossible)
    replay, queue, err = await reg.subscribe("s1", -1)
    assert err == "buffer_overflow"


@pytest.mark.asyncio
async def test_stream_exception_publishes_error_event():
    reg = RunRegistry()
    finished = {}

    async def on_finish(status, events):
        finished["status"] = status

    async def broken():
        yield msg("token", content="a")
        raise RuntimeError("provider exploded")

    await reg.start("s1", lambda: broken(), on_finish)
    await asyncio.sleep(0.1)

    replay, queue, err = await reg.subscribe("s1", -1)
    assert err is None and queue is None
    assert replay[-1]["event"] == "error"
    assert "provider exploded" in replay[-1]["data"]["error"]
    assert finished["status"] == "error"
