# HA AI Companion

An AI-powered Home Assistant companion that helps you manage configuration, automate your home, and remember your setup — all through natural language.

---

## What it does

- **Review, edit and create dashboards** — ask the AI to build or modify Lovelace dashboards
- **Surgical config editing** — `patch_config_key` / `patch_config_block` change a single YAML key or section without rewriting the whole file; diffs stay small and readable
- **Safe Node-RED editing** — stage new flow tabs or update a single existing tab; destructive replace-all is not available to the LLM
- **AI Task Entities** — write AI-generated text directly to `input_text` helpers for use in automations, dashboards, and TTS; schedule recurring daily tasks (e.g. morning briefings)
- **Semantic entity search** — for homes with 500+ entities, queries like "bedroom lights" return only the relevant ~40 entities instead of flooding context
- **Tool breadcrumbs** — each agent turn shows a live step-by-step view of every tool call (pending → running → done/error) with expandable args/result panels
- **Tool call error recovery** — failed tool calls are automatically retried up to 2 times with the error fed back to the LLM so it can self-correct
- **Suggest automations** — based on live entity states; live progress stream shows what data is being sent
- **Memory tab** — review, approve, edit and retire what the AI remembers (preferences and facts Home Assistant cannot tell it). Auto-extracted facts wait in **To review**; **Check health** retires memories that mention removed entities
- **Changes tab** — every applied changeset keeps a backup; **Restore** stages a revert you review as a normal diff before it is written
- **Persistent memory** — remembers your preferences and home layout across sessions
- **Node-RED integration** — reads your existing flows to avoid duplicate automations
- **Conversation history** — sessions saved and accessible across page reloads, with a mobile-friendly slide-in sidebar
- **Token + cost tracking** — see cumulative input/output tokens and provider-reported USD cost per session

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
| `openai_api_url` | API endpoint (any OpenAI-compatible URL) | `https://openrouter.ai/api/v1` |
| `openai_api_key` | API key | *(required)* |
| `openai_model` | Main model for every chat/tool-loop iteration | `z-ai/glm-5.3-flash` |
| `fast_model` | Optional model for suggestions, summaries, and memory tasks | main model |
| `log_level` | Logging level | `info` |
| `temperature` | Model temperature (optional) | model default |
| `system_prompt_file` | Custom system prompt file in `/config` (optional) | — |
| `suggestion_prompt` | Extra instructions appended to the system prompt (optional) | — |
| `enable_cache_control` | Enable prompt caching (Anthropic Claude only) | `false` |
| `usage_tracking` | Token tracking: `stream_options`, `usage`, `disabled` | `stream_options` |
| `max_iterations` | Max tool-call rounds per turn (0 = default 25) | `0` |
| `reasoning_effort` | OpenRouter reasoning effort (`none`, `minimal`, `low`, `medium`, `high`, `xhigh`) | blank |
| `nodered_url` | Node-RED base URL (optional) | — |
| `nodered_token` | Node-RED API token (optional, if auth enabled) | — |
| `nodered_flows_file` | Path to Node-RED flows JSON export relative to `/config` (optional) | — |

### AI Provider Examples

#### 💰 Cost — minimize spend

**Google Gemini Flash Lite (best value, free tier available):**
```yaml
openai_api_url: "https://generativelanguage.googleapis.com/v1beta/openai/"
openai_api_key: "your-google-api-key"
openai_model: "gemini-3.5-flash-lite"
usage_tracking: "stream_options"
```

**Anthropic Haiku (fast + cheap Claude):**
```yaml
openai_api_url: "https://api.anthropic.com/v1"
openai_api_key: "sk-ant-..."
openai_model: "claude-haiku-4-5-20251001"
enable_cache_control: true
usage_tracking: "usage"
```

#### ⚖️ Balance — good quality at reasonable cost

**Anthropic Sonnet 5:**
```yaml
openai_api_url: "https://api.anthropic.com/v1"
openai_api_key: "sk-ant-..."
openai_model: "claude-sonnet-5"
enable_cache_control: true
usage_tracking: "usage"
```

**OpenAI GPT-5.6 Terra:**
```yaml
openai_api_url: "https://api.openai.com/v1"
openai_api_key: "sk-proj-..."
openai_model: "gpt-5.6-terra"
usage_tracking: "stream_options"
```

#### 🏆 Quality — best results, cost secondary

**Anthropic Claude Opus 5:**
```yaml
openai_api_url: "https://api.anthropic.com/v1"
openai_api_key: "sk-ant-..."
openai_model: "claude-opus-5"
enable_cache_control: true
usage_tracking: "usage"
```

**Google Gemini Pro:**
```yaml
openai_api_url: "https://generativelanguage.googleapis.com/v1beta/openai/"
openai_api_key: "your-google-api-key"
openai_model: "gemini-3.1-pro-preview"
usage_tracking: "stream_options"
```

#### OpenRouter (100+ models via one key)
```yaml
openai_api_url: "https://openrouter.ai/api/v1"
openai_api_key: "sk-or-v1-..."
openai_model: "anthropic/claude-sonnet-5"
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

## 🧩 Main and fast model

The main model runs the whole chat and tool loop on every iteration. The agent does not switch models mid-turn, which helps keep the provider prompt cache stable. The optional `fast_model` is used only for Suggestions, conversation summaries, and memory extraction and consolidation. It uses the same API URL and key as the main model; blank means use the main model.

`COMPLEXITY_ROUTING=on` is an opt-in exception: simple read-only turns can use the fast model for the whole turn.

### Budget recommendations

OpenRouter prices are USD per 1M input/output tokens as of October 2026. Prices change, so check the model advisor in the Usage tab.

| Use | Model | Input / output per 1M | Notes |
|-----|-------|-----------------------|-------|
| Recommended main | `z-ai/glm-5.3-flash` | $0.15 / $0.50 (cache $0.03) | AA agentic score 50.9; about $0.014 per turn |
| Main alternative | `anthropic/claude-haiku-5.5` | $0.10 / $0.50 | Lower-cost alternative |
| Hard edits | `openai/gpt-6.1-sol` | $1 / $5 | About $0.09 per turn |
| Recommended fast model | `openai/gpt-6-luna` | $0.05 / $0.25 | Side tasks |

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
The Suggestions tab fetches live entity states and existing automations/Node-RED flows before asking the AI, so it never duplicates what you already have. You can select which data sources to include (entity states, automations, scenes, scripts, Node-RED, memory). Progress streams in real time — after generation a collapsible "Prompt sent to AI" panel shows the exact system prompt, model used, and a preview of every context section with its size. Each card includes a type badge (new / improvement) and a copyable YAML block.

### Memory tab
The **Memory** tab shows what the AI remembers, stored in `.ai_agent_memories/memory.db`. The AI keeps only facts it cannot read from Home Assistant: your preferences, who uses which device, unclear device names, and agreed decisions. Only **Active** memories go into chats, and only the ones that match your message (preferences always). Facts found automatically after a chat wait in **To review** until you approve them. **Check health** (also runs daily) retires memories that mention entities that no longer exist. Old markdown memory files are imported once into **To review** and moved to `archive_v1/`.

### Changes tab
The **Changes** tab lists applied changesets (stored in `.ai_agent_changesets/`). **Restore** builds a new changeset that puts the files back as they were before that change. Nothing is written until you approve it. The AI can do the same with the `restore_changeset` tool.

### Conversation sessions
All conversations are saved automatically. Use the sidebar to switch between past sessions or start a new one. "Clear all conversations" extracts memorable facts into **To review** before deleting.

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
