"""
Tests for ConfigurationManager (pure file I/O — no HA required).

Covers:
- Path traversal protection (_validate_path)
- Raw file read (read_file_raw)
- Raw file write with backup (write_file_raw)
- Backup creation and rotation
- Backup listing and restore
"""
import os
import time
import pytest
from pathlib import Path

from config.manager import ConfigurationManager, ConfigurationError


@pytest.fixture
def manager(tmp_path):
    config_dir = tmp_path / "config"
    backup_dir = tmp_path / "backups"
    config_dir.mkdir()
    return ConfigurationManager(
        config_dir=str(config_dir),
        backup_dir=str(backup_dir),
        max_backups=3,
    )


# ---------------------------------------------------------------------------
# _validate_path
# ---------------------------------------------------------------------------

class TestValidatePath:
    def test_valid_relative_path(self, manager):
        p = manager._validate_path("configuration.yaml")
        assert p == manager.config_dir / "configuration.yaml"

    def test_valid_nested_path(self, manager):
        p = manager._validate_path("packages/lights.yaml")
        assert p == manager.config_dir / "packages" / "lights.yaml"

    def test_path_traversal_single_dotdot(self, manager):
        with pytest.raises(ConfigurationError, match="outside config directory"):
            manager._validate_path("../etc/passwd")

    def test_path_traversal_deep(self, manager):
        with pytest.raises(ConfigurationError, match="outside config directory"):
            manager._validate_path("../../etc/shadow")

    def test_path_traversal_disguised(self, manager):
        with pytest.raises(ConfigurationError, match="outside config directory"):
            manager._validate_path("subdir/../../etc/passwd")

    def test_absolute_path_outside_config(self, manager, tmp_path):
        outside = str(tmp_path / "outside.yaml")
        with pytest.raises(ConfigurationError, match="outside config directory"):
            manager._validate_path(outside)


# ---------------------------------------------------------------------------
# read_file_raw
# ---------------------------------------------------------------------------

class TestReadFileRaw:
    async def test_reads_existing_file(self, manager):
        (manager.config_dir / "config.yaml").write_text("key: value", encoding="utf-8")
        content = await manager.read_file_raw("config.yaml")
        assert content == "key: value"

    async def test_missing_file_raises(self, manager):
        with pytest.raises(ConfigurationError, match="not found"):
            await manager.read_file_raw("nonexistent.yaml")

    async def test_missing_file_allow_missing(self, manager):
        result = await manager.read_file_raw("nonexistent.yaml", allow_missing=True)
        assert result is None

    async def test_path_traversal_in_read(self, manager):
        with pytest.raises(ConfigurationError):
            await manager.read_file_raw("../secret.yaml")


# ---------------------------------------------------------------------------
# write_file_raw
# ---------------------------------------------------------------------------

class TestWriteFileRaw:
    async def test_creates_new_file(self, manager):
        await manager.write_file_raw("new.yaml", "hello: world")
        assert (manager.config_dir / "new.yaml").read_text() == "hello: world"

    async def test_overwrites_existing_file(self, manager):
        (manager.config_dir / "config.yaml").write_text("old: content", encoding="utf-8")
        await manager.write_file_raw("config.yaml", "new: content")
        assert (manager.config_dir / "config.yaml").read_text() == "new: content"

    async def test_backup_created_for_existing_file(self, manager):
        (manager.config_dir / "config.yaml").write_text("original", encoding="utf-8")
        await manager.write_file_raw("config.yaml", "updated")
        backups = list(manager.backup_dir.glob("config_*.backup"))
        assert len(backups) == 1

    async def test_no_backup_for_new_file(self, manager):
        await manager.write_file_raw("brand_new.yaml", "content")
        backups = list(manager.backup_dir.glob("*.backup"))
        assert len(backups) == 0

    async def test_no_backup_when_disabled(self, manager):
        (manager.config_dir / "config.yaml").write_text("original", encoding="utf-8")
        await manager.write_file_raw("config.yaml", "updated", create_backup=False)
        backups = list(manager.backup_dir.glob("*.backup"))
        assert len(backups) == 0

    async def test_temp_file_cleaned_up_on_success(self, manager):
        await manager.write_file_raw("config.yaml", "content")
        tmp_files = list(manager.config_dir.glob("*.tmp"))
        assert len(tmp_files) == 0

    async def test_creates_parent_dirs_for_nested_path(self, manager):
        await manager.write_file_raw("packages/lights.yaml", "light: on")
        assert (manager.config_dir / "packages" / "lights.yaml").exists()

    async def test_path_traversal_in_write(self, manager):
        with pytest.raises(ConfigurationError):
            await manager.write_file_raw("../evil.yaml", "bad")


# ---------------------------------------------------------------------------
# _create_backup
# ---------------------------------------------------------------------------

class TestCreateBackup:
    def test_backup_created_in_backup_dir(self, manager, tmp_path):
        src = manager.config_dir / "config.yaml"
        src.write_text("data", encoding="utf-8")
        backup_path = manager._create_backup(src)
        assert backup_path.parent == manager.backup_dir
        assert backup_path.exists()

    def test_backup_contains_original_content(self, manager):
        src = manager.config_dir / "config.yaml"
        src.write_text("original content", encoding="utf-8")
        backup_path = manager._create_backup(src)
        assert backup_path.read_text() == "original content"

    def test_backup_name_includes_stem(self, manager):
        src = manager.config_dir / "automations.yaml"
        src.write_text("x", encoding="utf-8")
        backup_path = manager._create_backup(src)
        assert backup_path.name.startswith("automations_")

    def test_backup_nonexistent_file_raises(self, manager):
        ghost = manager.config_dir / "ghost.yaml"
        with pytest.raises(ConfigurationError):
            manager._create_backup(ghost)


# ---------------------------------------------------------------------------
# _rotate_backups
# ---------------------------------------------------------------------------

class TestRotateBackups:
    _counter: int = 0

    def _make_backup(self, manager, stem: str, content: str = "x") -> Path:
        """Create a fake backup file with a unique name and a slight mtime delay."""
        TestRotateBackups._counter += 1
        n = TestRotateBackups._counter
        name = f"{stem}_20240101_{n:06d}.yaml.backup"
        p = manager.backup_dir / name
        p.write_text(content)
        time.sleep(0.02)  # ensure mtime ordering is stable
        return p

    def test_keeps_max_backups(self, manager):
        for i in range(5):
            self._make_backup(manager, "config")
        manager._rotate_backups("config")
        remaining = list(manager.backup_dir.glob("config_*.backup"))
        assert len(remaining) == 3  # max_backups=3

    def test_deletes_oldest_first(self, manager):
        paths = [self._make_backup(manager, "config", content=str(i)) for i in range(5)]
        manager._rotate_backups("config")
        remaining = sorted(manager.backup_dir.glob("config_*.backup"), key=lambda p: p.stat().st_mtime)
        # The 2 oldest should be gone
        for old in paths[:2]:
            assert not old.exists()
        # The 3 newest should survive
        for new in paths[2:]:
            assert new.exists()

    def test_does_not_touch_other_file_backups(self, manager):
        for i in range(5):
            self._make_backup(manager, "config")
        other = self._make_backup(manager, "automations")
        manager._rotate_backups("config")
        assert other.exists()


# ---------------------------------------------------------------------------
# list_backups
# ---------------------------------------------------------------------------

class TestListBackups:
    async def test_lists_all_backups(self, manager):
        (manager.config_dir / "a.yaml").write_text("a", encoding="utf-8")
        (manager.config_dir / "b.yaml").write_text("b", encoding="utf-8")
        await manager.write_file_raw("a.yaml", "a2")
        await manager.write_file_raw("b.yaml", "b2")
        backups = manager.list_backups()
        assert len(backups) == 2

    async def test_filtered_by_file(self, manager):
        (manager.config_dir / "a.yaml").write_text("a", encoding="utf-8")
        (manager.config_dir / "b.yaml").write_text("b", encoding="utf-8")
        await manager.write_file_raw("a.yaml", "a2")
        await manager.write_file_raw("b.yaml", "b2")
        backups = manager.list_backups("a.yaml")
        assert len(backups) == 1
        assert backups[0]["original_file"] == "a"

    def test_returns_empty_when_no_backups(self, manager):
        assert manager.list_backups() == []

    async def test_backup_dict_has_expected_keys(self, manager):
        (manager.config_dir / "config.yaml").write_text("x", encoding="utf-8")
        await manager.write_file_raw("config.yaml", "y")
        backups = manager.list_backups()
        assert set(backups[0].keys()) == {"name", "original_file", "timestamp", "size"}


# ---------------------------------------------------------------------------
# restore_backup
# ---------------------------------------------------------------------------

class TestRestoreBackup:
    async def test_restores_file_content(self, manager):
        (manager.config_dir / "config.yaml").write_text("original", encoding="utf-8")
        await manager.write_file_raw("config.yaml", "modified")
        backup_name = manager.list_backups()[0]["name"]
        await manager.restore_backup(backup_name)
        assert (manager.config_dir / "config.yaml").read_text() == "original"

    async def test_restore_nonexistent_backup_raises(self, manager):
        with pytest.raises(ConfigurationError, match="not found"):
            await manager.restore_backup("phantom_20200101_000000.yaml.backup")
