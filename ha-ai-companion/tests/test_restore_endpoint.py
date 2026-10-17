"""POST /api/changesets/{id}/restore stages a restore through the agent tools."""
import asyncio
import os
import sys
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import src.main as main


def _agent(result):
    async def restore_changeset(cid):
        return {**result, "_cid": cid}
    return NS(tools=NS(restore_changeset=restore_changeset))


def test_restore_endpoint_returns_staged_changeset(monkeypatch):
    monkeypatch.setattr(main, "agent_system", _agent({"success": True, "changeset_id": "new1"}))
    res = asyncio.run(main.restore_applied_changeset("old1"))
    assert res["changeset_id"] == "new1" and res["_cid"] == "old1"


def test_restore_endpoint_maps_failure_to_400(monkeypatch):
    monkeypatch.setattr(main, "agent_system", _agent({"success": False, "error": "Nothing to restore"}))
    with pytest.raises(HTTPException) as e:
        asyncio.run(main.restore_applied_changeset("old1"))
    assert e.value.status_code == 400 and "Nothing" in e.value.detail
