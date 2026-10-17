"""Background-run persister: saves the final answer when the client is gone."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import src.main as main
from src.conversations.manager import ConversationManager


def _done(text):
    return [{"event": "message_complete", "data": {"message": {"content": text}}}]


def _tool_pair(i):
    return [
        {"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}", "type": "function",
                                                             "function": {"name": "x", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": f"c{i}", "content": "ok"},
    ]


def test_persister_does_not_duplicate_user_turn_after_many_tools(tmp_path, monkeypatch):
    cm = ConversationManager(sessions_dir=str(tmp_path))
    monkeypatch.setattr(main, "conversation_manager", cm)
    msgs = [{"role": "user", "content": "fix lights"}]
    for i in range(3):
        msgs += _tool_pair(i)
    asyncio.run(cm.save_session("s1", "t", msgs))

    asyncio.run(main._make_run_persister("s1", "fix lights")("completed", _done("Done.")))
    saved = asyncio.run(cm.load_session("s1"))["messages"]
    assert [m["content"] for m in saved if m["role"] == "user"] == ["fix lights"]
    assert saved[-1] == {"role": "assistant", "content": "Done."}


def test_persister_appends_user_turn_when_frontend_never_saved_it(tmp_path, monkeypatch):
    cm = ConversationManager(sessions_dir=str(tmp_path))
    monkeypatch.setattr(main, "conversation_manager", cm)
    asyncio.run(cm.save_session("s1", "t", [{"role": "user", "content": "old"},
                                             {"role": "assistant", "content": "old answer"}]))
    asyncio.run(main._make_run_persister("s1", "new q")("completed", _done("New answer.")))
    saved = asyncio.run(cm.load_session("s1"))["messages"]
    assert [m["content"] for m in saved[-2:]] == ["new q", "New answer."]
