"""Deterministic agent-loop tests: a scripted fake OpenAI client drives chat_stream()."""
import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from src.agents.agent_system import AgentSystem

AUTOMATIONS = """\
- id: '1'
  alias: Morning light
  trigger:
    - platform: time
      at: '07:00:00'
  action:
    - service: light.turn_on
      target:
        entity_id: light.kitchen
"""


def _chunk(content=None, tool_calls=None, finish=None, usage=None):
    choices = [] if usage else [NS(delta=NS(content=content, tool_calls=tool_calls), finish_reason=finish)]
    return NS(choices=choices, usage=usage)


def _tool_call(name, args, idx=0, cid="call_1"):
    raw = args if isinstance(args, str) else json.dumps(args)
    return [NS(index=idx, id=cid, type="function", function=NS(name=name, arguments=raw))]


class _Stream:
    def __init__(self, chunks):
        self._it = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class FakeClient:
    """Each create() call pops the next scripted response (a list of chunks)."""

    def __init__(self, script, reject_usage_once=False):
        self.script, self.requests, self._reject = list(script), [], reject_usage_once
        self.base_url = "https://openrouter.ai/api/v1"
        self.chat = NS(completions=NS(create=self._create))

    async def _create(self, **params):
        self.requests.append(params)
        if self._reject and "stream_options" in params:
            self._reject = False
            raise Exception("unknown parameter: stream_options")
        usage = NS(prompt_tokens=100, completion_tokens=10, total_tokens=110, prompt_tokens_details=None)
        return _Stream(self.script.pop(0) + [_chunk(usage=usage)])


def _final(text="Done."):
    return [_chunk(content=text), _chunk(finish="stop")]


def _call(name, args, cid="call_1"):
    return [_chunk(tool_calls=_tool_call(name, args, cid=cid)), _chunk(finish="tool_calls")]


@pytest.fixture
def agent(monkeypatch, tmp_path):
    """Factory: AgentSystem must be built inside the running loop (py3.9 asyncio.Lock)."""
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("OPENAI_API_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("OPENAI_MODEL", "openai/gpt-6.1-sol")
    for k in ("FAST_MODEL", "RESEARCH_MODEL", "SUGGESTION_MODEL", "REASONING_MODEL", "CONFIG_MODEL"):
        monkeypatch.delenv(k, raising=False)
    cm = MagicMock()
    files = {"automations.yaml": AUTOMATIONS}

    async def read_file_raw(path, allow_missing=True):
        return files.get(path)

    cm.read_file_raw = read_file_raw
    cm.hass = None

    async def _no_topology(*_a, **_k):
        return ""

    def make(**attrs):
        a = AgentSystem(cm)
        a._build_home_topology = _no_topology
        for k, v in attrs.items():
            setattr(a, k, v)
        return a

    return make


def _run(make, script, msg="change it", attrs=None, **kw):
    fake = FakeClient(script, **kw)

    async def go():
        a = make(**(attrs or {}))
        a.client = a.suggestion_client = a.config_client = fake
        return [e async for e in a.chat_stream(msg, [], session_id="sess-1")]

    events = asyncio.run(go())
    return fake, events, [e["event"] for e in events]


def test_tool_then_final_answer(agent):
    fake, events, kinds = _run(agent, [
        _call("patch_config_block", {"file_path": "automations.yaml", "anchor": "automations[alias=morning LIGHT]",
                                     "new_block": "id: '1'\nalias: Morning light\ntrigger: []\naction: []\n"}),
        _final("Proposed."),
    ])
    assert "error" not in kinds, events
    assert "tool_call" in kinds and "tool_result" in kinds and kinds[-1] == "complete"
    tool_msgs = [m for m in fake.requests[1]["messages"] if m.get("role") == "tool"]
    assert tool_msgs and '"success": true' in json.dumps(tool_msgs[-1]).replace("\\", "").lower()
    assert len(fake.requests) == 2


def test_invalid_tool_json_does_not_crash(agent):
    fake, events, kinds = _run(agent, [
        _call("patch_config_key", '{"file_path": "automations.yaml", "key_path": "a"b"}'),
        _final("Sorry, retrying differently."),
    ])
    assert "error" not in kinds, events
    assert kinds[-1] == "complete"
    sent = json.dumps(fake.requests[1]["messages"])
    assert "Malformed" in sent or "malformed" in sent


def test_usage_rejection_keeps_session_routing(agent):
    st = "stream_options"
    fake, _, kinds = _run(agent, [_final()], reject_usage_once=True,
                          attrs={"usage_tracking": st, "config_usage_tracking": st, "suggestion_usage_tracking": st})
    assert kinds[-1] == "complete"
    retry = fake.requests[-1]
    assert len(fake.requests) == 2 and "stream_options" in fake.requests[0]
    assert "stream_options" not in retry
    assert retry.get("extra_body", {}).get("session_id") == "sess-1"


def test_prefix_stable_and_context_in_user_message(agent):
    fake1, _, _ = _run(agent, [_final()], msg="first question")
    fake2, _, _ = _run(agent, [_final()], msg="another question")
    r1, r2 = fake1.requests[0], fake2.requests[0]
    # Cache prefix: system message and tool schemas byte-identical across turns.
    assert json.dumps(r1["messages"][0]) == json.dumps(r2["messages"][0])
    assert json.dumps(r1.get("tools")) == json.dumps(r2.get("tools"))
    user = r2["messages"][-1]
    assert user["role"] == "user" and user["content"].startswith("another question")
    assert user["content"].rstrip().endswith("</context>")


def test_cap_tool_result_only_for_listing_tools(monkeypatch):
    from src.agents.agent_system import _cap_tool_result
    monkeypatch.setenv("TOOL_RESULT_MAX_CHARS", "100")
    big = "x" * 500
    capped = _cap_tool_result("get_entity_states", big)
    assert len(capped) < 300 and capped.startswith("x" * 100)
    # File-content tools are never truncated: a cut read could be written back.
    assert _cap_tool_result("get_automation", big) == big
    assert _cap_tool_result("search_config_files", big) == big


def test_reasoning_effort_sent_only_when_set(agent, monkeypatch):
    fake, _, _ = _run(agent, [_final()])
    assert "reasoning" not in (fake.requests[0].get("extra_body") or {})
    monkeypatch.setenv("REASONING_EFFORT", "High")
    fake, _, _ = _run(agent, [_final()])
    assert fake.requests[0]["extra_body"]["reasoning"] == {"effort": "high"}
    monkeypatch.setenv("REASONING_EFFORT", "turbo")
    fake, _, _ = _run(agent, [_final()])
    assert "reasoning" not in fake.requests[0]["extra_body"]


def test_fast_model_slot(monkeypatch):
    """Main model runs the whole loop; FAST_MODEL only feeds side tasks, same client."""
    for k, v in {"OPENAI_API_KEY": "t", "OPENAI_API_URL": "https://openrouter.ai/api/v1",
                 "OPENAI_MODEL": "z-ai/glm-5.3-flash", "FAST_MODEL": "openai/gpt-6-luna"}.items():
        monkeypatch.setenv(k, v)
    for k in ("RESEARCH_API_URL", "RESEARCH_API_KEY", "SUGGESTION_API_URL", "SUGGESTION_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    async def build():
        return AgentSystem(MagicMock())

    a = asyncio.run(build())
    assert a.config_model == "z-ai/glm-5.3-flash" and a.config_client is a.client
    assert a._fast_client_and_model() == (a.client, "openai/gpt-6-luna")
