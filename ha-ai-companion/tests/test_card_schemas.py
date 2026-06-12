"""
Tests for custom-card schema validation and HA data formatters.

Covers:
- validate_lovelace_cards: bubble-card rules (card_type required,
  horizontal-buttons-stack numbered props, pop-up hash), nested walking,
  unknown cards pass
- format_system_log_entries: ordering, truncation, cap
- format_lovelace_resources: slug derivation
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.agents.card_schemas import (
    validate_lovelace_cards,
    format_system_log_entries,
    format_lovelace_resources,
)


# ---------------------------------------------------------------------------
# validate_lovelace_cards
# ---------------------------------------------------------------------------

def _dashboard(cards):
    return {"title": "T", "views": [{"title": "V", "cards": cards}]}


def test_valid_popup_with_nested_cards_passes():
    config = _dashboard([
        {
            "type": "custom:bubble-card",
            "card_type": "pop-up",
            "hash": "#kitchen",
            "cards": [
                {"type": "custom:bubble-card", "card_type": "button", "entity": "light.k"},
            ],
        },
    ])
    assert validate_lovelace_cards(config) == []


def test_buttons_array_blocked_with_numbered_hint():
    config = _dashboard([
        {
            "type": "custom:bubble-card",
            "card_type": "horizontal-buttons-stack",
            "buttons": [{"name": "Status", "tap_action": {"action": "navigate"}}],
        },
    ])
    violations = validate_lovelace_cards(config)
    assert len(violations) == 1
    assert "1_name" in violations[0]
    assert "buttons" in violations[0]


def test_hbs_without_numbered_props_blocked():
    config = _dashboard([
        {"type": "custom:bubble-card", "card_type": "horizontal-buttons-stack"},
    ])
    violations = validate_lovelace_cards(config)
    assert len(violations) == 1
    assert "numbered" in violations[0].lower() or "1_name" in violations[0]


def test_hbs_with_numbered_props_passes():
    config = _dashboard([
        {
            "type": "custom:bubble-card",
            "card_type": "horizontal-buttons-stack",
            "1_name": "Status", "1_icon": "mdi:shield", "1_link": "#status",
            "2_name": "Lights", "2_link": "#lights",
        },
    ])
    assert validate_lovelace_cards(config) == []


def test_missing_card_type_blocked():
    config = _dashboard([{"type": "custom:bubble-card", "entity": "light.x"}])
    violations = validate_lovelace_cards(config)
    assert len(violations) == 1
    assert "card_type" in violations[0]


def test_popup_without_hash_blocked():
    config = _dashboard([
        {"type": "custom:bubble-card", "card_type": "pop-up", "name": "K"},
    ])
    violations = validate_lovelace_cards(config)
    assert len(violations) == 1
    assert "hash" in violations[0]


def test_unknown_custom_card_passes():
    config = _dashboard([
        {"type": "custom:mushroom-light-card", "weird_key": [1, 2, 3]},
        {"type": "custom:apexcharts-card", "series": []},
    ])
    assert validate_lovelace_cards(config) == []


def test_deep_nesting_is_walked():
    # bubble-card inside vertical-stack inside a sections layout
    config = {
        "views": [{
            "type": "sections",
            "sections": [{
                "type": "grid",
                "cards": [{
                    "type": "vertical-stack",
                    "cards": [{
                        "type": "custom:bubble-card",
                        "card_type": "horizontal-buttons-stack",
                        "buttons": [{"name": "bad"}],
                    }],
                }],
            }],
        }],
    }
    violations = validate_lovelace_cards(config)
    assert len(violations) == 1


def test_non_dict_input_safe():
    assert validate_lovelace_cards(None) == []
    assert validate_lovelace_cards("just a string") == []
    assert validate_lovelace_cards([1, 2, 3]) == []


# ---------------------------------------------------------------------------
# format_system_log_entries
# ---------------------------------------------------------------------------

def test_log_format_orders_errors_first_newest_first():
    entries = [
        {"level": "WARNING", "name": "w1", "message": ["warn"], "timestamp": 300, "count": 1},
        {"level": "ERROR", "name": "e_old", "message": ["old err"], "timestamp": 100, "count": 2},
        {"level": "ERROR", "name": "e_new", "message": ["new err"], "timestamp": 200, "count": 1},
    ]
    lines = format_system_log_entries(entries)
    assert lines[0].startswith("ERROR e_new")
    assert lines[1].startswith("ERROR e_old")
    assert lines[2].startswith("WARNING w1")
    assert "(x2)" in lines[1]


def test_log_format_truncates_multiline_and_caps():
    long_msg = "x" * 500 + "\nsecond line"
    entries = [
        {"level": "ERROR", "name": f"e{i}", "message": [long_msg], "timestamp": i, "count": 1}
        for i in range(40)
    ]
    lines = format_system_log_entries(entries, max_entries=30)
    assert len(lines) == 30
    assert "second line" not in lines[0]
    assert len(lines[0]) < 250


def test_log_format_handles_empty_and_missing_fields():
    assert format_system_log_entries([]) == []
    lines = format_system_log_entries([{"message": []}])
    assert len(lines) == 1


# ---------------------------------------------------------------------------
# format_lovelace_resources
# ---------------------------------------------------------------------------

def test_resource_slugs_derived_from_filenames():
    resources = [
        {"url": "/hacsfiles/Bubble-Card/bubble-card.js?hacstag=680112919323", "type": "module"},
        {"url": "/hacsfiles/mini-graph-card/mini-graph-card-bundle.js?hacstag=1", "type": "module"},
        {"url": "/hacsfiles/lovelace-mushroom/mushroom.js", "type": "module"},
    ]
    result = format_lovelace_resources(resources)
    assert result["count"] == 3
    assert "bubble-card" in result["loaded_cards"]
    assert "mini-graph-card" in result["loaded_cards"]
    assert "mushroom" in result["loaded_cards"]


def test_resource_format_empty():
    result = format_lovelace_resources([])
    assert result == {"resources": [], "loaded_cards": [], "count": 0}
