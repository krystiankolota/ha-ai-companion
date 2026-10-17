"""Write-intent detection, complexity-routing default, reasoning_details merge.

Log 2026-10-16: main=openai/gpt-6.1-sol never ran. Complexity routing (default
on) kept short turns on the research model, and the Polish keywords only
matched with diacritics ("zrób"), while the user types "Zrob to".
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agents.agent_system import (
    _has_write_intent,
    _complexity_routing_enabled,
    _merge_reasoning_details,
)


def test_write_intent_polish_without_diacritics():
    assert _has_write_intent("Zrob to") is True
    assert _has_write_intent("usun ta automatyzacje") is True
    assert _has_write_intent("wlacz swiatlo w kuchni") is True


def test_write_intent_polish_with_diacritics():
    assert _has_write_intent("Zrób to") is True
    assert _has_write_intent("Usuń automatyzację") is True


def test_write_intent_english():
    assert _has_write_intent("Please fix the heating automation") is True


def test_no_write_intent_for_question():
    assert _has_write_intent("What is the state of the kitchen light?") is False
    assert _has_write_intent("") is False


def test_complexity_routing_off_by_default(monkeypatch):
    monkeypatch.delenv("COMPLEXITY_ROUTING", raising=False)
    assert _complexity_routing_enabled() is False


def test_complexity_routing_opt_in(monkeypatch):
    monkeypatch.setenv("COMPLEXITY_ROUTING", "on")
    assert _complexity_routing_enabled() is True
    monkeypatch.setenv("COMPLEXITY_ROUTING", "off")
    assert _complexity_routing_enabled() is False


def test_merge_reasoning_text_chunks():
    """Verbatim streaming shape from OpenRouter (DeepSeek, 2026-10-17)."""
    chunks = [
        {"type": "reasoning.text", "text": "I", "format": "unknown", "index": "0"},
        {"type": "reasoning.text", "text": " need to check", "format": "unknown", "index": "0"},
        {"type": "reasoning.text", "text": " the states.", "format": "unknown", "index": "0"},
    ]
    merged = _merge_reasoning_details(chunks)
    assert merged == [
        {"type": "reasoning.text", "text": "I need to check the states.", "format": "unknown", "index": "0"}
    ]


def test_merge_keeps_block_order_and_signature():
    chunks = [
        {"type": "reasoning.text", "text": "a", "index": 0},
        {"type": "reasoning.text", "text": "b", "index": 0, "signature": "sig"},
        {"type": "reasoning.encrypted", "data": "xyz", "index": 1},
    ]
    merged = _merge_reasoning_details(chunks)
    assert [m["type"] for m in merged] == ["reasoning.text", "reasoning.encrypted"]
    assert merged[0]["text"] == "ab"
    assert merged[0]["signature"] == "sig"
    assert merged[1]["data"] == "xyz"


def test_merge_empty():
    assert _merge_reasoning_details([]) == []
