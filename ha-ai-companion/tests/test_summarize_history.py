"""History summarization must shrink history even when the LLM returns nothing."""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.agents.agent_system import AgentSystem


def _fake(content):
    calls = []

    async def completion(*a, **k):
        calls.append(k["messages"][1]["content"])
        if isinstance(content, Exception):
            raise content
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    ns = SimpleNamespace(_completion_with_usage=completion, suggestion_client=None,
                         suggestion_model="m", _summary_cache={}, calls=calls)
    ns._summarize_messages = lambda *a: AgentSystem._summarize_messages(ns, *a)
    return ns


def _history(n):
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(n):
        msgs.append({"role": "user", "content": f"ask {i}\nmore"})
        msgs.append({"role": "assistant", "content": f"answer {i}"})
    return msgs


def test_empty_llm_summary_uses_extractive_fallback():
    out = asyncio.run(AgentSystem._summarize_old_history(_fake(None), _history(10)))
    assert len(out) == 12 and out[1]["role"] == "system"
    assert "- ask 0" in out[1]["content"] and "more" not in out[1]["content"]


def test_failed_llm_summary_uses_extractive_fallback():
    out = asyncio.run(AgentSystem._summarize_old_history(_fake(RuntimeError("x")), _history(10)))
    assert len(out) == 12 and "- ask 4" in out[1]["content"]


def test_llm_summary_used_when_present():
    out = asyncio.run(AgentSystem._summarize_old_history(_fake("- did things"), _history(10)))
    assert out[1]["content"].endswith("- did things")


def _run(fake, n, sid="s1"):
    return asyncio.run(AgentSystem._summarize_old_history(fake, _history(n), session_id=sid))


def test_summary_reused_until_step_boundary():
    fake = _fake("- sum")
    first = _run(fake, 10)
    # 2 more messages: split point unchanged -> same summary, no new LLM call, stable prefix.
    second = _run(fake, 11)
    assert len(fake.calls) == 1
    assert first[1] == second[1] and len(second) == 14


def test_summary_folds_only_new_messages_when_split_moves():
    fake = _fake("- sum")
    _run(fake, 10)
    out = _run(fake, 15)
    assert len(fake.calls) == 2 and len(out) == 12
    assert "PREVIOUS SUMMARY" in fake.calls[1] and "ask 0" not in fake.calls[1]


def test_short_history_untouched():
    fake = _fake("- sum")
    msgs = _history(9)
    assert asyncio.run(AgentSystem._summarize_old_history(fake, msgs)) == msgs and not fake.calls


def test_sliding_history_rebuilds_summary():
    # Frontend caps history: same length, window shifted -> cached summary is stale.
    fake = _fake("- sum")
    _run(fake, 10)
    shifted = [_history(11)[0]] + _history(11)[3:]
    out = asyncio.run(AgentSystem._summarize_old_history(fake, shifted, session_id="s1"))
    assert len(fake.calls) == 2 and "PREVIOUS SUMMARY" not in fake.calls[1]
    assert out[1]["content"].endswith("- sum")
