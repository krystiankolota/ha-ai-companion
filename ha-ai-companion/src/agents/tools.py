"""
AI Agent Tool Functions

Tool functions that agents can call to interact with configuration files.
These wrap the ConfigurationManager for safe AI operations.
"""
import asyncio
import logging
import math
import os
import time
from typing import Dict, Any, Optional, List, TYPE_CHECKING
from ..config import ConfigurationManager, ConfigurationError
from ..ha.ha_websocket import (
    get_lovelace_config_as_yaml,
    get_repairs_ws,
    list_lovelace_dashboards_ws,
    create_lovelace_dashboard_ws,
    delete_lovelace_dashboard_ws,
    reload_homeassistant_config,
)

if TYPE_CHECKING:
    from ..memory.manager import MemoryManager
    from ..conversations.manager import ConversationManager

try:
    import numpy as _np
    _HAS_NUMPY = True
except ImportError:
    _np = None
    _HAS_NUMPY = False

logger = logging.getLogger(__name__)

_EMBED_BATCH = 100      # entities per embeddings API call
_EMBED_TOP_K = 40       # entities returned by semantic search
_EMBED_TTL   = 1800     # cache lifetime in seconds (30 min)


class EntityEmbeddingCache:
    """Lightweight in-memory vector store for entity semantic search."""

    def __init__(self):
        self.entities: List[Dict] = []
        self._matrix = None          # numpy float32 (N, D) normalized, or list[list[float]]
        self._built_at: float = 0.0

    def is_valid(self) -> bool:
        return self._matrix is not None and (time.monotonic() - self._built_at) < _EMBED_TTL

    def build(self, entities: List[Dict], embeddings: List[List[float]]) -> None:
        self.entities = entities
        if _HAS_NUMPY:
            mat = _np.array(embeddings, dtype=_np.float32)
            norms = _np.linalg.norm(mat, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            self._matrix = mat / norms
        else:
            normed = []
            for emb in embeddings:
                mag = math.sqrt(sum(x * x for x in emb)) or 1.0
                normed.append([x / mag for x in emb])
            self._matrix = normed
        self._built_at = time.monotonic()
        logger.info(f"EntityEmbeddingCache built: {len(entities)} entities")

    def search(self, query_emb: List[float], top_k: int = _EMBED_TOP_K) -> List[int]:
        if _HAS_NUMPY:
            q = _np.array(query_emb, dtype=_np.float32)
            mag = _np.linalg.norm(q)
            if mag > 0:
                q = q / mag
            scores = self._matrix @ q
            return _np.argsort(scores)[::-1][:top_k].tolist()
        else:
            mag = math.sqrt(sum(x * x for x in query_emb)) or 1.0
            q = [x / mag for x in query_emb]
            scores = [sum(a * b for a, b in zip(q, e)) for e in self._matrix]
            return sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]

    def invalidate(self) -> None:
        self._matrix = None
        self.entities = []
        self._built_at = 0.0

    @staticmethod
    def entity_text(e: Dict) -> str:
        name = e.get("friendly_name") or e.get("entity_id", "")
        area = f" in {e['area']}" if e.get("area") else ""
        return f"{name} [{e['entity_id']}]{area}"


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
        conversation_manager: Optional['ConversationManager'] = None,
    ):
        """
        Initialize agent tools with a configuration manager.

        Args:
            config_manager: ConfigurationManager instance for file operations
            workflow: Optional ValidationWorkflow for approval management
            agent_system: Optional AgentSystem for changeset storage
            memory_manager: Optional MemoryManager for persistent memories
            conversation_manager: Optional ConversationManager for session search
        """
        self.config_manager = config_manager
        self.workflow = workflow
        self.agent_system = agent_system
        self.memory_manager = memory_manager
        self.conversation_manager = conversation_manager
        self._lovelace_cache: Dict[Optional[str], str] = {}  # {url_path: yaml_str}
        self._lovelace_lock = asyncio.Lock()
        self._turn_registry_cache: Dict[str, Any] = {}  # cleared each chat turn
        self._entity_cache = EntityEmbeddingCache()
        logger.info("AgentTools initialized")

    def clear_turn_cache(self) -> None:
        """Clear per-turn registry cache. Called at the start of each chat turn."""
        self._turn_registry_cache.clear()

    def _push_status(self, message: str) -> None:
        """Push a progress event to the agent's streaming queue (no-op if not streaming)."""
        q = getattr(self.agent_system, '_tool_status_queue', None)
        if q is not None:
            q.put_nowait({"message": message})

    async def _get_lovelace_config(self, url_path: Optional[str] = None) -> Optional[str]:
        """
        Internal helper to retrieve Lovelace config for one dashboard.

        Args:
            url_path: Dashboard URL path (e.g. 'kitchen'). None = default dashboard.

        Returns:
            YAML string of Lovelace config, or None if not available
        """
        cache_key = url_path  # None for default

        async with self._lovelace_lock:
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
                    yaml.preserve_quotes = True
                    yaml.indent(mapping=2, sequence=2, offset=2)
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
        Results are cached per-turn to avoid redundant WS calls when multiple
        search_config_files calls happen in the same agent iteration.

        Returns:
            List of device dictionaries
        """
        if "devices" in self._turn_registry_cache:
            return self._turn_registry_cache["devices"]
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
                self._turn_registry_cache["devices"] = devices
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
            self._turn_registry_cache["devices"] = devices
            return devices
        except Exception as e:
            logger.debug(f"Failed to get devices: {e}")
            return []

    async def _get_all_entities(self) -> List[Dict[str, Any]]:
        """
        Internal helper to retrieve all entities from registry.

        Uses hass API in custom component mode, WebSocket in add-on mode.
        Results are cached per-turn to avoid redundant WS calls.

        Returns:
            List of entity dictionaries
        """
        if "entities" in self._turn_registry_cache:
            return self._turn_registry_cache["entities"]
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
                self._turn_registry_cache["entities"] = entities
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
            self._turn_registry_cache["entities"] = entities
            return entities
        except Exception as e:
            logger.debug(f"Failed to get entities: {e}")
            return []

    async def _get_all_areas(self) -> List[Dict[str, Any]]:
        """
        Internal helper to retrieve all areas from registry.

        Uses hass API in custom component mode, WebSocket in add-on mode.
        Results are cached per-turn to avoid redundant WS calls.

        Returns:
            List of area dictionaries
        """
        if "areas" in self._turn_registry_cache:
            return self._turn_registry_cache["areas"]
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
                self._turn_registry_cache["areas"] = areas
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
            self._turn_registry_cache["areas"] = areas
            return areas
        except Exception as e:
            logger.debug(f"Failed to get areas: {e}")
            return []

    async def _validate_entity_ids_in_yaml(self, yaml_content: str) -> List[Dict[str, Any]]:
        """
        Extract entity_id references from YAML content and validate against the entity registry.

        Focuses on explicit `entity_id:` fields (single values and lists). Returns a list of
        unknown entity IDs with similar known entities as suggestions.

        Only matches entity_id fields — avoids false positives from service calls, template
        variables, or comment lines.
        """
        import re

        # Extract entity_ids from explicit entity_id: fields and list items under them
        found_ids: set = set()

        # Pattern 1: entity_id: domain.entity  (single value on same line)
        for m in re.finditer(r'entity_id\s*:\s+([a-z][a-z0-9_]*\.[a-z0-9_]+)', yaml_content):
            found_ids.add(m.group(1))

        # Pattern 2: entity_id as a list, items on subsequent lines
        # Find each "entity_id:" line then collect following "- domain.entity" lines
        lines = yaml_content.splitlines()
        i = 0
        while i < len(lines):
            stripped = lines[i].lstrip()
            if re.match(r'entity_id\s*:\s*$', stripped) or re.match(r'entity_id\s*:\s*\[', stripped):
                # Collect list items
                i += 1
                while i < len(lines):
                    item_line = lines[i].lstrip()
                    if not item_line or item_line.startswith('#'):
                        i += 1
                        continue
                    item_m = re.match(r'-\s+([a-z][a-z0-9_]*\.[a-z0-9_]+)', item_line)
                    if item_m:
                        found_ids.add(item_m.group(1))
                        i += 1
                    else:
                        break
            else:
                i += 1

        if not found_ids:
            return []

        # Get entity registry (cached per turn)
        entities = await self._get_all_entities()
        known_ids = {e.get('entity_id') for e in entities if e.get('entity_id')}

        if not known_ids:
            return []  # Can't validate without entity list

        warnings = []
        for eid in sorted(found_ids):
            if eid in known_ids:
                continue

            domain = eid.split('.')[0] if '.' in eid else ''
            slug = eid.split('.', 1)[1] if '.' in eid else eid

            # Find similar entities in same domain by slug overlap
            same_domain = [e['entity_id'] for e in entities if e.get('entity_id', '').startswith(f'{domain}.')]
            # Score: how much of the slug appears in the candidate
            def _similarity(candidate_id: str) -> int:
                c_slug = candidate_id.split('.', 1)[1] if '.' in candidate_id else candidate_id
                # Count shared substrings of length >= 3
                score = 0
                for n in range(3, len(slug) + 1):
                    for start in range(len(slug) - n + 1):
                        if slug[start:start+n] in c_slug:
                            score += n
                return score

            suggestions = sorted(same_domain, key=_similarity, reverse=True)[:3]
            warnings.append({
                "entity_id": eid,
                "status": "not_found_in_registry",
                "suggestions": suggestions,
                "action": "Fix the entity_id to a known one from suggestions, or remove this automation/reference if the entity no longer exists.",
            })

        return warnings

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
                # Find all YAML files + known text reports (watchman)
                matched_paths = list(config_dir.glob("**/*.yaml"))
                matched_paths += [p for p in config_dir.glob("*.txt") if p.is_file()]

            # Filter to only files (not directories) and exclude custom_components
            matched_paths = [
                p for p in matched_paths
                if p.is_file() and 'custom_components' not in p.parts and 'secrets.yaml' not in p.parts
            ]

            # Sort for consistent results
            matched_paths.sort()

            # Read files and optionally filter by content search
            files = []
            total_yaml = len(matched_paths)
            if total_yaml > 0:
                self._push_status(f"Scanning {total_yaml} YAML files…")
            for path in matched_paths:
                relative_path = str(path.relative_to(config_dir))
                self._push_status(f"Reading {relative_path}")
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
                    self._push_status("Checking device registry…")
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
                    self._push_status("Checking entity registry…")
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
                    self._push_status("Checking area registry…")
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
                self._push_status("Reading dashboards…")
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
        changes: List[Dict[str, str]],
        confirm_delete: bool = False,
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
                    # Covers both single-file setups (automations.yaml) and split-file setups
                    # (automations/heating.yaml, automations/lights.yaml, etc.)
                    _GUARDED_FILES = ('automations.yaml', 'scripts.yaml', 'scenes.yaml')
                    _GUARDED_DIRS = ('automations', 'scripts', 'scenes')
                    _is_guarded = (
                        any(file_path.endswith(g) for g in _GUARDED_FILES) or
                        any(f'/{d}/' in f'/{file_path}' for d in _GUARDED_DIRS)
                    )
                    if _is_guarded and not confirm_delete and current_content and current_content.strip():
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

                    # Entity ID validation for automation/script/scene YAML
                    # Soft check: warn LLM about unknown entity_ids without blocking.
                    # Only run for YAML files that reference entities, skip Lovelace/devices/etc.
                    entity_warnings: List[Dict] = []
                    _check_file = (
                        file_path.endswith('.yaml')
                        and not file_path.startswith('lovelace/')
                        and file_path != 'lovelace.yaml'
                        and not file_path.startswith('devices/')
                        and not file_path.startswith('entities/')
                        and not file_path.startswith('areas/')
                    )
                    if _check_file and new_content.strip():
                        try:
                            entity_warnings = await self._validate_entity_ids_in_yaml(new_content)
                        except Exception as _ev:
                            logger.debug(f"Entity ID check skipped for {file_path}: {_ev}")

                    # Add to file changes list
                    file_changes.append({
                        "file_path": file_path,
                        "current_content": current_content,
                        "new_content": new_content,
                        "entity_warnings": entity_warnings,
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

            # Compute per-file diff stats for UI display
            diff_stats = []
            for fc in file_changes:
                old_lines = fc["current_content"].splitlines()
                new_lines = fc["new_content"].splitlines()
                old_set = set(old_lines)
                new_set = set(new_lines)
                added = sum(1 for l in new_lines if l not in old_set)
                removed = sum(1 for l in old_lines if l not in new_set)
                is_new_file = not fc["current_content"].strip()
                diff_stats.append({
                    "file_path": fc["file_path"],
                    "added": added,
                    "removed": removed,
                    "is_new_file": is_new_file,
                })

            # Collect all entity warnings across files
            all_entity_warnings = []
            for fc in file_changes:
                for w in fc.get("entity_warnings", []):
                    all_entity_warnings.append({**w, "file": fc["file_path"]})

            result = {
                "success": True,
                "changeset_id": changeset_id,
                "files": [fc["file_path"] for fc in file_changes],
                "total_files": len(file_changes),
                "expires_at": expires_at,
                "diff_stats": diff_stats,
                "errors": errors if errors else None,
                "message": f"Successfully proposed changeset with {len(file_changes)} file(s). Awaiting user approval."
            }
            if all_entity_warnings:
                result["entity_warnings"] = all_entity_warnings
                result["message"] += (
                    f"\n\nWARNING: {len(all_entity_warnings)} entity_id reference(s) were NOT found in the entity registry. "
                    "Review each one — fix the entity_id, or remove/comment out the automation if the entity no longer exists. "
                    "Suggestions are provided where available."
                )
            return result

        except Exception as e:
            import traceback
            logger.error(f"Agent error in propose_config_changes: {e}")
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return {
                "success": False,
                "error": f"Error processing changes: {str(e)}"
            }

    # ------------------------------------------------------------------
    # Surgical config patching tools
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_yaml_path(key_path: str) -> List:
        """Parse dot-notation key path into navigation segments.

        Supports:
          "logger.default"                     → ["logger", "default"]
          "automations[0].trigger"             → ["automations", 0, "trigger"]
          "automations[alias=Morning light]"   → ["automations", {"field": "alias", "value": "Morning light"}]
        """
        import re
        segments = []
        for part in re.split(r'\.(?![^\[]*\])', key_path):
            if not part:
                continue
            m = re.match(r'^(.*?)\[(.+)\]$', part)
            if m:
                key_part, selector = m.group(1), m.group(2)
                if key_part:
                    segments.append(key_part)
                if re.match(r'^\d+$', selector):
                    segments.append(int(selector))
                elif '=' in selector:
                    field, value = selector.split('=', 1)
                    segments.append({'field': field.strip(), 'value': value.strip()})
                else:
                    segments.append(selector)
            else:
                segments.append(part)
        return segments

    @staticmethod
    def _navigate_yaml(data, segments):
        """Navigate ruamel.yaml data by path segments.

        Returns:
            (parent, final_key, success) — caller sets parent[final_key] = new_value.
            Returns (None, None, False) on navigation failure.
        """
        current = data
        parent = None
        last_key = None

        for seg in segments:
            parent = current
            if isinstance(seg, int):
                if not isinstance(current, list) or seg >= len(current):
                    return None, None, False
                last_key = seg
                current = current[seg]
            elif isinstance(seg, dict):
                # field=value selector for list items
                if not isinstance(current, list):
                    return None, None, False
                field, value = seg['field'], seg['value']
                idx = next(
                    (i for i, item in enumerate(current)
                     if isinstance(item, dict) and str(item.get(field, '')) == value),
                    None,
                )
                if idx is None:
                    return None, None, False
                last_key = idx
                current = current[idx]
            else:
                if not isinstance(current, dict) or seg not in current:
                    return None, None, False
                last_key = seg
                current = current[seg]

        return parent, last_key, True

    async def patch_config_key(
        self,
        file_path: str,
        key_path: str,
        new_value: Any,
        description: str = "",
    ) -> Dict[str, Any]:
        """
        Surgical patch: change a single YAML key without rewriting the whole file.

        Use this instead of propose_config_changes when changing one known key.
        Comments, ordering, and all other keys are preserved exactly.

        Args:
            file_path:   Relative path to the config file (e.g. 'configuration.yaml').
            key_path:    Dot-notation path to the key, e.g. 'logger.default'.
                         List index: 'automations[0].trigger'
                         List item by field: 'automations[alias=Morning light].trigger'
            new_value:   New value to set (string, number, bool, list, or dict).
            description: Optional human-readable description of what this patch does.

        Returns the same changeset dict as propose_config_changes — goes through
        the normal approval workflow.
        """
        try:
            from ruamel.yaml import YAML
            from io import StringIO
            from datetime import datetime, timedelta
            import uuid

            current_content = await self.config_manager.read_file_raw(file_path, allow_missing=False)
            if current_content is None:
                return {"success": False, "error": f"File not found: {file_path}"}

            yaml_parser = YAML()
            yaml_parser.preserve_quotes = True
            yaml_parser.default_flow_style = False
            data = yaml_parser.load(StringIO(current_content))
            if data is None:
                return {"success": False, "error": f"File is empty or unparseable: {file_path}"}

            segments = self._parse_yaml_path(key_path)
            if not segments:
                return {"success": False, "error": f"Invalid key_path: {key_path!r}"}

            parent, final_key, found = self._navigate_yaml(data, segments)
            if not found:
                return {
                    "success": False,
                    "error": (
                        f"Key path {key_path!r} not found in {file_path}. "
                        "Call search_config_files to read the file and verify the exact path."
                    ),
                }

            parent[final_key] = new_value

            buf = StringIO()
            yaml_parser.dump(data, buf)
            new_content = buf.getvalue()

            changeset_id = str(uuid.uuid4())[:8]
            stored_changes = [{"file_path": file_path, "new_content": new_content}]
            if self.agent_system:
                changeset_id = self.agent_system.store_changeset({
                    "changeset_id": changeset_id,
                    "file_changes": stored_changes,
                })

            old_lines = current_content.splitlines()
            new_lines = new_content.splitlines()
            old_set, new_set = set(old_lines), set(new_lines)
            diff_stats = [{
                "file_path": file_path,
                "added": sum(1 for l in new_lines if l not in old_set),
                "removed": sum(1 for l in old_lines if l not in new_set),
                "is_new_file": False,
            }]

            now = datetime.now()
            return {
                "success": True,
                "changeset_id": changeset_id,
                "files": [file_path],
                "total_files": 1,
                "expires_at": (now + timedelta(hours=1)).isoformat(),
                "diff_stats": diff_stats,
                "message": (
                    f"Patched {key_path!r} in {file_path}."
                    + (f" {description}" if description else "")
                    + " Awaiting user approval."
                ),
            }

        except Exception as e:
            import traceback
            logger.error(f"patch_config_key error: {e}\n{traceback.format_exc()}")
            return {"success": False, "error": str(e)}

    async def patch_config_block(
        self,
        file_path: str,
        anchor: str,
        new_block: str,
        description: str = "",
    ) -> Dict[str, Any]:
        """
        Replace an entire YAML block (subtree) without rewriting the whole file.

        Use this instead of propose_config_changes when replacing a named section
        such as one automation entry, the logger block, or a homeassistant section.
        All other parts of the file are preserved exactly.

        Args:
            file_path:  Relative path to the config file (e.g. 'automations.yaml').
            anchor:     Dot-notation path to the block to replace, e.g. 'logger' or
                        'automations[alias=Morning light]'. Same syntax as patch_config_key.
            new_block:  Valid YAML string for the replacement block. Must match the
                        structure of the node being replaced.
            description: Optional human-readable description of what this patch does.

        Returns the same changeset dict as propose_config_changes — goes through
        the normal approval workflow.
        """
        try:
            from ruamel.yaml import YAML
            from io import StringIO
            from datetime import datetime, timedelta
            import uuid

            current_content = await self.config_manager.read_file_raw(file_path, allow_missing=False)
            if current_content is None:
                return {"success": False, "error": f"File not found: {file_path}"}

            yaml_parser = YAML()
            yaml_parser.preserve_quotes = True
            yaml_parser.default_flow_style = False
            data = yaml_parser.load(StringIO(current_content))
            if data is None:
                return {"success": False, "error": f"File is empty or unparseable: {file_path}"}

            try:
                new_node = yaml_parser.load(StringIO(new_block))
            except Exception as e:
                return {"success": False, "error": f"Invalid YAML in new_block: {e}"}

            segments = self._parse_yaml_path(anchor)
            if not segments:
                return {"success": False, "error": f"Invalid anchor: {anchor!r}"}

            parent, final_key, found = self._navigate_yaml(data, segments)
            if not found:
                return {
                    "success": False,
                    "error": (
                        f"Anchor {anchor!r} not found in {file_path}. "
                        "Call search_config_files to read the file and verify the exact path."
                    ),
                }

            parent[final_key] = new_node

            buf = StringIO()
            yaml_parser.dump(data, buf)
            new_content = buf.getvalue()

            changeset_id = str(uuid.uuid4())[:8]
            stored_changes = [{"file_path": file_path, "new_content": new_content}]
            if self.agent_system:
                changeset_id = self.agent_system.store_changeset({
                    "changeset_id": changeset_id,
                    "file_changes": stored_changes,
                })

            old_lines = current_content.splitlines()
            new_lines = new_content.splitlines()
            old_set, new_set = set(old_lines), set(new_lines)
            diff_stats = [{
                "file_path": file_path,
                "added": sum(1 for l in new_lines if l not in old_set),
                "removed": sum(1 for l in old_lines if l not in new_set),
                "is_new_file": False,
            }]

            now = datetime.now()
            return {
                "success": True,
                "changeset_id": changeset_id,
                "files": [file_path],
                "total_files": 1,
                "expires_at": (now + timedelta(hours=1)).isoformat(),
                "diff_stats": diff_stats,
                "message": (
                    f"Replaced block at {anchor!r} in {file_path}."
                    + (f" {description}" if description else "")
                    + " Awaiting user approval."
                ),
            }

        except Exception as e:
            import traceback
            logger.error(f"patch_config_block error: {e}\n{traceback.format_exc()}")
            return {"success": False, "error": str(e)}

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
            if not ok:
                limit = self.memory_manager.MAX_FILE_CHARS
                actual = len(content.strip())
                if actual > limit:
                    return {"success": False, "filename": safe_name, "deleted": deleted,
                            "error": f"content too long: {actual} chars exceeds limit {limit}. Shorten the content and retry."}
                return {"success": False, "filename": safe_name, "deleted": deleted,
                        "error": "write failed (I/O error or file count limit reached)"}
            return {"success": True, "filename": safe_name, "deleted": deleted}
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

    async def consolidate_memories(self) -> Dict[str, Any]:
        """
        Audit all memory files and propose a consolidation plan.

        Reads every memory file, identifies: duplicates, near-empty files,
        contradictions between files, and stale facts. Returns a plain-text
        consolidation plan for the user to review. The plan is NOT applied
        automatically — the user confirms and you apply it using save_memory
        and delete_memory.

        Returns:
            Dict with success bool and 'plan' string describing proposed actions.
        """
        if not self.memory_manager:
            return {"success": False, "error": "Memory manager not available"}

        try:
            all_files = await self.memory_manager.get_all_files()
            if not all_files:
                return {"success": True, "plan": "No memory files to consolidate.", "file_count": 0}

            stats = await self.memory_manager.get_stats()
            stale_names = {f["filename"] for f in stats.get("files", []) if f.get("stale")}
            tiny_names  = {f["filename"] for f in stats.get("files", []) if f.get("chars", 999) < 50}

            # Build a summary for the LLM to analyse
            summary_parts = [
                "Review these memory files and produce a consolidation plan.\n",
                "For each action propose: MERGE (list files to combine + suggested new filename), "
                "DELETE (with reason), or KEEP.\n\n",
            ]
            for name, content in all_files.items():
                flags = []
                if name in stale_names:
                    flags.append("STALE")
                if name in tiny_names:
                    flags.append("TINY")
                flag_str = f" [{', '.join(flags)}]" if flags else ""
                summary_parts.append(f"=== {name}{flag_str} ===\n{content}\n\n")

            return {
                "success": True,
                "files_reviewed": len(all_files),
                "stale_count": len(stale_names),
                "tiny_count": len(tiny_names),
                "memory_content_for_analysis": "".join(summary_parts),
                "instruction": (
                    "Analyse the memory_content_for_analysis above. "
                    "Produce a numbered consolidation plan listing each proposed action "
                    "(MERGE/DELETE/KEEP) with clear reasoning. "
                    "Present the plan to the user as readable text before taking any action. "
                    "Only call save_memory/delete_memory after the user says to proceed."
                ),
            }
        except Exception as e:
            logger.error(f"consolidate_memories error: {e}")
            return {"success": False, "error": str(e)}

    async def search_past_sessions(self, query: str, limit: int = 3) -> Dict[str, Any]:
        """
        Keyword search across past conversation sessions.

        Searches user and assistant messages from stored sessions and returns
        the most relevant ones with excerpts.  Use this when the user references
        something that may have been discussed in a previous session, or to recall
        past decisions before starting a related task.

        Args:
            query: Natural-language search query (e.g. "morning routine", "boiler automation").
            limit: Max number of sessions to return (default 3, max 5).

        Returns:
            Dict with success bool and a 'sessions' list, each entry containing:
                session_id, title, updated_at, total_hits, and up to 3 message excerpts.
        """
        if not self.conversation_manager:
            return {"success": False, "error": "Conversation manager not available", "sessions": []}

        try:
            results = await self.conversation_manager.search_sessions(query, min(limit, 5))
            return {"success": True, "sessions": results, "count": len(results)}
        except Exception as e:
            logger.error(f"search_past_sessions error: {e}")
            return {"success": False, "error": str(e), "sessions": []}

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
        import re as _re
        if url_path and not _re.match(r'^[a-z0-9][a-z0-9_-]*$', url_path):
            return {"success": False, "error": f"Invalid url_path '{url_path}': must start with a letter/digit and contain only lowercase letters, digits, hyphens, underscores"}

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
        import re as _re
        if not _re.match(r'^[a-z0-9][a-z0-9_-]*$', url_path):
            return {"success": False, "error": f"Invalid url_path '{url_path}'"}
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
    # Node-RED helpers
    # ------------------------------------------------------------------

    async def _nodered_api_request(self, method: str, path: str, data=None) -> Dict:
        """
        Make a request to the Node-RED Admin API.

        Requires NODERED_TOKEN set to a long-lived HA access token.
        (SUPERVISOR_TOKEN is for the Supervisor API, not Node-RED's HTTP server.)
        """
        import aiohttp
        import json as _json

        nodered_url = os.getenv('NODERED_URL', '').rstrip('/')
        if not nodered_url:
            return {"status": 0, "error": "NODERED_URL not configured"}

        token = os.getenv('NODERED_TOKEN', '')
        headers = {
            "Accept": "application/json",
            "Node-RED-API-Version": "v2",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if data is not None:
            headers["Content-Type"] = "application/json"

        url = f"{nodered_url}{path}"
        logger.info(f"Node-RED API {method} {url}")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method, url, headers=headers,
                    data=_json.dumps(data) if data is not None else None,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    try:
                        j = await resp.json(content_type=None)
                    except Exception:
                        j = None
                    text = await resp.text() if j is None else ""
                    return {"status": resp.status, "json": j, "text": text}
        except aiohttp.ClientError as e:
            return {"status": 0, "error": str(e)}

    async def add_nodered_flow(
        self,
        flows_json: str,
        description: str = "",
    ) -> Dict[str, Any]:
        """
        Stage a new Node-RED flow tab for user approval (non-destructive).

        This is the safe way to add a new flow. It does NOT touch existing flows.
        Call get_nodered_flows first to confirm the flow doesn't already exist.

        Args:
            flows_json: JSON array containing one tab node plus its functional nodes.
                        Format: [{"type":"tab","id":"...","label":"..."},
                                 {"type":"inject",...}, ...]
            description: Brief description of what the flow does.

        Returns the same changeset dict as propose_config_changes.
        """
        import json as _json
        from datetime import datetime, timedelta
        import uuid

        try:
            nodes = _json.loads(flows_json)
        except _json.JSONDecodeError as e:
            return {"success": False, "error": f"Invalid JSON in flows_json: {e}"}
        if not isinstance(nodes, list):
            return {"success": False, "error": "flows_json must be a JSON array"}

        changeset_id = str(uuid.uuid4())[:8]
        stored_changes = [{"file_path": "nodered/new_flow.json", "new_content": flows_json}]
        if self.agent_system:
            changeset_id = self.agent_system.store_changeset({
                "changeset_id": changeset_id,
                "file_changes": stored_changes,
            })

        now = datetime.now()
        return {
            "success": True,
            "changeset_id": changeset_id,
            "files": ["nodered/new_flow.json"],
            "total_files": 1,
            "expires_at": (now + timedelta(hours=1)).isoformat(),
            "diff_stats": [{"file_path": "nodered/new_flow.json", "added": len(nodes), "removed": 0, "is_new_file": True}],
            "message": (
                f"New Node-RED flow staged for approval ({len(nodes)} nodes)."
                + (f" {description}" if description else "")
                + " Awaiting user approval."
            ),
        }

    async def edit_nodered_tab(
        self,
        tab_id: str,
        flows_json: str,
        description: str = "",
    ) -> Dict[str, Any]:
        """
        Stage an update to an existing Node-RED flow tab for user approval.

        This is the safe way to modify an existing flow tab — only that tab is
        affected. Call get_nodered_flows first to get the tab_id and current nodes.

        Args:
            tab_id:     The id of the tab to update (from get_nodered_flows).
            flows_json: JSON array containing the tab node plus all its updated nodes.
                        Must include ALL nodes for the tab (not just changed ones).
            description: Brief description of what changed.

        Returns the same changeset dict as propose_config_changes.
        """
        import json as _json
        from datetime import datetime, timedelta
        import uuid

        if not tab_id or not tab_id.strip():
            return {"success": False, "error": "tab_id is required. Call get_nodered_flows first to find the tab id."}

        try:
            nodes = _json.loads(flows_json)
        except _json.JSONDecodeError as e:
            return {"success": False, "error": f"Invalid JSON in flows_json: {e}"}
        if not isinstance(nodes, list):
            return {"success": False, "error": "flows_json must be a JSON array"}

        file_path = f"nodered/flow/{tab_id}.json"
        changeset_id = str(uuid.uuid4())[:8]
        stored_changes = [{"file_path": file_path, "new_content": flows_json}]
        if self.agent_system:
            changeset_id = self.agent_system.store_changeset({
                "changeset_id": changeset_id,
                "file_changes": stored_changes,
            })

        now = datetime.now()
        return {
            "success": True,
            "changeset_id": changeset_id,
            "files": [file_path],
            "total_files": 1,
            "expires_at": (now + timedelta(hours=1)).isoformat(),
            "diff_stats": [{"file_path": file_path, "added": len(nodes), "removed": 0, "is_new_file": False}],
            "message": (
                f"Node-RED tab '{tab_id}' update staged for approval ({len(nodes)} nodes)."
                + (f" {description}" if description else "")
                + " Awaiting user approval."
            ),
        }

    async def deploy_nodered_flows(self, flows_json: str, mode: str = "add", tab_id: str = "") -> Dict[str, Any]:
        """
        Deploy flows to Node-RED via the Admin API.

        Args:
            flows_json: JSON string — array of Node-RED node objects
            mode: "add"        → POST /flow (adds a new tab, non-destructive)
                  "update_tab" → PUT /flow/{tab_id} (replaces nodes in one tab only)
                  "replace"    → PUT /flows (replaces ALL flows — destructive)
            tab_id: required when mode="update_tab"

        Returns:
            Dict with success, message, error
        """
        import json as _json

        nodered_url = os.getenv('NODERED_URL', '').rstrip('/')
        if not nodered_url:
            return {"success": False, "error": "NODERED_URL not configured — cannot deploy flows"}

        try:
            flows = _json.loads(flows_json)
        except _json.JSONDecodeError as e:
            return {"success": False, "error": f"Invalid JSON: {e}"}

        if not isinstance(flows, list):
            return {"success": False, "error": "flows_json must be a JSON array"}

        if mode == "add":
            # POST /flow expects a single flow object: {id, label, nodes: [...]}
            # If the user passed a flat array of nodes, wrap them in a tab
            tab = next((n for n in flows if n.get("type") == "tab"), None)
            if tab:
                nodes = [n for n in flows if n.get("type") != "tab"]
                payload = {**tab, "nodes": nodes}
            else:
                # No tab node — wrap everything in an unnamed tab
                import uuid
                payload = {"id": str(uuid.uuid4()), "label": "AI Flow", "nodes": flows}
            result = await self._nodered_api_request("POST", "/flow", payload)
            if result["status"] in (200, 204):
                return {"success": True, "message": "Flow tab added to Node-RED successfully"}

        elif mode == "update_tab":
            # PUT /flow/{id} replaces only the nodes in one tab
            # Derive tab_id from the array if not explicitly provided
            tab = next((n for n in flows if n.get("type") == "tab"), None)
            effective_tab_id = tab_id or (tab.get("id") if tab else "")
            if not effective_tab_id:
                return {"success": False, "error": "update_tab mode requires a tab_id or a tab node in the array"}
            nodes = [n for n in flows if n.get("type") != "tab"]
            payload = {
                "id": effective_tab_id,
                "label": tab.get("label", "") if tab else "",
                "nodes": nodes,
                "configs": [],
            }
            result = await self._nodered_api_request("PUT", f"/flow/{effective_tab_id}", payload)
            if result["status"] in (200, 204):
                return {"success": True, "message": f"Flow tab '{effective_tab_id}' updated in Node-RED successfully"}

        else:
            # PUT /flows replaces all flows
            result = await self._nodered_api_request("PUT", "/flows", {"flows": flows})
            if result["status"] in (200, 204):
                return {"success": True, "message": "All Node-RED flows replaced successfully"}

        status = result.get("status")
        if status == 401:
            return {
                "success": False,
                "error": (
                    "Node-RED authentication failed (401). "
                    "The Node-RED HA add-on admin API does not accept HA long-lived tokens. "
                    "Fix: enable 'Leave front door open' in Node-RED add-on config, then leave nodered_token empty."
                ),
            }
        return {
            "success": False,
            "error": f"Node-RED API returned {status}: {result.get('text', result.get('json', ''))}"
        }

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

        # --- Primary: Node-RED Admin API ---
        if nodered_url:
            try:
                resp_data = await self._nodered_api_request("GET", "/flows")
                if resp_data.get("status") == 200:
                    data = resp_data["json"]
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
                elif resp_data.get("status") == 401:
                    logger.warning("Node-RED API returned 401 — auth failed")
                    return {
                        "success": False,
                        "error": (
                            "Node-RED API authentication failed (401). "
                            "The Node-RED HA add-on admin API does not accept HA long-lived tokens. "
                            "Fix: enable 'Leave front door open' in Node-RED add-on config, then leave nodered_token empty."
                        ),
                        "flows": [],
                        "count": 0,
                        "source": "api",
                    }
                else:
                    logger.warning(f"Node-RED API returned {resp_data.get('status')}: {resp_data.get('text', '')[:200]}")
                    # Fall through to file fallback
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

    async def _embed_texts(self, texts: List[str]) -> Optional[List[List[float]]]:
        """Embed a list of texts using the configured embeddings API. Returns None on failure."""
        client = getattr(self.agent_system, 'config_client', None)
        if client is None:
            return None
        model = os.getenv('EMBEDDING_MODEL', 'text-embedding-3-small')
        try:
            all_embeddings: List[List[float]] = []
            for i in range(0, len(texts), _EMBED_BATCH):
                batch = texts[i:i + _EMBED_BATCH]
                resp = await client.embeddings.create(model=model, input=batch)
                all_embeddings.extend(item.embedding for item in resp.data)
            return all_embeddings
        except Exception as e:
            logger.warning(f"Embedding API failed ({model}): {e}")
            return None

    async def _ensure_entity_cache(self, states_list: List[Dict]) -> bool:
        """Build the entity embedding cache from a freshly fetched states list. Returns True on success."""
        if self._entity_cache.is_valid():
            return True
        texts = [EntityEmbeddingCache.entity_text(e) for e in states_list]
        embeddings = await self._embed_texts(texts)
        if embeddings is None or len(embeddings) != len(states_list):
            return False
        self._entity_cache.build(states_list, embeddings)
        return True

    async def get_entity_states(
        self,
        domain_filter: Optional[str] = None,
        query: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get current live states of Home Assistant entities.

        When *query* is provided (and no domain_filter), performs semantic search
        and returns only the most relevant entities — ideal for large homes where
        a full dump would be too noisy. Always also includes unavailable entities
        and those changed in the last 10 minutes.

        When *domain_filter* is provided, returns all entities of that domain
        regardless of *query* (query is ignored for domain-filtered calls).

        Without either argument, returns all entities (full dump).

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

            # Semantic search path: filter by query when no domain is requested
            if query and not domain_filter:
                total = len(states_list)
                cache_ok = await self._ensure_entity_cache(states_list)
                if cache_ok:
                    # Embed the query
                    q_embs = await self._embed_texts([query])
                    if q_embs:
                        top_indices = set(self._entity_cache.search(q_embs[0], top_k=_EMBED_TOP_K))
                        # Always include unavailable and recently-changed entities
                        now_ts = time.time()
                        for i, e in enumerate(states_list):
                            if e.get("state") == "unavailable":
                                top_indices.add(i)
                                continue
                            lc = e.get("last_changed")
                            if lc:
                                try:
                                    from datetime import datetime, timezone
                                    lc_dt = datetime.fromisoformat(lc.replace("Z", "+00:00"))
                                    age = now_ts - lc_dt.timestamp()
                                    if age < 600:  # changed within last 10 min
                                        top_indices.add(i)
                                except Exception:
                                    pass
                        filtered = [states_list[i] for i in sorted(top_indices)]
                        logger.info(f"Semantic search '{query}': {len(filtered)}/{total} entities returned")
                        return {
                            "success": True,
                            "states": filtered,
                            "count": len(filtered),
                            "total_entities": total,
                            "semantic_search": True,
                            "query": query,
                        }
                # Embedding failed — log and fall through to full dump
                logger.warning("Semantic entity search failed, falling back to full entity dump")

            return {
                "success": True,
                "states": states_list,
                "count": len(states_list),
                "domain_filter": domain_filter,
            }

        except Exception as e:
            logger.error(f"get_entity_states error: {e}", exc_info=True)
            return {"success": False, "error": str(e), "states": [], "count": 0}

    async def get_ha_issues(self) -> Dict[str, Any]:
        """
        Get all current Home Assistant issues from Watchman and Spook/Repairs.

        Reads:
        - Watchman: missing entity references and missing service calls detected in config files
        - Repair issues: Spook and other integrations that file repair items in HA
        - watchman_report.txt: text report file (if present in /config)

        Returns:
            Dict with watchman (missing_entities, missing_services) and repairs lists
        """
        result: Dict[str, Any] = {"success": True}

        # 1. Watchman data from entity state attributes
        try:
            if self.config_manager.hass is not None:
                hass = self.config_manager.hass
                me_state = hass.states.get("sensor.watchman_missing_entities")
                ms_state = hass.states.get("sensor.watchman_missing_actions")
            else:
                supervisor_token = os.getenv('SUPERVISOR_TOKEN')
                if supervisor_token:
                    from ..ha.ha_websocket import HomeAssistantWebSocket
                    ws_client = HomeAssistantWebSocket("ws://supervisor/core/websocket", supervisor_token)
                    await ws_client.connect()
                    try:
                        raw_states = await ws_client.get_states()
                        states_by_id = {s["entity_id"]: s for s in raw_states}
                        me_state = states_by_id.get("sensor.watchman_missing_entities")
                        ms_state = states_by_id.get("sensor.watchman_missing_actions")
                    finally:
                        await ws_client.close()
                else:
                    me_state = ms_state = None

            if me_state is not None:
                attrs = me_state.attributes if hasattr(me_state, 'attributes') else me_state.get('attributes', {})
                missing_entities = attrs.get("entities", []) if attrs else []
                missing_count = int(me_state.state if hasattr(me_state, 'state') else me_state.get('state', 0))

                ms_attrs = ms_state.attributes if (ms_state and hasattr(ms_state, 'attributes')) else (ms_state or {}).get('attributes', {})
                missing_services = ms_attrs.get("entities", []) if ms_attrs else []
                missing_services_count = int(ms_state.state if (ms_state and hasattr(ms_state, 'state')) else (ms_state or {}).get('state', 0))

                result["watchman"] = {
                    "missing_entities_count": missing_count,
                    "missing_entities": missing_entities,
                    "missing_services_count": missing_services_count,
                    "missing_services": missing_services,
                }
            else:
                result["watchman"] = {"note": "Watchman integration not found or not installed"}
        except Exception as e:
            result["watchman_error"] = str(e)

        # 2. HA repair issues (Spook and others)
        try:
            if self.config_manager.hass is not None:
                # Custom component mode — use HA's issues registry directly
                from homeassistant.components.repairs import async_get_issue_registry
                issue_reg = async_get_issue_registry(self.config_manager.hass)
                issues = [
                    {
                        "issue_id": issue.issue_id,
                        "domain": issue.domain,
                        "severity": str(issue.severity) if issue.severity else None,
                        "is_fixable": issue.is_fixable,
                        "translation_key": issue.translation_key,
                        "ignored": issue.ignored,
                    }
                    for issue in issue_reg.issues.values()
                    if not issue.ignored
                ]
            else:
                supervisor_token = os.getenv('SUPERVISOR_TOKEN')
                if supervisor_token:
                    issues = await get_repairs_ws("ws://supervisor/core/websocket", supervisor_token)
                else:
                    issues = []
            result["repairs"] = issues
            result["repairs_count"] = len(issues)
        except Exception as e:
            result["repairs_error"] = str(e)
            result["repairs"] = []
            result["repairs_count"] = 0

        # 3. Watchman report text file (optional)
        try:
            report = await self.config_manager.read_file_raw("watchman_report.txt", allow_missing=True)
            if report:
                result["watchman_report_file"] = report
        except Exception:
            pass

        return result

    async def reload_config(self) -> Dict[str, Any]:
        """
        Reload Home Assistant configuration without restarting.

        Triggers homeassistant.reload_all which reloads templates, scripts,
        input_number, input_boolean, input_text, input_select, automations, etc.
        Call this after proposed YAML changes are approved to activate new entities.
        """
        try:
            if self.config_manager.hass is not None:
                await self.config_manager.hass.services.async_call(
                    "homeassistant", "reload_all"
                )
                logger.info("Configuration reloaded via hass API")
                return {"success": True, "message": "Configuration reloaded. New entities and changes are now active."}

            supervisor_token = os.getenv('SUPERVISOR_TOKEN')
            if not supervisor_token:
                return {"success": False, "message": "Cannot reload: no SUPERVISOR_TOKEN available. Reload manually via Developer Tools → YAML."}

            ws_url = "ws://supervisor/core/websocket"
            await reload_homeassistant_config(ws_url, supervisor_token)
            logger.info("Configuration reloaded via WebSocket")
            return {"success": True, "message": "Configuration reloaded. New entities and changes are now active."}

        except Exception as e:
            logger.error("reload_config error: %s", e)
            return {"success": False, "message": f"Reload failed: {e}. Try reloading manually via Developer Tools → YAML."}

    async def set_ha_text_entity(self, entity_id: str, value: str) -> Dict[str, Any]:
        """
        Set the value of an input_text entity in Home Assistant.

        Writes a plain-text value directly to an input_text helper — no approval needed.
        Use this after generating AI content (briefings, summaries, advice) so automations
        and dashboards can consume the result without reading the chat.
        """
        if not entity_id.startswith("input_text."):
            return {"success": False, "error": f"entity_id must be an input_text entity (got: {entity_id}). Create one via Settings → Helpers first."}

        value = str(value)[:255]  # HA hard limit for input_text

        try:
            if self.config_manager.hass is not None:
                await self.config_manager.hass.services.async_call(
                    "input_text", "set_value",
                    {"entity_id": entity_id, "value": value},
                    blocking=True,
                )
                logger.info(f"Set {entity_id} via hass API")
                return {"success": True, "entity_id": entity_id, "value": value}

            supervisor_token = os.getenv('SUPERVISOR_TOKEN')
            if not supervisor_token:
                return {"success": False, "error": "No SUPERVISOR_TOKEN available — cannot call HA services."}

            from ..ha.ha_websocket import HomeAssistantWebSocket
            ws_url = "ws://supervisor/core/websocket"
            ws_client = HomeAssistantWebSocket(ws_url, supervisor_token)
            await ws_client.connect()
            try:
                await ws_client.call(
                    "call_service",
                    domain="input_text",
                    service="set_value",
                    service_data={"entity_id": entity_id, "value": value},
                )
            finally:
                await ws_client.close()

            logger.info(f"Set {entity_id} via WebSocket")
            return {"success": True, "entity_id": entity_id, "value": value}

        except Exception as e:
            logger.error(f"set_ha_text_entity error: {e}")
            return {"success": False, "error": str(e)}

    async def schedule_ai_task(self, name: str, prompt: str, entity_id: str, schedule: str) -> Dict[str, Any]:
        """
        Schedule a recurring AI task that runs a prompt on a timetable and writes the result
        to an input_text entity. Supported schedule: 'daily HH:MM' (24-hour local time).
        """
        import re
        if not entity_id.startswith("input_text."):
            return {"success": False, "error": f"entity_id must be an input_text entity (got: {entity_id})."}
        if not re.match(r'^daily \d{2}:\d{2}$', schedule):
            return {"success": False, "error": "schedule must be 'daily HH:MM' (e.g. 'daily 08:00')."}

        task_manager = getattr(self.agent_system, 'task_manager', None)
        if task_manager is None:
            return {"success": False, "error": "Task scheduler is not available."}

        try:
            task = task_manager.create_task(
                name=name, prompt=prompt, entity_id=entity_id, schedule=schedule
            )
            logger.info(f"Scheduled task '{name}' ({task['id']}) → {entity_id} @ {schedule}")
            return {"success": True, "task_id": task["id"], "name": name, "entity_id": entity_id, "schedule": schedule}
        except Exception as e:
            logger.error(f"schedule_ai_task error: {e}")
            return {"success": False, "error": str(e)}
