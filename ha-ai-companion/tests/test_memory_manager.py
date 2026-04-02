"""
Tests for MemoryManager (pure file I/O — no HA required).

Covers:
- Filename sanitisation (_sanitise)
- write_file: success, MAX_FILE_CHARS rejection, MAX_FILES eviction
- read_file / delete_file
- list_files ordering
- get_context: newest-first, HTML comment stripping, MAX_CONTEXT_CHARS cap
- get_stats structure
"""
import os
import time
import pytest
from pathlib import Path

from memory.manager import MemoryManager, _sanitise


# ---------------------------------------------------------------------------
# _sanitise
# ---------------------------------------------------------------------------

class TestSanitise:
    def test_plain_name_unchanged(self):
        assert _sanitise("home_structure.md") == "home_structure.md"

    def test_forces_md_extension(self):
        assert _sanitise("notes.txt") == "notes.md"

    def test_no_extension(self):
        assert _sanitise("memories") == "memories.md"

    def test_strips_directory_components(self):
        assert _sanitise("../etc/passwd") == "passwd.md"
        assert _sanitise("/etc/shadow") == "shadow.md"

    def test_replaces_unsafe_chars(self):
        result = _sanitise("my file name!.md")
        assert " " not in result
        assert "!" not in result
        assert result.endswith(".md")

    def test_empty_stem_becomes_memory(self):
        assert _sanitise("!!!.md") == "memory.md"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mgr(tmp_path):
    return MemoryManager(memory_dir=str(tmp_path / "memories"))


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------

class TestWriteFile:
    async def test_creates_file(self, mgr):
        ok = await mgr.write_file("prefs.md", "I like dark mode")
        assert ok is True
        assert (mgr.memory_dir / "prefs.md").exists()

    async def test_file_contains_content(self, mgr):
        await mgr.write_file("prefs.md", "dark mode")
        raw = (mgr.memory_dir / "prefs.md").read_text()
        assert "dark mode" in raw

    async def test_prepends_timestamp_comment(self, mgr):
        await mgr.write_file("prefs.md", "content")
        raw = (mgr.memory_dir / "prefs.md").read_text()
        assert raw.startswith("<!-- updated:")

    async def test_overwrites_existing(self, mgr):
        await mgr.write_file("prefs.md", "old")
        await mgr.write_file("prefs.md", "new")
        raw = (mgr.memory_dir / "prefs.md").read_text()
        assert "new" in raw
        assert "old" not in raw

    async def test_rejects_content_over_max_chars(self, mgr):
        big = "x" * (MemoryManager.MAX_FILE_CHARS + 1)
        ok = await mgr.write_file("big.md", big)
        assert ok is False
        assert not (mgr.memory_dir / "big.md").exists()

    async def test_accepts_content_at_exact_limit(self, mgr):
        exact = "x" * MemoryManager.MAX_FILE_CHARS
        ok = await mgr.write_file("exact.md", exact)
        assert ok is True

    async def test_max_files_evicts_oldest(self, mgr):
        for i in range(MemoryManager.MAX_FILES):
            time.sleep(0.01)
            ok = await mgr.write_file(f"file_{i:03d}.md", f"content {i}")
            assert ok is True

        # Adding one more should evict the oldest
        ok = await mgr.write_file("overflow.md", "overflow content")
        assert ok is True

        files = list(mgr.memory_dir.glob("*.md"))
        assert len(files) == MemoryManager.MAX_FILES

    async def test_sanitises_filename(self, mgr):
        await mgr.write_file("my file!.md", "content")
        # Should exist under a sanitised name, not the raw name
        assert not (mgr.memory_dir / "my file!.md").exists()
        assert len(list(mgr.memory_dir.glob("*.md"))) == 1


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------

class TestReadFile:
    async def test_reads_content(self, mgr):
        await mgr.write_file("note.md", "hello world")
        content = await mgr.read_file("note.md")
        assert content is not None
        assert "hello world" in content

    async def test_missing_returns_none(self, mgr):
        result = await mgr.read_file("nonexistent.md")
        assert result is None


# ---------------------------------------------------------------------------
# delete_file
# ---------------------------------------------------------------------------

class TestDeleteFile:
    async def test_deletes_existing(self, mgr):
        await mgr.write_file("temp.md", "data")
        ok = await mgr.delete_file("temp.md")
        assert ok is True
        assert not (mgr.memory_dir / "temp.md").exists()

    async def test_missing_returns_false(self, mgr):
        ok = await mgr.delete_file("ghost.md")
        assert ok is False


# ---------------------------------------------------------------------------
# list_files
# ---------------------------------------------------------------------------

class TestListFiles:
    async def test_returns_sorted_names(self, mgr):
        for name in ["zebra.md", "alpha.md", "middle.md"]:
            await mgr.write_file(name, "x")
        files = await mgr.list_files()
        assert files == sorted(files)

    async def test_empty_dir(self, mgr):
        assert await mgr.list_files() == []


# ---------------------------------------------------------------------------
# get_context
# ---------------------------------------------------------------------------

class TestGetContext:
    async def test_empty_returns_empty_string(self, mgr):
        ctx = await mgr.get_context()
        assert ctx == ""

    async def test_strips_timestamp_comment(self, mgr):
        await mgr.write_file("prefs.md", "dark mode preference")
        ctx = await mgr.get_context()
        assert "<!-- updated:" not in ctx
        assert "dark mode preference" in ctx

    async def test_includes_section_header(self, mgr):
        await mgr.write_file("note.md", "some content")
        ctx = await mgr.get_context()
        assert "## Agent Memory" in ctx
        assert "### note.md" in ctx

    async def test_newest_file_appears_first(self, mgr):
        await mgr.write_file("older.md", "older content")
        time.sleep(0.05)
        await mgr.write_file("newer.md", "newer content")
        ctx = await mgr.get_context()
        assert ctx.index("newer.md") < ctx.index("older.md")

    async def test_respects_max_context_chars(self, mgr):
        # Write enough files to exceed MAX_CONTEXT_CHARS
        chunk = "a" * 700  # close to MAX_FILE_CHARS each
        for i in range(12):
            time.sleep(0.01)
            await mgr.write_file(f"file_{i:02d}.md", chunk)
        ctx = await mgr.get_context()
        assert len(ctx) <= MemoryManager.MAX_CONTEXT_CHARS + 200  # small headroom for headers

    async def test_stale_files_excluded(self, mgr, tmp_path):
        # Write a file then backdate its mtime to be stale
        await mgr.write_file("stale.md", "very old info")
        stale_path = mgr.memory_dir / "stale.md"
        old_ts = time.time() - (MemoryManager.MAX_CONTEXT_AGE_DAYS + 1) * 86400
        os.utime(stale_path, (old_ts, old_ts))

        await mgr.write_file("fresh.md", "current info")
        ctx = await mgr.get_context()
        assert "very old info" not in ctx
        assert "current info" in ctx


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------

class TestGetStats:
    async def test_returns_expected_structure(self, mgr):
        await mgr.write_file("a.md", "content a")
        stats = await mgr.get_stats()
        assert "files" in stats
        assert "total" in stats
        assert stats["total"] == 1
        assert stats["max_files"] == MemoryManager.MAX_FILES
        assert stats["max_file_chars"] == MemoryManager.MAX_FILE_CHARS

    async def test_file_entry_has_required_keys(self, mgr):
        await mgr.write_file("a.md", "hello")
        stats = await mgr.get_stats()
        entry = stats["files"][0]
        assert set(entry.keys()) == {"filename", "chars", "age_days", "stale"}

    async def test_empty_dir(self, mgr):
        stats = await mgr.get_stats()
        assert stats["total"] == 0
        assert stats["files"] == []
