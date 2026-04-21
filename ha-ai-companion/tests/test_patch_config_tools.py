"""
Tests for patch_config_key and patch_config_block surgical patching tools.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path

import sys, os
# Import via src package so relative imports inside agent_system.py resolve correctly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.agents.tools import AgentTools


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_tools(file_contents: dict) -> AgentTools:
    """Build an AgentTools instance with a mock config_manager that serves fake files."""
    cm = MagicMock()

    async def read_file_raw(file_path, allow_missing=True):
        return file_contents.get(file_path)

    cm.read_file_raw = read_file_raw

    tools = AgentTools.__new__(AgentTools)
    tools.config_manager = cm
    tools.memory_manager = None
    tools.conversation_manager = None
    tools.agent_system = None
    tools._lovelace_cache = {}
    tools._turn_cache = {}
    return tools


SIMPLE_YAML = """\
homeassistant:
  name: My Home
  unit_system: metric
logger:
  default: warning
  logs:
    custom_components: debug
"""

AUTOMATIONS_YAML = """\
- alias: Morning light
  trigger:
    - platform: time
      at: '07:00:00'
  action:
    - service: light.turn_on
      target:
        entity_id: light.kitchen
- alias: Evening light
  trigger:
    - platform: time
      at: '20:00:00'
  action:
    - service: light.turn_on
      target:
        entity_id: light.living_room
"""


# ---------------------------------------------------------------------------
# _parse_yaml_path
# ---------------------------------------------------------------------------

class TestParseYamlPath:
    def test_simple_dot(self):
        assert AgentTools._parse_yaml_path("logger.default") == ["logger", "default"]

    def test_single_key(self):
        assert AgentTools._parse_yaml_path("homeassistant") == ["homeassistant"]

    def test_integer_index(self):
        assert AgentTools._parse_yaml_path("automations[0]") == ["automations", 0]

    def test_nested_with_index(self):
        assert AgentTools._parse_yaml_path("automations[0].trigger") == ["automations", 0, "trigger"]

    def test_field_value_selector(self):
        result = AgentTools._parse_yaml_path("automations[alias=Morning light]")
        assert result == ["automations", {"field": "alias", "value": "Morning light"}]

    def test_field_value_nested(self):
        result = AgentTools._parse_yaml_path("automations[alias=Morning light].action")
        assert result == ["automations", {"field": "alias", "value": "Morning light"}, "action"]

    def test_empty_returns_empty(self):
        assert AgentTools._parse_yaml_path("") == []


# ---------------------------------------------------------------------------
# _navigate_yaml
# ---------------------------------------------------------------------------

class TestNavigateYaml:
    def test_simple_nested(self):
        data = {"logger": {"default": "warning"}}
        parent, key, found = AgentTools._navigate_yaml(data, ["logger", "default"])
        assert found is True
        assert parent == {"default": "warning"}
        assert key == "default"

    def test_missing_key(self):
        data = {"logger": {"default": "warning"}}
        _, _, found = AgentTools._navigate_yaml(data, ["logger", "nonexistent"])
        assert found is False

    def test_integer_index(self):
        data = {"automations": [{"alias": "A"}, {"alias": "B"}]}
        parent, key, found = AgentTools._navigate_yaml(data, ["automations", 1])
        assert found is True
        assert key == 1
        assert parent[key]["alias"] == "B"

    def test_field_value_selector(self):
        data = {"automations": [{"alias": "Morning light"}, {"alias": "Evening light"}]}
        parent, key, found = AgentTools._navigate_yaml(
            data, ["automations", {"field": "alias", "value": "Evening light"}]
        )
        assert found is True
        assert key == 1
        assert parent[key]["alias"] == "Evening light"

    def test_field_value_not_found(self):
        data = {"automations": [{"alias": "Morning light"}]}
        _, _, found = AgentTools._navigate_yaml(
            data, ["automations", {"field": "alias", "value": "Nonexistent"}]
        )
        assert found is False


# ---------------------------------------------------------------------------
# patch_config_key
# ---------------------------------------------------------------------------

class TestPatchConfigKey:
    @pytest.mark.asyncio
    async def test_simple_key_change(self):
        tools = make_tools({"configuration.yaml": SIMPLE_YAML})
        result = await tools.patch_config_key(
            file_path="configuration.yaml",
            key_path="logger.default",
            new_value="debug",
            description="Enable debug logging",
        )
        assert result["success"] is True
        assert result["changeset_id"] is not None
        assert result["total_files"] == 1
        assert "Awaiting user approval" in result["message"]

    @pytest.mark.asyncio
    async def test_preserves_other_keys(self):
        tools = make_tools({"configuration.yaml": SIMPLE_YAML})
        # Capture the new_content that would be stored
        stored = {}

        def store_changeset(data):
            stored['changes'] = data['file_changes']
            return "test123"

        tools.agent_system = MagicMock()
        tools.agent_system.store_changeset = store_changeset

        await tools.patch_config_key(
            file_path="configuration.yaml",
            key_path="logger.default",
            new_value="debug",
        )

        new_content = stored['changes'][0]['new_content']
        # Other keys must still be present
        assert "homeassistant:" in new_content
        assert "My Home" in new_content
        assert "custom_components: debug" in new_content
        # Changed key must be updated
        assert "default: debug" in new_content
        # Old value must not remain in the logger section
        # (ruamel.yaml will serialize "debug" replacing "warning")

    @pytest.mark.asyncio
    async def test_key_not_found(self):
        tools = make_tools({"configuration.yaml": SIMPLE_YAML})
        result = await tools.patch_config_key(
            file_path="configuration.yaml",
            key_path="logger.nonexistent_key",
            new_value="x",
        )
        assert result["success"] is False
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_file_not_found(self):
        tools = make_tools({})
        result = await tools.patch_config_key(
            file_path="missing.yaml",
            key_path="some.key",
            new_value="x",
        )
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_list_item_by_field(self):
        tools = make_tools({"automations.yaml": AUTOMATIONS_YAML})
        stored = {}

        def store_changeset(data):
            stored['changes'] = data['file_changes']
            return "abc123"

        tools.agent_system = MagicMock()
        tools.agent_system.store_changeset = store_changeset

        result = await tools.patch_config_key(
            file_path="automations.yaml",
            key_path="[alias=Morning light].action[0].service",
            new_value="light.turn_off",
        )
        assert result["success"] is True
        new_content = stored['changes'][0]['new_content']
        assert "light.turn_off" in new_content
        # Evening light automation must be untouched
        assert "Evening light" in new_content

    @pytest.mark.asyncio
    async def test_diff_stats_computed(self):
        tools = make_tools({"configuration.yaml": SIMPLE_YAML})
        result = await tools.patch_config_key(
            file_path="configuration.yaml",
            key_path="logger.default",
            new_value="debug",
        )
        assert result["success"] is True
        stats = result["diff_stats"][0]
        assert stats["file_path"] == "configuration.yaml"
        assert stats["is_new_file"] is False
        # Exactly 1 line added, 1 removed (the default: value)
        assert stats["added"] == 1
        assert stats["removed"] == 1


# ---------------------------------------------------------------------------
# patch_config_block
# ---------------------------------------------------------------------------

class TestPatchConfigBlock:
    @pytest.mark.asyncio
    async def test_replace_top_level_block(self):
        tools = make_tools({"configuration.yaml": SIMPLE_YAML})
        stored = {}

        def store_changeset(data):
            stored['changes'] = data['file_changes']
            return "blk123"

        tools.agent_system = MagicMock()
        tools.agent_system.store_changeset = store_changeset

        new_logger = "default: info\nlogs:\n  custom_components: warning\n"
        result = await tools.patch_config_block(
            file_path="configuration.yaml",
            anchor="logger",
            new_block=new_logger,
        )
        assert result["success"] is True
        new_content = stored['changes'][0]['new_content']
        # New logger values present
        assert "default: info" in new_content
        assert "custom_components: warning" in new_content
        # homeassistant block untouched
        assert "My Home" in new_content

    @pytest.mark.asyncio
    async def test_replace_list_item_by_alias(self):
        tools = make_tools({"automations.yaml": AUTOMATIONS_YAML})
        stored = {}

        def store_changeset(data):
            stored['changes'] = data['file_changes']
            return "blk456"

        tools.agent_system = MagicMock()
        tools.agent_system.store_changeset = store_changeset

        new_automation = (
            "alias: Morning light\n"
            "trigger:\n  - platform: time\n    at: '06:30:00'\n"
            "action:\n  - service: light.turn_on\n    target:\n      entity_id: light.kitchen\n"
        )
        result = await tools.patch_config_block(
            file_path="automations.yaml",
            anchor="[alias=Morning light]",
            new_block=new_automation,
        )
        assert result["success"] is True
        new_content = stored['changes'][0]['new_content']
        # Updated time
        assert "06:30:00" in new_content
        # Other automation unchanged
        assert "Evening light" in new_content
        assert "20:00:00" in new_content

    @pytest.mark.asyncio
    async def test_invalid_new_block_yaml(self):
        tools = make_tools({"configuration.yaml": SIMPLE_YAML})
        result = await tools.patch_config_block(
            file_path="configuration.yaml",
            anchor="logger",
            new_block=": invalid: yaml: {{{{",
        )
        assert result["success"] is False
        assert "Invalid YAML" in result["error"]

    @pytest.mark.asyncio
    async def test_anchor_not_found(self):
        tools = make_tools({"configuration.yaml": SIMPLE_YAML})
        result = await tools.patch_config_block(
            file_path="configuration.yaml",
            anchor="nonexistent_section",
            new_block="key: value\n",
        )
        assert result["success"] is False
        assert "not found" in result["error"]
