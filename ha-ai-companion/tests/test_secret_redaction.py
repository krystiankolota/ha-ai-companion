"""
Tests for inline credential redaction in search_config_files (agents/tools.py).

Covers _redact_secrets: values of credential-like YAML keys must never reach
the LLM/frontend even when not routed through secrets.yaml's !secret syntax.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agents.tools import _redact_secrets


def test_redacts_password_value():
    text = 'recorder:\n  db_url: mysql://user:pass@host/db\n  password: hunter2\n'
    out = _redact_secrets(text)
    assert 'hunter2' not in out
    assert 'password: "***REDACTED***"' in out


def test_redacts_api_key_variants():
    text = 'api_key: sk-abc123\napi-key: sk-def456\napikey: sk-ghi789\n'
    out = _redact_secrets(text)
    assert 'sk-abc123' not in out
    assert 'sk-def456' not in out
    assert 'sk-ghi789' not in out


def test_preserves_secret_yaml_reference():
    text = 'api_key: !secret openai_api_key'
    out = _redact_secrets(text)
    assert out == text


def test_leaves_unrelated_lines_untouched():
    text = 'homeassistant:\n  name: Home\n  latitude: 52.1'
    assert _redact_secrets(text) == text
