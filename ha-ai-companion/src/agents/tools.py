"""
AI Agent Tool Functions

Tool functions that agents can call to interact with configuration files.
These wrap the ConfigurationManager for safe AI operations.
"""
import logging
import os
from typing import Dict, Any, Optional, List, TYPE_CHECKING
from ..config import ConfigurationManager, ConfigurationError
from ..ha.ha_websocket import (
    get_lovelace_config_as_yaml,
    list_lovelace_dashboards_ws,
    create_lovelace_dashboard_ws,
    delete_lovelace_dashboard_ws,
)

if TYPE_CHECKING:
    from ..memory.manager import MemoryManager

logger = logging.getLogger(__name__)


class AgentTools:
    """
    Tool functions for AI agents to interact with Home Assistant configuration.

    All tools are designed to be called by AI agents and provide:
    - Clear, structured responses
    - Error handling with user-friendly messages
    - Logging of all operations
    - Safety through ConfigurationManager
    """

    def __init__(
        self,
        config_manager: ConfigurationManager,
        workflow: Optional['ValidationWorkflow'] = None,
        agent_system: Optional[Any] = None,
        memory_manager: Optional['MemoryManager'] = None,
    ):
        """
        Initialize agent tools with a configuration manager.

        Args:
            config_manager: ConfigurationManager instance for file operations
            workflow: Optional ValidationWorkflow for approval management
            agent_system: Optional AgentSystem for changeset storage
            memory_manager: Optional MemoryManager for persistent memories
        """
        self.config_manager = config_manager
        self.workflow = workflow
        self.agent_system = agent_system
        self.memory_manager = memory_manager
        self._lovelace_cache: Dict[Optional[str], str] = {}  # {url_path: yaml_str}
        logger.info("AgentTools initialized")

    async def _get_lovelace_config(self, url_path: Optional[str] = None) -> Optional[str]:
        """
        Internal helper to retrieve Lovelace config for one dashboard.

        Args:
            url_path: Dashboard URL path (e.g. 'kitchen'). None = default dashboard.

        Returns:
            YAML string of Lovelace config, or None if not available
        """
        cache_key = url_path  # None for default

        # Return cached version if available
        if cache_key in self._lovelace_cache:
            return self._lovelace_cache[cache_key]

        try:
            # Custom component mode: use hass directly
            if self.config_manager.hass is not None:
                from homeassistant.components.lovelace.const import DOMAIN as LOVELACE_DOMAIN
                from ruamel.yaml import YAML

                hass = self.config_manager.hass
                logger.info(f"Using hass API to retrieve Lovelace config (url_path={url_path})")

                if LOVELACE_DOMAIN not in hass.data:
                    logger.warning("Lovelace component not loaded in hass.data")
                    return None

                lovelace_data = hass.data.get(LOVELACE_DOMAIN)
                if not (lovelace_data and hasattr(lovelace_data, 'dashboards')):
                    logger.warning("No dashboards attribute on lovelace_data")
                    return None

                dashboards = lovelace_data.dashboards

                if url_path is None:
                    # Default dashboard — keyed as None or 'lovelace'
                    dashboard = dashboards.get(None) or dashboards.get('lovelace')
                else:
                    dashboard = dashboards.get(url_path)

                if not dashboard:
                    logger.warning(f"Dashboard '{url_path}' not found in {list(dashboards.keys())}")
                    return None

                config = await dashboard.async_load(False)
                if not config:
                    logger.warning(f"async_load returned empty config for '{url_path}'")
                    return None

                yaml = YAML()
                yaml.default_flow_style = False
                from io import StringIO
                stream = StringIO()
                yaml.dump(config, stream)
                lovelace_yaml = stream.getvalue()
                self._lovelace_cache[cache_key] = lovelace_yaml
                logger.info(f"Retrieved Lovelace config via hass API (url_path={url_path})")
                return lovelace_yaml

            # Add-on mode: use WebSocket API
            supervisor_token = os.getenv('SUPERVISOR_TOKEN')
            if not supervisor_token:
                logger.debug("No SUPERVISOR_TOKEN, skipping Lovelace config")
                return None

            ws_url = "ws://supervisor/core/websocket"
            lovelace_yaml = await get_lovelace_config_as_yaml(ws_url, supervisor_token, url_path)
            if lovelace_yaml:
                self._lovelace_cache[cache_key] = lovelace_yaml
                logger.info(f"Retrieved Lovelace config via WebSocket (url_path={url_path})")
            return lovelace_yaml

        except Exception as e:
            logger.debug(f"Failed to get Lovelace config (url_path={url_path}): {e}")
            return None

    async def _get_all_dashboards(self) -> List[Dict[str, Any]]:
        """
        Internal helper to list all Lovelace dashboards with metadata.

        Returns a list of dicts with at minimum: url_path (None=default), title.
        """
        try:
            if self.config_manager.hass is not None:
                from homeassistant.components.lovelace.const import DOMAIN as LOVELACE_DOMAIN
                hass = self.config_manager.hass
                if LOVELACE_DOMAIN not in hass.data:
                    return []
                lovelace_data = hass.data.get(LOVELACE_DOMAIN)
                if not (lovelace_data and hasattr(lovelace_data, 'dashboards')):
                    return []

                result = []
                for key, dashboard in lovelace_data.dashboards.items():
                    entry: Dict[str, Any] = {"url_path": key}
                    # Try to extract title/icon from config entry if available
                    if hasattr(dashboard, 'config_entry') and dashboard.config_entry:
                        ce = dashboard.config_entry
                        entry["title"] = getattr(ce, 'title', key or 'Default')
                        entry["icon"] = ce.data.get('icon') if hasattr(ce, 'data') else None
                    else:
                        entry["title"] = key or "Default"
                    result.append(entry)
                return result

            # Add-on mode
            supervisor_token = os.getenv('SUPERVISOR_TOKEN')
            if not supervisor_token:
                return []
            ws_url = "ws://supervisor/core/websocket"
            return await list_lovelace_dashboards_ws(ws_url, supervisor_token)

        except Exception as e:
            logger.debug(f"Failed to list dashboards: {e}")
            return []

    async def _get_all_devices(self) -> List[Dict[str, Any]]:
        """
        Internal helper to retrieve all devices from registry.

        Uses hass API in custom component mode, WebSocket in add-on mode.

        Returns:
            List of device dictionaries
        """
        try:
            # Custom component mode: use hass directly
            if self.config_manager.hass is not None:
                from homeassistant.helpers import device_registry as dr

                hass = self.config_manager.hass
                device_reg = dr.async_get(hass)
                devices = []
                for device in device_reg.devices.values():
                    devices.append({
                        "id": device.id,
                        "name": device.name,
                        "name_by_user": device.name_by_user,
                        "area_id": device.area_id,
                        "disabled_by": device.disabled_by,
                        "identifiers": list(device.identifiers),
                    })
                logger.debug(f"Retrieved {len(devices)} devices via hass API")
                return devices

            # Add-on mode: use WebSocket API
            from ..ha.ha_websocket import HomeAssistantWebSocket

            supervisor_token = os.getenv('SUPERVISOR_TOKEN')
            if not supervisor_token:
                logger.debug("No SUPERVISOR_TOKEN available, skipping devices")
                return []

            ws_url = "ws://supervisor/core/websocket"
            ws_client = HomeAssistantWebSocket(ws_url, supervisor_token)
            await ws_client.connect()
            devices = await ws_client.list_devices()
            await ws_client.close()
            return devices
        except Exception as e:
            logger.debug(f"Failed to get devices: {e}")
            return []

    async def _get_all_entities(self) -> List[Dict[str, Any]]:
        """
        Internal helper to retrieve all entities from registry.

        Uses hass API in custom component mode, WebSocket in add-on mode.

        Returns:
            List of entity dictionaries
        """
        try:
            # Custom component mode: use hass directly
            if self.config_manager.hass is not None:
                from homeassistant.helpers import entity_registry as er

                hass = self.config_manager.hass
                entity_reg = er.async_get(hass)
                entities = []
                for entity in entity_reg.entities.values():
                    entities.append({
                        "entity_id": entity.entity_id,
                        "name": entity.name,
                        "original_name": entity.original_name,
                        "icon": entity.icon,
                        "area_id": entity.area_id,
                        "device_id": entity.device_id,
                        "platform": entity.platform,
                        "disabled_by": entity.disabled_by,
                    })
                logger.debug(f"Retrieved {len(entities)} entities via hass API")
                return entities

            # Add-on mode: use WebSocket API
            from ..ha.ha_websocket import HomeAssistantWebSocket

            supervisor_token = os.getenv('SUPERVISOR_TOKEN')
            if not supervisor_token:
                logger.debug("No SUPERVISOR_TOKEN available, skipping entities")
                return []

            ws_url = "ws://supervisor/core/websocket"
            ws_client = HomeAssistantWebSocket(ws_url, supervisor_token)
            await ws_client.connect()
            entities = await ws_client.list_entities()
            await ws_client.close()
            return entities
        except Exception as e:
            logger.debug(f"Failed to get entities: {e}")
            return []

    async def _get_all_areas(self) -> List[Dict[str, Any]]:
        """
        Internal helper to retrieve all areas from registry.

        Uses hass API in custom component mode, WebSocket in add-on mode.

        Returns:
            List of area dictionaries
        """
        try:
            # Custom component mode: use hass directly
            if self.config_manager.hass is not None:
                from homeassistant.helpers import area_registry as ar

                hass = self.config_manager.hass
                area_reg = ar.async_get(hass)
                areas = []
                for area in area_reg.areas.values():
                    areas.append({
                        "area_id": area.id,
                        "name": area.name,
                        "picture": area.picture,
                        "icon": area.icon,
                        "aliases": area.aliases,
                    })
                logger.debug(f"Retrieved {len(areas)} areas via hass API")
                return areas

            # Add-on mode: use WebSocket API
            from ..ha.ha_websocket import HomeAssistantWebSocket

            supervisor_token = os.getenv('SUPERVISOR_TOKEN')
            if not supervisor_token:
                logger.debug("No SUPERVISOR_TOKEN available, skipping areas")
                return []

            ws_url = "ws://supervisor/core/websocket"
            ws_client = HomeAssistantWebSocket(ws_url, supervisor_token)
            await ws_client.connect()
            areas = await ws_client.list_areas()
            await ws_client.close()
            return areas
        except Exception as e:
            logger.debug(f"Failed to get areas: {e}")
            return []

    async def search_config_files(
        self,
        search_pattern: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Search ALL configuration files for a pattern and return matching files with full contents.

        This tool searches all YAML files, plus virtual files (lovelace.yaml, devices.json,
        entities.json). Returns files that contain the search pattern, or all files if no
        pattern is provided.

        Args:
            search_pattern: Optional text to search for in file contents.
                          Case-insensitive search.
                          If None, returns ALL configuration files.
                          If starts with "/", treats as file path pattern and only searches
                          actual files (skips virtual entities/devices/areas).

        Returns:
            Dict with:
                - success: bool
                - files: List[Dict] with keys:
                    - path: str (relative file path)
                    - content: str (file content as string)
                    - matches: Optional[int] (number of matches if search_pattern provided)
                - count: int (number of files found)
                - search_pattern: Optional[str]
                - error: Optional[str]

        Example:
            >>> await tools.search_config_files(search_pattern="mqtt")
            {
                "success": True,
                "files": [
                    {
                        "path": "configuration.yaml",
                        "content": "mqtt:\\n  broker: ...",
                        "matches": 3
                    }
                ],
                "count": 1,
                "search_pattern": "mqtt"
            }

            >>> await tools.search_config_files(search_pattern="/packages/*.yaml")
            {
                "success": True,
                "files": [
                    {
                        "path": "packages/mqtt.yaml",
                        "content": "...",
                        "matches": 1
                    }
                ],
                "count": 1,
                "search_pattern": "/packages/*.yaml"
            }
        """
        try:
            from pathlib import Path
            import re

            logger.info(f"Agent searching all files - pattern: '{search_pattern or 'none'}'")
            config_dir = self.config_manager.config_dir

            # Check if search_pattern is a file path pattern (starts with "/")
            is_file_path_pattern = search_pattern and search_pattern.startswith("/")

            if is_file_path_pattern:
                logger.info(f"Detected file path pattern: {search_pattern}")
                # Remove leading slash and use as glob pattern
                glob_pattern = search_pattern.lstrip("/")
                matched_paths = list(config_dir.glob(glob_pattern))
                logger.info(f"File path pattern matched {len(matched_paths)} files")
            else:
                # Find all YAML files
                matched_paths = list(config_dir.glob("**/*.yaml"))

            # Filter to only files (not directories) and exclude custom_components
            matched_paths = [
                p for p in matched_paths
                if p.is_file() and 'custom_components' not in p.parts and 'secrets.yaml' not in p.parts
            ]

            # Sort for consistent results
            matched_paths.sort()

            # Read files and optionally filter by content search
            files = []
            for path in matched_paths:
                relative_path = str(path.relative_to(config_dir))
                try:
                    content = await self.config_manager.read_file_raw(relative_path)

                    # If search pattern provided and NOT a file path pattern, check if file contains it or filename matches
                    if search_pattern and not is_file_path_pattern:
                        # Case-insensitive search in content
                        content_matches = len(re.findall(re.escape(search_pattern), content, re.IGNORECASE))
                        # Case-insensitive search in filename
                        filename_matches = len(re.findall(re.escape(search_pattern), relative_path, re.IGNORECASE))
                        total_matches = content_matches + filename_matches

                        if total_matches > 0:
                            files.append({
                                "path": relative_path,
                                "content": content,
                                "matches": total_matches
                            })
                    else:
                        # No search pattern OR file path pattern - include all matched files
                        files.append({
                            "path": relative_path,
                            "content": content,
                            "matches": 1 if is_file_path_pattern else None
                        })

                except Exception as e:
                    logger.warning(f"Could not read {relative_path}: {e}")
                    continue

            # Skip virtual file searches if using file path pattern
            if is_file_path_pattern:
                logger.info(f"Skipping virtual file searches for file path pattern")
                result = {
                    "success": True,
                    "files": files,
                    "count": len(files)
                }
                if search_pattern:
                    result["search_pattern"] = search_pattern
                return result

            # Include virtual files for devices and entities as individual files
            # Format: devices/{device_id}.json and entities/{entity_id}.json
            # Only include if search_pattern provided and matches
            import json

            # Handle individual device files
            if search_pattern:
                try:
                    devices = await self._get_all_devices()
                    device_count = 0
                    for device in devices:
                        device_id = device.get('id', 'unknown')
                        device_path = f"devices/{device_id}.json"
                        device_json = json.dumps(device, indent=2)

                        # Check both content and filename for matches
                        content_matches = len(re.findall(re.escape(search_pattern), device_json, re.IGNORECASE))
                        filename_matches = len(re.findall(re.escape(search_pattern), device_path, re.IGNORECASE))
                        total_matches = content_matches + filename_matches

                        if total_matches > 0:
                            files.append({
                                "path": device_path,
                                "content": device_json,
                                "matches": total_matches
                            })
                            device_count += 1
                    if device_count > 0:
                        logger.info(f"Found {device_count} device(s) matching search pattern")
                except Exception as e:
                    logger.debug(f"Could not retrieve devices (not critical): {e}")

            # Handle individual entity files
            if search_pattern:
                try:
                    entities = await self._get_all_entities()
                    entity_count = 0
                    for entity in entities:
                        entity_id = entity.get('entity_id', 'unknown')
                        entity_path = f"entities/{entity_id}.json"
                        entity_json = json.dumps(entity, indent=2)

                        # Check both content and filename for matches
                        content_matches = len(re.findall(re.escape(search_pattern), entity_json, re.IGNORECASE))
                        filename_matches = len(re.findall(re.escape(search_pattern), entity_path, re.IGNORECASE))
                        total_matches = content_matches + filename_matches

                        if total_matches > 0:
                            files.append({
                                "path": entity_path,
                                "content": entity_json,
                                "matches": total_matches
                            })
                            entity_count += 1
                    if entity_count > 0:
                        logger.info(f"Found {entity_count} entit(ies) matching search pattern")
                except Exception as e:
                    logger.debug(f"Could not retrieve entities (not critical): {e}")

            # Handle individual area files
            if search_pattern:
                try:
                    areas = await self._get_all_areas()
                    area_count = 0
                    for area in areas:
                        area_id = area.get('area_id', 'unknown')
                        area_path = f"areas/{area_id}.json"
                        area_json = json.dumps(area, indent=2)

                        # Check both content and filename for matches
                        content_matches = len(re.findall(re.escape(search_pattern), area_json, re.IGNORECASE))
                        filename_matches = len(re.findall(re.escape(search_pattern), area_path, re.IGNORECASE))
                        total_matches = content_matches + filename_matches

                        if total_matches > 0:
                            files.append({
                                "path": area_path,
                                "content": area_json,
                                "matches": total_matches
                            })
                            area_count += 1
                    if area_count > 0:
                        logger.info(f"Found {area_count} area(s) matching search pattern")
                except Exception as e:
                    logger.debug(f"Could not retrieve areas (not critical): {e}")

            # Handle Lovelace dashboards (default + all custom)
            try:
                all_dashboards = await self._get_all_dashboards()
                # Always include the default dashboard
                default_paths = {None, 'lovelace'}
                custom_url_paths = [d.get('url_path') for d in all_dashboards if d.get('url_path') not in default_paths]

                for dashboard_url_path in [None] + custom_url_paths:
                    try:
                        lovelace_content = await self._get_lovelace_config(dashboard_url_path)
                        if not lovelace_content:
                            continue
                        virtual_path = "lovelace.yaml" if dashboard_url_path is None else f"lovelace/{dashboard_url_path}.yaml"
                        if search_pattern:
                            content_matches = len(re.findall(re.escape(search_pattern), lovelace_content, re.IGNORECASE))
                            filename_matches = len(re.findall(re.escape(search_pattern), virtual_path, re.IGNORECASE))
                            total_matches = content_matches + filename_matches
                            if total_matches > 0:
                                files.append({"path": virtual_path, "content": lovelace_content, "matches": total_matches})
                                logger.info(f"{virtual_path} matched ({total_matches} matches)")
                        else:
                            files.append({"path": virtual_path, "content": lovelace_content})
                            logger.info(f"Included {virtual_path} in results")
                    except Exception as e:
                        logger.debug(f"Could not retrieve {dashboard_url_path} dashboard: {e}")
            except Exception as e:
                logger.debug(f"Could not retrieve Lovelace dashboards (not critical): {e}")

            logger.info(f"Agent found {len(files)} files (searched {len(matched_paths)} YAML files + 3 virtual files)")

            result = {
                "success": True,
                "files": files,
                "count": len(files)
            }

            if search_pattern:
                result["search_pattern"] = search_pattern

            return result

        except Exception as e:
            logger.error(f"Agent error searching config files: {e}")
            return {
                "success": False,
                "error": f"Error searching files: {str(e)}",
                "search_pattern": search_pattern
            }

    async def propose_config_changes(
        self,
        changes: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        """
        Propose changes to one or more configuration files using new content.

        This tool stages changes for user approval. Changes are NOT applied
        immediately - they go through the approval workflow. Multiple files
        can be changed in a single operation.

        Args:
            changes: List of change objects, each with:
                - file_path: str - Relative path to config file (e.g., 'configuration.yaml')
                - new_content: str - The complete new content of the file as a YAML string

        Returns:
            Dict with:
                - success: bool
                - changes: List[Dict] - Details of each proposed change with change_id
                - total_files: int
                - error: Optional[str]

        Example:
            >>> await tools.propose_config_changes(
            ...     changes=[
            ...         {
            ...             "file_path": "configuration.yaml",
            ...             "new_content": "logger:\\n  default: debug\\n"
            ...         },
            ...         {
            ...             "file_path": "automations.yaml",
            ...             "new_content": "- alias: Test\\n  trigger: []\\n"
            ...         }
            ...     ]
            ... )

        Workflow:
            1. First, call search_config_files to get current content
            2. Modify the content as needed for each file
            3. Call this function with all changes in one batch
        """
        try:
            logger.info(f"Agent proposing changes to {len(changes)} file(s)")

            from ruamel.yaml import YAML
            from io import StringIO

            yaml = YAML()
            yaml.preserve_quotes = True
            yaml.default_flow_style = False

            file_changes = []
            errors = []

            # Process and validate each file change
            for change in changes:
                file_path = change.get("file_path")
                new_content = change.get("new_content")

                if not file_path or not new_content:
                    errors.append({
                        "file_path": file_path or "unknown",
                        "error": "Missing file_path or new_content"
                    })
                    continue

                try:
                    logger.info(f"Validating change for: {file_path}")

                    # Special handling for virtual files
                    if file_path == "lovelace.yaml" or file_path.startswith("lovelace/"):
                        # Determine url_path from file path
                        if file_path == "lovelace.yaml":
                            lovelace_url_path = None
                        else:
                            lovelace_url_path = file_path[len("lovelace/"):].removesuffix(".yaml") or None
                        current_content = await self._get_lovelace_config(lovelace_url_path)
                        if not current_content:
                            errors.append({
                                "file_path": file_path,
                                "error": f"Could not retrieve Lovelace config for '{lovelace_url_path or 'default'}'"
                            })
                            continue
                    elif file_path.startswith("devices/"):
                        # Individual device file: devices/{device_id}.json
                        device_id = file_path.replace("devices/", "").replace(".json", "")
                        devices = await self._get_all_devices()
                        current_device = next((d for d in devices if d.get('id') == device_id), None)
                        if not current_device:
                            errors.append({
                                "file_path": file_path,
                                "error": f"Device {device_id} not found in registry"
                            })
                            continue
                        import json
                        current_content = json.dumps(current_device, indent=2)
                    elif file_path.startswith("entities/"):
                        # Individual entity file: entities/{entity_id}.json
                        entity_id = file_path.replace("entities/", "").replace(".json", "")
                        entities = await self._get_all_entities()
                        current_entity = next((e for e in entities if e.get('entity_id') == entity_id), None)
                        if not current_entity:
                            errors.append({
                                "file_path": file_path,
                                "error": f"Entity {entity_id} not found in registry"
                            })
                            continue
                        import json
                        current_content = json.dumps(current_entity, indent=2)
                    elif file_path.startswith("areas/"):
                        # Individual area file: areas/{area_id}.json
                        area_id = file_path.replace("areas/", "").replace(".json", "")
                        areas = await self._get_all_areas()
                        current_area = next((a for a in areas if a.get('area_id') == area_id), None)

                        # If area doesn't exist, it will be created - set empty current content
                        if current_area:
                            import json
                            current_content = json.dumps(current_area, indent=2)
                        else:
                            # New area - validate that required 'name' field is present
                            import json
                            proposed_area = json.loads(new_content)
                            if not proposed_area.get('name'):
                                errors.append({
                                    "file_path": file_path,
                                    "error": f"Cannot create area: 'name' field is required"
                                })
                                continue
                            current_content = "{}"  # Empty JSON for new area
                            logger.info(f"Area {area_id} will be created with name: {proposed_area.get('name')}")
                    else:
                        # Read current config as raw text for regular files
                        # Allow missing files (will be created as new files)
                        current_content = await self.config_manager.read_file_raw(file_path, allow_missing=True)
                        if current_content is None:
                            current_content = ""  # Empty content for new files
                            logger.info(f"File {file_path} will be created as a new file")

                    # Safety guard: prevent accidental deletion of automations/scripts/scenes
                    # by detecting when proposed content has far fewer items than current file.
                    _GUARDED_FILES = ('automations.yaml', 'scripts.yaml', 'scenes.yaml')
                    if any(file_path.endswith(g) for g in _GUARDED_FILES) and current_content and current_content.strip():
                        try:
                            _ry = YAML()
                            _current_list = _ry.load(StringIO(current_content))
                            _new_list = _ry.load(StringIO(new_content)) if new_content.strip() else []
                            _cur_count = len(_current_list) if isinstance(_current_list, list) else 0
                            _new_count = len(_new_list) if isinstance(_new_list, list) else 0
                            if _cur_count > 0 and _new_count < _cur_count * 0.8:
                                errors.append({
                                    "file_path": file_path,
                                    "error": (
                                        f"SAFETY GUARD: {file_path} currently has {_cur_count} items but your "
                                        f"proposed content only has {_new_count}. This would permanently DELETE "
                                        f"{_cur_count - _new_count} existing items. "
                                        f"Read the current file first with search_config_files, then include ALL "
                                        f"{_cur_count} existing items in your proposed content before adding new ones."
                                    )
                                })
                                continue
                        except Exception as _guard_err:
                            logger.warning(f"Safety guard check failed for {file_path}: {_guard_err}")

                    # Validate the new content based on file type
                    if file_path.endswith('.json'):
                        # Validate JSON files (devices.json, entities.json)
                        import json
                        try:
                            json.loads(new_content)
                        except Exception as e:
                            errors.append({
                                "file_path": file_path,
                                "error": f"Invalid JSON in new_content: {str(e)}"
                            })
                            continue
                    else:
                        # Validate YAML files
                        new_io = StringIO(new_content)
                        try:
                            new_config = yaml.load(new_io)
                        except Exception as e:
                            errors.append({
                                "file_path": file_path,
                                "error": f"Invalid YAML in new_content: {str(e)}"
                            })
                            continue

                    # Add to file changes list
                    file_changes.append({
                        "file_path": file_path,
                        "current_content": current_content,
                        "new_content": new_content
                    })

                except ConfigurationError as e:
                    logger.error(f"Agent config proposal error for {file_path}: {e}")
                    errors.append({
                        "file_path": file_path,
                        "error": str(e)
                    })
                except Exception as e:
                    import traceback
                    logger.error(f"Agent unexpected error proposing change for {file_path}: {e}")
                    logger.error(f"Full traceback: {traceback.format_exc()}")
                    errors.append({
                        "file_path": file_path,
                        "error": f"Unexpected error: {str(e)}"
                    })

            # If all files failed, return error
            if len(file_changes) == 0 and len(errors) > 0:
                return {
                    "success": False,
                    "error": f"All {len(errors)} file(s) failed to process",
                    "errors": errors
                }

            # Create a single changeset with all file changes
            import uuid
            from datetime import datetime, timedelta

            changeset_id = str(uuid.uuid4())[:8]
            now = datetime.now()
            expires_at = (now + timedelta(hours=1)).isoformat()

            # Prepare file changes for storage (only file_path and new_content)
            stored_changes = [
                {"file_path": fc["file_path"], "new_content": fc["new_content"]}
                for fc in file_changes
            ]

            # Store changeset in agent_system if available
            if self.agent_system:
                changeset_id = self.agent_system.store_changeset({
                    "changeset_id": changeset_id,
                    "file_changes": stored_changes
                })

            return {
                "success": True,
                "changeset_id": changeset_id,
                "files": [fc["file_path"] for fc in file_changes],
                "total_files": len(file_changes),
                "expires_at": expires_at,
                "errors": errors if errors else None,
                "message": f"Successfully proposed changeset with {len(file_changes)} file(s). Awaiting user approval."
            }

        except Exception as e:
            import traceback
            logger.error(f"Agent error in propose_config_changes: {e}")
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return {
                "success": False,
                "error": f"Error processing changes: {str(e)}"
            }

    # ------------------------------------------------------------------
    # Memory tools
    # ------------------------------------------------------------------

    async def read_memories(
        self,
        filename: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Read agent memory files that persist across sessions.

        Args:
            filename: Specific memory file to read (e.g. 'home_structure.md').
                      If omitted, all memory files are returned.

        Returns:
            Dict with:
                - success: bool
                - files: Dict[str, str]  {filename: content}
                - count: int
                - error: Optional[str]
        """
        if not self.memory_manager:
            return {"success": False, "error": "Memory manager not available", "files": {}, "count": 0}

        try:
            if filename:
                content = await self.memory_manager.read_file(filename)
                if content is None:
                    return {"success": True, "files": {}, "count": 0, "note": f"No memory file named '{filename}'"}
                return {"success": True, "files": {filename: content}, "count": 1}
            else:
                all_files = await self.memory_manager.get_all_files()
                return {"success": True, "files": all_files, "count": len(all_files)}
        except Exception as e:
            logger.error(f"read_memories error: {e}")
            return {"success": False, "error": str(e), "files": {}, "count": 0}

    async def save_memory(
        self,
        filename: str,
        content: str,
        replaces: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Save or update a persistent memory file.

        Use this to remember facts about the user's Home Assistant setup that
        will be useful in future sessions.

        Memory categories (use in filename, e.g. 'preference_temperature.md'):
        - preference: User-stated preferences ("I prefer 22°C in living room")
        - identity: Personal info, names, aliases
        - device: Device nicknames, groupings, locations
        - baseline: Normal sensor readings for this home
        - pattern: Recurring routines or schedules
        - correction: Overrides/corrections to previously stored facts

        What NOT to save:
        - Current device states or live sensor readings
        - Actions just performed (command echoes)
        - One-time commands vs. stated preferences
        - Device specs or capabilities (brightness range, color modes)
        - System-prompt content or inferred facts
        - Time-sensitive data (current weather, occupancy right now)

        Args:
            filename: Filename for the memory (e.g. 'preference_lighting.md').
                      Only alphanumerics, hyphens and underscores are kept;
                      the .md extension is forced.
            content: Markdown content to store. Keep concise and factual.
            replaces: Optional list of memory filenames that this new/corrected
                      memory supersedes. Those files will be deleted atomically
                      when this file is saved (use for corrections/updates).

        Returns:
            Dict with success bool, the sanitised filename used, and list of
            deleted filenames.
        """
        if not self.memory_manager:
            return {"success": False, "error": "Memory manager not available"}

        try:
            # Delete superseded files first
            deleted = []
            if replaces:
                for old_file in replaces:
                    if await self.memory_manager.delete_file(old_file):
                        deleted.append(old_file)
                        logger.info(f"save_memory: deleted superseded file '{old_file}'")

            ok = await self.memory_manager.write_file(filename, content)
            from pathlib import Path
            import re
            safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', Path(filename).stem).strip('_') or 'memory'
            safe_name = f"{safe_name}.md"
            return {"success": ok, "filename": safe_name, "deleted": deleted}
        except Exception as e:
            logger.error(f"save_memory error: {e}")
            return {"success": False, "error": str(e)}

    async def delete_memory(
        self,
        filename: str
    ) -> Dict[str, Any]:
        """
        Delete a persistent memory file that is no longer relevant.

        Args:
            filename: Name of the memory file to delete.

        Returns:
            Dict with success bool.
        """
        if not self.memory_manager:
            return {"success": False, "error": "Memory manager not available"}

        try:
            deleted = await self.memory_manager.delete_file(filename)
            return {"success": deleted, "deleted": deleted}
        except Exception as e:
            logger.error(f"delete_memory error: {e}")
            return {"success": False, "error": str(e)}

    async def list_memory_stats(self) -> Dict[str, Any]:
        """
        Return audit stats for all memory files: name, size, age in days.

        Use this periodically to review memory health — identify stale files
        (age_days > 90), oversized files, or files that could be merged.
        A file is flagged as stale when it hasn't been updated in 90+ days.

        Returns:
            Dict with files list (filename, chars, age_days, stale flag),
            total count, and enforced limits.
        """
        if not self.memory_manager:
            return {"success": False, "error": "Memory manager not available"}

        try:
            stats = await self.memory_manager.get_stats()
            return {"success": True, **stats}
        except Exception as e:
            logger.error(f"list_memory_stats error: {e}")
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Dashboard management tools
    # ------------------------------------------------------------------

    async def list_dashboards(self) -> Dict[str, Any]:
        """
        List all Lovelace dashboards in Home Assistant.

        Returns metadata for the default dashboard plus any custom dashboards.
        Use the url_path field to read or edit a specific dashboard via
        search_config_files (path: 'lovelace/{url_path}.yaml') or
        propose_config_changes (file_path: 'lovelace/{url_path}.yaml').

        Returns:
            Dict with:
                - success: bool
                - dashboards: List of dicts with url_path, title, icon, show_in_sidebar
                - count: int
        """
        try:
            dashboards = await self._get_all_dashboards()
            # Always prepend the default dashboard if not already present
            has_default = any(d.get('url_path') in (None, 'lovelace') for d in dashboards)
            result_list = []
            if not has_default:
                result_list.append({
                    "url_path": None,
                    "title": "Default",
                    "virtual_file": "lovelace.yaml",
                    "note": "Default Lovelace dashboard"
                })
            for d in dashboards:
                url_path = d.get('url_path')
                virtual_file = "lovelace.yaml" if url_path in (None, 'lovelace') else f"lovelace/{url_path}.yaml"
                result_list.append({
                    "url_path": url_path,
                    "title": d.get('title') or url_path or "Default",
                    "icon": d.get('icon'),
                    "show_in_sidebar": d.get('show_in_sidebar', True),
                    "virtual_file": virtual_file,
                })
            logger.info(f"list_dashboards returned {len(result_list)} dashboard(s)")
            return {"success": True, "dashboards": result_list, "count": len(result_list)}
        except Exception as e:
            logger.error(f"list_dashboards error: {e}")
            return {"success": False, "error": str(e), "dashboards": [], "count": 0}

    async def create_dashboard(
        self,
        title: str,
        url_path: Optional[str] = None,
        icon: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new Lovelace dashboard.

        After creating, you can populate it by calling propose_config_changes
        with file_path='lovelace/{url_path}.yaml'.

        Args:
            title: Human-readable dashboard title (e.g. 'Kitchen Dashboard')
            url_path: URL slug (e.g. 'kitchen'). Auto-generated from title if omitted.
            icon: Material Design icon string (e.g. 'mdi:silverware-fork-knife')

        Returns:
            Dict with:
                - success: bool
                - url_path: str — the dashboard's URL slug
                - virtual_file: str — use this path with propose_config_changes
                - error: Optional[str]
        """
        try:
            supervisor_token = os.getenv('SUPERVISOR_TOKEN')
            if self.config_manager.hass is None and not supervisor_token:
                return {
                    "success": False,
                    "error": "Dashboard creation requires add-on mode with SUPERVISOR_TOKEN"
                }

            if self.config_manager.hass is not None:
                # Custom component mode: use WebSocket via HA's internal connection
                # HA itself is running — connect via the internal token if available
                # Fall back to WebSocket via loopback if possible
                try:
                    from homeassistant.components.lovelace.const import DOMAIN as LOVELACE_DOMAIN
                    hass = self.config_manager.hass
                    # Use HA's lovelace storage manager directly
                    lovelace_data = hass.data.get(LOVELACE_DOMAIN)
                    if lovelace_data and hasattr(lovelace_data, 'async_create_dashboard'):
                        result = await lovelace_data.async_create_dashboard(
                            {"title": title, "url_path": url_path, "icon": icon}
                        )
                        created_url_path = result.get('url_path', url_path or title.lower().replace(' ', '-'))
                        virtual_file = f"lovelace/{created_url_path}.yaml"
                        logger.info(f"Created dashboard '{title}' via hass API")
                        return {"success": True, "url_path": created_url_path, "virtual_file": virtual_file, "dashboard": result}
                    else:
                        return {"success": False, "error": "Lovelace async_create_dashboard not available in this HA version"}
                except Exception as e:
                    return {"success": False, "error": f"Could not create dashboard via hass API: {e}"}

            # Add-on mode: WebSocket
            ws_url = "ws://supervisor/core/websocket"
            result = await create_lovelace_dashboard_ws(ws_url, supervisor_token, title, url_path, icon)
            if result is None:
                return {"success": False, "error": "Dashboard creation failed (check logs)"}

            created_url_path = result.get('url_path', url_path or title.lower().replace(' ', '-'))
            virtual_file = f"lovelace/{created_url_path}.yaml"
            logger.info(f"Created dashboard '{title}' (url_path={created_url_path})")
            return {"success": True, "url_path": created_url_path, "virtual_file": virtual_file, "dashboard": result}

        except Exception as e:
            logger.error(f"create_dashboard error: {e}")
            return {"success": False, "error": str(e)}

    async def delete_dashboard(self, url_path: str) -> Dict[str, Any]:
        """
        Delete a Lovelace dashboard.

        Args:
            url_path: The dashboard URL slug to delete

        Returns:
            Dict with success and optional error
        """
        if url_path in (None, 'lovelace', ''):
            return {"success": False, "error": "Cannot delete the default dashboard"}
        try:
            supervisor_token = os.getenv('SUPERVISOR_TOKEN')
            if supervisor_token:
                ws_url = "ws://supervisor/core/websocket"
                ok = await delete_lovelace_dashboard_ws(ws_url, supervisor_token, url_path)
                if ok:
                    # Invalidate cache for this dashboard
                    self._lovelace_cache.pop(url_path, None)
                    return {"success": True}
                return {"success": False, "error": "Deletion failed (check logs)"}

            # Custom component mode — try hass API
            if self.config_manager.hass is not None:
                from homeassistant.components.lovelace.const import DOMAIN as LOVELACE_DOMAIN
                hass = self.config_manager.hass
                lovelace_data = hass.data.get(LOVELACE_DOMAIN)
                if lovelace_data and hasattr(lovelace_data, 'async_delete_dashboard'):
                    await lovelace_data.async_delete_dashboard(url_path)
                    self._lovelace_cache.pop(url_path, None)
                    return {"success": True}
                return {"success": False, "error": "Lovelace async_delete_dashboard not available"}

            return {"success": False, "error": "No WebSocket token or hass instance available"}
        except Exception as e:
            logger.error(f"delete_dashboard error: {e}")
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Entity states tool (for automation suggestions)
    # ------------------------------------------------------------------

    async def get_nodered_flows(self) -> Dict[str, Any]:
        """
        Retrieve Node-RED flows via the Node-RED Admin REST API, with fallback
        to a locally exported JSON file.

        Primary method: GET {NODERED_URL}/flows
            - Set NODERED_URL to your Node-RED instance (e.g. http://homeassistant:1880)
            - Set NODERED_TOKEN if Node-RED admin auth is enabled (optional)

        Fallback: reads NODERED_FLOWS_FILE (path relative to HA config dir)

        Returns:
            Dict with:
                - success: bool
                - flows: list of Node-RED flow/node objects
                - count: int
                - source: "api" | "file"
                - error: Optional[str]
        """
        import json
        import aiohttp

        nodered_url = os.getenv('NODERED_URL', '').rstrip('/')
        nodered_token = os.getenv('NODERED_TOKEN', '')

        # --- Primary: Node-RED Admin API ---
        if nodered_url:
            try:
                headers = {
                    "Accept": "application/json",
                    "Node-RED-API-Version": "v2",
                }
                if nodered_token:
                    headers["Authorization"] = f"Bearer {nodered_token}"

                flows_url = f"{nodered_url}/flows"
                logger.info(f"Fetching Node-RED flows from {flows_url}")

                async with aiohttp.ClientSession() as session:
                    async with session.get(flows_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status == 200:
                            data = await resp.json(content_type=None)
                            # API v2 returns {"flows": [...], "rev": "..."}
                            # API v1 returns [...] directly
                            flows = data.get("flows", data) if isinstance(data, dict) else data
                            if not isinstance(flows, list):
                                raise ValueError(f"Unexpected response shape: {type(flows)}")
                            logger.info(f"Retrieved {len(flows)} Node-RED flow nodes via API")
                            return {
                                "success": True,
                                "flows": flows,
                                "count": len(flows),
                                "source": "api",
                            }
                        elif resp.status == 401:
                            logger.warning("Node-RED API returned 401 — token required or incorrect")
                            # Don't fall through to file on auth failure, report clearly
                            return {
                                "success": False,
                                "error": "Node-RED API authentication failed (401). Set NODERED_TOKEN in integration options.",
                                "flows": [],
                                "count": 0,
                                "source": "api",
                            }
                        else:
                            text = await resp.text()
                            logger.warning(f"Node-RED API returned {resp.status}: {text[:200]}")
                            # Fall through to file fallback
            except aiohttp.ClientError as e:
                logger.warning(f"Node-RED API request failed ({e}), trying file fallback")
            except Exception as e:
                logger.warning(f"Node-RED API error ({e}), trying file fallback")

        # --- Fallback: exported flows JSON file ---
        flows_file = os.getenv('NODERED_FLOWS_FILE', '')
        if flows_file:
            try:
                from pathlib import Path

                config_dir = self.config_manager.config_dir
                flows_path = Path(flows_file)
                if not flows_path.is_absolute():
                    flows_path = config_dir / flows_path

                resolved = flows_path.resolve()
                config_resolved = config_dir.resolve()
                if not str(resolved).startswith(str(config_resolved)) and not Path(flows_file).is_absolute():
                    return {
                        "success": False,
                        "error": "Flows file path must be within the HA config directory",
                        "flows": [], "count": 0,
                    }

                if not resolved.exists():
                    return {
                        "success": False,
                        "error": f"Node-RED flows file not found: {resolved}",
                        "flows": [], "count": 0,
                    }

                flows = json.loads(resolved.read_text(encoding="utf-8"))
                if not isinstance(flows, list):
                    return {
                        "success": False,
                        "error": "Node-RED flows file does not contain a JSON array",
                        "flows": [], "count": 0,
                    }

                logger.info(f"Loaded {len(flows)} Node-RED flow nodes from file {resolved}")
                return {"success": True, "flows": flows, "count": len(flows), "source": "file"}

            except json.JSONDecodeError as e:
                return {"success": False, "error": f"Invalid JSON in flows file: {e}", "flows": [], "count": 0}
            except Exception as e:
                logger.error(f"get_nodered_flows file fallback error: {e}", exc_info=True)
                return {"success": False, "error": str(e), "flows": [], "count": 0}

        # Neither configured
        return {
            "success": False,
            "error": (
                "Node-RED not configured. Set 'Node-RED URL' (e.g. http://homeassistant:1880) "
                "in the integration options, or provide a 'Node-RED Flows File' path as fallback."
            ),
            "flows": [],
            "count": 0,
        }

    async def get_entity_states(
        self,
        domain_filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get current live states of all Home Assistant entities.

        This is essential for automation suggestions: it shows what devices
        exist, their current states, and their attributes so the AI can
        reason about what automations would be useful.

        Args:
            domain_filter: Optional HA domain to filter by (e.g. 'light',
                           'switch', 'sensor', 'binary_sensor', 'climate').
                           If omitted, all entities are returned.

        Returns:
            Dict with:
                - success: bool
                - states: List[Dict]  each with entity_id, state, attributes, last_changed
                - count: int
                - domain_filter: Optional[str]
                - error: Optional[str]
        """
        def _truncate_attrs(attributes: dict, max_val_len: int = 200) -> dict:
            """Truncate overly long attribute values (e.g. color maps, media art)."""
            result = {}
            for k, v in attributes.items():
                sv = str(v)
                result[k] = sv[:max_val_len] + "…" if len(sv) > max_val_len else v
            return result

        try:
            states_list: List[Dict[str, Any]] = []

            # Custom component mode: use hass directly
            if self.config_manager.hass is not None:
                from homeassistant.helpers import entity_registry as er, area_registry as ar

                hass = self.config_manager.hass
                entity_reg = er.async_get(hass)
                area_reg = ar.async_get(hass)

                ha_states = hass.states.async_all(domain_filter) if domain_filter else hass.states.async_all()
                for state in ha_states:
                    entry = entity_reg.async_get(state.entity_id)
                    area_name = None
                    if entry and entry.area_id:
                        area = area_reg.async_get_area(entry.area_id)
                        if area:
                            area_name = area.name

                    friendly_name = state.attributes.get("friendly_name")
                    states_list.append({
                        "entity_id": state.entity_id,
                        "friendly_name": friendly_name,
                        "state": state.state,
                        "attributes": _truncate_attrs(dict(state.attributes)),
                        "area": area_name,
                        "last_changed": state.last_changed.isoformat() if state.last_changed else None,
                    })
                logger.info(f"Retrieved {len(states_list)} entity states via hass API")

            else:
                # Add-on mode: use WebSocket API
                supervisor_token = os.getenv('SUPERVISOR_TOKEN')
                if not supervisor_token:
                    return {"success": False, "error": "SUPERVISOR_TOKEN not available", "states": [], "count": 0}

                from ..ha.ha_websocket import HomeAssistantWebSocket
                ws_url = "ws://supervisor/core/websocket"
                ws_client = HomeAssistantWebSocket(ws_url, supervisor_token)
                await ws_client.connect()
                try:
                    raw_states = await ws_client.get_states()
                    for s in raw_states:
                        if domain_filter and not s.get("entity_id", "").startswith(f"{domain_filter}."):
                            continue
                        states_list.append({
                            "entity_id": s.get("entity_id"),
                            "friendly_name": s.get("attributes", {}).get("friendly_name"),
                            "state": s.get("state"),
                            "attributes": _truncate_attrs(s.get("attributes", {})),
                            "area": None,  # not available in WebSocket get_states response
                            "last_changed": s.get("last_changed"),
                        })
                finally:
                    await ws_client.close()
                logger.info(f"Retrieved {len(states_list)} entity states via WebSocket")

            return {
                "success": True,
                "states": states_list,
                "count": len(states_list),
                "domain_filter": domain_filter,
            }

        except Exception as e:
            logger.error(f"get_entity_states error: {e}", exc_info=True)
            return {"success": False, "error": str(e), "states": [], "count": 0}
