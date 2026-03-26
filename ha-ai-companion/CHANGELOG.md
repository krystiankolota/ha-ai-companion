# Changelog

All notable changes to the HA AI Companion add-on will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.8] - 2026-03-26

### Added
- **Session cost display**: When `input_price_per_1m` and `output_price_per_1m` are configured, cumulative session cost is shown in the footer (e.g. `💰 $0.0024`)
- **Pricing config options**: New optional `input_price_per_1m` and `output_price_per_1m` fields in add-on/component config (USD per 1M tokens)

### Changed
- **Mobile UI overhaul**: Sessions sidebar is now a slide-in overlay on mobile (80vw, backdrop tap to close, body scroll locked while open). Header hides text title on mobile, keeping only icon + version. Tab nav is full-width equal tabs. Tool result/call detail panels are collapsed by default on mobile. Suggestion toolbar stacks vertically. Approval card actions stack vertically.
- **Removed export/import buttons**: 📥📤 buttons removed from header — sessions tab already handles persistence

## [0.2.7] - 2026-03-26

### Fixed
- **cache_control 4-block crash**: Excess `cache_control` markers are now stripped from oldest messages before each API call — prevents "A maximum of 4 blocks with cache_control may be provided" error on long conversations
- **Automation deletion prevention**: `propose_config_changes` now rejects changes to `automations.yaml`, `scripts.yaml`, or `scenes.yaml` that would remove more than 20% of existing items — with a clear error telling the agent to include all existing entries
- **Change proposal not visible**: Arguments are now echoed back in the `tool_result` event for `propose_config_changes`, so the approval card always renders correctly without relying on the `tool_start` lookup
- **Language detection**: Agent now detects the language of HA entity/automation names from the injected home layout and responds + generates new content in that same language

### Changed
- System prompt: added explicit automation safety rules (read before write, include all existing)
- System prompt: added language detection and matching instruction

## [0.2.6] - 2026-03-24

### Added
- Suggestion cards now show a type badge: **new** (green) or **improvement** (blue)
- `yaml_block` rendered as a YAML code block with a one-click Copy button on each suggestion card
- "Add to chat" prefills the message with the YAML block included

### Changed
- Suggestion engine rewritten: randomized entity sampling (max 150), attribute slimming, reads `automations.yaml` / `scripts.yaml` / `scenes.yaml` directly, 24k token budget guard
- AI now generates both new automations (4–6) and improvements to existing ones (2–4) per run
- Node-RED flows included in suggestion context when `NODERED_URL` is configured
- Dismissed suggestions are excluded from future generation prompts

## [0.2.5] - 2026-03-24

### Added
- Dismiss button (✕) on each suggestion card — marks it as unwanted and hides it immediately
- Dismissed suggestions persist in `/config/.ai_agent_suggestions_dismissed.json`
- AI won't re-suggest dismissed items in future generation runs
- `POST /api/suggestions/dismiss`, `GET /api/suggestions/dismissed`, `DELETE /api/suggestions/dismissed` endpoints
- Status line shows how many suggestions are currently dismissed

## [0.2.4] - 2026-03-24

### Added
- Independent provider support per model phase: `suggestion_api_url` + `suggestion_api_key` and `config_api_url` + `config_api_key`
- Each model phase (suggestion/config) now uses its own OpenAI client — no URL/key mixing between providers
- All 3 fields (model + API URL + API key) must be set together to activate a phase; otherwise the main provider is used

## [0.2.3] - 2026-03-24

### Added
- Add-on configuration labels and descriptions for all options (via `translations/en.yaml`)
- Missing `suggestion_model` and `config_model` labels in HACS component translations
- Documentation updated with full option reference grouped by category

## [0.2.2] - 2026-03-24

### Fixed
- Add-on schema validation: changed `nodered_url` and `nodered_token` from `url?`/`password?` to `str?` to allow empty string defaults

## [0.2.1] - 2026-03-24

### Changed
- Renamed project from "AI Configuration Agent" to "HA AI Companion"
- Updated add-on slug, panel title, and repository metadata to match new name
- Added `repository.yaml` for proper Supervisor add-on repository registration

### Added
- Multi-dashboard Lovelace management (list, read, edit, create, delete)
- Device / entity / area management via virtual file system
- Persistent memory system (6-category taxonomy, markdown files)
- Session persistence with sidebar UI
- Automation suggestions tab with live entity context and Node-RED awareness
- Token usage tracking in UI footer
- Dual-model support (`suggestion_model` + `config_model`)

## [0.2.0] - 2025-11-01

Converted to support installation via HACS as custom component as well as add-on installation.

## [0.1.11] - 2025-10-31

Enhanced search functionality to support file path patterns. When search_pattern starts with "/", it's treated as a glob pattern and only searches actual files (skipping virtual entities/devices/areas). Example: `/packages/*.yaml` will match all YAML files in the packages directory.

## [0.1.10] - 2025-10-30

Made cache control configurable and added token usage tracking

## [0.1.9] - 2025-10-28

Added configurable temperature parameter for LLM calls. You can now specify the temperature (0.0-2.0) in the add-on configuration to control the randomness of the AI's responses. When not specified, the LLM provider's default temperature is used

## [0.1.8] - 2025-10-28

Import and export conversation history

## [0.1.7] - 2025-10-28

Prevent leaking secrets to LLMs

## [0.1.6] - 2025-10-28

Add prompt caching support for models that support it (currently only Gemini and Claude)

## [0.1.5] - 2025-10-28

General bug fixes and improvements.

## [0.1.4] - 2025-10-27

Moved system prompt into config file as full system prompt in options was breaking HA.

## [0.1.3] - 2025-10-27

Moved system prompt into configuration options and improved prompt.

## [0.1.2] - 2025-10-26

Refactored API to use websockets as streaming responses was not working properly.

## [0.1.1] - 2025-10-26

Enhanced API and UI to use streaming responses to provide faster feedback to the frontend as queries are processed involving tools. Added tool call results (and tool calls) into the chat history UI.

## [0.1.0] - 2025-10-26

### Initial Version

Initial version of the AI Configuration Agent add-on
