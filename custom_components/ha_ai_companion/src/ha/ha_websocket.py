"""
Home Assistant WebSocket API Client

Handles WebSocket connections to Home Assistant for operations
that require the WebSocket API, such as:
- Retrieving Lovelace UI configuration
- Saving Lovelace UI configuration
"""
import logging
from typing import Any, Dict, Optional, List
import aiohttp

logger = logging.getLogger(__name__)


class HomeAssistantWebSocket:
    """Client for Home Assistant WebSocket API."""

    def __init__(self, url: str, token: str):
        """
        Initialize WebSocket client.

        Args:
            url: Home Assistant WebSocket URL (e.g., ws://supervisor/core/websocket)
            token: Long-lived access token or supervisor token
        """
        self.url = url
        self.token = token
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self.message_id = 1
        self.authenticated = False

    async def connect(self) -> None:
        """Establish WebSocket connection and authenticate."""
        try:
            self.session = aiohttp.ClientSession()
            self.ws = await self.session.ws_connect(self.url)
            logger.info("WebSocket connection established")

            # Wait for auth_required message
            msg = await self.ws.receive_json()
            if msg.get("type") != "auth_required":
                raise Exception(f"Unexpected message type: {msg.get('type')}")

            # Send authentication
            await self.ws.send_json({
                "type": "auth",
                "access_token": self.token
            })

            # Wait for auth response
            auth_response = await self.ws.receive_json()
            if auth_response.get("type") == "auth_ok":
                self.authenticated = True
                logger.info("WebSocket authentication successful")
            else:
                raise Exception(f"Authentication failed: {auth_response}")

        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            await self.close()
            raise

    async def close(self) -> None:
        """Close WebSocket connection."""
        if self.ws:
            await self.ws.close()
        if self.session:
            await self.session.close()
        self.authenticated = False
        logger.info("WebSocket connection closed")

    async def call(self, message_type: str, **kwargs) -> Dict[str, Any]:
        """
        Send a WebSocket command and wait for response.

        Args:
            message_type: Type of message (e.g., "lovelace/config")
            **kwargs: Additional message parameters

        Returns:
            Response data

        Raises:
            Exception: If call fails or connection not authenticated
        """
        if not self.authenticated or not self.ws:
            raise Exception("WebSocket not authenticated")

        msg_id = self.message_id
        self.message_id += 1

        # Send message
        message = {
            "id": msg_id,
            "type": message_type,
            **kwargs
        }
        await self.ws.send_json(message)
        logger.debug(f"Sent WebSocket message: {message}")

        # Wait for response with matching ID
        while True:
            response = await self.ws.receive_json()
            logger.debug(f"Received WebSocket message: {response}")

            if response.get("id") == msg_id:
                if response.get("type") == "result":
                    if response.get("success", True):
                        return response.get("result", {})
                    else:
                        error = response.get("error", {})
                        raise Exception(f"WebSocket call failed: {error}")
                else:
                    raise Exception(f"Unexpected response type: {response.get('type')}")

    async def list_lovelace_dashboards(self) -> List[Dict[str, Any]]:
        """
        List all Lovelace dashboards.

        Returns:
            List of dashboard dicts (id, url_path, title, icon, etc.)

        Raises:
            Exception: If request fails
        """
        logger.info("Listing Lovelace dashboards via WebSocket")
        try:
            result = await self.call("lovelace/dashboards/list")
            dashboards = result or []
            logger.info(f"Found {len(dashboards)} dashboard(s)")
            return dashboards
        except Exception as e:
            logger.error(f"Failed to list Lovelace dashboards: {e}")
            raise

    async def get_lovelace_config(self, url_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Retrieve Lovelace UI configuration.

        Args:
            url_path: Dashboard URL path (e.g. 'kitchen'). None for default dashboard.

        Returns:
            Lovelace configuration as dictionary

        Raises:
            Exception: If retrieval fails
        """
        label = url_path or "default"
        logger.info(f"Retrieving Lovelace config ({label}) via WebSocket")
        try:
            kwargs: Dict[str, Any] = {"force": False}
            if url_path:
                kwargs["url_path"] = url_path
            config = await self.call("lovelace/config", **kwargs)
            logger.info(f"Successfully retrieved Lovelace config ({label})")
            return config
        except Exception as e:
            logger.error(f"Failed to retrieve Lovelace config ({label}): {e}")
            raise

    async def save_lovelace_config(self, config: Dict[str, Any], url_path: Optional[str] = None) -> None:
        """
        Save Lovelace UI configuration.

        Args:
            config: Lovelace configuration dictionary
            url_path: Dashboard URL path (e.g. 'kitchen'). None for default dashboard.

        Raises:
            Exception: If save fails
        """
        label = url_path or "default"
        logger.info(f"Saving Lovelace config ({label}) via WebSocket")
        try:
            kwargs: Dict[str, Any] = {"config": config}
            if url_path:
                kwargs["url_path"] = url_path
            await self.call("lovelace/config/save", **kwargs)
            logger.info(f"Successfully saved Lovelace config ({label})")
        except Exception as e:
            logger.error(f"Failed to save Lovelace config ({label}): {e}")
            raise

    async def create_lovelace_dashboard(
        self,
        title: str,
        url_path: Optional[str] = None,
        icon: Optional[str] = None,
        show_in_sidebar: bool = True,
        require_admin: bool = False,
    ) -> Dict[str, Any]:
        """
        Create a new Lovelace dashboard.

        Args:
            title: Human-readable dashboard title
            url_path: URL slug (e.g. 'kitchen'). Auto-generated from title if omitted.
            icon: Material Design icon (e.g. 'mdi:home')
            show_in_sidebar: Whether to show in the HA sidebar
            require_admin: Restrict dashboard to admin users

        Returns:
            Created dashboard info dict

        Raises:
            Exception: If creation fails
        """
        logger.info(f"Creating Lovelace dashboard: {title}")
        try:
            params: Dict[str, Any] = {
                "title": title,
                "show_in_sidebar": show_in_sidebar,
                "require_admin": require_admin,
            }
            if url_path:
                params["url_path"] = url_path
            if icon:
                params["icon"] = icon
            result = await self.call("lovelace/dashboards/create", **params)
            logger.info(f"Successfully created dashboard: {title} (url_path={result.get('url_path')})")
            return result
        except Exception as e:
            logger.error(f"Failed to create Lovelace dashboard '{title}': {e}")
            raise

    async def delete_lovelace_dashboard(self, url_path: str) -> None:
        """
        Delete a Lovelace dashboard.

        Args:
            url_path: Dashboard URL path to delete

        Raises:
            Exception: If deletion fails
        """
        logger.info(f"Deleting Lovelace dashboard: {url_path}")
        try:
            await self.call("lovelace/dashboards/delete", url_path=url_path)
            logger.info(f"Successfully deleted dashboard: {url_path}")
        except Exception as e:
            logger.error(f"Failed to delete Lovelace dashboard '{url_path}': {e}")
            raise

    async def reload_config(self) -> None:
        """
        Reload Home Assistant configuration (calls homeassistant.reload_all service).

        This reloads all reloadable components without requiring a full restart.

        Raises:
            Exception: If reload fails
        """
        logger.info("Reloading Home Assistant configuration via WebSocket")
        try:
            await self.call(
                "call_service",
                domain="homeassistant",
                service="reload_all",
                return_response=False,
                service_data={}
            )
            logger.info("Successfully triggered Home Assistant configuration reload")
        except Exception as e:
            logger.error(f"Failed to reload Home Assistant config: {e}")
            raise

    async def list_devices(self) -> List[Dict[str, Any]]:
        """
        Get list of all devices from device registry.

        Returns:
            List of device dictionaries with device information

        Raises:
            Exception: If request fails
        """
        logger.info("Retrieving device registry via WebSocket")
        try:
            devices = await self.call("config/device_registry/list")
            logger.info(f"Successfully retrieved {len(devices)} devices")
            return devices
        except Exception as e:
            logger.error(f"Failed to retrieve device registry: {e}")
            raise

    async def update_device(
        self,
        device_id: str,
        name_by_user: Optional[str] = None,
        area_id: Optional[str] = None,
        labels: Optional[List[str]] = None,
        disabled_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Update a device in the device registry.

        Args:
            device_id: ID of the device to update
            name_by_user: User-defined name for the device
            area_id: Area ID to assign device to
            labels: List of label IDs
            disabled_by: How device is disabled (None to enable)

        Returns:
            Updated device information

        Raises:
            Exception: If update fails
        """
        logger.info(f"Updating device {device_id}")
        try:
            params = {"device_id": device_id}
            if name_by_user is not None:
                params["name_by_user"] = name_by_user
            if area_id is not None:
                params["area_id"] = area_id
            if labels is not None:
                params["labels"] = labels
            if disabled_by is not None:
                params["disabled_by"] = disabled_by

            result = await self.call("config/device_registry/update", **params)
            logger.info(f"Successfully updated device {device_id}")
            return result
        except Exception as e:
            logger.error(f"Failed to update device: {e}")
            raise

    async def list_entities(self) -> List[Dict[str, Any]]:
        """
        Get list of all entities from entity registry.

        Returns:
            List of entity dictionaries with entity information

        Raises:
            Exception: If request fails
        """
        logger.info("Retrieving entity registry via WebSocket")
        try:
            entities = await self.call("config/entity_registry/list")
            logger.info(f"Successfully retrieved {len(entities)} entities")
            return entities
        except Exception as e:
            logger.error(f"Failed to retrieve entity registry: {e}")
            raise

    async def list_entities_for_display(self) -> List[Dict[str, Any]]:
        """
        Get list of entities optimized for display (includes state info).

        Returns:
            List of entity dictionaries with display information

        Raises:
            Exception: If request fails
        """
        logger.info("Retrieving entity registry for display via WebSocket")
        try:
            entities = await self.call("config/entity_registry/list_for_display")
            logger.info(f"Successfully retrieved {len(entities)} entities for display")
            return entities
        except Exception as e:
            logger.error(f"Failed to retrieve entity registry for display: {e}")
            raise

    async def update_entity(
        self,
        entity_id: str,
        name: Optional[str] = None,
        icon: Optional[str] = None,
        area_id: Optional[str] = None,
        labels: Optional[List[str]] = None,
        new_entity_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Update an entity in the entity registry.

        Args:
            entity_id: ID of the entity to update
            name: Friendly name for the entity (null to use default)
            icon: Icon for the entity
            area_id: Area ID to assign entity to
            labels: List of label IDs
            new_entity_id: New entity ID (for renaming entity ID itself)

        Returns:
            Updated entity information

        Raises:
            Exception: If update fails
        """
        logger.info(f"Updating entity {entity_id}")
        try:
            params = {"entity_id": entity_id}
            if name is not None:
                params["name"] = name
            if icon is not None:
                params["icon"] = icon
            if area_id is not None:
                params["area_id"] = area_id
            if labels is not None:
                params["labels"] = labels
            if new_entity_id is not None:
                params["new_entity_id"] = new_entity_id

            result = await self.call("config/entity_registry/update", **params)
            logger.info(f"Successfully updated entity {entity_id}")
            return result
        except Exception as e:
            logger.error(f"Failed to update entity: {e}")
            raise

    async def get_states(self) -> List[Dict[str, Any]]:
        """
        Get all current entity states from Home Assistant.

        Returns:
            List of state dictionaries (entity_id, state, attributes, last_changed, ...)
        """
        logger.info("Retrieving entity states via WebSocket")
        try:
            result = await self.call("get_states")
            states = result if isinstance(result, list) else []
            logger.info(f"Successfully retrieved {len(states)} entity states")
            return states
        except Exception as e:
            logger.error(f"Failed to retrieve entity states: {e}")
            raise

    async def list_areas(self) -> List[Dict[str, Any]]:
        """Get list of all areas from area registry."""
        logger.info("Retrieving area registry via WebSocket")
        try:
            result = await self.call("config/area_registry/list")
            areas = result if isinstance(result, list) else []
            logger.info(f"Successfully retrieved {len(areas)} areas")
            return areas
        except Exception as e:
            logger.error(f"Failed to retrieve area registry: {e}")
            raise

    async def create_area(
        self,
        name: str,
        picture: Optional[str] = None,
        icon: Optional[str] = None,
        aliases: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Create a new area in the area registry.

        Args:
            name: Name of the area (required)
            picture: Optional picture URL
            icon: Optional icon name
            aliases: Optional list of aliases

        Returns:
            Created area information including generated area_id
        """
        logger.info(f"Creating new area: {name}")
        try:
            params = {"name": name}
            if picture is not None:
                params["picture"] = picture
            if icon is not None:
                params["icon"] = icon
            if aliases is not None:
                params["aliases"] = aliases

            result = await self.call("config/area_registry/create", **params)
            logger.info(f"Successfully created area {name} with ID {result.get('area_id')}")
            return result
        except Exception as e:
            logger.error(f"Failed to create area: {e}")
            raise

    async def update_area(
        self,
        area_id: str,
        name: Optional[str] = None,
        picture: Optional[str] = None,
        icon: Optional[str] = None,
        aliases: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Update an area in the area registry."""
        logger.info(f"Updating area {area_id}")
        try:
            params = {"area_id": area_id}
            if name is not None:
                params["name"] = name
            if picture is not None:
                params["picture"] = picture
            if icon is not None:
                params["icon"] = icon
            if aliases is not None:
                params["aliases"] = aliases

            result = await self.call("config/area_registry/update", **params)
            logger.info(f"Successfully updated area {area_id}")
            return result
        except Exception as e:
            logger.error(f"Failed to update area: {e}")
            raise


async def get_lovelace_config_as_yaml(url: str, token: str, url_path: Optional[str] = None) -> Optional[str]:
    """
    Helper function to retrieve Lovelace config as YAML string.

    Args:
        url: WebSocket URL
        token: Access token
        url_path: Dashboard URL path (None for default dashboard)

    Returns:
        YAML string of Lovelace config, or None if retrieval fails
    """
    ws_client = HomeAssistantWebSocket(url, token)
    try:
        await ws_client.connect()
        config = await ws_client.get_lovelace_config(url_path)

        # Convert to YAML string
        from ruamel.yaml import YAML
        from io import StringIO

        yaml = YAML()
        yaml.default_flow_style = False
        yaml.preserve_quotes = True
        yaml.width = 4096

        stream = StringIO()
        yaml.dump(config, stream)
        return stream.getvalue()

    except Exception as e:
        logger.error(f"Failed to get Lovelace config: {e}")
        return None
    finally:
        await ws_client.close()


async def save_lovelace_config_from_yaml(url: str, token: str, yaml_content: str, url_path: Optional[str] = None) -> None:
    """
    Helper function to save Lovelace config from YAML string.

    Args:
        url: WebSocket URL
        token: Access token
        yaml_content: YAML string to parse and save
        url_path: Dashboard URL path (None for default dashboard)

    Raises:
        Exception: If save fails
    """
    ws_client = HomeAssistantWebSocket(url, token)
    try:
        await ws_client.connect()

        # Parse YAML to dict
        from ruamel.yaml import YAML
        from io import StringIO

        yaml = YAML()
        config = yaml.load(StringIO(yaml_content))

        await ws_client.save_lovelace_config(config, url_path)

    finally:
        await ws_client.close()


async def list_lovelace_dashboards_ws(url: str, token: str) -> List[Dict[str, Any]]:
    """
    Helper function to list all Lovelace dashboards.

    Args:
        url: WebSocket URL
        token: Access token

    Returns:
        List of dashboard dicts, or empty list on error
    """
    ws_client = HomeAssistantWebSocket(url, token)
    try:
        await ws_client.connect()
        return await ws_client.list_lovelace_dashboards()
    except Exception as e:
        logger.error(f"Failed to list Lovelace dashboards: {e}")
        return []
    finally:
        await ws_client.close()


async def create_lovelace_dashboard_ws(
    url: str,
    token: str,
    title: str,
    url_path: Optional[str] = None,
    icon: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Helper function to create a new Lovelace dashboard.

    Args:
        url: WebSocket URL
        token: Access token
        title: Dashboard title
        url_path: URL slug (auto-generated if omitted)
        icon: Material Design icon

    Returns:
        Created dashboard info dict, or None on error
    """
    ws_client = HomeAssistantWebSocket(url, token)
    try:
        await ws_client.connect()
        return await ws_client.create_lovelace_dashboard(title, url_path, icon)
    except Exception as e:
        logger.error(f"Failed to create Lovelace dashboard: {e}")
        return None
    finally:
        await ws_client.close()


async def delete_lovelace_dashboard_ws(url: str, token: str, url_path: str) -> bool:
    """
    Helper function to delete a Lovelace dashboard.

    Args:
        url: WebSocket URL
        token: Access token
        url_path: Dashboard URL path to delete

    Returns:
        True if deleted successfully, False on error
    """
    ws_client = HomeAssistantWebSocket(url, token)
    try:
        await ws_client.connect()
        await ws_client.delete_lovelace_dashboard(url_path)
        return True
    except Exception as e:
        logger.error(f"Failed to delete Lovelace dashboard: {e}")
        return False
    finally:
        await ws_client.close()


async def reload_homeassistant_config(url: str, token: str) -> None:
    """
    Helper function to reload Home Assistant configuration.

    Args:
        url: WebSocket URL
        token: Access token

    Raises:
        Exception: If reload fails
    """
    ws_client = HomeAssistantWebSocket(url, token)
    try:
        await ws_client.connect()
        await ws_client.reload_config()
    finally:
        await ws_client.close()