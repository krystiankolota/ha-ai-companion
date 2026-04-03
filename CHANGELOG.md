# Changelog

All notable changes to the HA AI Companion add-on will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.35] - 2026-04-03

### Fixed
- **Diff green highlighting** — CSS specificity bug: `.d2h-diff-table td` (0,1,1) was overriding `.d2h-ins` (0,1,0); fixed by using `.d2h-diff-table .d2h-ins` (0,2,0)
- **Diff RENAMED badge** — header strings in `createPatch` caused diff2html to treat same-path files as renames; now passes empty strings
- **Memory consolidation provider error** — strips trailing assistant turns before sending to Azure/Gemini; those providers require conversation ending with a user message
- **Pydantic field warning** — `validate` field renamed to `run_validation` with alias; JSON API unchanged
- **Suggestion buttons** ("Add to chat", "Fix in chat", "Fix all") — now correctly prefill the chat textarea via React state instead of custom events (fixes timing race on tab switch)

### Added
- **Memory tab** — dedicated top-level tab (next to Chat and Suggestions) to browse, expand, and delete AI memory files
- **Suggestion generation log** — live progress stream during generation; persists as collapsible after completion; last active step pulses
- **"Prompt sent to AI" panel** — after generation shows full system prompt, model name, total context size, and scrollable preview of each context section
- **Naming issues auto-dismiss** — "Fix in chat" removes that entity from the list; "Fix all" clears all entries
- **"Add to chat" auto-marks applied** — marks suggestion as applied when added to chat (implies intent to implement)
- **Mobile cache busting** — static assets now served with `?v={{ version }}` query string so mobile browsers pick up updates

### Changed
- **Entity name word wrap** — naming issue cards use `break-all` / `break-words` so long entity IDs don't cause horizontal scroll on mobile
- Removed `dashboards` from default suggestion context (noisy, large, rarely improves quality)
- Suggestion context deduplicates file paths across sections to avoid sending same content twice

## [1.1.18] - 2026-03-31

### Changed
- Complete frontend rewrite: React 18 + Vite + Tailwind CSS replacing vanilla JS
- New dark design: surface-950/900/800 palette, indigo accents, Inter font
- All existing functionality preserved: WebSocket streaming, approval cards, diff modal, suggestions tab, session management, token counter, clear-all with memory extraction
- Build source in `frontend/` — run `npm install && npm run build` to regenerate `static/dist/bundle.{js,css}`

## [1.1.17] - 2026-03-31

### Added
- `reload_config` tool — agent can now reload HA configuration after YAML changes are approved, activating new helpers/sensors/scripts without a restart
- System prompt guidance: agent now defines `input_number` (and other helpers) in YAML instead of asking the user to create them manually in the UI

## [1.1.16] - 2026-03-31

### Added
- "Clear All" button in conversations sidebar — analyzes all sessions for memorable facts (using the suggestion model), saves them to agent memory, then deletes all conversations

## [1.1.9] - 2026-03-30

### Fixed
- **Memory consolidation broken since 1.1.5** — `_run_memory_consolidation` iterated over `self.tools` (an `AgentTools` instance, not a list), causing `'AgentTools' object is not iterable` warning after every conversation turn; memory was never saved. Fix: memory tool schemas hardcoded directly in the method. Second bug in the same function: `self.agent_tools.execute_tool(...)` referenced a non-existent attribute; changed to `self._dispatch_tool(...)`.

## [1.1.8] - 2026-03-29

### Changed
- **UI rewritten mobile-first** — full CSS rewrite from scratch; uses `100dvh` for correct height on mobile browsers with dynamic address bar; safe-area insets for notched phones; `min-height: 0` on flex children fixes overflow on all screen sizes
- **Context sources always visible** — replaced collapsible `<details>` with inline chip-style toggles in the suggestions toolbar; all 7 sources visible by default, each toggleable as a pill; checked state highlighted in blue
- **Chat input layout** — stacked (textarea above button) on narrow screens, side-by-side on ≥600 px; 44 px minimum touch targets throughout
- **Modal** — full-screen on mobile, centered card on desktop; `flex-direction: column` footer stacks buttons on small screens
- **Sidebar** — overlay with `transform: translateX(-100%)` on mobile; inline collapsible on desktop; no layout shift when toggling
- **Footer** — compact on mobile (stats only, no label); full layout on desktop

## [1.1.7] - 2026-03-29

### Fixed
- **Config changes being silently rejected** — `_prune_old_tool_messages` was removing file-read tool results after just 2 tool blocks, causing the AI to propose config changes without the current file content in context; deletion guard then blocked proposals that appeared to remove >20% of items. Fix: pruning now only starts when a conversation exceeds 30 messages AND keeps the last 6 tool blocks (up from 2), so normal multi-step operations (read 3 files → propose changes) are never affected

### Reverted
- **Relevance-based memory injection (1.1.6)** — rolled back while investigating interaction with the above bug; recency-only injection (from 1.1.5) restored

## [1.1.6] - 2026-03-28

### Improved
- **Relevance-based memory injection** — memory files are now scored and ranked before being injected into each prompt instead of always dumping the N most-recent files:
  - **Category priority**: `identity_` and `preference_` files always included (home layout and user prefs are almost always relevant); `correction_` files close behind
  - **Keyword matching**: words from the user's message are extracted and matched against memory filenames and content; files with matching keywords score significantly higher
  - **Recency boost**: recently modified files get a small bonus over older ones
  - **Relevance threshold**: files that score below a minimum threshold are skipped when a query is present (typically saves 20–60% of injected memory tokens on focused queries)
  - **Suggestions tab**: memory is now filtered by the focus prompt if one is set; without a focus prompt all memory is included as before
  - Configurable staleness cutoff via `MEMORY_MAX_AGE_DAYS` env var (default: 180 days); stale files are skipped from injection but kept on disk

## [1.1.5] - 2026-03-28

### Improved
- **Memory consolidation is skipped on short/trivial turns** — background LLM call no longer fires after every single message; only runs when the conversation has ≥2 user turns or tool activity happened
- **Memory consolidation uses cheaper model** — background memory pass now uses the suggestion-phase model instead of the main model, reducing cost
- **Tool results excluded from consolidation context** — large tool JSON blobs no longer sent to the memory pass, keeping the background call lean
- **Timestamp comments stripped from injected memory** — `<!-- updated: ... -->` headers are removed before injection, saving a few tokens per file per request
- **Stale memory files skipped** — files older than 180 days (configurable via `MEMORY_MAX_AGE_DAYS` env var) are no longer injected into prompts; still kept on disk for manual review

## [1.1.4] - 2026-03-28

### Added
- **Applied suggestions tracking** — "✓ Applied" button on each suggestion card marks it as implemented; applied suggestions are hidden from future generations and filtered from the current list (saved in `.ai_agent_suggestions_applied.json`)
- **"Fix all in chat" button** — naming issues section now has a single button that sends all rename requests to the AI at once instead of one-by-one
- **Config changes in session history** — when changes are approved and applied, a `✅ Config changes applied to: <files>` record is saved to conversation history and shown when the session is replayed

### Fixed
- **Sessions list sometimes empty** — if the first `api/sessions` load returns empty (server still initializing), it auto-retries once after 2 seconds

## [1.1.3] - 2026-03-28

### Fixed
- **SSE stream interruption**: `openai.APIError: JSON error injected into SSE stream` no longer causes a hard `❌ Error` to the user — if the stream was interrupted after content was already received the partial response is delivered normally; only errors with no content at all show an error message

## [1.1.2] - 2026-03-28

### Fixed
- **Critical**: `asyncio` scoping bug — `import asyncio` inside `chat_stream` shadowed the module-level import, causing `UnboundLocalError` on every prompt

### Added
- **Tool execution status streaming** — loading indicator now shows live per-file progress during `search_config_files` ("Reading automation.yaml", "Checking device registry…", etc.) via a new `tool_status` WebSocket event
- **Compact entity states in suggestions** — `generate_suggestions()` now sends `domain: entity=state` lines instead of full JSON, reducing context size ~80% for large HA instances
- **Old tool result pruning** — conversation keeps only the last 2 tool call/result blocks, dropping older ones to save input tokens on multi-step operations
- **Deletion guard override** — `propose_config_changes` now accepts `confirm_delete=true` to bypass the safety guard when user explicitly wants to remove automations/scripts/scenes; guard stays active by default

## [1.1.1] - 2026-03-27

### Added
- **Per-phase usage tracking** — suggestion and config phases can now each have their own `usage_tracking` setting (`stream_options`, `usage`, `disabled`) independent of the global setting; useful when using different AI providers per phase that require different token reporting formats. Configure in options flow step 2 (Suggestion & Config Phases).

## [1.1.0] - 2026-03-27

### Added
- **Context sources filter** — suggestions toolbar now has a collapsible "Context sources" panel with checkboxes to control what is sent to the LLM (entity states, automations, scenes, scripts, dashboards, Node-RED flows, memory); selections persist in localStorage
- **Suggestion history** — past suggestion sets are saved (configurable `max_suggestions`, default 10) and browsable via a collapsible "Past suggestions" section in the UI
- **Multi-step config flow** — HA options flow split into 3 clearly labelled steps: Core Model → Suggestion & Config Phases → Node-RED & Advanced
- **WebSocket auto-reconnect** — exponential backoff reconnect (1s → 30s max) on dropped connection
- **Configurable topology cache TTL** — via `TOPOLOGY_CACHE_TTL` env var (default: 600s)
- `max_suggestions` config option to control suggestion history retention

### Fixed
- **Mobile suggestions tab** — no more horizontal overflow; YAML blocks scroll within their container; toolbar stacks vertically on small screens
- **Mobile sessions reliability** — sessions reload when the HA app returns to foreground (fixes disappearing history after location toggle)
- **XSS** — all AI-generated markdown is now sanitized with DOMPurify before insertion into the DOM
- **Path traversal** — config directory check now uses `os.sep` suffix to prevent false matches (e.g. `/config2` vs `/config`)
- **Cost calculation** — cached tokens are now excluded from billable input tokens (fixes overstated cost for Anthropic Claude)
- **UTC datetimes** — all `datetime.now()` calls replaced with `datetime.now(timezone.utc)` throughout
- **Lovelace cache race condition** — protected with `asyncio.Lock()` to prevent concurrent double-fetches
- **Dead SSE code** removed from `app.js` (~230 lines); event listeners now point directly to `sendMessageWebSocket`
- **N+1 fetch** in dismissed suggestion restore replaced with `Promise.all` parallel fetches
- **HA options flow** now correctly seeds from merged `entry.data + entry.options` and `__init__.py` reads both
- Tool calls timeout after 60 seconds (`asyncio.wait_for`) instead of hanging forever
- `url_path` validated against `^[a-z0-9][a-z0-9_-]*$` before passing to HA in dashboard create/delete
- Client-side `conversationHistory` capped at 200 messages to prevent mobile memory growth
- Corrupt suggestions history file is renamed rather than endlessly retried
- Memory filename sanitization now logs a warning when a name is modified
- API key masked in startup logs (shows "configured"/"NOT configured")
- Focus prompt capped at 500 characters
- ARIA labels added to all icon buttons; `spellcheck="true"` on chat textarea
- `theme-color` meta tag added for mobile browser chrome

## [1.0.0] - 2026-03-26

### First stable release

All core features are complete and production-tested. This release consolidates everything from the 0.x series.

**Full feature set:**
- Natural language chat with a streaming AI agent for Home Assistant configuration management
- Safe approval workflow — every proposed change shows a visual diff; applied only on confirmation; automatic backup + HA validation + rollback on failure
- Multi-dashboard Lovelace management — list, read, edit, create and delete dashboards
- Device / entity / area management via virtual file system
- Automation suggestions tab — based on live entity states, avoids duplicating existing HA automations and Node-RED flows; includes naming audit for unclear entity names
- Custom suggestion focus textarea — persists in localStorage, sent to the AI as extra context
- Dismissed suggestion management — view, restore individual, or clear all dismissed suggestions
- Persistent memory — remembers preferences, device roles, and home layout across sessions; consolidates after every conversation with ecosystem learning (`ecosystem_` category)
- Home topology injection — area→entity map pre-loaded into every prompt, 10-minute cache
- Session persistence — JSON files, configurable retention (`max_sessions`), slide-in sidebar with mobile overlay
- Token + cost tracking — cumulative input/output/cached tokens and optional USD cost in UI footer
- Mobile-optimised UI — full-width tabs, slide-in session sidebar, collapsed tool cards
- Language detection — responds and generates HA content in the language of your entity/automation names
- Per-phase provider config — independent API URL/key/model for suggestion and config phases
- Per-client usage tracking — auto-detects and caches whether a provider accepts `stream_options`/`usage` params
- Long conversation summarisation — conversations >30 messages summarised automatically to prevent context overflow
- Tool alias remapping — old tool names in session history remapped to current names transparently
- Split-file automation support — safety guard and suggester both handle `automations/`, `scripts/`, `scenes/` directories
- Token limits per phase — `max_tokens`, `suggestion_max_tokens`, `config_max_tokens` config options
- Node-RED integration — reads live flows via REST API with file fallback
- Prompt caching support (Anthropic Claude) — `enable_cache_control` option

## [0.2.9] - 2026-03-26

### Added
- **Custom suggestion focus prompt**: Textarea above the "Generate suggestions" button lets you focus the AI on specific areas (e.g. "focus on bedroom lights"). Value persists in localStorage between page loads.
- **Device naming audit**: When generating suggestions, the AI also reviews entity friendly_names and lists unclear or ambiguous ones (e.g. "pompa", "sensor_1") with suggested improvements. Results appear as a "🏷️ Unclear entity names" section with "Fix in chat" buttons.
- **Token limits per agent**: New optional config options `max_tokens`, `suggestion_max_tokens`, `config_max_tokens` — limit output tokens per phase independently.
- **Configurable session limit**: New `max_sessions` config option (default 50). Oldest sessions are pruned when the limit is exceeded.
- **Ecosystem memory categories**: Memory consolidation now actively captures device relationships, integration facts, and unclear device roles under `ecosystem_devices.md`, `ecosystem_integrations.md`, `user_patterns.md`.

### Fixed
- **Suggester shared memory context**: `generate_suggestions()` now injects the full persistent memory context — device nicknames, preferences, home layout — so suggestions are tailored to your specific setup.
- **Suggester reads full automation content**: Now reads the actual `automations.yaml` file content rather than just keyword search results, so existing automations are properly de-duplicated.
- **Per-client usage_tracking caching**: After a provider rejects `stream_options`/`usage` params, that decision is cached per client slot — no more redundant retries on every API call.
- **Long conversation summarization**: Conversations exceeding 30 messages (15 exchanges) now have their oldest messages summarized into a compact block, preventing context overflow and HTTP 400 errors.
- **Deprecated tool name remapping**: Conversations containing old tool name `call_config_files` are automatically remapped to `search_config_files` when replayed, preventing "unknown tool" errors on imported sessions.

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
