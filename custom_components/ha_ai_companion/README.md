# HA AI Companion — HACS Custom Component

AI-powered Home Assistant companion for managing configuration, dashboards, automations, and more through natural language.

## Installation

1. Open **HACS** → Integrations → ⋮ → Custom repositories
2. Add this repository URL, category: **Integration**
3. Search for "HA AI Companion" → Download
4. Restart Home Assistant
5. **Settings** → Devices & Services → Add Integration → **HA AI Companion**

## Configuration options

| Option | Description | Default |
|--------|-------------|---------|
| `openai_api_url` | API endpoint (any OpenAI-compatible) | Google Gemini |
| `openai_api_key` | API key | *(required)* |
| `openai_model` | Model name | `gemini-2.5-flash` |
| `log_level` | Logging verbosity | `info` |
| `temperature` | Model temperature (optional) | model default |
| `system_prompt_file` | Custom system prompt file path relative to `/config` | — |
| `suggestion_prompt` | Extra instructions added to the system prompt | — |
| `enable_cache_control` | Prompt caching (Anthropic Claude) | `false` |
| `usage_tracking` | Token tracking: `stream_options`, `usage`, `disabled` | `stream_options` |
| `max_iterations` | Max tool-call rounds per turn (0 = default 25) | `0` |
| `research_model` | Research layer (cheaper) — reading/exploring & suggestions (optional) | main model |
| `research_api_url` | API URL override for research layer (optional) | main URL |
| `research_api_key` | API key override for research layer (optional) | main key |
| `reasoning_model` | Reasoning layer (stronger) — planning & writing changes (optional) | main model |
| `reasoning_api_url` | API URL override for reasoning layer (optional) | main URL |
| `reasoning_api_key` | API key override for reasoning layer (optional) | main key |
| `nodered_url` | Node-RED base URL (e.g. `http://homeassistant:1880`) | — |
| `nodered_token` | Node-RED API token (if auth is enabled) | — |
| `nodered_flows_file` | Path to Node-RED flows JSON export relative to `/config` | — |
| `input_price_per_1m` | USD per 1M input tokens — enables cost display | `0.0` |
| `output_price_per_1m` | USD per 1M output tokens | `0.0` |

## What the companion can do

- **Read and edit** any YAML configuration file
- **Manage dashboards** — list, read, edit, create and delete Lovelace dashboards
- **Device / entity / area management** — rename, move to areas, update labels
- **Suggest automations** — based on live entity states; select context sources; live progress stream; never duplicates existing ones
- **Memory tab** — dedicated tab to browse, inspect and delete AI memory files
- **Avoid duplicates** — reads Node-RED flows before suggesting new ones
- **Persistent memory** — remembers your preferences and home layout across sessions
- **Conversation history** — sessions saved server-side, accessible across page reloads with mobile-friendly sidebar

## Services

### `ha_ai_companion.chat`

Send a message to the AI agent.

```yaml
service: ha_ai_companion.chat
data:
  message: "Create a new kitchen dashboard"
  session_id: ""  # optional, leave empty for current session
```

### `ha_ai_companion.approve`

Approve or reject a proposed change.

```yaml
service: ha_ai_companion.approve
data:
  change_id: "abc123"
  approved: true
  validate: true
```

## Data stored

| Location | Contents |
|----------|----------|
| `/config/.ai_agent_sessions/` | Conversation session JSON files (max 50) |
| `/config/.ai_agent_memories/` | Persistent memory markdown files |

## License

MIT
