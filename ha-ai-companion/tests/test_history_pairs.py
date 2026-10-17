"""Unpaired tool messages break strict providers (HTTP 400).

A stopped or interrupted run can leave an assistant tool_calls message whose
results were never saved, or tool results without their call.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agents.history_diet import drop_unpaired_tool_messages


def call(cid, name="get_entity_states"):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": "{}"}}


def test_complete_pairs_kept():
    h = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [call("a")]},
        {"role": "tool", "tool_call_id": "a", "content": "{}"},
        {"role": "assistant", "content": "done"},
    ]
    assert drop_unpaired_tool_messages(h) == h


def test_call_without_result_dropped_but_text_kept():
    h = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "Let me check.", "tool_calls": [call("a")]},
    ]
    assert drop_unpaired_tool_messages(h) == [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "Let me check."},
    ]


def test_partial_results_keep_only_answered_calls():
    h = [
        {"role": "assistant", "content": "", "tool_calls": [call("a"), call("b")]},
        {"role": "tool", "tool_call_id": "a", "content": "{}"},
    ]
    out = drop_unpaired_tool_messages(h)
    assert [tc["id"] for tc in out[0]["tool_calls"]] == ["a"]
    assert out[1]["tool_call_id"] == "a"


def test_empty_assistant_with_no_answered_calls_removed():
    h = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [call("a")]},
        {"role": "user", "content": "again"},
    ]
    assert drop_unpaired_tool_messages(h) == [
        {"role": "user", "content": "q"},
        {"role": "user", "content": "again"},
    ]


def test_orphan_tool_result_dropped():
    h = [
        {"role": "user", "content": "q"},
        {"role": "tool", "tool_call_id": "zzz", "content": "{}"},
    ]
    assert drop_unpaired_tool_messages(h) == [{"role": "user", "content": "q"}]


def test_input_not_mutated():
    h = [{"role": "assistant", "content": "x", "tool_calls": [call("a")]}]
    drop_unpaired_tool_messages(h)
    assert "tool_calls" in h[0]
