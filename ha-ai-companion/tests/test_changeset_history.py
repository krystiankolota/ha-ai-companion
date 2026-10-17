"""Applied-changeset history and restore round-trip."""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.agents.agent_system import AgentSystem
from src.agents.changeset_history import ChangesetHistory
from src.agents.tools import AgentTools
from src.config.manager import ConfigurationManager

BEFORE = "- id: '1'\n  alias: Morning\n  trigger: []\n"
AFTER = "- id: '1'\n  alias: Morning\n  trigger: [x]\n- id: '2'\n  alias: Night\n  trigger: []\n"


def make_agent(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    cm = ConfigurationManager(str(tmp_path), str(tmp_path / "bk"))
    agent = AgentSystem.__new__(AgentSystem)
    agent.config_manager = cm
    agent.pending_changesets = {}
    agent.changeset_history = ChangesetHistory(str(tmp_path / ".ai_agent_changesets"))
    tools = AgentTools.__new__(AgentTools)
    tools.config_manager = cm
    tools.agent_system = agent
    agent.tools = tools
    return agent, tools


def propose(tools, path, old, new):
    cid, _ = tools._store_changeset_with_diffs("c" + str(len(tools.agent_system.pending_changesets)), [(path, old, new)])
    return cid


def test_apply_then_restore_round_trip(tmp_path, monkeypatch):
    agent, tools = make_agent(tmp_path, monkeypatch)
    f = tmp_path / "automations.yaml"
    f.write_text(BEFORE, encoding="utf-8")

    cid = propose(tools, "automations.yaml", BEFORE, AFTER)
    assert asyncio.run(agent.process_approval(cid, True, validate=False))["applied"]
    assert f.read_text(encoding="utf-8") == AFTER

    listed = asyncio.run(tools.list_changesets())["changesets"]
    assert listed[0]["id"] == cid
    assert listed[0]["files"][0]["items"] == {"added": ["Night (2)"], "changed": ["Morning (1)"], "removed": []}

    restore = asyncio.run(tools.restore_changeset(cid))
    assert restore["success"], restore
    assert "warnings" not in restore
    assert agent.pending_changesets[restore["changeset_id"]].restored_from == cid
    assert asyncio.run(agent.process_approval(restore["changeset_id"], True, validate=False))["applied"]
    assert f.read_text(encoding="utf-8") == BEFORE
    assert asyncio.run(tools.list_changesets())["changesets"][0]["restored_from"] == cid


def test_restore_warns_when_file_edited_after_apply(tmp_path, monkeypatch):
    agent, tools = make_agent(tmp_path, monkeypatch)
    f = tmp_path / "automations.yaml"
    f.write_text(BEFORE, encoding="utf-8")
    cid = propose(tools, "automations.yaml", BEFORE, AFTER)
    asyncio.run(agent.process_approval(cid, True, validate=False))
    f.write_text(AFTER + "# later edit\n", encoding="utf-8")
    restore = asyncio.run(tools.restore_changeset(cid))
    assert restore["success"] and restore["warnings"]


def test_created_file_is_not_restorable(tmp_path, monkeypatch):
    agent, tools = make_agent(tmp_path, monkeypatch)
    cid = propose(tools, "packages/new.yaml", "", "a: 1\n")
    (tmp_path / "packages").mkdir()
    asyncio.run(agent.process_approval(cid, True, validate=False))
    restore = asyncio.run(tools.restore_changeset(cid))
    assert restore["success"] is False and restore["skipped"]


def test_unknown_id_and_path_traversal_safe(tmp_path, monkeypatch):
    _, tools = make_agent(tmp_path, monkeypatch)
    assert asyncio.run(tools.restore_changeset("../../etc/passwd"))["success"] is False


def test_rejected_changeset_is_not_recorded(tmp_path, monkeypatch):
    agent, tools = make_agent(tmp_path, monkeypatch)
    (tmp_path / "automations.yaml").write_text(BEFORE, encoding="utf-8")
    cid = propose(tools, "automations.yaml", BEFORE, AFTER)
    asyncio.run(agent.process_approval(cid, False))
    assert asyncio.run(tools.list_changesets())["changesets"] == []


def test_prune_keeps_max_records(tmp_path):
    h = ChangesetHistory(str(tmp_path), max_records=3)
    for i in range(5):
        h.record(f"id{i}", [{"file_path": "a.yaml", "original_content": "x: 1\n", "new_content": f"x: {i}\n"}], ["a.yaml"])
    assert len(h.list(10)) == 3
