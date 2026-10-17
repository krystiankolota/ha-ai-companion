"""Server-side changeset diffs and the apply-time conflict check."""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.agents.diffing import content_hash, describe_change, is_disk_file, unified_diff
from src.agents.agent_system import AgentSystem, Changeset
from src.agents.tools import AgentTools
from src.config.manager import ConfigurationManager

AUTOS = """\
- id: '1'
  alias: Morning light
  trigger: []
- id: '2'
  alias: Evening light
  trigger: []
"""


def test_crlf_only_difference_is_no_diff():
    assert unified_diff("a.yaml", "x: 1\r\ny: 2\r\n", "x: 1\ny: 2") == ""


def test_json_compact_vs_pretty_is_no_diff():
    assert unified_diff("nodered/flow/t.json", '[{"id":"a","x":1}]', '[\n  {"id": "a", "x": 1}\n]') == ""


def test_diff_is_minimal_hunk_not_full_file():
    old = "".join(f"line{i}\n" for i in range(100))
    new = old.replace("line50\n", "changed\n")
    info = describe_change("big.yaml", old, new)
    assert info["added"] == 1 and info["removed"] == 1
    assert "line10\n" not in info["diff"]  # outside the context window


def test_new_file_marked_and_from_dev_null():
    info = describe_change("new.yaml", "", "a: 1\n")
    assert info["is_new_file"]
    assert info["diff"].startswith("--- /dev/null")


def test_automation_item_summary_by_id():
    new = AUTOS.replace("trigger: []\n- id: '2'", "trigger: [x]\n- id: '2'").replace(
        "- id: '2'\n  alias: Evening light\n  trigger: []\n", "- id: '3'\n  alias: Night\n  trigger: []\n")
    items = describe_change("automations.yaml", AUTOS, new)["items"]
    assert items == {
        "added": ["Night (3)"],
        "changed": ["Morning light (1)"],
        "removed": ["Evening light (2)"],
    }


def test_reordered_keys_inside_item_count_as_unchanged_items():
    reordered = AUTOS.replace("- id: '1'\n  alias: Morning light", "- alias: Morning light\n  id: '1'")
    assert describe_change("automations.yaml", AUTOS, reordered)["items"] is None


def test_virtual_paths_have_no_disk_hash():
    assert is_disk_file("automations.yaml")
    assert not is_disk_file("lovelace.yaml")
    assert not is_disk_file("nodered/flow/abc.json")
    assert not is_disk_file("entities/light.x.json")


def test_patch_tool_stores_original_and_hash():
    stored = {}

    class FakeAgent:
        def store_changeset(self, data):
            stored.update(data)
            return data["changeset_id"]

    tools = AgentTools.__new__(AgentTools)
    tools.agent_system = FakeAgent()

    async def read_file_raw(path, allow_missing=True):
        return AUTOS

    tools.config_manager = type("CM", (), {"read_file_raw": staticmethod(read_file_raw)})()
    result = asyncio.run(tools.patch_config_key("automations.yaml", "[1].alias", "Late light"))
    assert result["success"], result
    fc = stored["file_changes"][0]
    assert fc["original_content"] == AUTOS
    assert fc["base_hash"] == content_hash(AUTOS)
    assert result["diff_stats"][0]["items"]["changed"] == ["Late light (2)"]


def _agent_with(tmp_path, file_changes):
    cm = ConfigurationManager(str(tmp_path), str(tmp_path / "bk"))
    agent = AgentSystem.__new__(AgentSystem)
    agent.config_manager = cm
    now = datetime.now(timezone.utc)
    agent.pending_changesets = {"cs1": Changeset(
        changeset_id="cs1", file_changes=file_changes,
        created_at=now.isoformat(), expires_at=(now + timedelta(hours=1)).isoformat(),
    )}
    return agent


def test_apply_refuses_when_file_changed_since_proposal(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    (tmp_path / "automations.yaml").write_text("edited: by user\n", encoding="utf-8")
    agent = _agent_with(tmp_path, [{
        "file_path": "automations.yaml", "new_content": "x: 1\n",
        "original_content": AUTOS, "base_hash": content_hash(AUTOS),
    }])
    result = asyncio.run(agent.process_approval("cs1", True, validate=False))
    assert result["applied"] is False and result["conflict"] is True
    assert (tmp_path / "automations.yaml").read_text(encoding="utf-8") == "edited: by user\n"


def test_apply_writes_when_file_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    (tmp_path / "automations.yaml").write_text(AUTOS, encoding="utf-8")
    agent = _agent_with(tmp_path, [{
        "file_path": "automations.yaml", "new_content": "x: 1\n",
        "original_content": AUTOS, "base_hash": content_hash(AUTOS),
    }])
    result = asyncio.run(agent.process_approval("cs1", True, validate=False))
    assert result["applied"] is True, result
    assert (tmp_path / "automations.yaml").read_text(encoding="utf-8") == "x: 1\n"


def test_new_file_proposal_conflicts_if_file_appeared(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    (tmp_path / "new.yaml").write_text("someone: else\n", encoding="utf-8")
    agent = _agent_with(tmp_path, [{
        "file_path": "new.yaml", "new_content": "a: 1\n",
        "original_content": "", "base_hash": content_hash(""),
    }])
    assert asyncio.run(agent.process_approval("cs1", True, validate=False))["conflict"] is True

def test_apply_rechecks_disk_file_after_slow_nodered_deploy(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    target = tmp_path / "automations.yaml"
    target.write_text(AUTOS, encoding="utf-8")
    agent = _agent_with(tmp_path, [
        {"file_path": "nodered/flow/t1.json", "new_content": "[]", "original_content": "[]"},
        {"file_path": "automations.yaml", "new_content": "x: 1\n",
         "original_content": AUTOS, "base_hash": content_hash(AUTOS)},
    ])

    class FakeTools:
        _lovelace_cache = {}

        async def deploy_nodered_flows(self, *a, **k):
            target.write_text("edited: during deploy\n", encoding="utf-8")
            return {"success": True}

    agent.tools = FakeTools()
    result = asyncio.run(agent.process_approval("cs1", True, validate=False))
    assert target.read_text(encoding="utf-8") == "edited: during deploy\n"
    assert any(f["file_path"] == "automations.yaml" for f in result["failed_files"]), result
