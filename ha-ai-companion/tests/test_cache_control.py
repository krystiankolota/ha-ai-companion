from src.agents.agent_system import AgentSystem


def test_blockify_moves_marker_to_text_block():
    cc = {"type": "ephemeral"}
    msgs = [
        {"role": "system", "content": [{"type": "text", "text": "S", "cache_control": cc}]},
        {"role": "user", "content": "hi", "cache_control": cc},
        {"role": "assistant", "content": None, "tool_calls": [], "cache_control": cc},
        {"role": "tool", "content": "res", "tool_call_id": "1"},
    ]
    out = AgentSystem._blockify_cache_control(msgs)
    assert out[0] is msgs[0] and out[3] is msgs[3]
    assert out[1] == {"role": "user", "content": [{"type": "text", "text": "hi", "cache_control": cc}]}
    assert "cache_control" not in out[2] and out[2]["content"] is None
    assert msgs[1]["content"] == "hi" and "cache_control" in msgs[1]  # stored history untouched


def test_strip_then_blockify_keeps_limit():
    cc = {"type": "ephemeral"}
    msgs = [{"role": "user", "content": f"m{i}", "cache_control": cc} for i in range(5)]
    out = AgentSystem._blockify_cache_control(AgentSystem._strip_excess_cache_control(msgs))
    marked = [m for m in out if isinstance(m["content"], list)]
    assert len(marked) == 3 and marked[-1]["content"][0]["text"] == "m4"
