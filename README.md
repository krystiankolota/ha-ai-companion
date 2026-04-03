# HA AI Companion

An AI-powered Home Assistant companion that helps you manage configuration, automate your home, and remember your setup — all through natural language.

---

## What it does

- **Review, edit and create dashboards** — ask the AI to build or modify Lovelace dashboards
- **Manage configuration files** — automations, scripts, YAML configs, devices, entities, areas
- **Suggest automations** — based on live entity states; select which context to include; live progress stream shows what data is being sent
- **Memory tab** — dedicated tab to browse, inspect and delete the AI's persistent memory files
- **Persistent memory** — remembers your preferences and home layout across sessions
- **Node-RED integration** — reads your existing flows to avoid duplicate automations
- **Conversation history** — sessions saved and accessible across page reloads, with a mobile-friendly slide-in sidebar
- **Token + cost tracking** — see cumulative input/output tokens and optional USD cost per session

All changes go through a safe approval workflow: the AI proposes, you see a visual diff, then approve or reject.

---

## Installation

### Supervisor Add-on (recommended)

1. Go to **Settings** → **Add-ons** → **Add-on Store**
2. Click ⋮ → **Repositories** → add `https://github.com/krystiankolota/ha-ai-companion`
3. Find **HA AI Companion** in the store and click **Install**
4. Go to the add-on **Configuration** tab and set your API key
5. Click **Start** — the companion appears in your sidebar as "AI Companion"

### HACS Custom Component

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
| `openai_api_url` | API endpoint (any OpenAI-compatible URL) | Google Gemini |
| `openai_api_key` | API key | *(required)* |
| `openai_model` | Model name | `gemini-2.5-flash` |
| `log_level` | Logging level | `info` |
| `temperature` | Model temperature (optional) | model default |
| `system_prompt_file` | Custom system prompt file in `/config` (optional) | — |
| `suggestion_prompt` | Extra instructions appended to the system prompt (optional) | — |
| `enable_cache_control` | Enable prompt caching (Anthropic Claude only) | `false` |
| `usage_tracking` | Token tracking: `stream_options`, `usage`, `disabled` | `stream_options` |
| `suggestion_model` | Separate model for the suggestion phase (optional) | main model |
| `suggestion_api_url` | API URL for suggestion model (optional) | main URL |
| `suggestion_api_key` | API key for suggestion model (optional) | main key |
| `config_model` | Separate model for config-editing phase (optional) | main model |
| `config_api_url` | API URL for config model (optional) | main URL |
| `config_api_key` | API key for config model (optional) | main key |
| `nodered_url` | Node-RED base URL (optional) | — |
| `nodered_token` | Node-RED API token (optional, if auth enabled) | — |
| `nodered_flows_file` | Path to Node-RED flows JSON export relative to `/config` (optional) | — |
| `input_price_per_1m` | USD per 1M input tokens — enables 💰 cost display in footer | `0.0` |
| `output_price_per_1m` | USD per 1M output tokens | `0.0` |
| `max_tokens` | Global output token limit | — |
| `suggestion_max_tokens` | Token limit for suggestion phase | — |
| `config_max_tokens` | Token limit for config-editing phase | — |
| `max_sessions` | Max conversation sessions to keep | `50` |

### AI Provider Examples

#### 💰 Cost — minimize spend

**Google Gemini Flash (best value, free tier available):**
```yaml
openai_api_url: "https://generativelanguage.googleapis.com/v1beta/openai/"
openai_api_key: "your-google-api-key"
openai_model: "gemini-2.5-flash"
usage_tracking: "stream_options"
input_price_per_1m: 0.075
output_price_per_1m: 0.30
```

**Anthropic Haiku (fast + cheap Claude):**
```yaml
openai_api_url: "https://api.anthropic.com/v1"
openai_api_key: "sk-ant-..."
openai_model: "claude-haiku-4-5-20251001"
enable_cache_control: true
usage_tracking: "usage"
input_price_per_1m: 0.80
output_price_per_1m: 4.00
```

#### ⚖️ Balance — good quality at reasonable cost

**Anthropic Sonnet:**
```yaml
openai_api_url: "https://api.anthropic.com/v1"
openai_api_key: "sk-ant-..."
openai_model: "claude-sonnet-4-5"
enable_cache_control: true
usage_tracking: "usage"
input_price_per_1m: 3.00
output_price_per_1m: 15.00
```

**OpenAI GPT-4o:**
```yaml
openai_api_url: "https://api.openai.com/v1"
openai_api_key: "sk-proj-..."
openai_model: "gpt-4o"
usage_tracking: "stream_options"
input_price_per_1m: 2.50
output_price_per_1m: 10.00
```

#### 🏆 Quality — best results, cost secondary

**Anthropic Claude Sonnet 4.6:**
```yaml
openai_api_url: "https://api.anthropic.com/v1"
openai_api_key: "sk-ant-..."
openai_model: "claude-sonnet-4-6"
enable_cache_control: true
usage_tracking: "usage"
input_price_per_1m: 3.00
output_price_per_1m: 15.00
```

**Google Gemini Pro:**
```yaml
openai_api_url: "https://generativelanguage.googleapis.com/v1beta/openai/"
openai_api_key: "your-google-api-key"
openai_model: "gemini-2.5-pro"
usage_tracking: "stream_options"
```

#### OpenRouter (100+ models via one key)
```yaml
openai_api_url: "https://openrouter.ai/api/v1"
openai_api_key: "sk-or-v1-..."
openai_model: "anthropic/claude-sonnet-4-5"
usage_tracking: "usage"
```

#### Local Ollama (zero cost, privacy)
```yaml
openai_api_url: "http://ollama:11434/v1"
openai_api_key: "ollama"
openai_model: "llama3.2"
usage_tracking: "disabled"
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
The Suggestions tab fetches live entity states and existing automations/Node-RED flows before asking the AI, so it never duplicates what you already have. You can select which data sources to include (entity states, automations, scenes, scripts, Node-RED, memory). Progress streams in real time so you can see exactly what context is being sent. Each card includes a type badge (new / improvement) and a copyable YAML block.

### Memory tab
The **Memory** tab (next to Chat and Suggestions) lists all AI memory files stored in `.ai_agent_memories/`. You can expand any file to read its content and delete stale or incorrect entries without having to SSH into the server.

### Conversation sessions
All conversations are saved automatically. Use the sidebar to switch between past sessions or start a new one. "Clear all conversations" extracts memorable facts to memory before deleting.

---

## Example prompts

```
Show me my Lovelace dashboard
Create a new kitchen dashboard with a light controls card
Enable debug logging for the MQTT integration
Rename "Office Button" device to "Desk Button"
Suggest automations based on my current devices
What automations do I have for the bedroom lights?
Analyze recent Home Assistant errors
```

---

## Acknowledgements

This project builds on ideas and code from:

- [yinzara/ha-config-ai-agent](https://github.com/yinzara/ha-config-ai-agent) — original HA config AI agent
- [ITSpecialist111/ai_automation_suggester](https://github.com/ITSpecialist111/ai_automation_suggester) — automation suggestion concept

---

## License

MIT
