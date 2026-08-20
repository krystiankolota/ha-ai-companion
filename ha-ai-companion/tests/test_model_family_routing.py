"""Regression tests for Tier B family routing + the pseudo-tool-call guard.

Locks the 1.18.5 fix. `_is_gemini` matched the substring "flash", so
`deepseek/deepseek-v4-flash-0731` was classified Gemini-family, which fired
Tier B plan-before-act: `tool_choice="none"` AND tools omitted entirely.
DeepSeek then emitted its native DSML tool-call markup as plain content, the
loop saw no tool_calls, logged "No tool calls, final response received" and
ended the run at iteration 1 having executed nothing — the user got raw
`<|DSML|invoke name="search_config_files">` markup in chat.
"""
import sys
from pathlib import Path

# agent_system uses package-relative imports — import via the src package root.
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agents.agent_system import (
    _is_text_first_family,
    _looks_like_pseudo_tool_call,
)


# --- Unit 1: family matcher -------------------------------------------------

def test_deepseek_flash_is_not_text_first():
    """The actual 1.18.5 regression: 'flash' must not imply Gemini-family."""
    assert _is_text_first_family("deepseek/deepseek-v4-flash-0731") is False


def test_gemini_flash_is_text_first():
    assert _is_text_first_family("google/gemini-2.5-flash") is True


def test_gemini_without_vendor_prefix_is_text_first():
    assert _is_text_first_family("gemini-2.0-flash-exp") is True


def test_claude_is_not_text_first():
    assert _is_text_first_family("anthropic/claude-sonnet-5") is False


def test_generic_tier_words_alone_do_not_match():
    """'pro'/'mini'/'flash' are provider-agnostic tier words, not families."""
    for model in ("openai/gpt-5-mini", "deepseek/deepseek-v4-pro", "some/model-flash"):
        assert _is_text_first_family(model) is False, model


def test_empty_and_none_are_safe():
    assert _is_text_first_family("") is False
    assert _is_text_first_family(None) is False


# --- Unit 2: pseudo-tool-call detection ------------------------------------

# Verbatim shape from the add-on log, 2026-08-20 00:38.
REAL_DSML_SAMPLE = (
    '<｜DSML｜tool_calls>\n'
    '<｜DSML｜invoke name="search_config_files">\n'
    '<｜DSML｜parameter name="search_term" string="true">rest:</｜DSML｜parameter>\n'
    '</｜DSML｜invoke>\n'
    '</｜DSML｜tool_calls>'
)


def test_real_dsml_markup_detected():
    assert _looks_like_pseudo_tool_call(REAL_DSML_SAMPLE) is True


def test_invoke_markup_detected():
    assert _looks_like_pseudo_tool_call('<invoke name="search_config_files">') is True


def test_function_calls_markup_detected():
    assert _looks_like_pseudo_tool_call("<function_calls>foo</function_calls>") is True


def test_normal_answer_not_detected():
    text = "Your septic tank is at 78%, roughly 55 cm of fluid. No action needed yet."
    assert _looks_like_pseudo_tool_call(text) is False


def test_prose_mentioning_tool_calls_not_detected():
    """False positives cost a wasted retry — prose about tool calling must pass."""
    text = (
        "The model emitted DSML markup instead of real tool calls, so the run "
        "ended early. Tool calls should go through the tool-calling API."
    )
    assert _looks_like_pseudo_tool_call(text) is False


def test_empty_and_none_are_safe_for_detector():
    assert _looks_like_pseudo_tool_call("") is False
    assert _looks_like_pseudo_tool_call(None) is False
