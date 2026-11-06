# HA AI Companion

HA AI Companion is an AI assistant for Home Assistant. It can inspect your configuration, suggest automations, and propose changes to YAML, dashboards, and Node-RED flows.

Review each proposed change before approving it. Some actions, such as writing to an `input_text` helper or scheduling a task, run without an approval card.

## Install

### Home Assistant add-on

1. Open Settings, Add-ons, and the Add-on Store.
2. Open the menu and select Repositories.
3. Add `https://github.com/krystiankolota/ha-ai-companion`.
4. Install HA AI Companion.
5. In the add-on configuration, enter your provider API key.
6. Start the add-on and open AI Companion from the sidebar.

### HACS integration

1. In HACS, open Integrations and select Custom repositories.
2. Add this repository as an Integration.
3. Download HA AI Companion and restart Home Assistant.
4. In Settings, Devices & Services, add the HA AI Companion integration.

For manual installation, copy `custom_components/ha_ai_companion` to the Home Assistant `custom_components` directory. Restart Home Assistant, then add the integration in Settings, Devices & Services.

## Configure a model

Use a provider that supports OpenAI-compatible chat completions and tool calls. The add-on configuration starts with these settings:

```yaml
openai_api_url: "https://openrouter.ai/api/v1"
openai_api_key: "your-api-key"
openai_model: "z-ai/glm-5.3-flash"
fast_model: ""
```

In the HACS integration, enter the API URL, API key, and model name in the integration settings. The optional fast model uses the same provider and key for suggestions, summaries, and memory tasks. If it is blank, the main model handles those tasks.

## Use the companion

- Ask questions about your configuration or request a change.
- Review the diff on the approval card before applying a proposal.
- Attach up to three images to a message. The file picker selects one image at a time. Your model must support images.
- Use Suggestions to generate automation ideas from selected Home Assistant data.
- Use Memory to review facts saved by the assistant.
- Use Changes to stage a restore of an applied changeset.
- Use the Usage tab to inspect available token and cost data.

The YAML pre-check can temporarily write proposed files and restore them before you approve a change. After approval, a validation or reload failure does not guarantee rollback. Check the result and keep separate Home Assistant backups.

See the [user and development guide](ha-ai-companion/DOCS.md) for configuration, safety details, data storage, and troubleshooting.

## More information

- [Add-on guide](ha-ai-companion/README.md)
- [HACS integration guide](custom_components/ha_ai_companion/README.md)
- [Frontend development](frontend/README.md)
- [Report a problem](https://github.com/krystiankolota/ha-ai-companion/issues)

## Acknowledgements

This project builds on ideas and code from:

- [yinzara/ha-config-ai-agent](https://github.com/yinzara/ha-config-ai-agent)
- [ITSpecialist111/ai_automation_suggester](https://github.com/ITSpecialist111/ai_automation_suggester)

## License

MIT
