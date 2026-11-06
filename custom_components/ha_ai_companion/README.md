# HA AI Companion HACS integration

Use natural language to inspect your Home Assistant configuration and propose changes.

## Install

1. In HACS, open Integrations and select Custom repositories.
2. Add `https://github.com/krystiankolota/ha-ai-companion` as an Integration.
3. Download HA AI Companion and restart Home Assistant.
4. In Settings, Devices & Services, add the HA AI Companion integration.
5. Enter your provider API key.

The default API URL is `https://openrouter.ai/api/v1` and the default model is `z-ai/glm-5.3-flash`. You can choose another OpenAI-compatible provider that supports tool calls. The optional fast model uses the same API URL and key as the main model.

## Safety

Review every proposed diff. A YAML pre-check can temporarily write and restore files before approval. After approval, a validation or reload error does not guarantee rollback. Keep a separate Home Assistant backup and check the result.

Some actions run without an approval card. These include writing to an `input_text` helper and scheduling an AI task.

## Home Assistant services

The `ha_ai_companion.chat` service sends a message to the assistant. Use `response_variable` to receive the reply:

```yaml
- action: ha_ai_companion.chat
  data:
    message: "Which lights are on?"
  response_variable: ai_reply
```

The response contains `response`, `messages`, `changesets`, and `truncated`. The `response` field holds the final text. The `messages` field holds the assistant and tool messages from this turn. To continue a conversation, append the user message and these messages to `conversation_history`. The `truncated` field is `true` if the provider cuts the reply short.

The `changesets` field lists proposed changes. For each proposal, use an authenticated Home Assistant request to `GET /api/ha_ai_companion/api/changeset/<changeset_id>` to inspect the diff. Review it before approval. If you omit `response_variable`, the action still runs but does not return the reply or proposals.

The `ha_ai_companion.approve` service accepts a `change_id` and optional `approved` and `validate` values. Use it only for a proposal you have reviewed. Set `approved: false` to reject it.

Request a response variable to receive the approval result:

```yaml
- action: ha_ai_companion.approve
  data:
    change_id: "abc123def456"
    approved: true
  response_variable: approval_result
```

The result includes `applied_files`, `failed_files`, `config_reloaded`, validation warnings, and any restart requirement. An expired proposal, failed file, validation failure, reload failure, or request error stops the automation. For a partial apply, the error lists files that were written and files that failed. Check the configuration before retrying.

If you do not request a response variable, the action still checks the result. It logs validation warnings, restart requirements, and when Home Assistant did not reload. It still stops on errors.

See the [full guide](https://github.com/krystiankolota/ha-ai-companion/blob/main/ha-ai-companion/DOCS.md) for configuration, data locations, and troubleshooting.

## License

MIT
