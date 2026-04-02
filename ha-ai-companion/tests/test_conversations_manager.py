"""
Tests for ConversationManager (pure file I/O — no HA required).

Covers:
- _auto_title: first user message extraction, truncation, fallback
- _path: session ID sanitisation
- save_session / load_session / delete_session CRUD
- created_at preservation on update
- list_sessions: sorted by mtime, message_count counts only user+assistant
- Pruning: _prune_sync on init, _prune after save
"""
import json
import time
import pytest
from pathlib import Path

from conversations.manager import ConversationManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mgr(tmp_path):
    return ConversationManager(sessions_dir=str(tmp_path / "sessions"), max_sessions=3)


def _msgs(*pairs):
    """Build a message list from (role, content) pairs."""
    return [{"role": role, "content": content} for role, content in pairs]


# ---------------------------------------------------------------------------
# _auto_title
# ---------------------------------------------------------------------------

class TestAutoTitle:
    def test_returns_first_user_message(self):
        msgs = _msgs(("assistant", "Hello!"), ("user", "Turn on the lights"))
        assert ConversationManager._auto_title(msgs) == "Turn on the lights"

    def test_truncates_at_60_chars(self):
        long = "A" * 80
        msgs = _msgs(("user", long))
        title = ConversationManager._auto_title(msgs)
        assert len(title) <= 60

    def test_fallback_when_no_user_message(self):
        msgs = _msgs(("assistant", "Hi"))
        assert ConversationManager._auto_title(msgs) == "New conversation"

    def test_fallback_for_empty_list(self):
        assert ConversationManager._auto_title([]) == "New conversation"

    def test_skips_empty_user_content(self):
        msgs = _msgs(("user", ""), ("user", "Real message"))
        assert ConversationManager._auto_title(msgs) == "Real message"


# ---------------------------------------------------------------------------
# _path (ID sanitisation)
# ---------------------------------------------------------------------------

class TestPath:
    def test_safe_id_unchanged(self, mgr):
        p = mgr._path("abc-123_XYZ")
        assert p.name == "abc-123_XYZ.json"

    def test_strips_unsafe_chars(self, mgr):
        p = mgr._path("id with spaces!")
        assert " " not in p.name
        assert "!" not in p.name

    def test_truncates_at_64(self, mgr):
        long_id = "a" * 100
        p = mgr._path(long_id)
        stem = p.stem  # without .json
        assert len(stem) <= 64

    def test_empty_id_becomes_session(self, mgr):
        p = mgr._path("!!!!")
        assert p.name == "session.json"


# ---------------------------------------------------------------------------
# save_session / load_session
# ---------------------------------------------------------------------------

class TestSaveLoadSession:
    async def test_save_creates_file(self, mgr):
        await mgr.save_session("s1", "My Chat", _msgs(("user", "hello")))
        assert (mgr.sessions_dir / "s1.json").exists()

    async def test_load_returns_correct_data(self, mgr):
        msgs = _msgs(("user", "hello"), ("assistant", "hi"))
        await mgr.save_session("s1", "Chat", msgs)
        data = await mgr.load_session("s1")
        assert data is not None
        assert data["id"] == "s1"
        assert data["title"] == "Chat"
        assert len(data["messages"]) == 2

    async def test_load_missing_returns_none(self, mgr):
        result = await mgr.load_session("nonexistent")
        assert result is None

    async def test_preserves_created_at_on_update(self, mgr):
        await mgr.save_session("s1", "Chat", _msgs(("user", "first")))
        data1 = await mgr.load_session("s1")
        created_at = data1["created_at"]

        time.sleep(0.05)
        await mgr.save_session("s1", "Chat", _msgs(("user", "second")))
        data2 = await mgr.load_session("s1")

        assert data2["created_at"] == created_at
        assert data2["updated_at"] != created_at

    async def test_updated_at_changes_on_resave(self, mgr):
        await mgr.save_session("s1", "Chat", _msgs(("user", "v1")))
        d1 = await mgr.load_session("s1")
        time.sleep(0.05)
        await mgr.save_session("s1", "Chat", _msgs(("user", "v2")))
        d2 = await mgr.load_session("s1")
        assert d2["updated_at"] > d1["updated_at"]


# ---------------------------------------------------------------------------
# delete_session
# ---------------------------------------------------------------------------

class TestDeleteSession:
    async def test_deletes_existing(self, mgr):
        await mgr.save_session("s1", "Chat", _msgs(("user", "hi")))
        ok = await mgr.delete_session("s1")
        assert ok is True
        assert not (mgr.sessions_dir / "s1.json").exists()

    async def test_missing_returns_false(self, mgr):
        ok = await mgr.delete_session("ghost")
        assert ok is False


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------

class TestListSessions:
    async def test_sorted_newest_first(self, mgr):
        for sid in ["s1", "s2", "s3"]:
            time.sleep(0.02)
            await mgr.save_session(sid, sid, _msgs(("user", "x")))
        sessions = await mgr.list_sessions()
        ids = [s["id"] for s in sessions]
        assert ids == ["s3", "s2", "s1"]

    async def test_message_count_only_user_and_assistant(self, mgr):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "tool", "content": "tool result"},
            {"role": "system", "content": "system msg"},
        ]
        await mgr.save_session("s1", "Chat", msgs)
        sessions = await mgr.list_sessions()
        assert sessions[0]["message_count"] == 2

    async def test_returns_expected_keys(self, mgr):
        await mgr.save_session("s1", "My Chat", _msgs(("user", "hello")))
        session = (await mgr.list_sessions())[0]
        assert set(session.keys()) == {"id", "title", "created_at", "updated_at", "message_count"}

    async def test_empty_dir(self, mgr):
        assert await mgr.list_sessions() == []


# ---------------------------------------------------------------------------
# Pruning
# ---------------------------------------------------------------------------

class TestPruning:
    def test_prune_sync_on_init_enforces_limit(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        # Pre-populate 5 sessions (limit will be 2)
        for i in range(5):
            time.sleep(0.02)
            p = sessions_dir / f"s{i}.json"
            p.write_text(json.dumps({"id": f"s{i}", "title": "t", "messages": []}))

        mgr2 = ConversationManager(sessions_dir=str(sessions_dir), max_sessions=2)
        remaining = list(sessions_dir.glob("*.json"))
        assert len(remaining) == 2

    async def test_save_triggers_prune(self, mgr):
        # mgr has max_sessions=3; add 4 sessions
        for i in range(4):
            time.sleep(0.02)
            await mgr.save_session(f"s{i}", "t", _msgs(("user", str(i))))

        remaining = list(mgr.sessions_dir.glob("*.json"))
        assert len(remaining) == 3

    async def test_oldest_session_pruned(self, mgr):
        for i in range(4):
            time.sleep(0.02)
            await mgr.save_session(f"s{i}", "t", _msgs(("user", str(i))))

        sessions = await mgr.list_sessions()
        ids = [s["id"] for s in sessions]
        assert "s0" not in ids  # oldest pruned
        assert "s3" in ids      # newest kept
