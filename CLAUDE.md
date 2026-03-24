# CLAUDE.md — HA AI Companion

> **This file is auto-loaded every conversation. Keep it under 200 lines.**
> Deep knowledge lives in `docs/knowledge/` — read those files on demand, not upfront.

---

## Project

**HA AI Companion** — AI assistant for Home Assistant via natural language.
Two deployment modes: HACS custom component (`custom_components/ha_ai_companion/`) and Supervisor add-on (`ha-ai-companion/`).
Version: `0.2.0` | Domain: `ha_ai_companion` | Stack: FastAPI + Uvicorn, OpenAI-compatible client, Vanilla JS

---

## Key File Map

| File | Role |
|------|------|
| `custom_components/ha_ai_companion/__init__.py` | Starts FastAPI server, sets env vars, registers HA services + panel |
| `custom_components/ha_ai_companion/const.py` | All `CONF_*` constants |
| `custom_components/ha_ai_companion/config_flow.py` | HA config + options flow |
| `src/main.py` | FastAPI app + all endpoints |
| `src/agents/agent_system.py` | Streaming agent loop, dual-model switching, `generate_suggestions()` |
| `src/agents/tools.py` | All tool functions |
| `src/config/manager.py` | YAML read/write, backups, HA validation |
| `src/ha/ha_websocket.py` | HA WebSocket client (devices, entities, areas, Lovelace) |
| `src/memory/manager.py` | MemoryManager — markdown files in `.ai_agent_memories/` |
| `src/conversations/manager.py` | ConversationManager — JSON sessions in `.ai_agent_sessions/` |
| `templates/index.html` | Chat + Suggestions tab UI |
| `static/js/app.js` | Session sidebar + chat logic |
| `static/js/suggestions.js` | Suggestions tab |

---

## Environment Variables (set by `__init__.py`)

`OPENAI_API_KEY`, `OPENAI_API_URL`, `OPENAI_MODEL`, `SUGGESTION_MODEL`, `CONFIG_MODEL`,
`LOG_LEVEL`, `TEMPERATURE`, `SYSTEM_PROMPT_FILE`, `SUGGESTION_PROMPT`,
`ENABLE_CACHE_CONTROL`, `USAGE_TRACKING`,
`NODERED_URL`, `NODERED_TOKEN`, `NODERED_FLOWS_FILE`,
`MEMORY_DIR`, `SESSIONS_DIR`, `SUPERVISOR_TOKEN`

---

## API Endpoints

`WS /ws/chat` · `POST /api/approve` · `GET|PUT|DELETE /api/sessions/{id}` · `GET /api/suggestions` · `POST /api/suggestions/generate`

---

## Critical Gotchas

> Full details in `docs/knowledge/` — check there before touching related code.

- **Haiku streaming bug** — guard `if not chunk.choices: continue` before `chunk.choices[0]`
- **stream_options retry** — catch API rejection and retry without `stream_options`/`usage` params
- **Dual-model switching** — `suggestion_model` until tool results appear in messages, then `config_model`
- **WebSocket proxy** — HA Ingress buffers SSE; all streaming goes through `/ws/chat` WebSocket proxy in `__init__.py`
- **chdir required** — FastAPI must run from component dir to resolve `static/` and `templates/`
- **set_hass_instance()** — must be called after server starts so `config_manager.hass` is set for HA validation

---

## Knowledge Library (`docs/knowledge/`)

Read `docs/knowledge/INDEX.md` to find relevant files. **Do not load all files upfront.**
Load a knowledge file only when working on its topic area.

---

## Self-Maintenance Rules

### CLAUDE.md — stay lean
- **Must stay under 200 lines.** If it grows beyond that, move details to `docs/knowledge/`.
- Contains: project name/version, key file map, env var list, endpoint list, critical gotchas, pointers to knowledge library.
- Does NOT contain: workflow examples, detailed feature descriptions, full API docs, code snippets.
- Update the version number and file map when architecture changes.

### Knowledge library (`docs/knowledge/`)
- One file per topic (e.g. `streaming_quirks.md`, `lovelace_management.md`).
- Each file: < 80 lines, factual, no prose — bullet points and code snippets only.
- Update a knowledge file when you discover a bug, workaround, or non-obvious pattern.
- Update `docs/knowledge/INDEX.md` whenever you add or rename a file.

### Auto-memory (`~/.claude/projects/.../memory/`)
- Stores: user preferences, home layout facts, project-level decisions.
- Does NOT store: things derivable from reading the code, ephemeral task state.
- Update proactively at session end — don't wait to be asked.

### Session logs
- Session JSON files in `/config/.ai_agent_sessions/` are runtime data, not a knowledge source.
- Don't reference them in CLAUDE.md or knowledge files.

---

## Session End Protocol

When the user writes **"session end"**, perform this ritual before responding:

1. **Architecture changed?** → Update the file map and version in this file if needed.
2. **New gotcha or workaround discovered?** → Add to the relevant `docs/knowledge/` file (create if needed), update `INDEX.md`.
3. **Persistent user/project fact learned?** → Save to auto-memory (`~/.claude/projects/.../memory/`).
4. **CLAUDE.md over 200 lines?** → Move the bloat to a knowledge file, replace with a one-line pointer.
5. Respond with a brief summary of what was updated.

---

**Last updated:** 2026-03-24
