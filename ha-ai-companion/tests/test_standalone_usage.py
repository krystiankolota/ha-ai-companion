"""
Tests for AgentSystem usage recording on standalone (non-streaming) LLM calls.

Summarization, memory consolidation, scheduled tasks and clear-all extraction all
route through `_completion_with_usage`, which must record token/cost so they show
up in the Usage tab instead of silently burning tokens at $0.
"""
import types
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.agents.agent_system import AgentSystem
from src.usage.manager import UsageManager


# --- Fakes -----------------------------------------------------------------
class FakeUsage:
    def __init__(self, prompt, completion, cost, cached=0):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.cost = cost
        self.prompt_tokens_details = types.SimpleNamespace(cached_tokens=cached)


class FakeResponse:
    def __init__(self, usage):
        self.usage = usage
        self.choices = []


class FakeCompletions:
    def __init__(self, response, fail_first_with=None):
        self._response = response
        self._fail = fail_first_with
        self.calls = []

    async def create(self, **params):
        self.calls.append(params)
        if self._fail and len(self.calls) == 1:
            raise Exception(self._fail)
        return self._response


class FakeClient:
    def __init__(self, completions, base_url="https://openrouter.ai/api/v1"):
        self.chat = types.SimpleNamespace(completions=completions)
        self.base_url = base_url


def make_self(um):
    """Minimal stand-in carrying only the attributes the helpers touch."""
    s = types.SimpleNamespace(
        usage_manager=um,
        config_usage_tracking="stream_options",
        suggestion_usage_tracking="stream_options",
        _client_usage_ok={"config": None, "suggestion": None},
    )
    s._record_response_usage = types.MethodType(AgentSystem._record_response_usage, s)
    return s


@pytest.fixture
def um(tmp_path):
    return UsageManager(usage_dir=str(tmp_path / "usage"))


# --- Tests -----------------------------------------------------------------
class TestCompletionWithUsage:
    async def test_records_tokens_and_cost(self, um):
        resp = FakeResponse(FakeUsage(prompt=120, completion=30, cost=0.004, cached=10))
        comp = FakeCompletions(resp)
        client = FakeClient(comp)
        s = make_self(um)

        out = await AgentSystem._completion_with_usage(
            s, client, 'suggestion', phase="summarization", model="kimi",
            messages=[], stream=False,
        )

        assert out is resp
        # OpenRouter cost request was attached
        assert comp.calls[0].get("extra_body") == {"usage": {"include": True}}
        agg = um.aggregate(days=30)
        assert agg["by_phase"]["summarization"]["calls"] == 1
        assert agg["by_phase"]["summarization"]["input_tokens"] == 120
        assert agg["by_phase"]["summarization"]["output_tokens"] == 30
        assert agg["by_phase"]["summarization"]["cached_tokens"] == 10
        assert round(agg["totals"]["cost_usd"], 6) == 0.004

    async def test_retries_clean_on_usage_rejection(self, um):
        resp = FakeResponse(FakeUsage(prompt=50, completion=5, cost=0.0))
        comp = FakeCompletions(resp, fail_first_with="unexpected keyword argument 'usage'")
        client = FakeClient(comp)
        s = make_self(um)

        out = await AgentSystem._completion_with_usage(
            s, client, 'suggestion', phase="consolidation", model="kimi", messages=[],
        )

        assert out is resp
        # Two attempts: first with extra_body (rejected), retry without it
        assert len(comp.calls) == 2
        assert "extra_body" in comp.calls[0]
        assert "extra_body" not in comp.calls[1]
        # Slot remembered as rejecting so future calls skip the param
        assert s._client_usage_ok["suggestion"] is False

    async def test_skips_usage_param_when_slot_known_bad(self, um):
        resp = FakeResponse(FakeUsage(prompt=10, completion=2, cost=0.0))
        comp = FakeCompletions(resp)
        client = FakeClient(comp)
        s = make_self(um)
        s._client_usage_ok["config"] = False  # already known to reject

        await AgentSystem._completion_with_usage(
            s, client, 'config', phase="task", model="sonnet", messages=[],
        )

        assert "extra_body" not in comp.calls[0]

    async def test_no_usage_manager_is_safe(self):
        resp = FakeResponse(FakeUsage(prompt=10, completion=2, cost=0.001))
        comp = FakeCompletions(resp)
        client = FakeClient(comp)
        s = make_self(None)
        s.usage_manager = None

        out = await AgentSystem._completion_with_usage(
            s, client, 'suggestion', phase="memory_extraction", model="kimi", messages=[],
        )
        assert out is resp  # records skipped, call still returns
