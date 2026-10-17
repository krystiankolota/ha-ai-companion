"""
Tests for the low-value memory content guard (agents/tools.py).

Covers _low_value_memory_reason: a code-level check that rejects saves
matching known low-value patterns (session-action echoes, live sensor
readings), since prompt rules alone ("NEVER save...") were being violated
in practice.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agents.tools import AgentTools, _low_value_memory_reason
from src.memory.store import MemoryStore


def test_rejects_empty_content():
    assert _low_value_memory_reason("") is not None
    assert _low_value_memory_reason("   ") is not None


def test_rejects_too_short_content():
    assert _low_value_memory_reason("ok") is not None


def test_rejects_created_automation_echo():
    assert _low_value_memory_reason("Created automation to turn off lights at 23:00") is not None


def test_rejects_we_edited_echo():
    assert _low_value_memory_reason("We edited the configuration.yaml recorder section") is not None


def test_rejects_live_sensor_reading():
    assert _low_value_memory_reason("Living room temperature is currently 21.5C") is not None


def test_accepts_legit_user_preference():
    assert _low_value_memory_reason("User prefers 22C at night in the bedroom") is None


def test_accepts_legit_device_alias():
    assert _low_value_memory_reason("Krystian's phone = mobile_krystian, used for notifications") is None


def test_accepts_legit_routine():
    assert _low_value_memory_reason("Household goes to bed around 23:00 on weekdays") is None


def test_rejects_headings_long_content_and_entity_id_lists(tmp_path):
    assert _low_value_memory_reason("# Home setup\nThe user prefers Polish") is not None
    assert _low_value_memory_reason("one\ntwo\nthree\nfour") is not None
    assert _low_value_memory_reason(
        "light.kitchen sensor.bedroom switch.garage climate.office"
    ) is not None


def test_rejects_extracted_candidates_over_300_chars(tmp_path):
    store = MemoryStore(str(tmp_path))
    tools = AgentTools.__new__(AgentTools)
    tools.memory_manager = store
    result = asyncio.run(tools.save_memory("fact", "A durable fact " + ("useful " * 45), status="candidate"))
    assert not result["success"] and "300 characters" in result["error"]
