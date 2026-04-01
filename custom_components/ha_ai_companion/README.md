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
| `suggestion_model` | Cheaper/faster model for suggestion phase (optional) | main model |
| `suggestion_api_url` | API URL override for suggestion model (optional) | main URL |
| `suggestion_api_key` | API key override for suggestion model (optional) | main key |
| `config_model` | Stronger model for config-editing phase (optional) | main model |
| `config_api_url` | API URL override for config model (optional) | main URL |
| `config_api_key` | API key override for config model (optional) | main key |
| `nodered_url` | Node-RED base URL (e.g. `http://homeassistant:1880`) | — |
| `nodered_token` | Node-RED API token (if auth is enabled) | — |
| `nodered_flows_file` | Path to Node-RED flows JSON export relative to `/config` | — |
| `input_price_per_1m` | USD per 1M input tokens — enables cost display | `0.0` |
| `output_price_per_1m` | USD per 1M output tokens | `0.0` |
| `max_tokens` | Global output token limit | — |
| `max_sessions` | Max conversation sessions to keep | `50` |

## What the companion can do

- **Read and edit** any YAML configuration file
- **Manage dashboards** — list, read, edit, create and delete Lovelace dashboards
- **Device / entity / area management** — rename, move to areas, update labels
- **Suggest automations** — based on live entity states and existing automations
- **Avoid duplicates** — reads Node-RED flows before suggesting new ones
- **Persistent memory** — remembers your preferences and home layout across sessions
- **Log viewer** — fetch and AI-analyze `home-assistant.log` from the companion UI
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
