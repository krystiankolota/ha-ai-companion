# HA AI Companion

An AI-powered Home Assistant companion that helps you manage configuration, automate your home, and remember your setup — all through natural language.

---

## What it does

- **Review, edit and create dashboards** — ask the AI to build or modify Lovelace dashboards
- **Manage configuration files** — automations, scripts, YAML configs, devices, entities, areas
- **Suggest automations** — based on your actual live entity states
- **Persistent memory** — remembers your preferences and setup across sessions
- **Node-RED integration** — reads your existing flows to avoid duplicate automations
- **Conversation history** — sessions saved and accessible across page reloads

All changes go through a safe approval workflow: the AI proposes, you see a visual diff, then approve or reject.

---

## Installation

### HACS Custom Component (recommended)

1. Open HACS → Integrations → ⋮ → Custom repositories
2. Add this repository URL, category: Integration
3. Search for "HA AI Companion" and install
4. Restart Home Assistant
5. Settings → Devices & Services → Add Integration → "HA AI Companion"

### Manual

Copy the `custom_components/ha_ai_companion/` folder into your Home Assistant `custom_components/` directory and restart.

---

## Configuration

| Option | Description | Default |
|--------|-------------|---------|
| `openai_api_url` | API endpoint (any OpenAI-compatible URL) | `https://api.openai.com/v1` |
| `openai_api_key` | API key | *(required)* |
| `openai_model` | Model name | `gpt-4o` |
| `log_level` | Logging level | `info` |
| `temperature` | Model temperature (optional) | model default |
| `system_prompt_file` | Custom system prompt file in `/config` (optional) | — |
| `suggestion_prompt` | Extra instructions appended to the system prompt (optional) | — |
| `enable_cache_control` | Enable prompt caching (Anthropic Claude) | `false` |
| `usage_tracking` | Token tracking method: `stream_options`, `usage`, `disabled` | `stream_options` |
| `nodered_url` | Node-RED base URL (optional, e.g. `http://homeassistant:1880`) | — |
| `nodered_token` | Node-RED API token (optional, if auth enabled) | — |

### AI Provider Examples

**Anthropic Claude (recommended):**
```yaml
openai_api_url: "https://api.anthropic.com/v1"
openai_api_key: "sk-ant-..."
openai_model: "claude-haiku-4-5"
enable_cache_control: true
usage_tracking: "usage"
```

**Google Gemini:**
```yaml
openai_api_url: "https://generativelanguage.googleapis.com/v1beta/openai/"
openai_api_key: "your-google-api-key"
openai_model: "gemini-2.5-flash"
```

**OpenAI:**
```yaml
openai_api_url: "https://api.openai.com/v1"
openai_api_key: "sk-proj-..."
openai_model: "gpt-4o"
```

**OpenRouter (100+ models):**
```yaml
openai_api_url: "https://openrouter.ai/api/v1"
openai_api_key: "sk-or-v1-..."
openai_model: "anthropic/claude-3.5-sonnet"
usage_tracking: "usage"
```

**Local Ollama:**
```yaml
openai_api_url: "http://ollama:11434/v1"
openai_api_key: "ollama"
openai_model: "llama3.2"
```

---

## Features in detail

### Safe change workflow
1. You ask for a change in plain English
2. The AI reads the current configuration
3. Proposes changes — shown as a visual diff
4. You approve or reject (no change is applied without your confirmation)
5. On approval: backup created → change written → HA validates → rollback if validation fails

### Dashboard management
- List all Lovelace dashboards
- Read and edit existing dashboards (default and custom)
- Create new dashboards with a title and icon
- Delete dashboards

### Memory system
The AI remembers facts across sessions using categorised memory files:
- `preference_` — your stated preferences
- `device_` — device names, locations
- `identity_` — home structure, room layout
- `baseline_` — normal sensor ranges
- `pattern_` — recurring routines
- `correction_` — corrections to previous facts

### Automation suggestions
Ask "suggest automations for my home" — the AI fetches live entity states and existing automations/Node-RED flows before suggesting, so it never duplicates what you already have.

### Conversation sessions
All conversations are saved automatically. Use the sidebar to switch between past sessions or start a new one.

---

## Example prompts

```
Show me my Lovelace dashboard
Create a new kitchen dashboard with a light controls card
Enable debug logging for the MQTT integration
Rename "Office Button" device to "Desk Button"
Suggest automations based on my current devices
What automations do I have for the bedroom lights?
```

---

## License

MIT
