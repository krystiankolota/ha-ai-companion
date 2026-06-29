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
- **Memory tab** — browse, inspect and delete the AI's persistent memory files; `consolidate_memories` tool reviews all files and produces a merge plan before taking action
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
| `max_iterations` | Max tool-call rounds per turn (0 = default 25) | `0` |
| `research_model` | Research layer (cheaper) — reading/exploring & suggestions (optional) | main model |
| `research_api_url` | API URL for research layer (optional) | main URL |
| `research_api_key` | API key for research layer (optional) | main key |
| `reasoning_model` | Reasoning layer (stronger) — planning & writing changes (optional) | main model |
| `reasoning_api_url` | API URL for reasoning layer (optional) | main URL |
| `reasoning_api_key` | API key for reasoning layer (optional) | main key |
| `nodered_url` | Node-RED base URL (optional) | — |
| `nodered_token` | Node-RED API token (optional, if auth enabled) | — |
| `nodered_flows_file` | Path to Node-RED flows JSON export relative to `/config` (optional) | — |
| `input_price_per_1m` | USD per 1M input tokens — enables 💰 cost display in footer | `0.0` |
| `output_price_per_1m` | USD per 1M output tokens | `0.0` |

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

## 🧩 Model layers — which model for which layer

The agent runs two layers. Pointing each at a different model is the single biggest cost/quality lever:

- **Research layer** (`research_model`) — the high-volume work: reading and exploring your config, answering simple read-only questions, and generating Suggestions. Runs *before* any change is written. Optimise for the **best output quality per dollar** — a strong mid-tier model (not the rock-bottom cheapest), since suggestion quality is what you actually read.
- **Reasoning layer** (`reasoning_model`) — the correctness-critical work: planning and writing config / dashboard / Node-RED changes once context is gathered. Optimise for top **coding + reasoning quality**.

Leave a layer blank to fall back to `openai_model`. A layer activates its own provider only when all three of its fields (model + API URL + API key) are set.

### Picking models from a benchmark

Rankings shift monthly — combine a live quality leaderboard like **[livebench.ai](https://livebench.ai/)** or **[llm-stats.com](https://llm-stats.com/)** (quality + price + context side by side) with the live slugs/pricing on **[openrouter.ai/models](https://openrouter.ai/models)**. Which livebench columns matter depends on the layer:

| Layer | livebench columns to weigh | Then pick the… |
|-------|----------------------------|----------------|
| **Research** (cheap) | *Instruction Following*, *Language*, *Data Analysis* — plus the model's $/1M price | cheapest model that still scores well (price/perf sweet spot) |
| **Reasoning** (strong) | *Coding*, *Reasoning* (YAML/automation logic lives here) | highest-scoring model your budget allows |

### Good picks via OpenRouter (one key, both layers)

OpenRouter slugs + approximate $ per 1M (input / output) pulled from its live catalog — **verify current prices on [openrouter.ai/models](https://openrouter.ai/models)**, they change often:

| Tier | Research layer (high-volume, quality-per-$) | Reasoning layer (correctness-critical) |
|------|---------------------------------------------|----------------------------------------|
| **💰 Cheapest** | `deepseek/deepseek-v3.2` ($0.23 / $0.34) | `deepseek/deepseek-r1-0528` ($0.50 / $2.15) |
| **⚖️ Balanced** *(recommended)* | `google/gemini-3.5-flash` ($1.50 / $9.00)<br>`openai/gpt-5-mini` ($0.25 / $2.00) | `google/gemini-2.5-pro` ($1.25 / $10)<br>`openai/gpt-5.1` ($1.25 / $10) |
| **🏆 Highest** | `anthropic/claude-haiku-4.5` ($1.00 / $5.00) | `anthropic/claude-opus-4.8` ($5 / $25)<br>`anthropic/claude-sonnet-4.6` ($3 / $15) |

The research layer carries most of the *token volume* (every read/explore step) **and** produces the suggestions you read — so pick a strong mid-tier model there (e.g. `gemini-3.5-flash`), not the absolute cheapest. The reasoning layer carries most of the *cost per token*, so spend your top budget on it.

### Example: layered setup via OpenRouter

```yaml
openai_api_url: "https://openrouter.ai/api/v1"
openai_api_key: "sk-or-v1-..."
openai_model: "anthropic/claude-sonnet-4.6"   # fallback for anything not layered
usage_tracking: "usage"

# Research layer — high-volume reading/exploring + suggestions (quality-per-$)
research_model: "google/gemini-3.5-flash"
research_api_url: "https://openrouter.ai/api/v1"
research_api_key: "sk-or-v1-..."

# Reasoning layer — strong, planning & writing changes
reasoning_model: "anthropic/claude-opus-4.8"
reasoning_api_url: "https://openrouter.ai/api/v1"
reasoning_api_key: "sk-or-v1-..."
```

> Tip: set `input_price_per_1m` / `output_price_per_1m` to your **reasoning** model's price to see real cost in the footer — it dominates spend.

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
