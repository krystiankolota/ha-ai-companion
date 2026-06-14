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

from memory.manager import (
    MemoryManager, _sanitise, _fix_mojibake, _mojibake_score,
    extract_entities, _read_marker,
)


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

    # --- critical marker ---

    async def test_critical_true_writes_marker(self, mgr):
        await mgr.write_file("device.md", "pumpa=basement pump", critical=True)
        raw = (mgr.memory_dir / "device.md").read_text()
        assert raw.startswith("<!-- critical -->")

    async def test_critical_false_no_marker(self, mgr):
        await mgr.write_file("device.md", "pumpa=basement pump", critical=False)
        raw = (mgr.memory_dir / "device.md").read_text()
        assert "<!-- critical -->" not in raw

    async def test_critical_none_new_file_no_marker(self, mgr):
        # Default (critical=None) on a new file — no marker
        await mgr.write_file("device.md", "pumpa=basement pump")
        raw = (mgr.memory_dir / "device.md").read_text()
        assert "<!-- critical -->" not in raw

    async def test_critical_preserved_on_update(self, mgr):
        # Mark critical, then update content without specifying critical — marker preserved
        await mgr.write_file("device.md", "original content", critical=True)
        await mgr.write_file("device.md", "updated content")  # critical=None default
        raw = (mgr.memory_dir / "device.md").read_text()
        assert "<!-- critical -->" in raw
        assert "updated content" in raw

    async def test_critical_demoted(self, mgr):
        # Mark critical, then explicitly demote with critical=False
        await mgr.write_file("device.md", "content", critical=True)
        await mgr.write_file("device.md", "content", critical=False)
        raw = (mgr.memory_dir / "device.md").read_text()
        assert "<!-- critical -->" not in raw

    async def test_critical_none_non_critical_stays_non_critical(self, mgr):
        # Update a non-critical file without specifying critical — marker stays absent
        await mgr.write_file("prefs.md", "original", critical=False)
        await mgr.write_file("prefs.md", "updated")  # critical=None
        raw = (mgr.memory_dir / "prefs.md").read_text()
        assert "<!-- critical -->" not in raw


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
        assert "## Memory" in ctx
        assert "[note]" in ctx

    async def test_newest_file_appears_first(self, mgr):
        await mgr.write_file("older.md", "older content")
        time.sleep(0.05)
        await mgr.write_file("newer.md", "newer content")
        ctx = await mgr.get_context()
        assert ctx.index("[newer]") < ctx.index("[older]")

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


# ---------------------------------------------------------------------------
# get_context — critical tier (Tier 1)
# ---------------------------------------------------------------------------

class TestGetContextCritical:
    async def test_critical_file_always_injected_with_no_query_match(self, mgr):
        """Critical file injected even when query keywords don't match its content."""
        await mgr.write_file("device_pumps.md", "pumpa=basement pump", critical=True)
        time.sleep(0.02)
        await mgr.write_file("preference_language.md", "respond in Polish")
        ctx = await mgr.get_context(query="heating temperature climate")
        # Critical file has zero keyword overlap with "heating temperature climate"
        # but must still appear because it's critical
        assert "pumpa=basement pump" in ctx

    async def test_critical_appears_before_priority_file(self, mgr):
        """Critical file (Tier 1) appears before prefix-priority file (Tier 2)."""
        await mgr.write_file("preference_language.md", "respond in Polish", critical=False)
        time.sleep(0.02)
        # Write critical AFTER priority so mtime is newer for the priority file —
        # but critical should still win the sort.
        await mgr.write_file("device_pumps.md", "pumpa=basement pump", critical=True)
        ctx = await mgr.get_context()
        assert ctx.index("[device_pumps]") < ctx.index("[preference_language]")

    async def test_critical_marker_stripped_from_context_output(self, mgr):
        """The <!-- critical --> marker must not appear in the returned context string."""
        await mgr.write_file("device_pumps.md", "pumpa=basement pump", critical=True)
        ctx = await mgr.get_context()
        assert "<!-- critical -->" not in ctx
        assert "pumpa=basement pump" in ctx

    async def test_context_within_max_chars_with_critical_files(self, mgr):
        """Total context stays within MAX_CONTEXT_CHARS even when critical files are present."""
        chunk = "a" * 600
        for i in range(3):
            time.sleep(0.01)
            await mgr.write_file(f"critical_{i}.md", chunk, critical=True)
        for i in range(5):
            time.sleep(0.01)
            await mgr.write_file(f"regular_{i}.md", chunk)
        ctx = await mgr.get_context()
        assert len(ctx) <= MemoryManager.MAX_CONTEXT_CHARS + 200

    async def test_non_critical_zero_relevance_still_skipped(self, mgr):
        """Non-critical, non-priority file with zero query overlap is still skipped."""
        await mgr.write_file("device_pumps.md", "pumpa=basement pump", critical=False)
        await mgr.write_file("preference_language.md", "respond in Polish")
        ctx = await mgr.get_context(query="heating temperature climate")
        # preference_ file always injected (Tier 2)
        assert "respond in Polish" in ctx
        # device_ file with zero overlap is gated out
        assert "pumpa=basement pump" not in ctx


# ---------------------------------------------------------------------------
# Mojibake repair (_fix_mojibake)
# ---------------------------------------------------------------------------

class TestMojibake:
    def test_repairs_known_corruption(self):
        # Genuine mojibake: correct UTF-8 bytes mis-decoded as latin-1
        original = "Ogród i Oświetlenie"
        mojibake = original.encode("utf-8").decode("latin-1")
        assert _mojibake_score(mojibake) > 0
        assert _fix_mojibake(mojibake) == original

    def test_leaves_clean_text_untouched(self):
        clean = "Garaż: żarówki LSC, Oświetlenie salon"
        assert _fix_mojibake(clean) == clean

    def test_leaves_ascii_untouched(self):
        assert _fix_mojibake("plain ascii content") == "plain ascii content"

    async def test_write_repairs_incoming_mojibake(self, mgr):
        original = "Salon: Listwa LED, Ogród nawadnianie"
        mojibake = original.encode("utf-8").decode("latin-1")
        await mgr.write_file("device_x.md", mojibake)
        raw = (mgr.memory_dir / "device_x.md").read_text(encoding="utf-8")
        assert original in raw
        assert _mojibake_score(raw) == 0

    async def test_repair_existing_fixes_files_on_init(self, tmp_path):
        d = tmp_path / "memories"
        d.mkdir(parents=True)
        original = "Oświetlenie i Ogród"
        (d / "broken.md").write_text(original.encode("utf-8").decode("latin-1"), encoding="utf-8")
        # Init triggers _repair_existing
        MemoryManager(memory_dir=str(d))
        assert original in (d / "broken.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Entity extraction + markers (schema)
# ---------------------------------------------------------------------------

class TestEntityExtraction:
    def test_extracts_entity_ids(self):
        content = "Garaż kinkiety switch.kinkiety_garaz i light.listwa_led_biuro"
        ents = extract_entities(content)
        assert "switch.kinkiety_garaz" in ents
        assert "light.listwa_led_biuro" in ents

    def test_ignores_file_extensions(self):
        content = "Edit lovelace/panel.yaml and config.json"
        assert extract_entities(content) == []

    def test_sorted_unique(self):
        content = "sensor.time sensor.time binary_sensor.door"
        assert extract_entities(content) == ["binary_sensor.door", "sensor.time"]

    async def test_write_stores_entities_marker(self, mgr):
        await mgr.write_file("device_g.md", "Kinkiety: switch.kinkiety_garaz")
        raw = (mgr.memory_dir / "device_g.md").read_text(encoding="utf-8")
        assert _read_marker(raw, "entities") == "switch.kinkiety_garaz"

    async def test_write_stores_and_preserves_type(self, mgr):
        await mgr.write_file("device_g.md", "content", mem_type="device")
        raw = (mgr.memory_dir / "device_g.md").read_text(encoding="utf-8")
        assert _read_marker(raw, "type") == "device"
        # Update without type — preserved
        await mgr.write_file("device_g.md", "updated content")
        raw2 = (mgr.memory_dir / "device_g.md").read_text(encoding="utf-8")
        assert _read_marker(raw2, "type") == "device"

    async def test_markers_stripped_from_context(self, mgr):
        await mgr.write_file("device_g.md", "Kinkiety switch.kinkiety_garaz", mem_type="device")
        ctx = await mgr.get_context()
        assert "<!-- type:" not in ctx
        assert "<!-- entities:" not in ctx
        assert "switch.kinkiety_garaz" in ctx


# ---------------------------------------------------------------------------
# find_similar (dedup) + get_memory_entities
# ---------------------------------------------------------------------------

class TestFindSimilar:
    async def test_detects_type_match(self, mgr):
        await mgr.write_file("device_a.md", "fridge kitchen appliance", mem_type="device")
        similar = await mgr.find_similar("device_b.md", "washer laundry appliance", mem_type="device")
        names = [s["filename"] for s in similar]
        assert "device_a.md" in names

    async def test_detects_keyword_overlap(self, mgr):
        await mgr.write_file("notes_a.md", "garage lighting sunset elevation threshold winter summer")
        similar = await mgr.find_similar("notes_b.md", "garage lighting sunset elevation threshold spring autumn")
        assert any(s["filename"] == "notes_a.md" for s in similar)

    async def test_excludes_self(self, mgr):
        await mgr.write_file("same.md", "garage lighting sunset elevation threshold winter")
        similar = await mgr.find_similar("same.md", "garage lighting sunset elevation threshold winter")
        assert all(s["filename"] != "same.md" for s in similar)

    async def test_get_memory_entities(self, mgr):
        await mgr.write_file("device_g.md", "Kinkiety switch.kinkiety_garaz light.listwa_led_biuro")
        mapping = await mgr.get_memory_entities()
        assert "device_g.md" in mapping
        assert "switch.kinkiety_garaz" in mapping["device_g.md"]
