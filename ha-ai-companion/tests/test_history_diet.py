"""
Tests for history_diet — truncation of old-turn tool payloads.

Covers:
- tool results older than last turn truncated; last turn kept verbatim
- assistant tool_call arguments truncated to valid JSON stub
- short payloads untouched; input never mutated
- empty / no-user-message histories safe
"""
import json
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.agents.history_diet import (
    truncate_old_tool_content,
    TOOL_RESULT_MAX_CHARS,
    TOOL_ARGS_MAX_CHARS,
)


def big(n=5000):
    return "x" * n


def make_history():
    """Two completed turns: turn 1 has big tool payloads, turn 2 is last."""
    return [
        {"role": "user", "content": "read my config"},                                  # 0 turn 1
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a", "function": {"name": "search_config_files", "arguments": json.dumps({"q": big()})}},
        ]},                                                                              # 1
        {"role": "tool", "tool_call_id": "a", "content": big()},                         # 2
        {"role": "assistant", "content": "done"},                                        # 3
        {"role": "user", "content": "now add automation"},                               # 4 turn 2 (last)
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "b", "function": {"name": "propose_config_changes", "arguments": json.dumps({"c": big()})}},
        ]},                                                                              # 5
        {"role": "tool", "tool_call_id": "b", "content": big()},                         # 6
        {"role": "assistant", "content": "proposed"},                                    # 7
    ]


def test_old_turn_tool_result_truncated_last_turn_kept():
    history = make_history()
    out = truncate_old_tool_content(history)

    assert len(out[2]["content"]) < TOOL_RESULT_MAX_CHARS + 100   # old turn: truncated
    assert "truncated" in out[2]["content"]
    assert len(out[6]["content"]) == 5000                          # last turn: verbatim
    assert out[5]["tool_calls"][0]["function"]["arguments"] == history[5]["tool_calls"][0]["function"]["arguments"]


def test_old_turn_tool_args_truncated_to_valid_json():
    out = truncate_old_tool_content(make_history())
    args = out[1]["tool_calls"][0]["function"]["arguments"]
    assert len(args) < TOOL_ARGS_MAX_CHARS
    parsed = json.loads(args)  # must stay valid JSON
    assert "truncated" in parsed


def test_short_payloads_untouched_and_no_mutation():
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a", "function": {"name": "t", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "a", "content": "small"},
        {"role": "user", "content": "next"},
        {"role": "assistant", "content": "ok"},
    ]
    snapshot = json.dumps(history)
    out = truncate_old_tool_content(history)
    assert out[2]["content"] == "small"
    assert json.dumps(history) == snapshot  # input not mutated


def test_empty_and_no_user_history_safe():
    assert truncate_old_tool_content([]) == []
    only_tools = [{"role": "tool", "tool_call_id": "a", "content": big()}]
    out = truncate_old_tool_content(only_tools)
    assert "truncated" in out[0]["content"]  # boundary -1: everything is "old"
