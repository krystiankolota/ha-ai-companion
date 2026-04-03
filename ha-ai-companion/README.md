# HA AI Companion — Home Assistant Add-on

AI-powered Home Assistant companion running as a Supervisor add-on. Manage configuration, dashboards, automations, devices, and more through natural language — with a built-in approval workflow.

---

## Installation

1. **Settings** → Add-ons → Add-on Store → ⋮ → Repositories
2. Add this repository URL
3. Find **HA AI Companion** and click Install
4. Go to the add-on **Configuration** tab and set at minimum your API key
5. Click **Start** — the UI appears in your sidebar

---

## Configuration

```yaml
openai_api_url: "https://generativelanguage.googleapis.com/v1beta/openai/"
openai_api_key: "your-api-key"
openai_model: "gemini-2.5-flash"
log_level: "info"
system_prompt_file: ""          # Optional: path relative to /config
temperature: ""                 # Optional: model temperature
enable_cache_control: false     # Prompt caching (Anthropic Claude only)
usage_tracking: "stream_options"
input_price_per_1m: 0.0         # Optional: USD/1M input tokens (enables 💰 cost display)
output_price_per_1m: 0.0        # Optional: USD/1M output tokens
```

### AI provider examples

#### 💰 Cost — minimize spend

**Google Gemini Flash (best value, free tier available):**
```yaml
openai_api_url: "https://generativelanguage.googleapis.com/v1beta/openai/"
openai_api_key: "your-google-key"
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

**Anthropic Claude Sonnet 4.6 (newest, near-Opus quality):**
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
openai_api_key: "your-google-key"
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
openai_api_url: "http://homeassistant:11434/v1"
openai_api_key: "ollama"
openai_model: "llama3.2"
usage_tracking: "disabled"
```

### Node-RED integration (optional)

If you use Node-RED, the companion can read your flows before suggesting automations to avoid duplicating existing logic:

```yaml
nodered_url: "http://homeassistant:1880"
nodered_token: ""   # Only needed if Node-RED auth is enabled
```

### Custom system prompt (optional)

1. Create a text file in `/config`, e.g. `ai_prompt.txt`
2. Write your instructions
3. Set `system_prompt_file: "ai_prompt.txt"`

The `suggestion_prompt` option adds extra text to the default prompt without replacing it — useful for home-specific context like "My home has 3 floors and a garage".

---

## Features

| Feature | Description |
|---------|-------------|
| Natural language | No YAML expertise needed |
| Approval workflow | Review visual diffs before any change is applied |
| Automatic backups | Backup created before every change |
| HA validation | Config validated by HA before applying; auto-rollback on failure |
| Dashboard management | List, read, edit, create and delete Lovelace dashboards |
| Virtual files | Lovelace dashboards, devices, entities and areas treated as editable files |
| Automation safety guard | Rejects changes that would delete >20% of existing automations |
| Automation suggestions | Based on live entity states; select context sources; live progress stream; avoids duplicating existing HA or Node-RED flows |
| Memory viewer | Dedicated Memory tab — browse, inspect and delete AI memory files |
| Persistent memory | Remembers preferences and home layout across sessions; consolidates after each chat |
| Home topology injection | Area→entity map pre-loaded into every prompt, reducing unnecessary tool calls |
| Conversation history | Sessions saved to disk, visible in the sidebar (mobile: slide-in drawer) |
| Token + cost display | Cumulative token counter and optional USD cost in the UI footer |
| Language detection | Responds in the same language as your HA entity/automation names |
| Mobile-optimised UI | Slide-in sidebar, full-width input, collapsible tool cards |

---

## Example prompts

```
Show me my current Lovelace dashboard
Create a new kitchen dashboard with light controls
Enable debug logging for the MQTT integration
Rename "Office Button" device to "Desk Button"
Suggest automations for my bedroom lights
What automations do I already have for the porch?
Add a motion sensor to the Living Room area
```

---

## Data stored

| Location | Contents |
|----------|----------|
| `/config/.ai_agent_sessions/` | Conversation sessions (JSON, max 50) |
| `/config/.ai_agent_memories/` | Persistent memory files (Markdown) |
| `/backup/` | Configuration backups (max 10 per file) |

---

## License

MIT
