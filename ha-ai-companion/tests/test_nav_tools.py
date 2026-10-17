"""Navigation + automation tools (NavigationToolsMixin) against a temp config dir."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest

from src.agents.nav_tools import format_ha_health, summarize_trace
from src.agents.tools import AgentTools, _large_structured_content_error
from src.config.manager import ConfigurationManager

AUTOS = """\
- id: '1'
  alias: Morning light
  triggers: []
  actions:
    - action: light.turn_on
      target:
        entity_id: light.kitchen
- id: '2'
  alias: Evening light
  triggers: []
  actions: []
"""

STATES = [
    {"entity_id": "automation.morning_light", "state": "on",
     "attributes": {"id": "1", "friendly_name": "Morning light", "last_triggered": None}},
    {"entity_id": "automation.evening_light", "state": "off",
     "attributes": {"id": "2", "friendly_name": "Evening light", "last_triggered": "2026-10-17T06:00:00+00:00"}},
    {"entity_id": "automation.ui_only", "state": "on", "attributes": {"id": "9", "friendly_name": "UI only"}},
    {"entity_id": "light.kitchen", "state": "on", "attributes": {"friendly_name": "Kitchen Ceiling"}},
    {"entity_id": "light.bedroom", "state": "off", "attributes": {"friendly_name": "Bedroom Lamp"}},
    {"entity_id": "sensor.kitchen_temp", "state": "21", "attributes": {"friendly_name": "Kitchen Temperature"}},
]


@pytest.fixture
def tools(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    (tmp_path / "configuration.yaml").write_text(
        "automation: !include automations.yaml\nautomation split: !include_dir_merge_list automations\n",
        encoding="utf-8")
    (tmp_path / "automations.yaml").write_text(AUTOS, encoding="utf-8")
    (tmp_path / "automations").mkdir()
    (tmp_path / "automations" / "heat.yaml").write_text("- id: h1\n  alias: Heat\n", encoding="utf-8")
    (tmp_path / "secrets.yaml").write_text("pw: x\n", encoding="utf-8")

    t = AgentTools.__new__(AgentTools)
    t.config_manager = ConfigurationManager(str(tmp_path), str(tmp_path / "bk"))
    t._turn_registry_cache = {}
    stored = {}

    class FakeAgent:
        def store_changeset(self, data):
            stored["cs"] = data
            return data["changeset_id"]

    t.agent_system = FakeAgent()
    t.stored = stored
    t.ws_calls = []

    async def all_states():
        return STATES

    async def ws_call(msg_type, **kw):
        t.ws_calls.append((msg_type, kw))
        if msg_type == "automation/config":
            return {"config": {"id": "9", "alias": "UI only", "triggers": []}}
        if msg_type == "trace/list":
            return [{"run_id": "r1", "timestamp": {"start": "2026-10-17T06:00:00+00:00"}, "state": "stopped",
                     "script_execution": "error", "last_step": "action/0", "trigger": "time", "error": "boom"}]
        if msg_type == "trace/get":
            return {"run_id": "r1", "trace": {"action/0": [{"path": "action/0", "error": "boom",
                                                            "changed_variables": {"huge": "x" * 1000}}]}}
        raise AssertionError(msg_type)

    async def no_warnings(_yaml):
        return []

    async def empty():
        return []

    t._all_states = all_states
    t._ws_call = ws_call
    t._validate_entity_ids_in_yaml = no_warnings
    t._get_all_entities = t._get_all_devices = t._get_all_areas = empty
    return t


def run(coro):
    return asyncio.run(coro)


def test_list_automations_maps_ids_to_files(tools):
    r = run(tools.list_automations())
    lines = r["automations"].splitlines()
    assert any(l.startswith("2 | Evening light | off | last: 2026-10-17") and "automations.yaml:8-" in l for l in lines)
    assert any(l.startswith("9 | UI only") and "location unknown" in l for l in lines)
    assert any(l.startswith("h1 | Heat | NOT LOADED") for l in lines)
    assert run(tools.list_automations(query="evening"))["count"] == 1


def test_get_automation_returns_exact_block(tools):
    r = run(tools.get_automation("automation.evening_light"))
    assert r["file_path"] == "automations.yaml" and r["yaml"].startswith("- id: '2'\n  alias: Evening light")
    assert run(tools.get_automation("Morning light"))["id"] == "1"


def test_get_automation_falls_back_to_ha_config(tools):
    r = run(tools.get_automation("9"))
    assert r["success"] and "alias: UI only" in r["yaml"] and "Not found" in r["note"]


def test_upsert_replaces_in_place_and_stages_changeset(tools):
    r = run(tools.upsert_automation("id: '2'\nalias: Evening light v2\ntriggers: []\nactions: []\n"))
    assert r["success"], r
    fc = tools.stored["cs"]["file_changes"][0]
    assert fc["file_path"] == "automations.yaml"
    assert fc["original_content"] == AUTOS
    assert "alias: Evening light v2" in fc["new_content"] and "Morning light" in fc["new_content"]
    assert r["diff_stats"][0]["items"]["changed"] == ["Evening light v2 (2)"]


def test_upsert_new_goes_to_default_file_with_generated_id(tools):
    r = run(tools.upsert_automation("alias: New one\ntriggers: []\nactions: []\n"))
    assert r["success"], r
    assert tools.stored["cs"]["file_changes"][0]["file_path"] == "automations.yaml"
    assert r["diff_stats"][0]["items"]["added"][0].startswith("New one (")


def test_upsert_existing_split_file_item_edits_that_file(tools):
    r = run(tools.upsert_automation("id: h1\nalias: Heat v2\n"))
    assert r["success"] and tools.stored["cs"]["file_changes"][0]["file_path"] == "automations/heat.yaml"


def test_upsert_rejects_non_automation_target(tools):
    r = run(tools.upsert_automation("alias: X\n", file_path="configuration.yaml"))
    assert not r["success"] and "not an automation list file" in r["error"]
    r = run(tools.upsert_automation("alias: X\n", file_path="automations/../secrets.yaml"))
    assert not r["success"]


def test_upsert_allows_new_file_in_merge_list_dir(tools):
    r = run(tools.upsert_automation("alias: X\n", file_path="automations/new.yaml"))
    assert r["success"], r
    assert r["diff_stats"][0]["is_new_file"]


def test_remove_automation(tools):
    r = run(tools.remove_automation("automation.morning_light"))
    assert r["success"], r
    new = tools.stored["cs"]["file_changes"][0]["new_content"]
    assert "Morning light" not in new and new.startswith("- id: '2'")


def test_read_range_and_get_automation_redact_secrets(tools, tmp_path):
    (tmp_path / "automations.yaml").write_text(AUTOS + "- id: '3'\n  alias: Secretive\n  password: hunter2\n",
                                               encoding="utf-8")
    assert "hunter2" not in run(tools.read_range("automations.yaml", 1, 40))["content"]
    assert "hunter2" not in run(tools.get_automation("3"))["yaml"]


def test_ambiguous_alias_is_not_resolved(tools, tmp_path):
    (tmp_path / "automations.yaml").write_text("- alias: Dup\n  triggers: []\n- alias: Dup\n  triggers: []\n",
                                               encoding="utf-8")
    r = run(tools.remove_automation("Dup"))
    assert not r["success"] and "unique" in r["error"]


def test_script_and_scene_items_read_and_stage_one_item(tools, tmp_path):
    (tmp_path / "configuration.yaml").write_text(
        "automation: !include automations.yaml\nscript: !include scripts.yaml\nscene: !include scenes.yaml\n",
        encoding="utf-8")
    scripts = ("script.one:\n  alias: First\n  sequence: []\n"
               "# Keep this comment\nscript.two:\n  alias: Second\n  sequence: []\n")
    scenes = ("- id: one\n  name: First\n  entities: {}\n"
              "# Keep scene comment\n- id: two\n  name: Second\n  entities: {}\n")
    (tmp_path / "scripts.yaml").write_text(scripts, encoding="utf-8")
    (tmp_path / "scenes.yaml").write_text(scenes, encoding="utf-8")

    got = run(tools.get_automation("First", kind="script"))
    assert got["success"] and got["id"] == "script.one" and "alias: First" in got["yaml"]
    got = run(tools.get_automation("two", kind="scene"))
    assert got["success"] and got["id"] == "two" and "name: Second" in got["yaml"]

    staged = run(tools.upsert_automation(
        "id: script.one\nalias: First v2\nsequence: []\n", kind="script"))
    assert staged["success"], staged
    staged_text = tools.stored["cs"]["file_changes"][0]["new_content"]
    assert "script.one:\n  alias: First v2\n  sequence: []\n" in staged_text
    assert staged_text.endswith("# Keep this comment\nscript.two:\n  alias: Second\n  sequence: []\n")

    staged = run(tools.upsert_automation(
        "id: one\nname: First v2\nentities: {}\n", kind="scene"))
    assert staged["success"], staged
    staged_text = tools.stored["cs"]["file_changes"][0]["new_content"]
    assert staged_text.startswith("- id: one\n  name: First v2")
    assert staged_text.endswith("# Keep scene comment\n- id: two\n  name: Second\n  entities: {}\n")


def test_full_content_guard_for_large_structured_file(tools, tmp_path):
    content = "alias: needle\n" + ("# large\n" * 2200)
    assert _large_structured_content_error("automations.yaml", content)
    assert _large_structured_content_error("automations.yaml", content, force=True) is None
    (tmp_path / "automations.yaml").write_text(content, encoding="utf-8")
    blocked = run(tools.search_config_files(search_pattern="needle", full_content=True))
    assert not blocked["success"] and "read_range" in blocked["error"] and "force=true" in blocked["error"]
    allowed = run(tools.search_config_files(search_pattern="needle", full_content=True, force=True))
    assert allowed["success"] and len(allowed["files"][0]["content"]) > 12000


def test_health_summary_formats_repairs_failures_updates_and_grouped_logs():
    report = format_ha_health(
        [{"issue_id": "active", "title": "Fix me"}, {"issue_id": "ignored", "ignored": True}],
        [{"domain": "mqtt", "title": "MQTT", "state": "setup_error", "reason": "bad config"},
         {"domain": "light", "state": "loaded"}],
        [{"entity_id": "update.core", "state": "on",
          "attributes": {"friendly_name": "Core", "installed_version": "1", "latest_version": "2"}}],
        [{"level": "ERROR", "name": "mqtt", "message": ["Failed to connect"], "count": 2},
         {"level": "ERROR", "name": "mqtt", "message": ["Failed to connect"], "count": 3}],
    )
    assert [i["issue_id"] for i in report["repairs"]] == ["active"]
    assert report["failed_config_entries"][0]["domain"] == "mqtt"
    assert report["pending_updates"][0]["latest"] == "2"
    assert report["logs"] == [{"level": "ERROR", "source": "mqtt", "count": 5,
                              "message": "Failed to connect"}]


def test_get_ha_health_uses_fake_websocket_data():
    t = AgentTools.__new__(AgentTools)
    t.config_manager = type("Config", (), {"hass": None})()
    t._turn_registry_cache = {}

    async def ws_call(command, **kwargs):
        return {
            "repairs/list_issues": {"issues": [{"issue_id": "repair", "title": "Repair"}]},
            "config_entries/get": {"config_entries": [{"domain": "mqtt", "state": "setup_error"}]},
            "system_log/list": [{"level": "WARNING", "name": "mqtt", "message": ["Warn"], "count": 2}],
        }[command]

    async def states():
        return [{"entity_id": "update.core", "state": "on",
                 "attributes": {"friendly_name": "Core", "latest_version": "2"}}]

    t._ws_call = ws_call
    t._all_states = states
    result = run(t.get_ha_health())
    assert result["success"] and result["repairs"][0]["issue_id"] == "repair"
    assert result["failed_config_entries"][0]["domain"] == "mqtt"
    assert result["pending_updates"][0]["name"] == "Core"


def test_trace_summary_keeps_step_results_and_drops_variable_payloads():
    result = summarize_trace({
        "run_id": "r1", "trigger": "time", "error": "boom",
        "trace": {"action/0": [{"result": {"done": True, "params": {"huge": "x"},
                                             "conditions": [True]},
                                "changed_variables": {"secret": "large"}}]},
    })
    assert result["trigger"] == "time" and result["error"] == "boom"
    assert result["steps"][0]["result"] == {"done": True, "conditions": [True]}
    assert "changed_variables" not in result["steps"][0]


def test_single_item_dir_files_are_indexed_read_only(tmp_path, monkeypatch, tools):
    (tmp_path / "configuration.yaml").write_text(
        "automation: !include automations.yaml\nautomation one: !include_dir_list single\n", encoding="utf-8")
    (tmp_path / "single").mkdir()
    (tmp_path / "single" / "s.yaml").write_text("id: s1\nalias: Single\ntriggers: []\n", encoding="utf-8")
    r = run(tools.upsert_automation("id: s1\nalias: Single v2\n"))
    assert not r["success"] and "single-item" in r["error"]
    assert not run(tools.remove_automation("s1"))["success"]


def test_outline_small_file_returns_content_and_blocks_secrets(tools):
    r = run(tools.outline_file("automations.yaml"))
    assert r["content"].rstrip() == AUTOS.rstrip()
    assert not run(tools.outline_file("secrets.yaml"))["success"]
    assert not run(tools.read_range("secrets.yaml", 1, 2))["success"]


def test_outline_large_file_returns_items(tools):
    big = "".join(f"- id: 'a{i}'\n  alias: A{i}\n  triggers: []\n  actions: []\n" for i in range(30))
    (tools.config_manager.config_dir / "automations.yaml").write_text(big, encoding="utf-8")
    r = run(tools.outline_file("automations.yaml"))
    assert "content" not in r and len(r["items"]) == 30 and r["items"][1] == {"lines": "5-8", "item": "A1 (id=a1)"}
    rr = run(tools.read_range("automations.yaml", 5, 6))
    assert rr["content"] == "5: - id: 'a1'\n6:   alias: A1"


def test_search_entities_all_terms_and_domain(tools):
    r = run(tools.search_entities("kitchen"))
    assert r["count"] == 2
    r = run(tools.search_entities("kitchen", domain="light"))
    assert r["entities"] == '"Kitchen Ceiling"[light.kitchen]=on'
    assert run(tools.search_entities("kitchen lamp"))["count"] == 0


def test_traces_are_compact(tools):
    r = run(tools.get_automation_traces("Evening light"))
    assert r["success"] and r["runs"][0]["error"] == "boom"
    assert r["detail"]["steps"] == [{"path": "action/0", "error": "boom"}]
    assert ("trace/list", {"domain": "automation", "item_id": "2"}) in tools.ws_calls


def test_summarize_trace_drops_variables():
    s = summarize_trace({"trace": {"condition/0": [{"path": "condition/0", "result": {"result": False, "entities": ["x"]}}]}})
    assert s["steps"] == [{"path": "condition/0", "result": {"result": False}}]


def test_nav_schemas_match_dispatch_and_tools():
    import inspect
    from src.agents.agent_system import AgentSystem
    names = {s["function"]["name"] for s in AgentSystem._nav_tool_schemas()}
    assert names == set(AgentSystem._NAV_TOOLS)
    for n in names:
        assert asyncio.iscoroutinefunction(getattr(AgentTools, n)), n
    for s in AgentSystem._nav_tool_schemas():
        f = s["function"]
        sig = set(inspect.signature(getattr(AgentTools, f["name"])).parameters) - {"self"}
        assert set(f["parameters"]["properties"]) == sig, f["name"]


def test_get_entity_states_query_falls_back_to_lexical_not_full_dump(tmp_path, monkeypatch):
    import src.ha.ha_websocket as hws

    class FakeWS:
        def __init__(self, *a, **k): pass
        async def connect(self): pass
        async def close(self): pass
        async def get_states(self):
            return [{"entity_id": f"sensor.s{i}", "state": "1", "attributes": {"friendly_name": f"Sensor {i}"}}
                    for i in range(200)] + [{"entity_id": "light.salon", "state": "on",
                                             "attributes": {"friendly_name": "Salon lamp"}}]

    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
    monkeypatch.setattr(hws, "HomeAssistantWebSocket", FakeWS)
    async def no_cache(_):
        return False

    async def run():
        t = AgentTools(ConfigurationManager(config_dir=str(tmp_path), backup_dir=str(tmp_path / "bk")))
        monkeypatch.setattr(t, "_ensure_entity_cache", no_cache)
        return await t.get_entity_states(query="salon")
    r = asyncio.run(run())
    assert r["success"] and r["count"] == 1 and r["total_entities"] == 201


def test_search_config_files_without_pattern_outlines_big_files(tmp_path):
    (tmp_path / "small.yaml").write_text("a: 1\n")
    big = "".join(f"- id: '{i}'\n  alias: A{i}\n  actions: []\n" for i in range(30))
    (tmp_path / "automations.yaml").write_text(big)

    async def run():
        t = AgentTools(ConfigurationManager(config_dir=str(tmp_path), backup_dir=str(tmp_path / "bk")))
        return await t.search_config_files()
    files = {f["path"]: f for f in asyncio.run(run())["files"]}
    assert files["small.yaml"]["content"].strip() == "a: 1"
    assert files["automations.yaml"]["content"] is None and len(files["automations.yaml"]["outline"]) == 30


def test_script_upsert_into_empty_mapping_keeps_secret_tags(tools, tmp_path):
    (tmp_path / "configuration.yaml").write_text("script: !include scripts.yaml\n", encoding="utf-8")
    (tmp_path / "scripts.yaml").write_text("{}\n", encoding="utf-8")
    staged = run(tools.upsert_automation(
        "notify_me:\n  alias: Notify\n  sequence:\n    - action: rest_command.x\n      data:\n        token: !secret tok\n",
        kind="script"))
    assert staged["success"], staged
    text = tools.stored["cs"]["file_changes"][0]["new_content"]
    assert "{}" not in text
    assert "token: !secret " in text
    from src.ha.yaml_blocks import load_tolerant
    parsed = load_tolerant(text)["notify_me"]
    assert parsed["sequence"][0]["data"]["token"] == "!secret tok"
