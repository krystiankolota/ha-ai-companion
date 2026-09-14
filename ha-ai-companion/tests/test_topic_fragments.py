"""
Tests for topic-fragment injection (AgentSystem._select_topic_fragments).

Covers the system-prompt simplification: dashboard/HACS and automation-
suggestion procedures are moved out of the always-loaded core prompt and
injected only when the current turn is actually on-topic (by keyword in the
user message, or by tool names already used this session).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agents.agent_system import AgentSystem


def test_unrelated_message_gets_no_fragments():
    assert AgentSystem._select_topic_fragments("What's the temperature in the bedroom?") == []


def test_dashboard_keyword_triggers_dashboard_fragment():
    frags = AgentSystem._select_topic_fragments("Can you build me a new dashboard for the kitchen?")
    assert AgentSystem._DASHBOARD_TOPIC_PROMPT in frags
    assert AgentSystem._AUTOMATION_SUGGESTIONS_TOPIC_PROMPT not in frags


def test_hacs_card_keyword_triggers_dashboard_fragment():
    frags = AgentSystem._select_topic_fragments("Add a bubble-card to my lovelace view")
    assert AgentSystem._DASHBOARD_TOPIC_PROMPT in frags


def test_suggestion_keyword_triggers_suggestions_fragment():
    frags = AgentSystem._select_topic_fragments("Suggest some automations for the living room")
    assert AgentSystem._AUTOMATION_SUGGESTIONS_TOPIC_PROMPT in frags


def test_nodered_keyword_triggers_suggestions_fragment():
    frags = AgentSystem._select_topic_fragments("Update my node-red flow to add a delay")
    assert AgentSystem._AUTOMATION_SUGGESTIONS_TOPIC_PROMPT in frags


def test_prior_dashboard_tool_call_keeps_fragment_loaded():
    history = [
        {"role": "assistant", "tool_calls": [{"function": {"name": "list_dashboards"}}]},
    ]
    frags = AgentSystem._select_topic_fragments("looks good, add one more tile", history)
    assert AgentSystem._DASHBOARD_TOPIC_PROMPT in frags


def test_prior_nodered_tool_call_keeps_fragment_loaded():
    history = [
        {"role": "assistant", "tool_calls": [{"function": {"name": "get_nodered_flows"}}]},
    ]
    frags = AgentSystem._select_topic_fragments("yes go ahead", history)
    assert AgentSystem._AUTOMATION_SUGGESTIONS_TOPIC_PROMPT in frags


def test_both_topics_can_trigger_together():
    frags = AgentSystem._select_topic_fragments("suggest a dashboard card for my node-red flow")
    assert AgentSystem._DASHBOARD_TOPIC_PROMPT in frags
    assert AgentSystem._AUTOMATION_SUGGESTIONS_TOPIC_PROMPT in frags


def test_history_tool_names_ignores_malformed_entries():
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "tool_calls": [{"function": {"name": "search_config_files"}}]},
    ]
    assert AgentSystem._history_tool_names(history) == {"search_config_files"}
    assert AgentSystem._history_tool_names(None) == set()
    assert AgentSystem._history_tool_names([]) == set()
