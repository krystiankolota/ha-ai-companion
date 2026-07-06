"""Regression tests for AgentSystem._parse_tool_args — malformed tool-call JSON.

Locks the 1.18.3 fix: the LLM can emit invalid JSON in tool-call arguments
(unescaped quote/newline inside a long YAML content field). Unhandled, the
JSONDecodeError killed the whole agent run and dumped the raw Python error
into chat ("Expecting ',' delimiter: line 1 column 3818").
"""
import sys
from pathlib import Path

# agent_system uses package-relative imports — import via the src package root.
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agents.agent_system import AgentSystem


def test_valid_json_parses():
    args, err = AgentSystem._parse_tool_args('{"file": "automations.yaml", "n": 1}')
    assert err is None
    assert args == {"file": "automations.yaml", "n": 1}


def test_none_returns_empty_dict():
    args, err = AgentSystem._parse_tool_args(None)
    assert err is None
    assert args == {}


def test_empty_string_returns_empty_dict():
    args, err = AgentSystem._parse_tool_args("")
    assert err is None
    assert args == {}


def test_unescaped_quote_returns_error_not_raise():
    # The actual 1.18.3 bug shape: unescaped quote inside a YAML content string.
    bad = '{"path": "automations.yaml", "content": "alias: "broken" name"}'
    args, err = AgentSystem._parse_tool_args(bad)
    assert args == {}
    assert err is not None
    assert "delimiter" in err or "Expecting" in err


def test_truncated_json_returns_error_not_raise():
    args, err = AgentSystem._parse_tool_args('{"path": "automations.yaml", "content": "id: x')
    assert args == {}
    assert err is not None
