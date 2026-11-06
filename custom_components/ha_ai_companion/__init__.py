"""AI Configuration Agent custom component for Home Assistant."""
from __future__ import annotations

import logging
import os
import asyncio
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import (
    DOMAIN,
    CONF_API_KEY,
    CONF_API_URL,
    CONF_MODEL,
    CONF_LOG_LEVEL,
    CONF_TEMPERATURE,
    CONF_REASONING_EFFORT,
    CONF_SYSTEM_PROMPT_FILE,
    CONF_ENABLE_CACHE_CONTROL,
    CONF_USAGE_TRACKING,
    CONF_NODERED_FLOWS_FILE,
    CONF_NODERED_URL,
    CONF_NODERED_TOKEN,
    CONF_SUGGESTION_PROMPT,
    CONF_FAST_MODEL,
    CONF_AA_API_KEY,
    CONF_MAX_SUGGESTIONS,
    CONF_MAX_ITERATIONS,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = []

# This integration is only configurable via config entries
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the AI Configuration Agent component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up AI Configuration Agent from a config entry."""
    _LOGGER.info("Setting up AI Configuration Agent")

    # Store configuration
    hass.data[DOMAIN][entry.entry_id] = {
        "config": entry.data,
        "server_task": None,
        "port": None
    }

    # Start the FastAPI server in background
    await _start_server(hass, entry)

    # Register services
    await _register_services(hass, entry)

    # Register frontend panel
    await _register_panel(hass, entry)

    return True


async def _start_server(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Start the FastAPI server."""
    import uvicorn
    from pathlib import Path
    import sys

    # Get the component directory (where __init__.py is located)
    component_dir = Path(__file__).parent

    # Add src directory to path (it's in the component directory)
    src_dir = component_dir / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    # Change to component directory so FastAPI can find static/templates
    try:
        os.chdir(str(component_dir))
    except (FileNotFoundError, OSError) as err:
        _LOGGER.warning("Could not change to component directory, continuing anyway: %s", err)

    # Set environment variables from config (options override initial setup data)
    config = {**entry.data, **entry.options}
    os.environ["OPENAI_API_KEY"] = config.get(CONF_API_KEY, "")
    os.environ["OPENAI_API_URL"] = config.get(CONF_API_URL, "https://openrouter.ai/api/v1")
    os.environ["OPENAI_MODEL"] = config.get(CONF_MODEL, "gpt-4o")
    os.environ["HA_CONFIG_DIR"] = hass.config.config_dir
    os.environ["BACKUP_DIR"] = os.path.join(hass.config.config_dir, ".ai_agent_backups")
    os.environ["LOG_LEVEL"] = config.get(CONF_LOG_LEVEL, "info")

    if config.get(CONF_TEMPERATURE):
        os.environ["TEMPERATURE"] = str(config.get(CONF_TEMPERATURE))

    os.environ["REASONING_EFFORT"] = config.get(CONF_REASONING_EFFORT) or ""

    if config.get(CONF_SYSTEM_PROMPT_FILE):
        os.environ["SYSTEM_PROMPT_FILE"] = config.get(CONF_SYSTEM_PROMPT_FILE)

    os.environ["ENABLE_CACHE_CONTROL"] = str(config.get(CONF_ENABLE_CACHE_CONTROL, False)).lower()
    os.environ["USAGE_TRACKING"] = config.get(CONF_USAGE_TRACKING, "stream_options")
    os.environ["MEMORY_DIR"] = os.path.join(hass.config.config_dir, ".ai_agent_memories")
    os.environ["SESSIONS_DIR"] = os.path.join(hass.config.config_dir, ".ai_agent_sessions")

    if config.get(CONF_NODERED_URL):
        os.environ["NODERED_URL"] = config.get(CONF_NODERED_URL)
    elif "NODERED_URL" in os.environ:
        del os.environ["NODERED_URL"]

    if config.get(CONF_NODERED_TOKEN):
        os.environ["NODERED_TOKEN"] = config.get(CONF_NODERED_TOKEN)
    elif "NODERED_TOKEN" in os.environ:
        del os.environ["NODERED_TOKEN"]

    if config.get(CONF_NODERED_FLOWS_FILE):
        os.environ["NODERED_FLOWS_FILE"] = config.get(CONF_NODERED_FLOWS_FILE)
    elif "NODERED_FLOWS_FILE" in os.environ:
        del os.environ["NODERED_FLOWS_FILE"]

    if config.get(CONF_AA_API_KEY):
        os.environ["ARTIFICIAL_ANALYSIS_API_KEY"] = config.get(CONF_AA_API_KEY)
    elif "ARTIFICIAL_ANALYSIS_API_KEY" in os.environ:
        del os.environ["ARTIFICIAL_ANALYSIS_API_KEY"]

    if config.get(CONF_SUGGESTION_PROMPT):
        os.environ["SUGGESTION_PROMPT"] = config.get(CONF_SUGGESTION_PROMPT)
    elif "SUGGESTION_PROMPT" in os.environ:
        del os.environ["SUGGESTION_PROMPT"]

    # Legacy "research_model" feeds the fast slot only when it used the main
    # provider and the user has never set fast_model (blank means "use main").
    if CONF_FAST_MODEL in config:
        _fast = config.get(CONF_FAST_MODEL)
    elif config.get("research_api_url") or config.get("research_api_key"):
        _fast = None
    else:
        _fast = config.get("research_model")
    if _fast:
        os.environ["FAST_MODEL"] = _fast
    else:
        os.environ.pop("FAST_MODEL", None)

    for _conf, _env in [
        (CONF_MAX_SUGGESTIONS, "MAX_SUGGESTIONS"),
        (CONF_MAX_ITERATIONS, "MAX_ITERATIONS"),
    ]:
        if config.get(_conf) is not None:
            os.environ[_env] = str(config.get(_conf))
        elif _env in os.environ:
            del os.environ[_env]

    # Create backup directory if it doesn't exist
    os.makedirs(os.environ["BACKUP_DIR"], exist_ok=True)

    # Find available port
    import socket
    sock = socket.socket()
    sock.bind(('', 0))
    port = sock.getsockname()[1]
    sock.close()

    hass.data[DOMAIN][entry.entry_id]["port"] = port

    # Import and configure the FastAPI app
    try:
        from .src.main import app, set_hass_instance
        _LOGGER.info("FastAPI app imported successfully")
    except Exception as err:
        _LOGGER.error("Failed to import FastAPI app: %s", err, exc_info=True)
        raise

    # Create uvicorn config
    config_uvicorn = uvicorn.Config(
        app=app,
        host="127.0.0.1",  # Only bind to localhost for security
        port=port,
        log_level=config.get(CONF_LOG_LEVEL, "info").lower(),
        access_log=False
    )

    server = uvicorn.Server(config_uvicorn)

    # Start server in background task
    async def run_server():
        try:
            _LOGGER.info("Starting FastAPI server on 127.0.0.1:%s", port)
            await server.serve()
        except Exception as err:
            _LOGGER.error("Failed to start AI Config Agent server: %s", err, exc_info=True)

    hass.data[DOMAIN][entry.entry_id]["server_task"] = hass.async_create_task(run_server())
    hass.data[DOMAIN][entry.entry_id]["server"] = server

    # Wait a moment for server to start
    await asyncio.sleep(0.5)

    # Set the hass instance on the config manager for validation
    set_hass_instance(hass)

    _LOGGER.info("AI Configuration Agent server task created on port %s", port)


async def _register_services(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Register component services."""

    async def handle_chat(call: ServiceCall) -> dict[str, Any] | None:
        """Handle chat service call."""
        from .src import main as backend

        if not backend.agent_system:
            raise HomeAssistantError("The AI agent is not initialized.")

        changesets = {}
        completion = None
        error = None

        def chat_error(message: str) -> HomeAssistantError:
            if changesets:
                message += f" Pending changesets: {', '.join(changesets)}."
            return HomeAssistantError(message)

        stream = backend._agent_event_stream(
            call.data["message"], call.data.get("conversation_history", [])
        )
        try:
            async for event in stream:
                data = event.get("data")
                if not isinstance(data, dict):
                    raise chat_error("Chat returned invalid event data.")
                if event.get("event") == "tool_result":
                    result = data.get("result")
                    if isinstance(result, dict) and result.get("success") and result.get("changeset_id"):
                        changesets[result["changeset_id"]] = result
                elif event.get("event") == "complete":
                    completion = data
                elif event.get("event") == "error":
                    error = str(data.get("error") or "The AI agent reported an error.")
        finally:
            await stream.aclose()

        if error:
            raise chat_error(error)
        if completion is None or not isinstance(completion.get("messages"), list):
            raise chat_error("Chat ended without a valid completion.")

        messages = completion["messages"]
        response = next(
            (
                message["content"]
                for message in reversed(messages)
                if isinstance(message, dict)
                and message.get("role") == "assistant"
                and not message.get("tool_calls")
                and isinstance(message.get("content"), str)
            ),
            None,
        )
        if response is None:
            raise chat_error("Chat completed without a final assistant reply.")
        if not call.return_response:
            return None
        return {
            "response": response,
            "messages": messages,
            "changesets": list(changesets.values()),
            "truncated": bool(completion.get("truncated")),
        }

    async def handle_approve(call: ServiceCall) -> dict[str, Any] | None:
        """Handle approve service call."""
        import aiohttp

        port = hass.data[DOMAIN][entry.entry_id]["port"]
        change_id = call.data.get("change_id")
        approved = call.data.get("approved", True)
        validate = call.data.get("validate", True)

        uncertain = "Changes may have been applied; check the configuration before retrying."
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"http://localhost:{port}/api/approve",
                    json={
                        "change_id": change_id,
                        "approved": approved,
                        "validate": validate
                    }
                ) as resp:
                    if resp.status != 200:
                        raise HomeAssistantError(
                            f"Approval request failed (HTTP {resp.status}): {await resp.text()}. {uncertain}"
                        )
                    try:
                        result = await resp.json()
                    except (aiohttp.ContentTypeError, ValueError) as err:
                        raise HomeAssistantError(f"Approval returned invalid JSON. {uncertain}") from err
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise HomeAssistantError(
                f"Approval request failed: {str(err) or type(err).__name__}. {uncertain}"
            ) from err

        if not isinstance(result, dict):
            raise HomeAssistantError(f"Approval returned an invalid result. {uncertain}")
        applied_files = result.get("applied_files", [])
        failed_files = result.get("failed_files", [])
        if not isinstance(applied_files, list) or not isinstance(failed_files, list):
            raise HomeAssistantError(f"Approval returned invalid file details. {uncertain}")
        reload_error = result.get("reload_error")
        if (
            result.get("success") is not True
            or result.get("applied") is not approved
            or failed_files
            or reload_error
        ):
            details = [str(result.get("message") or "Approval did not complete successfully.")]
            if applied_files:
                details.append("Already applied: " + ", ".join(map(str, applied_files)))
            if failed_files:
                details.append("Failed: " + "; ".join(
                    f"{item.get('file_path', 'unknown')}: {item.get('error', 'unknown error')}"
                    if isinstance(item, dict) else str(item)
                    for item in failed_files
                ))
            if reload_error:
                details.append(f"Reload error: {reload_error}")
            raise HomeAssistantError(". ".join(details))

        if call.return_response:
            return result
        if result.get("validation_warnings"):
            _LOGGER.warning("Approval validation warnings: %s", result["validation_warnings"])
        if result.get("restart_required"):
            _LOGGER.warning("Approval requires a Home Assistant restart: %s", result["restart_required"])
        if approved and result.get("config_reloaded") is False:
            _LOGGER.warning("Approval applied changes, but Home Assistant configuration was not reloaded.")
        return None

    # Register services
    hass.services.async_register(
        DOMAIN,
        "chat",
        handle_chat,
        schema=vol.Schema({
            vol.Required("message"): cv.string,
            vol.Optional("conversation_history"): list,
        }),
        supports_response=SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        DOMAIN,
        "approve",
        handle_approve,
        schema=vol.Schema({
            vol.Required("change_id"): cv.string,
            vol.Optional("approved", default=True): cv.boolean,
            vol.Optional("validate", default=True): cv.boolean,
        }),
        supports_response=SupportsResponse.OPTIONAL,
    )


async def _register_panel(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Register the frontend panel."""
    from homeassistant.components.http import HomeAssistantView
    from aiohttp import web
    import aiohttp

    port = hass.data[DOMAIN][entry.entry_id]["port"]

    class AIConfigAgentView(HomeAssistantView):
        """Proxy view for AI Config Agent."""

        # Enforce HA's normal session/token auth (cookie or long-lived token) —
        # this view proxies session history, memory files, and config
        # read/write, so it must never be reachable without a valid HA login.
        requires_auth = True
        url = "/api/ha_ai_companion/{path:.*}"
        name = "api:ha_ai_companion"

        async def get(self, request, path=""):
            """Proxy GET requests."""
            async with aiohttp.ClientSession() as session:
                url = f"http://localhost:{port}/{path}"
                if request.query_string:
                    url += f"?{request.query_string}"

                # Copy relevant headers
                headers = {}
                for header in ['Accept', 'Accept-Encoding', 'Accept-Language']:
                    if header in request.headers:
                        headers[header] = request.headers[header]

                try:
                    async with session.get(url, headers=headers) as resp:
                        body = await resp.read()

                        # Create response and set Content-Type header directly to preserve charset
                        response = web.Response(body=body, status=resp.status)

                        # Copy all relevant headers from upstream response
                        for header in ['Content-Type', 'Cache-Control', 'ETag', 'Last-Modified']:
                            if header in resp.headers:
                                response.headers[header] = resp.headers[header]

                        return response
                except Exception as err:
                    _LOGGER.error(f"Proxy GET error for {url}: {err}")
                    return web.Response(text=f"Proxy error: {err}", status=502)

        async def post(self, request, path=""):
            """Proxy POST requests."""
            data = await request.read()
            async with aiohttp.ClientSession() as session:
                url = f"http://localhost:{port}/{path}"
                headers = {"Content-Type": request.content_type or "application/json"}

                try:
                    async with session.post(url, data=data, headers=headers) as resp:
                        content_type = resp.headers.get('Content-Type', '')
                        if content_type.startswith('text/event-stream'):
                            stream = web.StreamResponse(status=resp.status, headers={
                                'Content-Type': content_type, 'Cache-Control': 'no-cache',
                            })
                            await stream.prepare(request)
                            async for chunk, _ in resp.content.iter_chunks():
                                await stream.write(chunk)
                            await stream.write_eof()
                            return stream

                        body = await resp.read()

                        # Create response and copy headers
                        response = web.Response(body=body, status=resp.status)

                        # Copy Content-Type header to preserve charset
                        if 'Content-Type' in resp.headers:
                            response.headers['Content-Type'] = resp.headers['Content-Type']

                        return response
                except Exception as err:
                    _LOGGER.error(f"Proxy POST error for {url}: {err}")
                    return web.Response(text=f"Proxy error: {err}", status=502)

    hass.http.register_view(AIConfigAgentView())

    # Register WebSocket proxy handler
    async def websocket_proxy(request):
        """Proxy WebSocket connections to the FastAPI server."""
        import aiohttp
        from homeassistant.components.http.const import KEY_HASS_USER

        # This route is registered directly on the aiohttp app (not via
        # HomeAssistantView), so HA's auth middleware still runs and stamps
        # KEY_HASS_USER on authenticated requests, but nothing rejects an
        # unauthenticated one automatically — check it ourselves.
        if request.get(KEY_HASS_USER) is None:
            return web.Response(status=401, text="Unauthorized")

        _LOGGER.info(f"WebSocket proxy request received for path: {request.match_info.get('path', '')}")

        # Chat messages may carry attached images (up to ~2 MB) plus history; aiohttp's default is 4 MB.
        ws_server = web.WebSocketResponse(max_msg_size=16 * 1024 * 1024)
        await ws_server.prepare(request)

        # Extract path from request
        # The path from match_info will be like "chat" when URL is /api/ha_ai_companion/ws/chat
        # We need to prepend "ws/" to match FastAPI's route
        path = request.match_info.get('path', '')
        ws_url = f"ws://localhost:{port}/ws/{path}"

        _LOGGER.info(f"Connecting to upstream WebSocket: {ws_url}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(ws_url, max_msg_size=16 * 1024 * 1024) as ws_client:
                    _LOGGER.info("WebSocket connection established to upstream server")

                    # Create bidirectional proxy
                    async def forward_to_client():
                        """Forward messages from FastAPI to client."""
                        try:
                            async for msg in ws_client:
                                if msg.type == aiohttp.WSMsgType.TEXT:
                                    _LOGGER.debug(f"Forwarding text to client: {msg.data[:100]}")
                                    await ws_server.send_str(msg.data)
                                elif msg.type == aiohttp.WSMsgType.BINARY:
                                    await ws_server.send_bytes(msg.data)
                                elif msg.type == aiohttp.WSMsgType.ERROR:
                                    _LOGGER.error(f"WebSocket error from upstream: {ws_client.exception()}")
                                    break
                                elif msg.type == aiohttp.WSMsgType.CLOSE:
                                    _LOGGER.info("Upstream WebSocket closed")
                                    await ws_server.close()
                                    break
                        except Exception as err:
                            _LOGGER.error(f"Error forwarding to client: {err}", exc_info=True)

                    async def forward_to_server():
                        """Forward messages from client to FastAPI."""
                        try:
                            async for msg in ws_server:
                                if msg.type == aiohttp.WSMsgType.TEXT:
                                    _LOGGER.debug(f"Forwarding text to server: {msg.data[:100]}")
                                    await ws_client.send_str(msg.data)
                                elif msg.type == aiohttp.WSMsgType.BINARY:
                                    await ws_client.send_bytes(msg.data)
                                elif msg.type == aiohttp.WSMsgType.ERROR:
                                    _LOGGER.error(f"WebSocket error from client")
                                    break
                                elif msg.type == aiohttp.WSMsgType.CLOSE:
                                    _LOGGER.info("Client WebSocket closed")
                                    await ws_client.close()
                                    break
                        except Exception as err:
                            _LOGGER.error(f"Error forwarding to server: {err}", exc_info=True)

                    # Run both directions concurrently
                    await asyncio.gather(
                        forward_to_client(),
                        forward_to_server(),
                        return_exceptions=True
                    )
        except aiohttp.ClientError as err:
            _LOGGER.error(f"WebSocket client error: {err}", exc_info=True)
        except Exception as err:
            _LOGGER.error(f"WebSocket proxy error: {err}", exc_info=True)
        finally:
            if not ws_server.closed:
                await ws_server.close()
            _LOGGER.info("WebSocket proxy connection closed")

        return ws_server

    # Register WebSocket route
    hass.http.app.router.add_get('/api/ha_ai_companion/ws/{path:.*}', websocket_proxy)

    # Register iframe panel that points to our proxy
    from homeassistant.components.frontend import async_register_built_in_panel

    async_register_built_in_panel(
        hass,
        component_name="iframe",
        sidebar_title="AI Companion",
        sidebar_icon="mdi:robot-outline",
        frontend_url_path="ha_ai_companion",
        config={"url": "/api/ha_ai_companion/"},
        require_admin=True
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading AI Configuration Agent")

    # Stop the server
    entry_data = hass.data[DOMAIN].get(entry.entry_id)
    if entry_data:
        server = entry_data.get("server")
        if server:
            server.should_exit = True
            await asyncio.sleep(0.1)

        server_task = entry_data.get("server_task")
        if server_task and not server_task.done():
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass

    # Remove services
    hass.services.async_remove(DOMAIN, "chat")
    hass.services.async_remove(DOMAIN, "approve")

    # Remove panel
    from homeassistant.components.frontend import async_remove_panel
    async_remove_panel(hass, "ha_ai_companion")

    # Clean up stored data
    hass.data[DOMAIN].pop(entry.entry_id)

    return True
