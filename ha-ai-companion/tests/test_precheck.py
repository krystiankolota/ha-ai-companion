"""Pre-approval HA config check: temp write, check, always restore."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.config.manager import ConfigurationManager, ValidationError, _problem_key


def make_cm(tmp_path, monkeypatch, checker):
    """checker(cm) -> warnings str / None, or raises ValidationError; sees the temp-written disk."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
    cm = ConfigurationManager(str(tmp_path), str(tmp_path / "bk"))

    async def fake_validate():
        return checker(cm)
    cm.validate_config = fake_validate
    return cm


def test_problem_key_ignores_line_numbers():
    assert _problem_key("Invalid config at x.yaml, line 12: bad") == _problem_key("Invalid config at x.yaml, line 40: bad")


def test_skipped_without_backend(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    cm = ConfigurationManager(str(tmp_path), str(tmp_path / "bk"))
    (tmp_path / "a.yaml").write_text("x: 1\n", encoding="utf-8")
    assert asyncio.run(cm.precheck_files([("a.yaml", "x: 2\n")])) is None
    assert (tmp_path / "a.yaml").read_text(encoding="utf-8") == "x: 1\n"


def test_reports_only_new_problems_and_restores(tmp_path, monkeypatch):
    (tmp_path / "a.yaml").write_text("ok: 1\n", encoding="utf-8")

    def checker(cm):
        lines = ["old problem, line 3"]
        if "bad" in (tmp_path / "a.yaml").read_text(encoding="utf-8"):
            lines += ["old problem, line 9", "Invalid option 'state_characteristics', line 5"]
        return "\n".join(lines)
    cm = make_cm(tmp_path, monkeypatch, checker)
    problems = asyncio.run(cm.precheck_files([("a.yaml", "bad: 1\n")]))
    assert problems == "Invalid option 'state_characteristics', line 5"
    assert (tmp_path / "a.yaml").read_text(encoding="utf-8") == "ok: 1\n"
    assert not list((tmp_path / "bk").glob("*"))


def test_clean_proposal_returns_none(tmp_path, monkeypatch):
    (tmp_path / "a.yaml").write_text("ok: 1\n", encoding="utf-8")
    cm = make_cm(tmp_path, monkeypatch, lambda cm: None)
    assert asyncio.run(cm.precheck_files([("a.yaml", "ok: 2\n")])) is None


def test_invalid_config_error_is_reported_and_new_file_removed(tmp_path, monkeypatch):
    def checker(cm):
        if (tmp_path / "packages" / "new.yaml").exists():
            raise ValidationError("Configuration invalid: expected a dictionary")
        return None
    (tmp_path / "packages").mkdir()
    cm = make_cm(tmp_path, monkeypatch, checker)
    problems = asyncio.run(cm.precheck_files([("packages/new.yaml", "sensor: x\n")]))
    assert "expected a dictionary" in problems
    assert not (tmp_path / "packages" / "new.yaml").exists()


def test_restores_even_when_check_crashes(tmp_path, monkeypatch):
    (tmp_path / "a.yaml").write_text("ok: 1\n", encoding="utf-8")

    def checker(cm):
        if "bad" in (tmp_path / "a.yaml").read_text(encoding="utf-8"):
            raise RuntimeError("supervisor down")
        return None
    cm = make_cm(tmp_path, monkeypatch, checker)
    with pytest.raises(RuntimeError):
        asyncio.run(cm.precheck_files([("a.yaml", "bad: 1\n")]))
    assert (tmp_path / "a.yaml").read_text(encoding="utf-8") == "ok: 1\n"


def test_does_not_overwrite_file_changed_during_check(tmp_path, monkeypatch):
    (tmp_path / "a.yaml").write_text("ok: 1\n", encoding="utf-8")

    def checker(cm):
        (tmp_path / "a.yaml").write_text("user edit\n", encoding="utf-8")
        return None
    cm = make_cm(tmp_path, monkeypatch, checker)
    asyncio.run(cm.precheck_files([("a.yaml", "ok: 2\n")]))
    assert (tmp_path / "a.yaml").read_text(encoding="utf-8") == "user edit\n"


def test_agent_precheck_sends_only_disk_yaml(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from src.agents.agent_system import AgentSystem, Changeset
    seen = []

    async def fake_precheck(files):
        seen.extend(files)
        return "x" * 3000
    agent = AgentSystem.__new__(AgentSystem)
    agent.config_manager = ConfigurationManager(str(tmp_path), str(tmp_path / "bk"))
    agent.config_manager.precheck_files = fake_precheck
    now = datetime.now(timezone.utc)
    agent.pending_changesets = {"cs1": Changeset(
        changeset_id="cs1", created_at=now.isoformat(), expires_at=(now + timedelta(hours=1)).isoformat(),
        file_changes=[
            {"file_path": "packages/a.yaml", "new_content": "a: 1\n"},
            {"file_path": "lovelace/dash.yaml", "new_content": "views: []\n"},
            {"file_path": "nodered/flows.json", "new_content": "[]"},
            {"file_path": "scripts.py", "new_content": ""},
        ])}
    problems = asyncio.run(agent._precheck_changeset("cs1"))
    assert seen == [("packages/a.yaml", "a: 1\n")]
    assert len(problems) == 2000
    assert asyncio.run(agent._precheck_changeset("missing")) is None


def test_crlf_proposal_is_restored(tmp_path, monkeypatch):
    (tmp_path / "a.yaml").write_bytes(b"ok: 1\r\n")
    cm = make_cm(tmp_path, monkeypatch, lambda cm: None)
    asyncio.run(cm.precheck_files([("a.yaml", "proposed: 2\r\n"), ("b.yaml", "new: 1\r\n")]))
    assert (tmp_path / "a.yaml").read_bytes() == b"ok: 1\r\n"
    assert not (tmp_path / "b.yaml").exists()
    assert not (tmp_path / "bk" / "precheck_recovery.json").exists()


def test_one_failed_restore_still_restores_others(tmp_path, monkeypatch):
    (tmp_path / "a.yaml").write_text("a: 1\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("b: 1\n", encoding="utf-8")
    cm = make_cm(tmp_path, monkeypatch, lambda cm: None)
    real = cm._restore_precheck_entry

    def flaky(entry):
        if entry["path"] == "b.yaml":
            raise OSError("disk busy")
        real(entry)
    cm._restore_precheck_entry = flaky
    with pytest.raises(Exception):
        asyncio.run(cm.precheck_files([("a.yaml", "a: 2\n"), ("b.yaml", "b: 2\n")]))
    assert (tmp_path / "a.yaml").read_text(encoding="utf-8") == "a: 1\n"
    assert (tmp_path / "bk" / "precheck_recovery.json").exists()
    # Startup recovery finishes the job.
    ConfigurationManager(str(tmp_path), str(tmp_path / "bk"))
    assert (tmp_path / "b.yaml").read_text(encoding="utf-8") == "b: 1\n"
    assert not (tmp_path / "bk" / "precheck_recovery.json").exists()


def test_startup_recovers_after_crash_mid_check(tmp_path, monkeypatch):
    (tmp_path / "a.yaml").write_text("orig: 1\n", encoding="utf-8")

    class Crash(BaseException):
        pass

    def checker(cm):
        if "proposed" in (tmp_path / "a.yaml").read_text(encoding="utf-8"):
            cm._restore_precheck = lambda manifest: None  # simulate process death: no cleanup
            raise Crash()
        return None
    cm = make_cm(tmp_path, monkeypatch, checker)
    with pytest.raises(Crash):
        asyncio.run(cm.precheck_files([("a.yaml", "proposed: 1\n"), ("new.yaml", "x: 1\n")]))
    assert "proposed" in (tmp_path / "a.yaml").read_text(encoding="utf-8")
    ConfigurationManager(str(tmp_path), str(tmp_path / "bk"))
    assert (tmp_path / "a.yaml").read_text(encoding="utf-8") == "orig: 1\n"
    assert not (tmp_path / "new.yaml").exists()


def test_unresolved_manifest_blocks_next_precheck_and_keeps_only_failures(tmp_path, monkeypatch):
    import json as _json
    (tmp_path / "a.yaml").write_text("a: 1\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("b: 1\n", encoding="utf-8")
    cm = make_cm(tmp_path, monkeypatch, lambda cm: None)
    real = cm._restore_precheck_entry

    def always_fail_b(entry):
        if entry["path"] == "b.yaml":
            raise OSError("disk busy")
        real(entry)
    cm._restore_precheck_entry = always_fail_b
    with pytest.raises(Exception):
        asyncio.run(cm.precheck_files([("a.yaml", "a: 2\n"), ("b.yaml", "b: 2\n")]))
    manifest = _json.loads((tmp_path / "bk" / "precheck_recovery.json").read_text(encoding="utf-8"))
    assert [e["path"] for e in manifest] == ["b.yaml"]
    with pytest.raises(Exception):
        asyncio.run(cm.precheck_files([("a.yaml", "a: 3\n")]))
    assert (tmp_path / "a.yaml").read_text(encoding="utf-8") == "a: 1\n"
    assert (tmp_path / "bk" / "precheck_recovery.json").exists()
