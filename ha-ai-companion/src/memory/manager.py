"""
Memory Manager for HA AI Companion

Stores agent memories as markdown files that persist across sessions.
Memories are organised by topic (e.g. home_structure.md, preferences.md)
and are injected into the system prompt as contextual background.

Design goals:
- Self-improving: the agent can add/update/delete memory files at any time
- Auto-maintaining: old or contradicted information is replaced, not appended
- Transparent: all memories are plain markdown files the user can inspect/edit
"""
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Filename sanitisation: allow only safe chars
_SAFE_FILENAME = re.compile(r'[^a-zA-Z0-9_\-]')


def _sanitise(filename: str) -> str:
    """Return a safe filename (no path traversal, only .md extension)."""
    # Strip directory components
    name = Path(filename).name
    # Remove extension so we can force .md
    stem = Path(name).stem
    # Replace unsafe chars with underscore
    sanitised_stem = _SAFE_FILENAME.sub('_', stem).strip('_') or 'memory'
    result = f"{sanitised_stem}.md"
    if result != f"{stem}.md":
        logger.warning(f"Memory filename sanitized: '{filename}' → '{result}'")
    return result


class MemoryManager:
    """
    Manages persistent markdown memory files for the AI agent.

    Files are stored in `memory_dir` (default: /config/.ai_agent_memories/).
    Each file is a markdown document on a single topic.  The agent is
    expected to keep them concise and up-to-date rather than growing them
    indefinitely.

    Anti-bloat rules (enforced here, not just in the prompt):
    - MAX_FILES: hard cap on number of files; oldest file is deleted when exceeded
    - MAX_FILE_CHARS: per-file content size limit; write is rejected if exceeded
    - MAX_CONTEXT_CHARS: total context injected into system prompt
    """

    MAX_CONTEXT_CHARS = 3500   # ~875 tokens — reduced from 6000; relevance gate keeps quality
    MAX_CRITICAL_CHARS = 1500  # Reserved budget for critical-marked files (always injected)
    MAX_FILES = 25             # Hard cap on total memory files
    MAX_FILE_CHARS = 800       # Max content chars per file — matches system prompt rule

    def __init__(self, memory_dir: str):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        logger.info("MemoryManager initialised at %s", self.memory_dir)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _path(self, filename: str) -> Path:
        return self.memory_dir / _sanitise(filename)

    # ------------------------------------------------------------------
    # Public API (async wrappers around synchronous file I/O so they can
    # be awaited inside async tool functions without blocking)
    # ------------------------------------------------------------------

    async def list_files(self) -> List[str]:
        """Return sorted list of memory filenames (basename only)."""
        try:
            files = sorted(
                p.name for p in self.memory_dir.glob("*.md") if p.is_file()
            )
            return files
        except Exception as exc:
            logger.error("MemoryManager.list_files error: %s", exc)
            return []

    async def read_file(self, filename: str) -> Optional[str]:
        """Read and return the content of a memory file, or None if missing."""
        path = self._path(filename)
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except Exception as exc:
            logger.error("MemoryManager.read_file(%s) error: %s", filename, exc)
            return None

    async def write_file(self, filename: str, content: str, critical: Optional[bool] = None) -> bool:
        """
        Write (create or overwrite) a memory file.

        Enforces per-file and total-file limits before writing:
        - Rejects content exceeding MAX_FILE_CHARS
        - Deletes the oldest file when MAX_FILES would be exceeded

        Args:
            critical: Controls the ``<!-- critical -->`` marker:
                - None (default): preserve the marker if the file already has one.
                - True: add the marker (file always injected into every session).
                - False: remove the marker (demote from critical tier).

        Returns True on success, False on validation failure or I/O error.
        """
        path = self._path(filename)

        # Per-file size guard
        if len(content.strip()) > self.MAX_FILE_CHARS:
            logger.warning(
                "MemoryManager.write_file(%s) rejected: content %d chars exceeds MAX_FILE_CHARS %d",
                path.name, len(content.strip()), self.MAX_FILE_CHARS
            )
            return False

        try:
            # File count guard: evict oldest file if at cap and this is a new file
            if not path.exists():
                existing = sorted(
                    self.memory_dir.glob("*.md"),
                    key=lambda p: p.stat().st_mtime
                )
                while len(existing) >= self.MAX_FILES:
                    oldest = existing.pop(0)
                    oldest.unlink()
                    logger.warning("MemoryManager evicted oldest file %s (MAX_FILES=%d reached)", oldest.name, self.MAX_FILES)

            # Determine critical marker
            if critical is None:
                # Preserve existing marker when caller doesn't specify
                try:
                    existing_raw = path.read_text(encoding="utf-8") if path.exists() else ""
                except Exception:
                    existing_raw = ""
                critical_marker = "<!-- critical -->\n" if "<!-- critical -->" in existing_raw else ""
            elif critical:
                critical_marker = "<!-- critical -->\n"
            else:
                critical_marker = ""

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            full_content = f"{critical_marker}<!-- updated: {timestamp} -->\n{content.strip()}\n"
            path.write_text(full_content, encoding="utf-8")
            logger.info("MemoryManager wrote %s (%d chars, critical=%s)", path.name, len(full_content), critical)
            return True
        except Exception as exc:
            logger.error("MemoryManager.write_file(%s) error: %s", filename, exc)
            return False

    async def delete_file(self, filename: str) -> bool:
        """Delete a memory file. Returns True if deleted, False if not found."""
        path = self._path(filename)
        try:
            path.unlink()
            logger.info("MemoryManager deleted %s", path.name)
            return True
        except FileNotFoundError:
            return False
        except Exception as exc:
            logger.error("MemoryManager.delete_file(%s) error: %s", filename, exc)
            return False

    # Files older than this many days are skipped from context injection (still exist on disk)
    MAX_CONTEXT_AGE_DAYS = int(os.environ.get("MEMORY_MAX_AGE_DAYS", "180"))

    # Prefixes whose files are always injected regardless of query relevance (Tier 2)
    _ALWAYS_INJECT_PREFIXES = ("preference_", "identity_")

    async def get_context(self, query: str = "") -> str:
        """
        Return memory files up to MAX_CONTEXT_CHARS using 3-tier injection priority.

        Tier 1 — Critical (``<!-- critical -->`` marker): always injected first,
            guaranteed budget of MAX_CRITICAL_CHARS chars regardless of query.
        Tier 2 — Priority (``preference_``/``identity_`` prefix): always injected
            after critical files, using remaining total budget.
        Tier 3 — Gated: scored by keyword overlap with ``query``; zero-score files
            are skipped when a query is provided.

        When ``query`` is empty, all non-stale files are treated as relevant.
        Files older than MAX_CONTEXT_AGE_DAYS are always excluded.
        All HTML comments (including markers) are stripped before injection.
        """
        now_ts = time.time()
        try:
            paths = sorted(
                (p for p in self.memory_dir.glob("*.md") if p.is_file()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,  # newest first
            )
            # Filter out very stale files — they waste tokens on irrelevant old facts
            paths = [
                p for p in paths
                if (now_ts - p.stat().st_mtime) / 86400.0 <= self.MAX_CONTEXT_AGE_DAYS
            ]
        except Exception:
            paths = []

        if not paths:
            return ""

        # Keyword set from query (lower-case tokens, 3+ chars)
        query_tokens: set = set()
        if query:
            query_tokens = {w.lower() for w in re.split(r'\W+', query) if len(w) >= 3}

        def _is_priority(name: str) -> bool:
            return any(name.startswith(p) for p in self._ALWAYS_INJECT_PREFIXES)

        def _relevance(name: str, content: str) -> int:
            if not query_tokens:
                return 1  # no query — treat all as relevant
            haystack = (name + " " + content).lower()
            return sum(1 for tok in query_tokens if tok in haystack)

        # Read and score all candidate files.
        # Tuple: (is_critical, score, priority, mtime, fname, content)
        scored: List[Tuple[bool, int, bool, float, str, str]] = []
        for p in paths:
            fname = p.name
            raw = await self.read_file(fname)
            if not raw:
                continue
            # Detect critical marker BEFORE stripping comments
            is_critical = "<!-- critical -->" in raw
            content = re.sub(r"<!--.*?-->\n?", "", raw, flags=re.DOTALL).strip()
            if not content:
                continue
            priority = _is_priority(fname)
            score = _relevance(fname, content)
            # Skip non-critical, non-priority files with zero relevance when a query exists
            if not is_critical and not priority and query_tokens and score == 0:
                continue
            scored.append((is_critical, score, priority, p.stat().st_mtime, fname, content))

        if not scored:
            return ""

        # Sort: Tier 1 (critical) → Tier 2 (priority) → Tier 3 (relevance score DESC, mtime DESC)
        scored.sort(key=lambda x: (not x[0], not x[2], -x[1], -x[3]))

        sections: List[str] = []
        critical_chars = 0
        total_chars = 0

        for is_critical, _score, _priority, _mtime, fname, content in scored:
            stem = fname[:-3] if fname.endswith(".md") else fname
            header = f"[{stem}]\n"
            section = header + content
            section_chars = len(section)

            if is_critical:
                # Critical files get their own reserved budget
                if critical_chars + section_chars <= self.MAX_CRITICAL_CHARS:
                    sections.append(section)
                    critical_chars += section_chars
                    total_chars += section_chars
                else:
                    remaining = self.MAX_CRITICAL_CHARS - critical_chars - len(header) - 50
                    if remaining > 200:
                        section = header + content[:remaining] + "\n*(truncated)*"
                        sections.append(section)
                        critical_chars = self.MAX_CRITICAL_CHARS
                        total_chars += len(section)
                    # else: critical budget exhausted — skip remainder of this file
            else:
                # Non-critical files fill whatever total budget remains
                if total_chars + section_chars > self.MAX_CONTEXT_CHARS:
                    remaining = self.MAX_CONTEXT_CHARS - total_chars - len(header) - 50
                    if remaining > 200:
                        section = header + content[:remaining] + "\n*(truncated)*"
                        sections.append(section)
                    break
                sections.append(section)
                total_chars += section_chars

        if not sections:
            return ""

        return (
            "## Memory\n\n"
            + "\n\n".join(sections)
            + "\n\n---\n"
        )

    async def get_stats(self) -> Dict:
        """
        Return audit stats for all memory files: name, size, age in days.
        Used by the agent to review memory health and decide what to prune.
        """
        stats = []
        now = datetime.now()
        try:
            for p in sorted(self.memory_dir.glob("*.md")):
                if not p.is_file():
                    continue
                mtime = datetime.fromtimestamp(p.stat().st_mtime)
                age_days = (now - mtime).days
                stats.append({
                    "filename": p.name,
                    "chars": p.stat().st_size,
                    "age_days": age_days,
                    "stale": age_days > 90,
                })
        except Exception as exc:
            logger.error("MemoryManager.get_stats error: %s", exc)
        return {
            "files": stats,
            "total": len(stats),
            "max_files": self.MAX_FILES,
            "max_file_chars": self.MAX_FILE_CHARS,
        }

    async def get_all_files(self) -> Dict[str, str]:
        """Return dict of {filename: content} for all memory files."""
        files = await self.list_files()
        result: Dict[str, str] = {}
        for fname in files:
            content = await self.read_file(fname)
            if content is not None:
                result[fname] = content
        return result
