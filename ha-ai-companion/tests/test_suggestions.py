import json
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from src import main
from src.agents.agent_system import (
    _build_suggestion_outlines,
    _detect_naming_issues,
    _is_duplicate_suggestion,
    _parse_suggestion_response,
    _suggestion_states,
)


def test_suggestions_json_write_is_atomic_utf8(tmp_path):
    path = tmp_path / "suggestions.json"

    main._write_suggestions_json(str(path), {"title": "Éclairage"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"title": "Éclairage"}
    assert not list(tmp_path.glob(".suggestions-*.tmp"))

def test_suggestions_json_replace_failure_preserves_existing_file(tmp_path, monkeypatch):
    path = tmp_path / "suggestions.json"
    path.write_text('{"title": "Old"}', encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr(main.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        main._write_suggestions_json(str(path), {"title": "New"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"title": "Old"}
    assert not list(tmp_path.glob(".suggestions-*.tmp"))


def test_restore_suggestion_removes_title_and_returns_404_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("HA_CONFIG_DIR", str(tmp_path))

    @asynccontextmanager
    async def empty_lifespan(app):
        yield

    monkeypatch.setattr(main.app.router, "lifespan_context", empty_lifespan)
    main._write_dismissed(["Éclairage", "Night lights"])
    with TestClient(main.app) as client:
        restored = client.post("/api/suggestions/restore", json={"title": "Éclairage"})
        assert restored.status_code == 200
        assert restored.json() == {"dismissed": ["Night lights"]}
        missing = client.post("/api/suggestions/restore", json={"title": "Éclairage"})
        assert missing.status_code == 404


@pytest.mark.parametrize("raw", [
    'Here are the results: [{"title":"Kitchen lights"}] Thanks!',
    '```json\n{"suggestions":[{"title":"Kitchen lights"}]}\n```',
])
def test_suggestion_response_parser_extracts_json(raw):
    parsed = _parse_suggestion_response(raw)
    if isinstance(parsed, dict):
        parsed = parsed["suggestions"]
    assert parsed == [{"title": "Kitchen lights"}]


def test_suggestion_response_parser_rejects_unparseable_content():
    with pytest.raises(ValueError, match="invalid JSON"):
        _parse_suggestion_response("No structured result")


def test_duplicate_suggestion_matches_normalized_existing_alias():
    assert _is_duplicate_suggestion(
        {"title": "Kitchen evening lights", "entities": ["light.kitchen"]},
        ["Kitchen Evening Lights"],
    )
    assert not _is_duplicate_suggestion(
        {"title": "Bedroom motion lighting", "entities": ["light.bedroom"]},
        ["Kitchen Evening Lights"],
    )


def test_suggestion_outlines_are_compact_and_extract_aliases():
    outlines, aliases = _build_suggestion_outlines({
        "automations": [{
            "path": "automations.yaml",
            "content": "- id: kitchen\n  alias: Kitchen lights\n  triggers:\n    - trigger: state\n      entity_id: sensor.kitchen\n  actions:\n    - action: light.turn_on\n      target:\n        entity_id: light.kitchen\n",
        }],
        "scenes": [{
            "path": "scenes.yaml",
            "content": "kitchen_evening:\n  name: Kitchen evening\n  entities:\n    light.kitchen: 'on'\n",
        }],
        "scripts": [{
            "path": "scripts.yaml",
            "content": "night_mode:\n  alias: Night mode\n  sequence:\n    - action: light.turn_off\n      target:\n        entity_id: light.living_room\n",
        }],
    })

    assert aliases == ["Kitchen lights"]
    assert "kitchen | Kitchen lights | trigger: state+sensor.kitchen | action: light.turn_on+light.kitchen" in outlines["automations"]
    assert "Kitchen evening | entities: light.kitchen" in outlines["scenes"]
    assert "Night mode | action: light.turn_off+light.living_room" in outlines["scripts"]
    assert len("".join(outlines.values())) <= 20000


def test_suggestion_outlines_obey_shared_character_budget():
    outlines, _ = _build_suggestion_outlines({
        "automations": [{
            "path": "automations.yaml",
            "content": "- id: kitchen\n  alias: " + ("Kitchen " * 100) + "\n",
        }],
        "scenes": [{
            "path": "scenes.yaml",
            "content": "scene:\n  name: " + ("Scene " * 100) + "\n",
        }],
    }, max_chars=40)

    assert sum(len(text) for text in outlines.values()) <= 40

def test_detect_naming_issues_flags_only_provably_raw_names():
    def s(eid, name=None):
        return {"entity_id": eid, "attributes": {"friendly_name": name} if name else {}}

    issues = _detect_naming_issues([
        s("sensor.sonoff_1002560190_energy_cost"),
        s("sensor.total_gas_cost_daily", "total_gas_cost_daily"),
        s("switch.tz3000_m8f3z8ju_ts000f", "_TZ3000_m8f3z8ju TS000F"),
        s("sensor.t1", "Czujnik temperatura"),
        s("sensor.t2", "czujnik temperatura"),
        s("light.salon", "Salon lampa"),
        s("automation.raw", "raw_slug"),
    ])
    reasons = {i["entity_id"]: i["reason"] for i in issues}

    assert reasons["sensor.sonoff_1002560190_energy_cost"] == "has no friendly name"
    assert reasons["sensor.total_gas_cost_daily"] == "name is a raw slug"
    assert "Tuya" in reasons["switch.tz3000_m8f3z8ju_ts000f"]
    assert reasons["sensor.t1"] == "same name as sensor.t2"
    assert "light.salon" not in reasons
    assert "automation.raw" not in reasons
    assert all("suggested_name" not in i for i in issues)

def test_suggestion_states_skip_diagnostic_config_hidden_and_plumbing():
    states = [{"entity_id": e} for e in (
        "light.salon", "sensor.rssi", "switch.child_lock", "sensor.hidden", "update.fw", "button.restart", "sensor.temp",
    )]
    registry = [
        {"entity_id": "sensor.rssi", "entity_category": "diagnostic"},
        {"entity_id": "switch.child_lock", "entity_category": "config"},
        {"entity_id": "sensor.hidden", "hidden_by": "user"},
    ]

    kept = [s["entity_id"] for s in _suggestion_states(states, registry)]

    assert kept == ["light.salon", "sensor.temp"]
