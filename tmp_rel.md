### Fixed
- **Summarization crash on empty model output** — long-conversation summarization called `.strip()` on `None` content (and on empty `choices`), failing every turn so history never compressed. Now skips the API call when there's nothing to summarize and guards null content / empty choices.

### Added — memory system hardening
- **Mojibake auto-repair** — memory content double-encoded by some models (`OÅwietlenie`, `OgrÃ³d`, `âï¸`) is repaired on write (conservative latin-1 round-trip); existing corrupted files repaired once at startup.
- **Entity reference validation** — new `audit_memory_entities` tool checks entity_ids cited in memories against the live HA registry: flags missing entities and likely domain renames (`light.kinkiety_garaz` → `switch.kinkiety_garaz`).
- **Duplicate-memory detection** — `save_memory` surfaces overlapping existing files so the agent updates instead of forking near-duplicates.
- **Memory schema tags** — files now carry `type:` and auto-extracted `entities:` markers (stripped from context) powering validation, dedup, and typed recall.

195 tests passing.
