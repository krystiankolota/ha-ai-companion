from fastapi import FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List
import asyncio
import os
import re
import logging
import tempfile
from datetime import datetime, timezone
import json as json_lib

from .config import ConfigurationManager
from .agents import AgentSystem
from .agents.diffing import describe_change
from .memory import MemoryStore
from .memory.store import extraction_rejection_reason
from .conversations import ConversationManager
from .tasks import TaskManager
from .runs import RunRegistry

version = "1.23.3"

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
memory_manager: Optional[MemoryStore] = None

# Conversation session manager
conversation_manager: Optional[ConversationManager] = None

# Scheduled AI task manager
task_manager: Optional[TaskManager] = None

# Agent run registry — keeps runs alive across WebSocket disconnects
run_registry = RunRegistry()

# Phase 2: Pydantic models for API requests
class RestoreBackupRequest(BaseModel):
    backup_name: str
    run_validation: bool = Field(True, alias="validate")
    model_config = {"populate_by_name": True}

# Phase 3: Pydantic models for agent chat
class ChatRequest(BaseModel):
    message: str
    conversation_history: Optional[List[Dict[str, Any]]] = None

class ApprovalRequest(BaseModel):
    change_id: str
    approved: bool
    run_validation: bool = Field(True, alias="validate")
    model_config = {"populate_by_name": True}

class SaveSessionRequest(BaseModel):
    title: Optional[str] = None
    messages: List[Dict[str, Any]]

# Startup/shutdown event handler
@asynccontextmanager
async def lifespan(_: FastAPI):
    """Initialize application on startup."""
    global config_manager, agent_system, memory_manager, conversation_manager, task_manager

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
        memory_manager = MemoryStore(memory_dir=memory_dir)
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
                conversation_manager=conversation_manager,
            )
            logger.info("Agent system initialized")
        else:
            logger.warning("Agent system not initialized - config manager unavailable")
    except Exception as e:
        logger.error(f"Failed to initialize agent system: {e}")

    # Initialize task manager and start scheduler
    try:
        tasks_dir = os.path.join(os.getenv('HA_CONFIG_DIR', '/config'), '.ai_agent_tasks')
        task_manager = TaskManager(tasks_dir=tasks_dir)
        if agent_system:
            agent_system.task_manager = task_manager
            await task_manager.start(agent_system)
        logger.info(f"Task manager initialized at {tasks_dir}")
    except Exception as e:
        logger.error(f"Failed to initialize task manager: {e}", exc_info=True)

    async def _memory_doctor_daily():
        while True:
            try:
                if agent_system and memory_manager:
                    r = await agent_system.tools.memory_doctor()
                    logger.info(
                        "Memory doctor: %d stale, %d duplicate pair(s), %d conflict pair(s), "
                        "%d unused, %d expired candidate(s), %d candidate(s)",
                        len(r.get("stale", [])), len(r.get("duplicates", [])),
                        len(r.get("conflicts", [])), len(r.get("unused", [])),
                        len(r.get("expired_candidates", [])), r.get("candidates", 0),
                    )
                    if (r.get("note") or "").startswith("Entity check skipped"):
                        await asyncio.sleep(60)
                        continue
            except Exception as e:
                logger.warning(f"Memory doctor failed: {e}")
            await asyncio.sleep(86400)

    doctor_task = asyncio.create_task(_memory_doctor_daily())

    yield

    # Shutdown
    logger.info("=== HA AI Companion Shutting Down ===")
    doctor_task.cancel()
    if task_manager:
        await task_manager.stop()

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

    pump_task: Optional[asyncio.Task] = None
    subscribed: Optional[tuple] = None  # (run_key, queue)

    async def send_safe(message) -> bool:
        """Send or report dead socket — never raise (fixes double-fault on closed WS)."""
        try:
            await websocket.send_json(message)
            return True
        except Exception:
            return False

    async def detach():
        nonlocal pump_task, subscribed
        if pump_task:
            pump_task.cancel()
            try:
                await pump_task
            except (asyncio.CancelledError, Exception):
                pass
            pump_task = None
        if subscribed:
            await run_registry.unsubscribe(subscribed[0], subscribed[1])
            subscribed = None

    async def pump(run_key: str, replay: list, queue: Optional[asyncio.Queue]):
        """Relay replayed + live events to this socket. Dead socket = silent stop."""
        for message in replay:
            if not await send_safe(message):
                return
        if queue is None:
            return
        while True:
            item = await queue.get()
            if item is None:  # run finished sentinel
                return
            if not await send_safe(item):
                await run_registry.unsubscribe(run_key, queue)
                return

    async def attach(run_key: str, last_seq: int) -> Optional[str]:
        """Subscribe this socket to a run; returns error string or None."""
        nonlocal pump_task, subscribed
        await detach()
        replay, queue, err = await run_registry.subscribe(run_key, last_seq)
        if err:
            return err
        if queue is not None:
            subscribed = (run_key, queue)
        pump_task = asyncio.create_task(pump(run_key, replay, queue))
        return None

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            logger.info(f"WebSocket received: type={msg_type}, message_len={len(data.get('message', ''))}")

            if msg_type == "chat":
                if not agent_system:
                    await send_safe({
                        "event": "error",
                        "data": {"error": "Agent system not initialized. Please configure OPENAI_API_KEY."}
                    })
                    continue

                session_id = data.get("session_id") or f"anon-{id(websocket)}"
                user_message = data.get("message", "")
                conversation_history = data.get("conversation_history")

                ok, err = await run_registry.start(
                    session_id,
                    stream_factory=lambda um=user_message, ch=conversation_history, sid=session_id: _agent_event_stream(um, ch, sid),
                    on_finish=_make_run_persister(data.get("session_id"), user_message),
                )
                if not ok:
                    await send_safe({"event": "error", "data": {"error": err, "code": "run_active"}})
                    continue
                await attach(session_id, -1)

            elif msg_type == "stop":
                session_id = data.get("session_id")
                if not session_id or not await run_registry.cancel(session_id):
                    # Nothing running server-side (finished or add-on restarted):
                    # still send a terminal event so the UI never stays "busy".
                    await send_safe({"event": "error", "data": {"error": "No active run", "code": "stopped"}})

            elif msg_type == "resume":
                session_id = data.get("session_id")
                last_seq = data.get("last_seq", -1)
                if not session_id:
                    await send_safe({"event": "resume_failed", "data": {"reason": "missing_session_id"}})
                    continue
                err = await attach(session_id, last_seq if isinstance(last_seq, int) else -1)
                if err:
                    await send_safe({"event": "resume_failed", "data": {"reason": err}})
                else:
                    await send_safe({"event": "resumed", "data": {"session_id": session_id}})

            else:
                await send_safe({
                    "event": "error",
                    "data": {"error": "Invalid message type"}
                })

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected (run keeps executing if active)")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
    finally:
        await detach()


async def _agent_event_stream(user_message: str, conversation_history, session_id=None):
    """Adapt agent_system.chat_stream into normalized {event, data} messages."""
    async for event in agent_system.chat_stream(
        user_message=user_message,
        conversation_history=conversation_history,
        session_id=session_id,
    ):
        event_data = event.get("data", "{}")
        if isinstance(event_data, str):
            event_data = json_lib.loads(event_data)
        yield {"event": event.get("event"), "data": event_data}


def _make_run_persister(session_id: Optional[str], user_message: str):
    """
    Build the on_finish callback that persists run output to the session file.

    Fallback only — the frontend's session PUT (richer: breadcrumbs, approvals)
    overwrites this when the client is still around. This path matters when the
    user never returns: tab closed mid-run, result must land in the session.
    """
    if not session_id or not conversation_manager:
        return None

    async def persist(status: str, events: list):
        try:
            assistant_parts = [
                ((e["data"].get("message") or {}).get("content") or "")
                for e in events
                if e.get("event") == "message_complete" and isinstance(e.get("data"), dict)
            ]
            assistant_text = "\n\n".join(p for p in assistant_parts if p).strip()
            if not assistant_text:
                return

            session = await conversation_manager.load_session(session_id)
            messages = (session or {}).get("messages", [])

            def _content(m):
                return m.get("content") if isinstance(m.get("content"), str) else None

            # Frontend may have saved the user message already (debounced PUT/sendBeacon).
            # Find the latest user turn; if an assistant reply follows it, the frontend
            # persisted the full exchange and this fallback has nothing to do.
            last_user_idx = next(
                (i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "user"),
                None,
            )
            if last_user_idx is not None and _content(messages[last_user_idx]) != user_message:
                last_user_idx = None
            if last_user_idx is not None:
                # Only a final text answer counts: the frontend autosaves
                # intermediate tool_calls messages mid-run.
                if any(
                    m.get("role") == "assistant" and _content(m) and not m.get("tool_calls")
                    for m in messages[last_user_idx + 1:]
                ):
                    return
            else:
                messages.append({"role": "user", "content": user_message})
            messages.append({"role": "assistant", "content": assistant_text})

            title = (session or {}).get("title") or user_message[:60].strip() or "New conversation"
            await conversation_manager.save_session(session_id, title, messages)
            logger.info(f"Run result persisted to session {session_id} (status={status})")
        except Exception as e:
            logger.error(f"Run persistence failed for {session_id}: {e}", exc_info=True)

    return persist


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
            validate=request.run_validation
        )

        return result

    except Exception as e:
        logger.error(f"Approval error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


@app.get("/api/changeset/{changeset_id}")
async def get_changeset(changeset_id: str):
    """Return a pending changeset with server-computed diffs.

    Each file carries a unified diff built from the exact content the proposal
    was based on, plus an added/changed/removed item summary for YAML lists.
    The frontend only renders it — it never reconstructs the "before" side.
    """
    if not agent_system:
        raise HTTPException(status_code=503, detail="Agent system not initialized")
    changeset = agent_system.pending_changesets.get(changeset_id)
    if not changeset:
        raise HTTPException(status_code=404, detail="Changeset not found or expired")
    return {
        "changeset_id": changeset_id,
        "file_changes": [
            {
                **describe_change(fc["file_path"], fc.get("original_content", ""), fc["new_content"]),
                "new_content": fc["new_content"],
            }
            for fc in changeset.file_changes
        ],
    }


@app.get("/api/changesets")
async def list_applied_changesets(limit: int = 20):
    """Recently applied changesets (restorable via the restore_changeset tool)."""
    history = getattr(agent_system, "changeset_history", None) if agent_system else None
    if not history:
        raise HTTPException(status_code=503, detail="Changeset history not available")
    return {"changesets": history.list(max(1, min(limit, 100)))}


@app.post("/api/changesets/{changeset_id}/restore")
async def restore_applied_changeset(changeset_id: str):
    """Stage a restore of an applied changeset; the result goes through normal approval."""
    if not agent_system:
        raise HTTPException(status_code=503, detail="Agent system not initialized")
    result = await agent_system.tools.restore_changeset(changeset_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Restore failed"))
    return result


# --- Scheduled task endpoints ---

@app.get("/api/scheduled-tasks")
async def list_scheduled_tasks():
    """List all scheduled AI tasks."""
    if not task_manager:
        raise HTTPException(status_code=503, detail="Task manager not initialized")
    return {"tasks": task_manager.list_tasks()}


@app.delete("/api/scheduled-tasks/{task_id}")
async def delete_scheduled_task(task_id: str):
    """Delete a scheduled AI task by ID."""
    if not task_manager:
        raise HTTPException(status_code=503, detail="Task manager not initialized")
    if not task_manager.delete_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return {"success": True, "task_id": task_id}


@app.post("/api/scheduled-tasks/{task_id}/run")
async def run_scheduled_task(task_id: str):
    """Manually trigger a scheduled task immediately."""
    if not task_manager or not agent_system:
        raise HTTPException(status_code=503, detail="Task manager not initialized")
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    try:
        result = await task_manager._run_ai_task(task, agent_system)
        if result and task.get("entity_id"):
            await agent_system.tools.set_ha_text_entity(
                entity_id=task["entity_id"], value=result[:255]
            )
        task_manager._mark_run(task_id)
        return {"success": True, "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Session persistence endpoints ---

@app.get("/api/runs/{session_id}")
async def run_status(session_id: str):
    """Agent run state for a session: running/done/error/cancelled, or none."""
    st = await run_registry.status(session_id)
    return st or {"status": "none", "last_seq": -1}


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


@app.post("/api/sessions/clear-all")
async def clear_all_sessions():
    """Analyze all sessions for memorable facts, save them, then delete all sessions."""
    if not conversation_manager:
        raise HTTPException(status_code=503, detail="Conversation manager not initialized")

    sessions = await conversation_manager.list_sessions()
    if not sessions:
        return {"sessions_deleted": 0, "memories_saved": []}

    memories_saved = []

    if agent_system and agent_system.client:
        # Collect readable user+assistant content (skip tool calls)
        conv_parts = []
        for meta in sessions:
            session = await conversation_manager.load_session(meta["id"])
            if not session:
                continue
            msgs = [
                f"{m['role'].upper()}: {str(m.get('content', ''))[:400]}"
                for m in session.get("messages", [])
                if m.get("role") in ("user", "assistant") and m.get("content")
            ]
            if msgs:
                conv_parts.append(f"### {meta.get('title', 'Untitled')}\n" + "\n".join(msgs[:15]))

        conv_text = "\n\n".join(conv_parts)[:15000]

        if conv_text:
            prompt = (
                "Review these Home Assistant conversation sessions and extract facts worth saving as persistent agent memories.\n\n"
                "Save ONLY durable facts the user stated that HA itself cannot tell:\n"
                "- User preferences\n"
                "- Decisions, including their reasons\n"
                "- Facts that are not derivable from Home Assistant\n\n"
                "Skip entity lists, areas, devices, dashboard structure, automation contents, IDs, one-off tasks, and live state.\n"
                "Save one fact per record, use no Markdown headings, and keep each record to 200 characters or less.\n\n"
                'Return JSON: {"memories": [{"kind": "preference|fact|decision", "text": "one short fact"}]}\n'
                'If nothing worth saving: {"memories": []}\n\n'
                f"Conversations:\n{conv_text}"
            )

            data = {"memories": []}
            fast_client, fast_model = agent_system._fast_client_and_model()
            try:
                resp = await agent_system._completion_with_usage(
                    fast_client, 'suggestion', phase="memory_extraction",
                    model=fast_model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    max_tokens=1500,
                    temperature=0.2,
                )
                data = json_lib.loads(resp.choices[0].message.content)
            except Exception:
                try:
                    import re as _re
                    resp = await agent_system._completion_with_usage(
                        fast_client, 'suggestion', phase="memory_extraction",
                        model=fast_model,
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=1500,
                        temperature=0.2,
                    )
                    text = resp.choices[0].message.content
                    match = _re.search(r'\{.*\}', text, _re.DOTALL)
                    if match:
                        data = json_lib.loads(match.group())
                except Exception as e:
                    logger.warning("Memory extraction failed during clear-all (sessions will still be deleted): %s", e)

            for mem in data.get("memories", []):
                kind, text = mem.get("kind", "fact"), (mem.get("text") or "").strip()
                if text and memory_manager and not extraction_rejection_reason(text):
                    try:
                        r = memory_manager.add(kind if kind in ("preference", "fact", "decision") else "fact",
                                               text, status="candidate", source="session-extract")
                        if not r.get("duplicate_of"):
                            memories_saved.append(text[:80])
                    except ValueError:
                        pass

    deleted = 0
    for meta in sessions:
        if await conversation_manager.delete_session(meta["id"]):
            deleted += 1

    return {"sessions_deleted": deleted, "memories_saved": memories_saved}


# --- Suggestions endpoints ---

SUGGESTIONS_FILE_KEY = ".ai_agent_suggestions.json"
DISMISSED_FILE_KEY  = ".ai_agent_suggestions_dismissed.json"

def _suggestions_path() -> str:
    config_dir = os.getenv("HA_CONFIG_DIR", "/config")
    return os.path.join(config_dir, SUGGESTIONS_FILE_KEY)

def _dismissed_path() -> str:
    config_dir = os.getenv("HA_CONFIG_DIR", "/config")
    return os.path.join(config_dir, DISMISSED_FILE_KEY)

def _write_suggestions_json(path: str, data: Any):
    directory = os.path.dirname(path) or "."
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory, prefix=".suggestions-", suffix=".tmp",
            delete=False,
        ) as f:
            temp_path = f.name
            json_lib.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(temp_path, path)
    except Exception:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
        raise

def _read_dismissed() -> list:
    path = _dismissed_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json_lib.load(f)
        except Exception:
            pass
    return []

def _write_dismissed(titles: list):
    _write_suggestions_json(_dismissed_path(), titles)


SUGGESTIONS_HISTORY_KEY = ".ai_agent_suggestions_history.json"
SUGGESTIONS_HISTORY_DEFAULT_MAX = 10

def _suggestions_history_path() -> str:
    config_dir = os.getenv("HA_CONFIG_DIR", "/config")
    return os.path.join(config_dir, SUGGESTIONS_HISTORY_KEY)

def _read_suggestions_history() -> list:
    path = _suggestions_history_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
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
        _write_suggestions_json(_suggestions_history_path(), history)
    except Exception as e:
        logger.warning(f"Failed to write suggestions history: {e}")


@app.get("/api/suggestions")
async def get_suggestions():
    """Return cached suggestions from disk, or empty list if none."""
    path = _suggestions_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
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

@app.post("/api/suggestions/restore")
async def restore_suggestion(request: Request):
    """Remove a suggestion title from the persistent dismissed list."""
    body = await request.json()
    title = body.get("title", "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    dismissed = _read_dismissed()
    if title not in dismissed:
        raise HTTPException(status_code=404, detail="suggestion is not dismissed")
    dismissed.remove(title)
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
            with open(path, "r", encoding="utf-8") as f:
                return json_lib.load(f)
        except Exception:
            pass
    return []

def _write_applied(titles: list):
    _write_suggestions_json(_applied_path(), titles)

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
    """Ask the AI to generate fresh automation suggestions (NDJSON stream of status + result)."""
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

    queue: asyncio.Queue = asyncio.Queue()
    error_event_emitted = False

    async def progress_cb(payload):
        nonlocal error_event_emitted
        if isinstance(payload, dict) and payload.get("event") == "error":
            error_event_emitted = True
        await queue.put(payload)

    async def run_generation():
        try:
            result = await agent_system.generate_suggestions(
                extra_prompt=extra_prompt,
                resource_types=resource_types,
                progress_cb=progress_cb,
            )
            if result.get("success"):
                payload = {
                    "event": "result",
                    "suggestions": result["suggestions"],
                    "naming_issues": result.get("naming_issues", []),
                    "context_summary": result.get("context_summary", []),
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                }
                try:
                    cache = {k: v for k, v in payload.items() if k != "event"}
                    _write_suggestions_json(_suggestions_path(), cache)
                except Exception as e:
                    logger.warning(f"Failed to cache suggestions: {e}")
                try:
                    _append_to_history({k: v for k, v in payload.items() if k != "event"})
                except Exception as e:
                    logger.warning(f"Failed to append to suggestions history: {e}")
                await queue.put(payload)
            else:
                if not error_event_emitted:
                    await queue.put({"event": "error", "message": result.get("error", "Generation failed")})
        except Exception as e:
            logger.error(f"Suggestions generation error: {e}", exc_info=True)
            await queue.put({"event": "error", "message": str(e)})
        finally:
            await queue.put(None)  # sentinel

    asyncio.create_task(run_generation())

    async def stream():
        while True:
            item = await queue.get()
            if item is None:
                break
            yield json_lib.dumps(item) + "\n"

    # HA ingress compresses application/*json and buffers the whole stream; event-stream is exempt.
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- Memory endpoints ---

@app.get("/api/usage")
async def get_usage(days: int = 30):
    """Return aggregated per-call token/cost usage over the last `days`."""
    um = getattr(agent_system, "usage_manager", None) if agent_system else None
    if not um:
        return {"days": days, "totals": {"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0},
                "by_model": {}, "by_phase": {}, "by_day": {}, "by_session": {}}
    try:
        days = max(1, min(int(days), 365))
        return um.aggregate(days=days)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


_model_advisor = None


@app.get("/api/models/advice")
async def get_model_advice(refresh: bool = False):
    """Data-backed model suggestions: OpenRouter catalog + optional AA scores + own usage mix."""
    global _model_advisor
    from src.usage.advisor import ModelAdvisor
    um = getattr(agent_system, "usage_manager", None) if agent_system else None
    if _model_advisor is None:
        cache_dir = str(um.usage_dir) if um else os.path.join(os.getenv("MEMORY_DIR", "/config/.ai_agent_memories"), "..", ".ai_agent_usage")
        _model_advisor = ModelAdvisor(cache_dir)
    _model_advisor.aa_api_key = os.getenv("ARTIFICIAL_ANALYSIS_API_KEY") or None
    configured, or_roles = {}, set()
    if agent_system:
        configured = {"main": agent_system.model, "fast": agent_system.suggestion_model}
        clients = {"main": "client", "fast": "suggestion_client"}
        for role, attr in clients.items():
            if "openrouter.ai" in str(getattr(getattr(agent_system, attr, None), "base_url", "")):
                or_roles.add(role)
    try:
        usage = um.aggregate(days=30) if um else None
        return await _model_advisor.advise(configured, usage, or_roles, force=refresh)
    except Exception as e:
        logger.error(f"Model advice failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _memory_or_503() -> MemoryStore:
    if not memory_manager:
        raise HTTPException(status_code=503, detail="Memory not initialized")
    return memory_manager


@app.get("/api/memory")
async def list_memories(status: Optional[str] = None):
    """Return memory records (all statuses unless filtered) and per-status counts."""
    if not memory_manager:
        return {"memories": [], "counts": {}}
    return {"memories": memory_manager.list(status=status), "counts": memory_manager.counts()}


@app.post("/api/memory")
async def create_memory(request: Request):
    """Create an active memory written by the user."""
    store = _memory_or_503()
    body = await request.json()
    try:
        r = store.add(body.get("kind", "fact"), body.get("text", ""), key=body.get("key") or None, source="user")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, **r}


@app.put("/api/memory/{mem_id}")
async def update_memory(mem_id: int, request: Request):
    """Edit text/kind or change status (approve candidate = status 'active')."""
    store = _memory_or_503()
    body = await request.json()
    try:
        ok = store.update(mem_id, text=body.get("text"), kind=body.get("kind"), status=body.get("status"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"success": True, "memory": store.get(mem_id)}


@app.delete("/api/memory/{mem_id}")
async def delete_memory(mem_id: int):
    """Hard-delete a memory record."""
    if not _memory_or_503().delete(mem_id):
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"success": True}


@app.post("/api/memory/doctor")
async def run_memory_doctor():
    """Check memory against live HA entities; retires stale records."""
    _memory_or_503()
    if not agent_system:
        raise HTTPException(status_code=503, detail="Agent system not initialized")
    return await agent_system.tools.memory_doctor()
