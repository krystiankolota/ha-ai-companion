# Changelog

All notable changes to the HA AI Companion add-on will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.11.0] - 2026-06-12

### Changed — token cost reduction
- **History tool-payload diet** — tool results and `propose_config_changes` arguments from turns older than the last completed one are truncated to ~1KB before being sent to the LLM (they were re-billed in full on every iteration of every later turn). The model re-reads files when it genuinely needs them.
- **Earlier history summarization** — long conversations are summarized at >14 messages (was >30), keeping the newest 10 verbatim (was 20). Summary window can no longer start mid tool-exchange (orphaned tool messages broke strict providers).
- **Slimmer tool schemas** — verbose tool descriptions (Node-RED, scheduler, patches, HACS docs) cut to essentials; schemas are resent on every LLM iteration.

> Tip: with Anthropic models, also set `enable_cache_control: true` and `max_iterations: 10` in the add-on options — cached input is billed at 10%, the agent loop is the ideal caching pattern.

## [1.10.0] - 2026-06-12

### Added
- **Sessions survive tab switches** — agent execution is now decoupled from the WebSocket connection (run registry with sequence-numbered event replay). Switching HA panels or closing the tab mid-response no longer kills the run: the agent keeps working server-side, the UI auto-resumes the stream on return, and if you never return the result is persisted to the session file.
- **`get_ha_error_log` tool** — the AI can now read Home Assistant core errors and warnings (`system_log`, structured and deduplicated) to diagnose broken integrations, failed config, and entity errors.
- **`get_lovelace_resources` tool** — lists installed custom card JS modules so the AI verifies a card (e.g. Bubble Card) is actually installed before using it in a dashboard.
- **Custom-card schema guard** — `propose_config_changes` now validates Bubble Card structures (required `card_type`, numbered-prop `horizontal-buttons-stack`, pop-up `hash`) and blocks invented schemas with a correction hint before they can render a blank dashboard.

### Changed
- **Storage-mode dashboards only** — system prompt now forbids adding `lovelace:` entries to `configuration.yaml`; new dashboards are always created UI-editable via `create_dashboard`, with migration guidance for legacy YAML-mode dashboards. New dashboards prefer the responsive `sections` layout.

### Fixed
- **Double-fault on disconnected WebSocket** — error handler no longer raises `Cannot call "send" once a close message has been sent` when the client is already gone.

## [1.9.9] - 2026-06-10

### Fixed
- **YAML-mode dashboard save fails with "Not supported"** — dashboards configured in YAML mode reject WebSocket writes; `_write_lovelace_yaml` now falls back to writing the file directly to `/homeassistant/lovelace/{url_path}.yaml` when HA returns this error.

## [1.9.8] - 2026-05-27

### Added
- **Copy button on code blocks** — every code block in assistant messages now has a top-right Copy button; turns green on success, falls back to `execCommand` for Safari.

### Fixed
- **Session lost on page reload** — last active session ID now persisted in `localStorage`; on reload the app auto-restores it instead of starting a blank chat.

## [1.9.7] - 2026-05-27

### Fixed
- **`edit_nodered_tab` fails with wrong parameter names** — model sometimes calls the tool using `new_nodes` or `nodes` instead of the schema-defined `flows_json`, causing `unexpected keyword argument` errors and two wasted retry iterations (expensive output tokens). Dispatch layer now normalises `new_nodes`, `nodes`, `updated_nodes`, `flow_nodes` → `flows_json` before the call. Same guard added to `add_nodered_flow`. List inputs are auto-serialised to JSON string.

## [1.9.6] - 2026-05-26

### Fixed
- **fetch_url infinite loop** — volume guards appended warning text to tool results but never blocked execution; LLM ignored text and kept calling. Now a pre-dispatch guard intercepts `fetch_url` / `learn_hacs_component` BEFORE the HTTP call: blocks if same exact args called ≥2 times, or same function called ≥3 times total per turn. Blocked calls return `{"blocked": true}` and never trigger retry directives.
- **Verbose "Wait..." reasoning flood** — LLM wrote thousands of characters of "Wait, let me check..." chain-of-thought before tool calls, streaming it all to the user. Added system prompt VERBOSITY RULE (≤1 sentence before tool calls) and a code guard: if response text exceeds 1500 chars before tool calls, injects a correction nudge into the first tool result.

## [1.9.5] - 2026-05-26

### Fixed
- **Over-research spiral burns iterations/money** — agent called `search_past_sessions` 5× and `read_memories` 3× with no volume guard, burning iterations after already having sufficient context. Added volume guards for context-lookup tools (`search_past_sessions`, `read_memories`, `list_memory_stats`, `list_dashboards`): warn at 2 calls, hard-block at 3.
- **Reflect nudges too sparse** — previously only fired at call 2 and 4, leaving 20+ unchecked calls after. Now fire at 2, 4, 6, 8, and 10 with escalating pressure. Call 10+ injects a hard stop blocking everything except propose/patch tools.
- **Post-doc research spiral** — after `learn_hacs_component` succeeds, agent continued reading configs and searching history instead of writing YAML. Now injects a hard directive after successful doc fetch: "NEXT action must be propose_config_changes."

## [1.9.4] - 2026-05-26

### Fixed
- **New dashboard write blocked** — `propose_config_changes` hard-failed when trying to populate a newly created dashboard (storage-mode dashboards have no YAML file on disk yet, so `_get_lovelace_config` returns None). Now checks the dashboard registry: if the url_path exists (just created), treats content as empty and allows the initial write. Matches the same pattern already used for new areas and new regular config files.

### Changed
- **System prompt dashboard create flow** — clarified that after `create_dashboard`, call `propose_config_changes` directly without `search_config_files` first. New dashboards have nothing to read; the system handles the empty-content case automatically.

## [1.9.3] - 2026-05-26

### Fixed
- **Max iteration limit on complex tasks** — hardcoded limit of 10 caused "Maximum iteration limit reached" on multi-step tasks (dashboard builds, multi-area setups). Default raised to 25. Configurable via new `max_iterations` option in HA add-on settings and `MAX_ITERATIONS` env var.

### Changed
- **Smarter iteration budget** — system prompt now instructs the model to (1) batch parallel tool calls in a single response instead of sequential iterations, (2) use entity IDs from the already-injected Home Layout for dashboard YAML instead of calling `get_entity_states` per domain, (3) stop gathering data when it has enough to propose.

## [1.9.2] - 2026-05-26

### Fixed
- **Infinite loop in HACS doc fetch** — `fetch_url` and `learn_hacs_component` were not covered by the agent loop guard. When a README was truncated and the searched term wasn't found in the fetched portion, the model would retry indefinitely with higher `max_chars`. Now hard-stopped at 3 calls (warn) / 4 calls (blocked), matching the existing search-tool guards. System prompt also adds explicit rules: max 2 fetches per topic per turn, no retry on `truncated=true`.

## [1.9.1] - 2026-05-26

### Fixed
- Blank white screen on install — frontend bundle was missing for 1.9.0 (version bump ran without `npm run build`). Bundle `1.9.1` now included.

## [1.9.0] - 2026-05-26

### Added
- **`fetch_url` tool** — companion can fetch raw text from GitHub URLs (github.com, raw.githubusercontent.com, api.github.com, data.home-assistant.io). Max 8,000 chars with truncation notice. Rejects non-GitHub domains without making an HTTP call.
- **`learn_hacs_component` tool** — companion self-researches any HACS component before writing Lovelace YAML. Resolves component name → GitHub repo via HACS store JSON, then fetches README, CHANGELOG, and up to 3 example YAML files. Results saved to `pattern_<slug>_*.md` memory files (syntax, examples, changelog). 30-day freshness cache: returns `status=cached` if docs are recent. GitHub API rate limit and missing files handled with graceful skip.
- **Memory caveman style** — companion now writes all `.ai_agent_memories/` files in terse bullet style (no articles, no filler prose).

### Changed
- **HACS custom card workflow** — before writing YAML for any custom card, companion checks memory for `pattern_<slug>_*` files. If missing, asks user before fetching. If present and fresh, proceeds without a network call.

## [1.8.5] - 2026-05-25

### Added
- **Critical memory tagging** — `save_memory(critical=true)` marks a file as always injected into every session, regardless of query relevance. Use for essential facts: home language, primary resident, key device aliases. `critical=false` demotes the file back to normal tier. `critical` omitted on an update preserves the existing status.

### Changed
- **Memory context budget** — reduced from 6,000 → 3,500 chars. Critical files get a guaranteed 1,500-char sub-budget; remaining 2,000 chars filled by `preference_`/`identity_` and query-gated files.
- **Conditional Node-RED tools** — NR tool schemas (~3,000 chars) no longer added to the tools list unless `NODERED_API_URL` env var is set. Saves tokens on every turn for users not using Node-RED.
- **History pruning** — `keep_blocks` reduced from 6 → 3. Older tool-result blocks pruned sooner; very long sessions trade off older context for lower cost.
- **Topology reduction** — `_build_home_topology` max chars 2,000 → 1,500; per-area entity cap 8 → 5.
- **Compact Node-RED suggestions JSON** — `indent=2` removed from `json.dumps` in `generate_suggestions`, reducing Node-RED flow JSON size.

## [1.8.4] - 2026-05-21

### Added
- **Entity embedding cache — disk persistence** — entity embeddings now saved to `.ai_agent_cache/entity_embeddings.npz` at first use. Cold starts skip the 9+ API embedding calls if entities haven't changed. Delta-only re-embedding: only new or renamed entities are re-embedded on subsequent starts.

### Changed
- **Memory relevance injection** — `preference_*` and `identity_*` memory files always injected into context (high-priority). All other files are now keyword-gated against the user's query — zero-score files are skipped, freeing context budget for relevant memories.
- **`search_config_files` snippet mode** — by default, returns ±12 lines of context around each match instead of full file content. Files ≤60 lines always return full content. Pass `full_content: true` when reading a file before editing it. Reduces tool result tokens significantly for large config repos.
- **Plan-before-act** — Gemini-family models are forced to emit a planning text response (tool_choice=none) on the first iteration when write intent is detected. Claude-family models receive the same instruction via the system prompt. Reduces impulsive writes without reading first.
- **Phase-gated tools** — on the first iteration (before any tool results), only read tools are exposed (search, get_entity_states, get_nodered_flows, etc.). Write tools become available after the model has gathered context.

## [1.8.0] - 2026-05-21

### Added
- **Reflection nudge** — after the 2nd and 4th tool calls per turn, a directive is injected into the tool result prompting the model to reflect before its next action.
- **Clarification pathway** — system prompt now instructs the model to ask a clarifying question after 2 failed search attempts, rather than continuing to search.

### Changed
- **Memory consolidation gating** — consolidation no longer fires every turn. Requires ≥5 user turns AND keyword match on correction/preference patterns (`"I prefer"`, `"remember"`, `"no, not"`, etc.).
- **Summarization model** — `_summarize_old_history()` now uses the suggestion model (Haiku) instead of the config model, matching the intent of dual-model routing.

## [1.7.9] - 2026-05-21

### Fixed
- **Search loop bug** — Gemini Flash repeatedly exhausted all 10 iterations doing redundant `search_config_files` calls without acting. Added two loop-detection guards: exact-duplicate detection (same tool + same args) injects a hard-stop directive; volume guard (≥4 calls to the same search tool per turn) injects a "stop searching, act now" directive.

## [1.7.8] - 2026-05-20

### Changed
- **Tool result token reduction** — three fixes that cut per-request token cost significantly, especially for automation sessions:
  - `search_config_files` no longer fetches Lovelace dashboards by default. All 7 dashboards were fetched and matching ones returned as full YAML on every call, even for automation searches. Pass `include_lovelace: true` only when editing dashboard UI.
  - `get_entity_states` tool result now uses compact format (`"name"[entity_id]=state` per entity) instead of raw JSON attribute dicts. Reduces entity state output by ~10×.
  - Entity and device records in `search_config_files` trimmed to key fields only (`entity_id`, `name`, `platform`, `area_id`) — verbose registry fields removed.
- **Entity result hard cap** — `get_entity_states` semantic search now caps total returned at 80 entities. "Unavailable" and recently-changed entity additions could inflate the 40-entity semantic result to 200+.

## [1.7.7] - 2026-04-30

### Changed
- **Memory injection token reduction** — compressed `get_context()` format: section header shortened from `## Agent Memory (persistent knowledge from previous sessions)` to `## Memory`, per-file headers from `### filename.md` to `[filename]` (strips `###` prefix and `.md` extension). Saves ~80 tokens per request with 25 files.
- **`MAX_FILE_CHARS` aligned to system prompt rule** — was 1500, now 800 (matching the "max 800 chars/file" rule the LLM is given). Prevents LLM from writing 1500-char files while believing the limit is 800.

## [1.7.6] - 2026-04-30

### Changed
- **System prompt token reduction (~25%)** — rewrote the default system prompt in compact style: removed the redundant "Key Responsibilities" block (covered by specific rules below it) and the "Response Style" section (inferred by the model), and tightened all prose sections by eliminating articles, filler phrases and verbose explanations. Both CRITICAL rule blocks (automation safety, entity ID rules) kept verbatim. Saves ~750 tokens per request with no quality impact.

## [1.7.5] - 2026-04-29

### Fixed
- **400 error on follow-up messages with multiple tool calls** — when the LLM called two or more tools in a single iteration, the `tool_call` event was broadcast during streaming before all tool call IDs were accumulated. The frontend stored an assistant message with only the first tool call, leaving subsequent tool results as orphans. On the next user message, Claude/Azure rejected the request: "unexpected `tool_use_id` found in `tool_result` blocks". Fixed by removing the premature in-stream announcement; the event now fires only after the full stream is processed with all tool calls complete.
- **Memory consolidation calling unexpected tools** — tool-call-only assistant messages (empty `content`) were included in the slim history passed to the consolidation LLM. These empty messages primed the model to keep calling main agent tools (`search_config_files`, `get_entity_states`) despite the restricted tool list. They are now excluded from slim history.

## [1.7.4] - 2026-04-22

### Fixed
- **Retry counter stuck at 1/2** — tool retry counter was keyed by the unique `tool_call_id` (new ID each API call), so the LLM always saw "Auto-retry 1/2" and never reached the "Max retries" stop message — causing runaway retry loops (observed: 4 consecutive `edit_nodered_tab` failures). Counter is now keyed by function name per turn, giving correct 1/2 → 2/2 → stop behaviour.
- **Tool failures silent in logs** — when a tool returned `success=False`, only the boolean was logged; the actual error message was never surfaced. Error is now logged at WARNING level (up to 300 chars) before the retry directive is injected, making log-based debugging possible.

## [1.7.3] - 2026-04-21

### Fixed
- **Suggestions crash on malformed JSON** — when the AI model returned malformed JSON, `generate_suggestions` raised an unhandled `JSONDecodeError`. Now uses a two-stage fallback: extract outermost JSON block via regex; if still unparseable, return empty result gracefully. Also improved code-fence stripping to use proper regex.

## [1.7.2] - 2026-04-21

### Fixed
- **Suggestions crash on malformed JSON** — when the AI model returned malformed JSON, `generate_suggestions` raised an unhandled `JSONDecodeError`. Now uses a two-stage fallback: extract outermost JSON block via regex; if still unparseable, return empty result gracefully. Also improved code-fence stripping to use proper regex.
- **Usage-param retry fires twice per session** — when a provider rejects `stream_options`/`usage` params, rejection is now propagated to all client slots at once so only one wasted round-trip occurs per restart instead of two.
- **Memory extraction error level** — `clear-all` memory extraction failure is now logged as WARNING instead of ERROR; sessions are still deleted correctly when JSON parsing fails.

## [1.7.0] - 2026-04-21

### Added
- **Surgical config editing** — two new tools replace noisy full-file rewrites: `patch_config_key` changes a single YAML key by dot-notation path (e.g. `homeassistant.name`); `patch_config_block` replaces a named section. Both create changesets through the same approval workflow. `propose_config_changes` description updated to steer the LLM toward patch tools for targeted edits.
- **Safe Node-RED editing** — `add_nodered_flow` stages a new flow tab for approval; `edit_nodered_tab` updates a single existing tab. Both use virtual path routing (`nodered/new_flow.json`, `nodered/flow/{tab_id}.json`) so only the intended tab is ever touched. The destructive replace-all path is no longer accessible to the LLM.
- **Memory consolidation** — new `consolidate_memories` tool reads all memory files and returns a merge/delete/keep plan for the user to review before any files change.
- **Tool breadcrumbs UI** — each agent turn now shows a compact step-by-step breadcrumb row for every tool call (pending → running → done/error). "Show details" expands full args + result JSON per tool. Replaces the old generic spinner.
- **AI Task Entities** — `set_ha_text_entity` writes plain text directly to an `input_text` helper in Home Assistant (no approval needed); `schedule_ai_task` creates a recurring task that runs a prompt on a `daily HH:MM` schedule and writes the result to an entity. Tasks are stored in `.ai_agent_tasks/` and survive restarts. Manage tasks via `GET/DELETE /api/scheduled-tasks` and `POST /api/scheduled-tasks/{id}/run`.
- **Semantic entity search** — `get_entity_states` now accepts an optional `query` parameter. When provided, entity descriptions are embedded via the configured embeddings API (`EMBEDDING_MODEL` env var, default `text-embedding-3-small`) and cosine similarity returns the ~40 most relevant entities rather than dumping all of them. Unavailable entities and those changed in the last 10 minutes are always included. Falls back to full dump if the embeddings API is unavailable.
- **Tool call error recovery** — when a tool returns a failure (bad entity ID, malformed YAML, etc.), the error is fed back to the LLM with a "correct and retry" directive. Up to 2 retries per tool call. Approval-gated and infrastructure errors are never auto-retried.

## [1.6.3] - 2026-04-13

### Fixed
- **Blank page on install** — 1.6.2 was released without a frontend rebuild; the HTML template referenced `bundle.1.6.2.*` assets that didn't exist, causing a blank white page

## [1.6.2] - 2026-04-12

### Fixed
- **Memory save reliability** — raised per-file character limit from 800 to 1500; `save_memory` tool now returns a descriptive error with char counts when content is rejected so the agent can shorten and retry instead of silently failing

## [1.6.1] - 2026-04-11

### Fixed
- **Diff modal scrolling** — file headers and line numbers no longer stay fixed in place when scrolling the diff view; overrode diff2html's default `position: sticky` on `.d2h-file-header` and `.d2h-code-linenumber`

## [1.6.0] - 2026-04-11

### Added
- **Watchman & Spook integration** — new `get_ha_issues` tool reads Watchman missing entity/service references directly from entity state attributes (`sensor.watchman_missing_entities`, `sensor.watchman_missing_actions`) and fetches Spook repair issues via the HA repairs API; agent now understands and can fix broken config references when asked
- **Entity ID validation** — `propose_config_changes` now checks every `entity_id:` field in proposed YAML against the entity registry; unknown entity IDs return suggestions from the same domain so the agent can self-correct before you see the approval dialog
- **watchman_report.txt readable** — `search_config_files` now includes `.txt` files from the config root, so the agent can directly read the watchman text report

### Fixed
- **Dashboard url_path underscores** — HA requires hyphens in dashboard URL slugs; underscores in `url_path` are now automatically converted to hyphens before the API call (was causing `invalid_format` errors)
- **Entity ID hallucination** — added system prompt rules instructing the agent to never invent entity IDs and to verify against `get_entity_states` before writing YAML; `propose_config_changes` warnings are now actionable (fix or remove)

### Removed
- **Health tab** — replaced by Watchman and Spook integrations which detect the same issues more reliably; all `/api/health/*` backend endpoints removed

## [1.5.0] - 2026-04-10

### Added
- **Conversation history search** — new `search_past_sessions` tool lets the agent search stored conversation sessions by keyword; when the user references prior work ("that automation we made last week", "remember when we fixed...") the agent retrieves relevant past exchanges verbatim before responding, instead of asking the user to re-explain

## [1.4.1] - 2026-04-10

### Fixed
- **Health: dead ref false positives** — entity_id references are now only flagged if their domain actually has entities in HA; service-only domains (`notify`, `homeassistant`, `persistent_notification`, etc.) are automatically excluded since they never appear in entity states
- **Health: scan results lost on reload** — findings are now persisted to localStorage and restored on tab open; dismissed state is re-synced from backend on mount

## [1.4.0] - 2026-04-10

### Added
- **Health tab** — new fourth tab for config maintenance; scans for four categories of problems:
  - **Dead entity refs**: entities referenced in automations/scripts/scenes/dashboards that are unavailable >7 days or missing from the registry entirely
  - **Orphaned helpers**: `input_boolean`, `input_number`, etc. defined in YAML but never referenced anywhere — one-click fix with approval diff
  - **Duplicate automations**: AI-detected pairs/groups with same trigger and action
  - **Unused scripts/scenes**: defined but never called from any automation, dashboard, or other script — one-click fix with approval diff
- **Health: dismissible findings** — dismiss any finding to hide it from future scans; "Show dismissed" toggle to restore; persisted in `.ai_agent_health_dismissed.json`
- **Health: pre-computed fixes** — orphaned helpers and unused scripts/scenes show "Apply fix" which stages the change through the existing approval flow (backup created before write)
- **Health: "Fix →" button** — dead refs and duplicates are sent to the chat agent with full context to handle safely
- **Health: restore backup shortcut** — "↩ Restore backup" button sends to chat to list and restore recent backups

### Fixed
- **Approval card: reject confirmation** — clicking "Reject" now shows an inline "Discard all changes? / Confirm reject / Cancel" prompt to prevent accidental rejection of multi-file changesets

## [1.3.1] - 2026-04-10

### Fixed
- **Memory: content not printed in chat** — explicit instruction prevents the AI from reproducing or quoting memory content in its responses
- **Memory consolidation: session logs no longer saved** — rewrote consolidation prompt with strict disqualifiers; model no longer saves "created automation X" or other session activity as persistent facts; only durable user-stated preferences, resident info, and device purpose qualify
- **Suggestions: naming issues now require genuine ambiguity** — naming suggestions are only raised when a knowledgeable person could not determine the device's purpose from its name; cosmetic issues (punctuation, capitalisation, hyphenation) are suppressed

## [1.3.0] - 2026-04-09

### Added
- **Suggestions: "Implement →" button** — one-click auto-send to agent directly from a suggestion card; old "Add to chat" renamed to "Edit first" for when you want to review before sending
- **Suggestions: auto-refresh** — interval selector (Off / 30 min / 1 hr / 2 hr / 4 hr) with live countdown; skips if already generating; preference persists across sessions
- **Memory: inline edit and create** — edit any memory file directly in the Memory tab; "New" button to create files without going through chat; 800-char limit counter shown in editor
- **Approval card: diff stats** — each file in a changeset now shows `+N -N` lines changed; new files labelled "new file"

### Fixed
- **Model compliance: read-before-write guard** — if a model (e.g. Gemini Flash) calls `propose_config_changes` without first calling `search_config_files`, the tool now returns an explicit error instructing it to read first; prevents the "only aliases changed" failure pattern
- **Memory consolidation: tool hallucination** — background memory consolidation no longer crashes when a weak model hallucinates calls to `search_config_files`; non-memory tools are silently ignored
- **Performance: registry cache per turn** — device, entity, and area registries are cached within a single agent turn; when Sonnet makes 3+ parallel `search_config_files` calls in one iteration, WS connections drop from 9 to 3

## [1.2.1] - 2026-04-03

### Changed
- **Suggestions: also propose improvements to existing automations/scripts/flows** — prompt now requests a mix of new items and improvements to existing ones (missing conditions, better triggers, edge cases, notifications, redundancy reduction); `type` field (`new` or `improvement`) appears as an amber/indigo badge on each suggestion card

## [1.2.0] - 2026-04-03

### Fixed
- **Mobile cache busting** — HA Ingress and mobile WebViews cache static files by path, ignoring query strings; bundles are now served as `bundle.{version}.js` / `bundle.{version}.css` so each release forces a fresh download
- **Diff green highlighting** — CSS specificity bug caused added lines to render without green background; `.d2h-diff-table .d2h-ins` selector now wins over the blanket transparent override
- **Diff modal RENAMED badge** — `Diff.createPatch` was appending tab-separated `'Original'`/`'Proposed'` suffixes to filenames; diff2html included them in path comparison, making same-file diffs appear as renames. Now passes empty strings.
- **Memory consolidation provider error** — strips trailing assistant turns from history before sending; Azure/Gemini/OpenRouter require conversation to end with a user message
- **Pydantic `validate` field warning** — renamed to `run_validation` with `Field(alias="validate")`; JSON API unchanged
- **Suggestion buttons** ("Add to chat", "Fix in chat", "Fix all") — prefill via React state (`chatPrefill`) instead of custom DOM events, fixing timing race on tab switch

### Added
- **Memory tab** — dedicated top-level tab to browse, read, and delete AI memory files from `.ai_agent_memories/`
- **Suggestion generation log** — live NDJSON progress stream; persists as collapsible after run; last step pulses while active
- **"Prompt sent to AI" panel** — shows model name, total context chars, full system prompt, and scrollable section previews after generation
- **Naming issues auto-dismiss** — "Fix in chat" removes the entity; "Fix all" clears the list
- **"Add to chat" auto-marks applied**
- **Mobile cache busting** — `?v={{ version }}` query string on `bundle.js` and `bundle.css`
- Memory CRUD endpoints: `GET /api/memory`, `GET /api/memory/{filename}`, `DELETE /api/memory/{filename}`

### Changed
- **Entity name word wrap** — naming issue cards use `break-all` / `break-words` to prevent horizontal scroll on mobile
- Removed `dashboards` from default suggestion context
- Suggestion context deduplicates file paths across sections

## [1.1.34] - 2026-04-02

### Fixed
- **Suggestions: resource type selection now controls suggestion type** — selecting only "dashboards" now requests Lovelace dashboard improvements (new cards, missing tiles, layout) instead of automations; the system prompt is built dynamically from the active resource types
- **Suggestions: entity hallucination** — when entity states are included, the LLM is now explicitly instructed to only use entity_ids from the provided list, preventing invented entities (e.g. motion detector in a room that has none)
- **Suggestions: dashboard context** — when "dashboards" is selected, the actual Lovelace YAML is now fetched for each dashboard (not just metadata), so the LLM can see existing cards and avoid re-suggesting them

### Changed
- **Suggestions: temperature** — suggestion calls now default to `0.2` (down from API default ~1.0) to reduce hallucination; configurable via `suggestion_temperature` add-on option

### Added
- **`suggestion_temperature` option** — new add-on config field to control temperature for suggestion generation; defaults to `0.2`

## [1.1.33] - 2026-04-02

### Fixed
- **Diff dark theme** — comprehensive CSS overrides for all diff2html elements (td cells, line numbers, context rows, file list, empty placeholders); switched to line-by-line format for readability on mobile
- **Backup filename collision** — `_create_backup` now uses microsecond-precision timestamps, preventing same-second overwrites when restore is called immediately after write
- **Backup restore parsing** — `restore_backup` and `list_backups` now correctly strip the full `YYYYMMDD_HHMMSS_ffffff` timestamp suffix when recovering the original filename (was splitting on last `_` only, leaving date in the stem)
- **Entity display** — entity state format changed to `"friendly_name"[entity_id]=state` so the AI reads the human-readable name first, improving intent recognition for renamed/customised entities

### Changed
- **Chat message spacing** — unified to `space-y-3` on the message list container; removed per-component `mb-3`/`my-1` margins that were inconsistently applied
- **Revert awareness** — system prompt now explicitly instructs the AI to use `list_backups` + `restore_backup` when asked to undo a change

### Added
- **Test suite** — 83 unit tests for `ConfigurationManager`, `MemoryManager`, and `ConversationManager` (pytest + pytest-asyncio, no HA required); run from `ha-ai-companion/` with `python -m pytest tests/`

## [1.1.32] - 2026-04-01

### Fixed
- **Diff unreadable for JSON files** — both sides of the diff are now pretty-printed before comparison, so a compact one-liner proposed by the LLM diffs correctly against the formatted original

## [1.1.31] - 2026-04-01

### Removed
- **Logs tab** — removed entirely; the Supervisor API journal contained too much unrelated noise (x265, FFmpeg from camera integrations) and couldn't reliably surface HA-specific log entries

## [1.1.30] - 2026-04-01

### Fixed
- **Node-RED diff shows empty original** — approval diff now populates the original side from the `get_nodered_flows` result already in conversation history; `nodered/flow/{tab_id}.json` shows the current tab nodes, `nodered/flows.json` shows the full flows array

## [1.1.29] - 2026-04-01

### Added
- **Node-RED tab-level update** — new `nodered/flow/{tab_id}.json` path in `propose_config_changes` calls `PUT /flow/{id}` to update only one tab's nodes without touching the rest of the flows (safe alternative to the destructive full replace)

### Fixed
- **Node-RED partial edit using flows.json** — system prompt now explicitly instructs: use `nodered/flow/{tab_id}.json` for modifying existing tabs; `nodered/flows.json` is DESTRUCTIVE (replaces everything) and must never be used for partial edits

## [1.1.28] - 2026-04-01

### Fixed
- **Logs tab showing x265/FFmpeg noise** — Supervisor API returns the full HA core container journal including raw subprocess output (camera integrations, Frigate, etc.); now filters to lines starting with a date (`2026-...`) or whitespace-indented continuation lines (tracebacks), stripping all non-HA output

## [1.1.27] - 2026-04-01

### Fixed
- **Stream interruption leaves UI hanging** — when the provider cuts the response mid-stream (e.g. on very long generations), the UI now shows a clear notice: "Response was cut short — ask the AI to continue"
- **LLM generating full file content in text before calling tool** — updated system prompt to instruct the model to call `propose_config_changes` immediately after a brief explanation, not reproduce the full content in text first (this caused 60+ second generations that triggered stream timeouts)

## [1.1.26] - 2026-04-01

### Fixed
- **Logs tab: "Log file not found"** — now reads HA core logs via the Supervisor API (`GET http://supervisor/core/logs`) as primary source; file-based fallback kept for non-add-on mode

## [1.1.25] - 2026-04-01

### Fixed
- **Node-RED 401 error message** — HA long-lived tokens don't work for the Node-RED admin API; error now explains to enable "Leave front door open" in Node-RED add-on config instead

## [1.1.24] - 2026-04-01

### Fixed
- **Node-RED 401 error message** — clarified that `SUPERVISOR_TOKEN` does not work for Node-RED auth (it's a Supervisor API token, not a HA user token); error now says to use a long-lived HA access token from HA Profile

## [1.1.23] - 2026-04-01

### Added
- **Per-phase token usage tracking** — `suggestion_usage_tracking` and `config_usage_tracking` options now exposed in add-on config, allowing independent tracking modes (`stream_options`, `usage`, `disabled`, `default`) for suggestion and config LLMs when using different providers

## [1.1.22] - 2026-04-01

### Fixed
- **Logs tab: "Log file not found"** — HA log file location varies by install type. Now tries multiple candidate paths (`{HA_CONFIG_DIR}/home-assistant.log`, `/homeassistant/home-assistant.log`, `/config/home-assistant.log`) and uses whichever exists.

## [1.1.21] - 2026-04-01

### Fixed
- **Node-RED API authentication (401 errors)** — the HA Node-RED add-on uses HA-based auth; the companion now automatically tries `SUPERVISOR_TOKEN` as the Bearer token before falling back to `NODERED_TOKEN`, so the live API works without any extra configuration

### Added
- **Node-RED flow creation and editing** — companion can now create and deploy Node-RED flows via the approval workflow. Use `propose_config_changes` with `file_path="nodered/new_flow.json"` (adds a tab) or `nodered/flows.json` (full replace). Flow JSON diff is shown before approval, then deployed to Node-RED on confirm.

## [1.1.20] - 2026-04-01

### Added
- **Logs tab** — new tab in the UI to fetch and view `home-assistant.log` with optional keyword filter and configurable line count (100–1000)
- **AI log analysis** — "Analyze with AI" button sends fetched log lines to the suggestion model and returns a structured report: severity badges, component, likely cause, and suggested fix per issue
- **Build automation** — `npm run build` now auto-syncs `src/`, `static/dist/`, and `templates/` to `custom_components/` via postbuild script; `npm run sync` for Python-only changes; `npm run version:bump -- X.Y.Z` bumps all 3 version files atomically

## [1.1.18] - 2026-03-31

### Changed
- **Frontend rewritten with React 18 + Vite + Tailwind CSS** — modern dark UI replacing the old vanilla JS/CSS. Pre-built bundle committed to git so no build step required for end users. Features: streaming chat, diff viewer, approval cards, suggestions tab, session sidebar, cost display, clear-all with memory extraction.

## [1.1.17] - 2026-03-31

### Added
- **`reload_config` tool** — AI can now call `homeassistant.reload_all` after proposing YAML helper changes (input_number, template sensors, scripts), activating them without a full HA restart. System prompt updated to guide the AI to define helpers in YAML and reload after approval.

## [1.1.16] - 2026-03-31

### Added
- **Clear all conversations** — new button in the sessions sidebar that analyzes all sessions for memorable facts (via suggestion model), saves them to memory, then deletes all sessions. Returns count of sessions deleted and memory files saved.

## [1.1.15] - 2026-03-30

### Fixed
- **`max_sessions` limit not applied to existing sessions** — pruning only ran on `save_session`, so changing the limit in config had no effect until a new message was sent. `ConversationManager` now runs a synchronous prune on startup, so sessions are trimmed to the configured limit immediately on every restart.

## [1.1.14] - 2026-03-30

### Fixed
- **No loading indicator between text response and tool execution** — after `message_complete` (the LLM finishes streaming its planning text) there was a silent gap before the next `tool_call` event while the backend prepares the config change. A "Preparing…" loading indicator is now shown immediately after `message_complete` if the send button is still disabled, closing the gap.

## [1.1.13] - 2026-03-30

### Fixed
- **LLM responses collapsed to one line** — `overflow-x: hidden` on `.assistant-message` implicitly forced `overflow-y` from `visible` to `auto` (per CSS spec), causing the bubble to behave as a scrollable box with only one line visible. Removed `overflow-x: hidden` from both `.assistant-message` and `.chat-messages`; horizontal overflow on code blocks is handled correctly by the existing `pre { overflow-x: auto; max-width: 100% }` rules and `word-break: break-word`.

## [1.1.12] - 2026-03-30

### Fixed
- **Naming suggestions re-appear after rename** — `_format_entity_states_compact` was serialising entities as `entity_id=state` only, so the LLM judged names purely from the entity_id slug and ignored the `friendly_name` entirely. A renamed entity (e.g. `switch.pompa` → friendly name "Water Pump") was still flagged because the slug still looks bad. Now formatted as `entity_id["friendly_name"]=state` so the LLM evaluates the actual display name.

## [1.1.11] - 2026-03-30

### Fixed
- **Dashboard editing: stale content after approved edit** — `_lovelace_cache` in `AgentTools` was never cleared after a dashboard write; if the AI read a dashboard, the user approved changes, and the AI tried to read it again in the same session, it got the pre-edit cached YAML and proposed wrong edits. Cache is now invalidated per-dashboard immediately after a successful write in `process_approval`.
- **Dashboard YAML formatting inconsistency** — the YAML dump in `_get_lovelace_config` now uses the same ruamel.yaml settings as `ConfigurationManager` (`preserve_quotes`, `indent mapping=2 sequence=2 offset=2`), so the YAML shown to the AI is consistent with what gets written back.

## [1.1.10] - 2026-03-29

### Fixed
- **Session history missing tool results** — `switchSession` now renders `tool` role messages as expandable result cards; expired `propose_config_changes` changesets shown with a notice instead of an interactive approval card
- **Cross-session response contamination** — switching sessions while a response is in-flight now calls `resetWebSocket()` to abort the old connection; the old response can no longer land in the new session
- **API error when sending to historical session** — `system_info` role messages (display-only) are now filtered from the conversation history before it is sent to the backend, preventing unknown-role errors from the LLM API
- **Loading indicator gap** — after a tool result and before the next AI iteration there is now a persistent loading indicator; previously the UI went silent for several seconds between the last tool result and the approval card appearing
- **Mobile overflow** — assistant message bubbles, code blocks, context-source chip row, and naming-issue cards no longer extend beyond the viewport width on narrow screens

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
