"""History summarization must shrink history even when the LLM returns nothing."""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.agents.agent_system import AgentSystem


def _fake(content):
    async def completion(*a, **k):
        if isinstance(content, Exception):
            raise content
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    return SimpleNamespace(_completion_with_usage=completion, suggestion_client=None, suggestion_model="m")


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
