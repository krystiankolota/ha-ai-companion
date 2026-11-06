# HA AI Companion guide

See the [README](../README.md) for add-on and HACS installation.

## Add-on configuration

Set these options in the add-on Configuration tab. The HACS integration presents its settings in the Home Assistant integration flow.

| Option | Default | Purpose |
|--------|---------|---------|
| `openai_api_url` | `https://openrouter.ai/api/v1` | OpenAI-compatible API URL |
| `openai_api_key` | empty | Provider API key |
| `openai_model` | `z-ai/glm-5.3-flash` | Main chat and tool model |
| `fast_model` | empty | Optional model for suggestions, summaries, and memory tasks |
| `log_level` | `info` | `debug`, `info`, `warning`, or `error` |
| `temperature` | empty | Optional model temperature |
| `reasoning_effort` | empty | OpenRouter reasoning effort |
| `enable_cache_control` | `false` | Explicitly enable prompt cache markers |
| `max_iterations` | `0` | Tool-call rounds per turn. `0` uses the default of 25 |
| `usage_tracking` | `stream_options` | `stream_options`, `usage`, or `disabled` |
| `system_prompt_file` | empty | Custom prompt file relative to the Home Assistant configuration directory |
| `suggestion_prompt` | empty | Extra instructions for automation suggestions |
| `nodered_url` | empty | Node-RED API URL |
| `nodered_token` | empty | Node-RED API token, if required |
| `nodered_flows_file` | empty | Path to an exported Node-RED flows JSON file |
| `artificial_analysis_api_key` | empty | Optional key for additional model advisor scores |

Use a provider that supports OpenAI-compatible chat completions and tool calls. The fast model uses the main model's API URL and key. If `fast_model` is empty, the main model handles those tasks.

By default, the main model handles each chat turn. If `COMPLEXITY_ROUTING=on`, short read-only turns can use the fast model for the whole turn.

Prompt cache markers are also enabled automatically for OpenRouter and Claude models. The `DISABLE_CACHE_AUTO` environment variable disables that automatic choice. An explicit `enable_cache_control: true` still enables the markers.

The Usage tab shows token and cost data when the provider reports it. Prices and model availability change, so check the provider before selecting a model.

### Provider URLs

| Provider | API URL | Setup reference |
|----------|---------|-----------------|
| OpenRouter | `https://openrouter.ai/api/v1` | [OpenRouter documentation](https://openrouter.ai/docs) |
| OpenAI | `https://api.openai.com/v1` | [OpenAI API reference](https://platform.openai.com/docs/api-reference) |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai/` | [Gemini OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai) |
| Anthropic | `https://api.anthropic.com/v1/` | [Anthropic OpenAI compatibility](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk) |
| Local Ollama | `http://<ollama-host>:11434/v1` | [Ollama OpenAI compatibility](https://github.com/ollama/ollama/blob/main/docs/api/openai-compatibility.mdx) |

For local Ollama, use a host name that Home Assistant can reach. The client requires a nonempty API key, but Ollama ignores it. You can enter `ollama`.

Anthropic describes its OpenAI-compatible endpoint as mainly intended for testing. That endpoint does not support prompt caching. Sending cache markers does not guarantee a cache hit.

### Custom system prompt

Create a text file in the Home Assistant configuration directory and set `system_prompt_file` to its relative path. Restart the add-on or integration after changing the setting.

A custom prompt replaces the built-in prompt. It can weaken the instructions that guide safe behavior, so review and test it carefully. If the application cannot load the file, it uses the built-in prompt.

## Chat and images

Ask questions about your setup or request changes in the chat. The sidebar lists saved conversations.

You can attach up to three images to one message. The file picker selects one image at a time. You can also paste or drop images into the chat. The browser resizes them before sending. The model must support images.

The assistant uses image data only for the message that includes it. Conversation history records that a message had images, but does not store the image data.

## Changes and approval

1. Ask the assistant to propose a change.
2. Open the diff on the approval card and review it.
3. Approve or reject the proposal.
4. Check the result for failed files, validation errors, reload errors, or a required Home Assistant restart.

For YAML proposals, the pre-check can temporarily write files before approval and then restore them. It runs the Home Assistant configuration check when available. This check is not a substitute for a backup.

After approval, the application can apply only part of a change. The application does not guarantee a rollback if validation or reload fails. Existing configuration files have backups, but new files and virtual resources do not always have equivalent backups. Keep separate Home Assistant backups.

If a disk file changed after the proposal, the application refuses to overwrite the changed version. Ask the assistant to read the file and propose the change again.

The Changes tab lists applied changesets. Restore creates a new proposal that you must review and approve.

Not every action uses an approval card. Writing to an `input_text` helper and creating a scheduled AI task run immediately.

## Memory and conversations

The Memory tab lets you review, approve, edit, and retire stored facts. Automatically extracted facts wait for review. The assistant can use active memories in later chats.

The application stores memory in `.ai_agent_memories/memory.db` in the Home Assistant configuration directory. It stores conversations in `.ai_agent_sessions`. By default, it keeps the newest 50.

## Suggestions and Node-RED

The Suggestions tab uses the data sources you select to propose automation ideas. Review each suggestion and its YAML before using it.

Set `nodered_url` to use the Node-RED API. Set `nodered_token` if the API requires one. The `nodered_flows_file` option can provide an exported JSON file when the API is unavailable. The application reports HTTP 401 as an authentication error instead of using the file fallback.

The assistant can propose a new flow tab or an update to an existing tab. Updating a tab replaces that whole tab, so review the proposal carefully.

## Text helpers and scheduled tasks

The assistant can write text to an existing `input_text` helper without an approval card. It limits each value to 255 characters.

It can also create a daily task that runs a prompt and writes the result to an `input_text` helper. The application stores tasks in `.ai_agent_tasks` in the Home Assistant configuration directory.

The backend exposes these task endpoints:

- `GET /api/scheduled-tasks` lists tasks.
- `POST /api/scheduled-tasks/{id}/run` runs a task.
- `DELETE /api/scheduled-tasks/{id}` deletes a task.

## Data and backups

| Location | Contents |
|----------|----------|
| `.ai_agent_sessions` | Conversation files |
| `.ai_agent_memories/memory.db` | Stored memories |
| `.ai_agent_tasks` | Scheduled tasks |
| `.ai_agent_changesets` | Applied changeset history |
| `.ai_agent_usage` | Usage records |
| `/backup/config-agent` | Add-on configuration backups inside the add-on |
| `.ai_agent_backups` | HACS configuration backups |

Paths beginning with a dot are relative to the Home Assistant configuration directory. The application keeps up to 10 backups per existing configuration file by default. Keep a separate Home Assistant backup.

## Troubleshooting

- If the agent does not start, check the API key, URL, model, and add-on or Home Assistant logs.
- If a model cannot use tools or images, choose a model that supports the requested capability.
- If a proposal reports a conflict, ask the assistant to read the file again before proposing a new change.
- If validation or reload fails, inspect the result and your configuration. Applied files can remain changed.
- If Node-RED returns HTTP 401, check its API authentication. A Home Assistant token is not accepted by the Node-RED add-on API.
- If a new YAML platform does not appear after a reload, try restarting Home Assistant.

## Development

From the `ha-ai-companion` directory, create a Python environment:

```bash
python -m venv .venv
```

Activate the environment. On Linux or macOS, run `source .venv/bin/activate`. On Windows PowerShell, run `.venv\Scripts\Activate.ps1`. Then install the dependencies and run the tests:

```bash
python -m pip install -r requirements.txt -r requirements-test.txt
python -m pytest tests/
```

To run the backend locally, set `HA_CONFIG_DIR`, `BACKUP_DIR`, `OPENAI_API_KEY`, `OPENAI_API_URL`, and `OPENAI_MODEL`. Then run `uvicorn src.main:app --reload --port 8099` from the same directory. Home Assistant-backed tools need a working Home Assistant connection. Do not commit API keys.

From the `frontend` directory, run `npm ci` and `npm run build`. The build also syncs shared files to the HACS component. See the [frontend guide](../frontend/README.md) for development-server limits.
