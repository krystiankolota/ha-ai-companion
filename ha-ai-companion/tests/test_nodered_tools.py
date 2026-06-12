"""
Tests for Node-RED tab editing safety: edit_nodered_tab staging guards and
deploy_nodered_flows update_tab metadata preservation.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

import sys, os
# Import via src package so relative imports inside agent_system.py resolve correctly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.agents.tools import AgentTools


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TAB_ID = "7e5d8d64d8660fed"

CURRENT_FLOW = {
    "id": TAB_ID,
    "label": "Kinkiety Garaż",
    "disabled": False,
    "info": "garage sconce flow",
    "env": [],
    "nodes": [
        {"id": "n1", "type": "inject", "z": TAB_ID, "wires": [["n2"]]},
        {"id": "n2", "type": "function", "z": TAB_ID, "wires": [[]]},
    ],
    "configs": [{"id": "cfg1", "type": "some-config", "z": TAB_ID}],
}


def make_tools(api_responses: dict) -> AgentTools:
    """Build an AgentTools instance whose _nodered_api_request is mocked.

    api_responses maps (method, path) -> response dict.
    Every call is recorded on tools.api_calls.
    """
    tools = AgentTools.__new__(AgentTools)
    tools.config_manager = MagicMock()
    tools.memory_manager = None
    tools.conversation_manager = None
    tools.agent_system = None
    tools._lovelace_cache = {}
    tools._turn_cache = {}
    tools.api_calls = []

    async def fake_api(method, path, data=None):
        tools.api_calls.append((method, path, data))
        return api_responses.get((method, path), {"status": 404, "json": None, "text": ""})

    tools._nodered_api_request = fake_api
    return tools


def payload_with_ids(*ids, tab_label="Kinkiety Garaż"):
    nodes = [{"type": "tab", "id": TAB_ID, "label": tab_label}]
    nodes += [{"id": i, "type": "function", "z": TAB_ID, "wires": [[]]} for i in ids]
    return json.dumps(nodes)


# ---------------------------------------------------------------------------
# edit_nodered_tab staging guards
# ---------------------------------------------------------------------------

class TestEditNoderedTabGuards:
    @pytest.mark.asyncio
    async def test_rejects_full_id_rewrite(self):
        tools = make_tools({("GET", f"/flow/{TAB_ID}"): {"status": 200, "json": CURRENT_FLOW}})
        result = await tools.edit_nodered_tab(TAB_ID, payload_with_ids("kg_a", "kg_b"))
        assert result["success"] is False
        assert "REJECTED" in result["error"]

    @pytest.mark.asyncio
    async def test_accepts_partial_id_overlap(self):
        tools = make_tools({("GET", f"/flow/{TAB_ID}"): {"status": 200, "json": CURRENT_FLOW}})
        result = await tools.edit_nodered_tab(TAB_ID, payload_with_ids("n1", "n2", "kg_new"))
        assert result["success"] is True
        assert result["changeset_id"]

    @pytest.mark.asyncio
    async def test_rejects_unknown_tab(self):
        tools = make_tools({})  # GET returns 404
        result = await tools.edit_nodered_tab(TAB_ID, payload_with_ids("n1"))
        assert result["success"] is False
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_guard_skipped_when_api_unreachable(self):
        tools = make_tools({("GET", f"/flow/{TAB_ID}"): {"status": 0, "error": "conn refused"}})
        result = await tools.edit_nodered_tab(TAB_ID, payload_with_ids("kg_a"))
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_rejects_path_traversal_tab_id(self):
        tools = make_tools({})
        for bad in ("../settings", "x/../../auth", "a/b", "id%2F..", "id with space"):
            result = await tools.edit_nodered_tab(bad, payload_with_ids("n1"))
            assert result["success"] is False
            assert "Invalid tab_id" in result["error"]
        assert tools.api_calls == []  # rejected before any API call

    @pytest.mark.asyncio
    async def test_accepts_legacy_dotted_tab_id(self):
        flow = dict(CURRENT_FLOW, id="889d4cbc.12e89")
        tools = make_tools({("GET", "/flow/889d4cbc.12e89"): {"status": 200, "json": flow}})
        nodes = [{"type": "tab", "id": "889d4cbc.12e89", "label": "X"},
                 {"id": "n1", "type": "function", "z": "889d4cbc.12e89", "wires": [[]]}]
        result = await tools.edit_nodered_tab("889d4cbc.12e89", json.dumps(nodes))
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_heals_empty_tab_label(self):
        tools = make_tools({("GET", f"/flow/{TAB_ID}"): {"status": 200, "json": CURRENT_FLOW}})
        result = await tools.edit_nodered_tab(TAB_ID, payload_with_ids("n1", tab_label=""))
        assert result["success"] is True
        # The staged changeset must carry the healed label
        # (agent_system is None so stored_changes only lives in the return path;
        # re-parse the staged file content via the diff stats node count instead)
        # Easiest check: re-run staging with agent_system mock capturing content.
        captured = {}

        class FakeAgentSystem:
            def store_changeset(self, cs):
                captured.update(cs)
                return cs["changeset_id"]

        tools.agent_system = FakeAgentSystem()
        result = await tools.edit_nodered_tab(TAB_ID, payload_with_ids("n1", tab_label=""))
        staged = json.loads(captured["file_changes"][0]["new_content"])
        tab_node = next(n for n in staged if n.get("type") == "tab")
        assert tab_node["label"] == "Kinkiety Garaż"


# ---------------------------------------------------------------------------
# deploy_nodered_flows update_tab metadata preservation
# ---------------------------------------------------------------------------

class TestDeployUpdateTab:
    @pytest.mark.asyncio
    async def test_preserves_label_and_configs_when_payload_omits_them(self, monkeypatch):
        monkeypatch.setenv("NODERED_URL", "http://nr:1880")
        tools = make_tools({
            ("GET", f"/flow/{TAB_ID}"): {"status": 200, "json": CURRENT_FLOW},
            ("PUT", f"/flow/{TAB_ID}"): {"status": 200, "json": {}, "text": ""},
        })
        # No tab node at all — worst case
        flows = json.dumps([{"id": "n1", "type": "inject", "z": TAB_ID, "wires": [[]]}])
        result = await tools.deploy_nodered_flows(flows, mode="update_tab", tab_id=TAB_ID)
        assert result["success"] is True
        put = next(c for c in tools.api_calls if c[0] == "PUT")
        payload = put[2]
        assert payload["label"] == "Kinkiety Garaż"
        assert payload["configs"] == CURRENT_FLOW["configs"]
        assert payload["disabled"] is False
        assert payload["info"] == "garage sconce flow"

    @pytest.mark.asyncio
    async def test_payload_tab_fields_win_over_current(self, monkeypatch):
        monkeypatch.setenv("NODERED_URL", "http://nr:1880")
        tools = make_tools({
            ("GET", f"/flow/{TAB_ID}"): {"status": 200, "json": CURRENT_FLOW},
            ("PUT", f"/flow/{TAB_ID}"): {"status": 200, "json": {}, "text": ""},
        })
        flows = json.dumps([
            {"type": "tab", "id": TAB_ID, "label": "New Label", "disabled": True},
            {"id": "n1", "type": "inject", "z": TAB_ID, "wires": [[]]},
        ])
        result = await tools.deploy_nodered_flows(flows, mode="update_tab", tab_id=TAB_ID)
        assert result["success"] is True
        put = next(c for c in tools.api_calls if c[0] == "PUT")
        payload = put[2]
        assert payload["label"] == "New Label"
        assert payload["disabled"] is True

    @pytest.mark.asyncio
    async def test_deploy_rejects_path_traversal_tab_id(self, monkeypatch):
        monkeypatch.setenv("NODERED_URL", "http://nr:1880")
        tools = make_tools({})
        flows = json.dumps([{"id": "n1", "type": "inject", "wires": [[]]}])
        result = await tools.deploy_nodered_flows(flows, mode="update_tab", tab_id="../settings")
        assert result["success"] is False
        assert "Invalid tab_id" in result["error"]
        assert tools.api_calls == []

    @pytest.mark.asyncio
    async def test_deploy_works_when_current_fetch_fails(self, monkeypatch):
        monkeypatch.setenv("NODERED_URL", "http://nr:1880")
        tools = make_tools({
            ("GET", f"/flow/{TAB_ID}"): {"status": 0, "error": "conn refused"},
            ("PUT", f"/flow/{TAB_ID}"): {"status": 200, "json": {}, "text": ""},
        })
        flows = json.dumps([
            {"type": "tab", "id": TAB_ID, "label": "Kinkiety Garaż"},
            {"id": "n1", "type": "inject", "z": TAB_ID, "wires": [[]]},
        ])
        result = await tools.deploy_nodered_flows(flows, mode="update_tab", tab_id=TAB_ID)
        assert result["success"] is True
        put = next(c for c in tools.api_calls if c[0] == "PUT")
        assert put[2]["label"] == "Kinkiety Garaż"
        assert put[2]["configs"] == []
