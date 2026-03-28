from fastapi import FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager
from pydantic import BaseModel
from typing import Dict, Any, Optional, List
import os
import logging
from datetime import datetime, timezone
import json as json_lib

from .config import ConfigurationManager
from .agents import AgentSystem
from .memory import MemoryManager
from .conversations import ConversationManager

version = "1.1.4"

# Configure logging
log_level = os.getenv('LOG_LEVEL', 'info').upper()
logging.basicConfig(
    level=getattr(logging, log_level),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Phase 2: Global configuration manager instance
config_manager: Optional[ConfigurationManager] = None

# Phase 3: Global agent system instance
agent_system: Optional[AgentSystem] = None

# Memory manager instance
memory_manager: Optional[MemoryManager] = None

# Conversation session manager
conversation_manager: Optional[ConversationManager] = None

# Phase 2: Pydantic models for API requests
class RestoreBackupRequest(BaseModel):
    backup_name: str
    validate: bool = True

# Phase 3: Pydantic models for agent chat
class ChatRequest(BaseModel):
    message: str
    conversation_history: Optional[List[Dict[str, Any]]] = None

class ApprovalRequest(BaseModel):
    change_id: str
    approved: bool
    validate: bool = True

class SaveSessionRequest(BaseModel):
    title: Optional[str] = None
    messages: List[Dict[str, Any]]

# Startup/shutdown event handler
@asynccontextmanager
async def lifespan(_: FastAPI):
    """Initialize application on startup."""
    global config_manager, agent_system, memory_manager, conversation_manager

    logger.info("=== HA AI Companion Starting ===")
    logger.info(f"OpenAI API URL: {os.getenv('OPENAI_API_URL', 'Not configured')}")
    logger.info(f"OpenAI Model: {os.getenv('OPENAI_MODEL', 'Not configured')}")
    logger.info(f"OpenAI API Key: {'configured' if os.getenv('OPENAI_API_KEY') else 'NOT configured'}")
    logger.info(f"HA Config Dir: {os.getenv('HA_CONFIG_DIR', 'Not configured')}")
    logger.info(f"Log Level: {log_level}")

    # Phase 2: Initialize configuration manager
    try:
        # Run in executor to avoid blocking the event loop
        import asyncio
        loop = asyncio.get_event_loop()

        def init_config_manager():
            return ConfigurationManager(
                config_dir=os.getenv('HA_CONFIG_DIR', '/config'),
                backup_dir=os.getenv('BACKUP_DIR', '/backup')
            )

        config_manager = await loop.run_in_executor(None, init_config_manager)
        logger.info("Configuration manager initialized")
    except Exception as e:
        logger.error(f"Failed to initialize configuration manager: {e}", exc_info=True)

    # Initialize conversation manager
    try:
        sessions_dir = os.getenv('SESSIONS_DIR', os.path.join(os.getenv('HA_CONFIG_DIR', '/config'), '.ai_agent_sessions'))
        _max_sessions_str = os.getenv('MAX_SESSIONS', '').strip()
        _max_sessions = int(_max_sessions_str) if _max_sessions_str.isdigit() else ConversationManager.DEFAULT_MAX_SESSIONS
        conversation_manager = ConversationManager(sessions_dir=sessions_dir, max_sessions=_max_sessions)
        logger.info(f"Conversation manager initialized at {sessions_dir}")
    except Exception as e:
        logger.error(f"Failed to initialize conversation manager: {e}", exc_info=True)

    # Initialize memory manager
    try:
        memory_dir = os.getenv('MEMORY_DIR', os.path.join(os.getenv('HA_CONFIG_DIR', '/config'), '.ai_agent_memories'))
        memory_manager = MemoryManager(memory_dir=memory_dir)
        logger.info(f"Memory manager initialized at {memory_dir}")
    except Exception as e:
        logger.error(f"Failed to initialize memory manager: {e}", exc_info=True)

    # Phase 3: Initialize agent system
    try:
        if config_manager:
            # Load custom system prompt from file if specified
            system_prompt = None
            system_prompt_file = os.getenv('SYSTEM_PROMPT_FILE')
            if system_prompt_file:
                try:
                    config_dir = os.getenv('HA_CONFIG_DIR', '/config')
                    prompt_path = os.path.join(config_dir, system_prompt_file)

                    # Security: Ensure path is within config directory
                    real_config = os.path.realpath(config_dir)
                    real_prompt = os.path.realpath(prompt_path)
                    if not real_prompt.startswith(real_config):
                        logger.error(f"System prompt file path {system_prompt_file} is outside config directory")
                    else:
                        with open(prompt_path, 'r') as f:
                            system_prompt = f.read()
                        logger.info(f"Loaded custom system prompt from {system_prompt_file}")
                except FileNotFoundError:
                    logger.warning(f"System prompt file not found: {system_prompt_file}, using default")
                except Exception as e:
                    logger.error(f"Error reading system prompt file: {e}, using default")

            # Read enable_cache_control setting
            enable_cache_control_str = os.getenv('ENABLE_CACHE_CONTROL', 'false').lower()
            enable_cache_control = enable_cache_control_str in ('true', '1', 'yes')

            # Read usage_tracking setting
            usage_tracking = os.getenv('USAGE_TRACKING', 'stream_options').lower()
            if usage_tracking not in ('stream_options', 'usage', 'disabled'):
                logger.warning(f"Invalid usage_tracking value '{usage_tracking}', defaulting to 'stream_options'")
                usage_tracking = 'stream_options'

            agent_system = AgentSystem(
                config_manager,
                system_prompt=system_prompt,
                enable_cache_control=enable_cache_control,
                usage_tracking=usage_tracking,
                memory_manager=memory_manager,
            )
            logger.info("Agent system initialized")
        else:
            logger.warning("Agent system not initialized - config manager unavailable")
    except Exception as e:
        logger.error(f"Failed to initialize agent system: {e}")

    yield

    # Shutdown
    logger.info("=== HA AI Companion Shutting Down ===")

# Initialize FastAPI application with lifespan
app = FastAPI(
    title="HA AI Companion",
    description="AI-powered Home Assistant configuration management",
    version=version,
    lifespan=lifespan
)


@app.middleware("http")
async def strip_double_slash_middleware(request: Request, call_next):
    """
    Middleware to remove a leading double slash from the request URL path.
    """
    path = request.scope.get("path")
    if path and path.startswith("//"):
        # Modify the path in the request scope
        request.scope["path"] = path[1:]

    response = await call_next(request)
    return response

# Mount static files and templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Function to set hass instance (called from custom component __init__.py)
def set_hass_instance(hass):
    """Set the Home Assistant instance for custom component mode."""
    global config_manager
    if config_manager:
        config_manager.hass = hass
        logger.info("Home Assistant instance set on config_manager")

# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint for Docker and monitoring."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": version,
        "config_manager_ready": config_manager is not None,
        "agent_system_ready": agent_system is not None,
        "openai_configured": bool(os.getenv('OPENAI_API_KEY'))
    }

@app.get("/api/logs")
async def get_logs(lines: int = 200, filter: str = "ha_ai_companion"):
    """Return recent lines from home-assistant.log matching the filter."""
    config_dir = os.getenv("HA_CONFIG_DIR", "/config")
    log_path = os.path.join(config_dir, "home-assistant.log")
    try:
        with open(log_path, "r", errors="replace") as f:
            all_lines = f.readlines()
        # Return last `lines` lines that contain the filter keyword (case-insensitive)
        kw = filter.lower()
        matched = [l.rstrip() for l in all_lines if kw in l.lower()] if kw else [l.rstrip() for l in all_lines]
        return {"log_path": log_path, "total_matched": len(matched), "lines": matched[-lines:]}
    except FileNotFoundError:
        return {"error": f"Log file not found: {log_path}", "lines": []}
    except Exception as e:
        return {"error": str(e), "lines": []}


# Root endpoint - will serve the UI
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Serve main interface."""
    return templates.TemplateResponse("index.html", {
        "request": request,
        "version": version
    })

@app.websocket("/ws/chat")
async def chat_websocket(websocket: WebSocket):
    """
    Chat with the AI configuration assistant using WebSocket.

    This is an alternative to the SSE endpoint that avoids buffering issues
    with Home Assistant Ingress proxy.

    The client sends:
        {
            "type": "chat",
            "message": "user message",
            "conversation_history": [...]
        }

    The server sends:
        {
            "event": "token" | "tool_call" | "tool_start" | "tool_result" | "message_complete" | "complete" | "error",
            "data": {...}
        }
    """
    await websocket.accept()
    logger.info("WebSocket connection accepted")

    try:
        while True:
            # Receive message from client
            data = await websocket.receive_json()
            logger.info(f"WebSocket received: type={data.get('type')}, message_len={len(data.get('message', ''))}")

            if data.get("type") != "chat":
                await websocket.send_json({
                    "event": "error",
                    "data": {"error": "Invalid message type"}
                })
                continue

            if not agent_system:
                await websocket.send_json({
                    "event": "error",
                    "data": {"error": "Agent system not initialized. Please configure OPENAI_API_KEY."}
                })
                continue

            # Stream responses
            try:
                async for event in agent_system.chat_stream(
                    user_message=data.get("message", ""),
                    conversation_history=data.get("conversation_history")
                ):
                    # Parse the JSON data if it's a string
                    event_data = event.get("data", "{}")
                    if isinstance(event_data, str):
                        event_data = json_lib.loads(event_data)

                    # Send each event immediately
                    message = {
                        "event": event.get("event"),
                        "data": event_data
                    }
                    await websocket.send_json(message)
                    logger.debug(f"WebSocket sent: {event.get('event')}")

            except Exception as e:
                logger.error(f"Stream error: {e}", exc_info=True)
                await websocket.send_json({
                    "event": "error",
                    "data": {"error": str(e)}
                })

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)


@app.post("/api/approve")
async def approve_changes(request: ApprovalRequest):
    """
    Approve or reject proposed configuration changes.

    Args:
        request: ApprovalRequest with change_id, approval status, and validation flag

    Returns:
        Dict with:
            - success: bool
            - applied: bool
            - message: str
            - error: Optional[str]

    Raises:
        HTTPException: 500 if agent system not initialized or error occurs

    Note: Full approval workflow will be implemented in Phase 4.
    """
    if not agent_system:
        raise HTTPException(
            status_code=500,
            detail="Agent system not initialized"
        )

    try:
        result = await agent_system.process_approval(
            change_id=request.change_id,
            approved=request.approved,
            validate=request.validate
        )

        return result

    except Exception as e:
        logger.error(f"Approval error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


# --- Session persistence endpoints ---

@app.get("/api/sessions")
async def list_sessions():
    """List all saved conversation sessions, newest first."""
    if not conversation_manager:
        return {"sessions": []}
    sessions = await conversation_manager.list_sessions()
    return {"sessions": sessions}


@app.get("/api/sessions/{session_id}")
async def load_session(session_id: str):
    """Load a specific session by ID."""
    if not conversation_manager:
        raise HTTPException(status_code=503, detail="Conversation manager not initialized")
    session = await conversation_manager.load_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@app.put("/api/sessions/{session_id}")
async def save_session(session_id: str, request: SaveSessionRequest):
    """Create or overwrite a session."""
    if not conversation_manager:
        raise HTTPException(status_code=503, detail="Conversation manager not initialized")
    title = request.title or ConversationManager._auto_title(request.messages)
    ok = await conversation_manager.save_session(session_id, title, request.messages)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to save session")
    return {"success": True, "session_id": session_id, "title": title}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session."""
    if not conversation_manager:
        raise HTTPException(status_code=503, detail="Conversation manager not initialized")
    deleted = await conversation_manager.delete_session(session_id)
    return {"success": deleted}


# --- Suggestions endpoints ---

SUGGESTIONS_FILE_KEY = ".ai_agent_suggestions.json"
DISMISSED_FILE_KEY  = ".ai_agent_suggestions_dismissed.json"

def _suggestions_path() -> str:
    config_dir = os.getenv("HA_CONFIG_DIR", "/config")
    return os.path.join(config_dir, SUGGESTIONS_FILE_KEY)

def _dismissed_path() -> str:
    config_dir = os.getenv("HA_CONFIG_DIR", "/config")
    return os.path.join(config_dir, DISMISSED_FILE_KEY)

def _read_dismissed() -> list:
    path = _dismissed_path()
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json_lib.load(f)
        except Exception:
            pass
    return []

def _write_dismissed(titles: list):
    with open(_dismissed_path(), "w") as f:
        json_lib.dump(titles, f, indent=2)


SUGGESTIONS_HISTORY_KEY = ".ai_agent_suggestions_history.json"
SUGGESTIONS_HISTORY_DEFAULT_MAX = 10

def _suggestions_history_path() -> str:
    config_dir = os.getenv("HA_CONFIG_DIR", "/config")
    return os.path.join(config_dir, SUGGESTIONS_HISTORY_KEY)

def _read_suggestions_history() -> list:
    path = _suggestions_history_path()
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                data = json_lib.load(f)
            if isinstance(data, list):
                return data
            logger.warning("Suggestions history file has unexpected format, resetting")
        except Exception as e:
            logger.warning(f"Failed to read suggestions history (corrupt file?): {e}")
            # Rename corrupt file so we don't keep failing
            try:
                os.rename(path, path + ".corrupt")
            except Exception:
                pass
    return []

def _append_to_history(payload: dict):
    max_s = int(os.getenv("MAX_SUGGESTIONS", str(SUGGESTIONS_HISTORY_DEFAULT_MAX)))
    history = _read_suggestions_history()
    history.insert(0, payload)
    history = history[:max(1, max_s)]
    try:
        with open(_suggestions_history_path(), "w") as f:
            json_lib.dump(history, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to write suggestions history: {e}")


@app.get("/api/suggestions")
async def get_suggestions():
    """Return cached suggestions from disk, or empty list if none."""
    path = _suggestions_path()
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json_lib.load(f)
        except Exception as e:
            logger.warning(f"Failed to read suggestions file: {e}")
    return {"suggestions": [], "generated_at": None}


@app.post("/api/suggestions/dismiss")
async def dismiss_suggestion(request: Request):
    """Add a suggestion title to the persistent dismissed list."""
    body = await request.json()
    title = body.get("title", "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    dismissed = _read_dismissed()
    if title not in dismissed:
        dismissed.append(title)
        _write_dismissed(dismissed)
    return {"dismissed": dismissed}


@app.delete("/api/suggestions/dismissed")
async def clear_dismissed():
    """Clear all dismissed suggestions."""
    _write_dismissed([])
    return {"dismissed": []}


@app.get("/api/suggestions/dismissed")
async def get_dismissed():
    """Return the current dismissed suggestions list."""
    return {"dismissed": _read_dismissed()}


@app.get("/api/suggestions/history")
async def get_suggestions_history():
    """Return past suggestion sets (newest first)."""
    return {"history": _read_suggestions_history()}


APPLIED_FILE_KEY = ".ai_agent_suggestions_applied.json"

def _applied_path() -> str:
    config_dir = os.getenv("HA_CONFIG_DIR", "/config")
    return os.path.join(config_dir, APPLIED_FILE_KEY)

def _read_applied() -> list:
    path = _applied_path()
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json_lib.load(f)
        except Exception:
            pass
    return []

def _write_applied(titles: list):
    with open(_applied_path(), "w") as f:
        json_lib.dump(titles, f, indent=2)

@app.post("/api/suggestions/applied")
async def mark_suggestion_applied(request: Request):
    """Mark a suggestion title as applied."""
    body = await request.json()
    title = body.get("title", "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    applied = _read_applied()
    if title not in applied:
        applied.append(title)
        _write_applied(applied)
    return {"applied": applied}

@app.get("/api/suggestions/applied")
async def get_applied():
    """Return the list of applied suggestion titles."""
    return {"applied": _read_applied()}

@app.delete("/api/suggestions/applied")
async def clear_applied():
    """Clear all applied suggestions."""
    _write_applied([])
    return {"applied": []}


@app.post("/api/suggestions/generate")
async def generate_suggestions(request: Request):
    """Ask the AI to generate fresh automation suggestions and cache them."""
    if not agent_system:
        raise HTTPException(status_code=503, detail="Agent system not initialized")

    extra_prompt = None
    resource_types = None
    try:
        body = await request.json()
        extra_prompt = body.get("extra_prompt", "").strip() or None
        rt = body.get("resource_types")
        if isinstance(rt, list) and rt:
            resource_types = rt
    except Exception:
        pass

    try:
        result = await agent_system.generate_suggestions(extra_prompt=extra_prompt, resource_types=resource_types)
        if result.get("success"):
            payload = {
                "suggestions": result["suggestions"],
                "naming_issues": result.get("naming_issues", []),
                "generated_at": datetime.now(timezone.utc).isoformat()
            }
            try:
                with open(_suggestions_path(), "w") as f:
                    json_lib.dump(payload, f, indent=2)
            except Exception as e:
                logger.warning(f"Failed to cache suggestions: {e}")
            try:
                _append_to_history(payload)
            except Exception as e:
                logger.warning(f"Failed to append to suggestions history: {e}")
            return payload
        else:
            raise HTTPException(status_code=500, detail=result.get("error", "Generation failed"))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Suggestions generation error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
