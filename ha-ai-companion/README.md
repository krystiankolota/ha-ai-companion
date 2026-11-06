# HA AI Companion add-on

Use natural language to inspect your Home Assistant configuration and propose changes.

## Install

1. Open Settings, Add-ons, and the Add-on Store.
2. Open the menu and select Repositories.
3. Add `https://github.com/krystiankolota/ha-ai-companion`.
4. Install HA AI Companion.
5. Enter your provider API key in the add-on configuration.
6. Start the add-on and open AI Companion from the sidebar.

The default provider is OpenRouter. You can use another OpenAI-compatible provider that supports tool calls. The optional `fast_model` uses the same API URL and key as the main model.

## Safety

Review every proposed diff. A YAML pre-check can temporarily write and restore files before approval. After approval, a validation or reload error does not guarantee rollback. Keep a separate Home Assistant backup and check the result.

Some actions run without an approval card. These include writing to an `input_text` helper and scheduling an AI task.

See the [full guide](https://github.com/krystiankolota/ha-ai-companion/blob/main/ha-ai-companion/DOCS.md) for configuration options, data locations, and troubleshooting.

## License

MIT
