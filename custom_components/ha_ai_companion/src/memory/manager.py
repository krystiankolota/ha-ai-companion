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
from typing import Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Filename sanitisation: allow only safe chars
_SAFE_FILENAME = re.compile(r'[^a-zA-Z0-9_\-]')

# Generic English stop-words that carry no topic signal for HA queries
_STOP_WORDS: FrozenSet[str] = frozenset({
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "had",
    "her", "was", "one", "our", "out", "his", "how", "did", "has", "got",
    "him", "its", "let", "put", "say", "she", "too", "use", "will", "with",
    "that", "this", "they", "from", "have", "been", "also", "when", "what",
    "make", "like", "time", "just", "know", "into", "your", "good", "some",
    "than", "them", "want", "look", "more", "well", "please", "could",
    "would", "should", "about", "there", "here", "their", "then", "been",
    "were", "very", "only", "over", "such", "need", "which", "each", "both",
    "does", "done", "much", "many", "other", "while", "same",
})

# Category priority scores — controls which files are always included
_CATEGORY_PRIORITY: Dict[str, float] = {
    "identity_":    8.0,   # Home layout/rooms — always relevant
    "preference_":  7.0,   # User preferences — almost always relevant
    "correction_":  6.0,   # Corrections to facts — should always override stale info
    "ecosystem_":   3.0,   # Device relationships / integration facts
    "device_":      2.5,   # Device nicknames / roles
    "user_":        2.0,   # User patterns
    "pattern_":     1.5,   # Recurring schedules
    "baseline_":    1.0,   # Sensor ranges
}

# Score threshold: files below this are excluded when a non-empty query is given.
# Files with a category priority that already meets the threshold are always kept.
_RELEVANCE_THRESHOLD = 2.5


def _extract_keywords(text: str) -> FrozenSet[str]:
    """
    Extract meaningful words from text for relevance scoring.
    Returns lowercase words of 3–30 chars, minus stop-words.
    """
    words = re.split(r'\W+', text.lower())
    return frozenset(
        w for w in words
        if 3 <= len(w) <= 30 and w not in _STOP_WORDS
    )


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

    MAX_CONTEXT_CHARS = 6000   # ~1500 tokens — keep memory injection lean
    MAX_FILES = 25             # Hard cap on total memory files
    MAX_FILE_CHARS = 800       # Max content chars per file (excluding metadata header)

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

    async def write_file(self, filename: str, content: str) -> bool:
        """
        Write (create or overwrite) a memory file.

        Enforces per-file and total-file limits before writing:
        - Rejects content exceeding MAX_FILE_CHARS
        - Deletes the oldest file when MAX_FILES would be exceeded

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

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            full_content = f"<!-- updated: {timestamp} -->\n{content.strip()}\n"
            path.write_text(full_content, encoding="utf-8")
            logger.info("MemoryManager wrote %s (%d chars)", path.name, len(full_content))
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

    @staticmethod
    def _score_file(
        filename: str,
        content: str,
        keywords: FrozenSet[str],
        age_days: float,
    ) -> float:
        """
        Score a memory file for relevance to a query.

        Scoring factors:
        - Category priority: identity/preference files always get a high base score
        - Keyword match: words from the query appearing in filename or content
        - Recency: recently modified files get a small bonus

        Higher score = inject first.
        """
        score = 0.0
        stem = filename.lower()

        # Category-based base score
        for prefix, priority in _CATEGORY_PRIORITY.items():
            if stem.startswith(prefix):
                score += priority
                break  # only first matching prefix counts

        # Keyword relevance — keywords extracted from the user's query
        if keywords:
            # Extract searchable text from filename (e.g. "device_pump.md" → "device pump")
            fname_words = set(re.split(r'[_.\-]+', stem.replace(".md", "")))
            content_lower = content.lower()
            for kw in keywords:
                if kw in fname_words:
                    score += 4.0   # Strong signal: keyword matches the file's topic slug
                elif kw in content_lower:
                    score += 1.5   # Weaker: keyword appears somewhere in content

        # Recency bonus (decays over 60 days)
        if age_days <= 7:
            score += 2.0
        elif age_days <= 30:
            score += 1.0
        elif age_days <= 60:
            score += 0.5

        return score

    async def get_context(self, query: str = "") -> str:
        """
        Return relevant memory files for the given query, up to MAX_CONTEXT_CHARS.

        When *query* is provided the files are scored by relevance (keyword
        match + category priority + recency).  Files that score below
        _RELEVANCE_THRESHOLD are excluded unless they have high category
        priority (identity_, preference_, correction_).

        When *query* is empty all non-stale files are returned ordered by
        recency (backward-compatible behaviour for consolidation pass).
        """
        now_ts = time.time()

        # Collect all candidate paths with their metadata
        try:
            raw_paths = [p for p in self.memory_dir.glob("*.md") if p.is_file()]
        except Exception:
            raw_paths = []

        if not raw_paths:
            return ""

        keywords = _extract_keywords(query) if query else frozenset()
        has_query = bool(keywords)

        # Build scored list: (score, path, stripped_content)
        scored: List[Tuple[float, Path, str]] = []
        for p in raw_paths:
            try:
                stat = p.stat()
                age_days = (now_ts - stat.st_mtime) / 86400.0
            except Exception:
                age_days = 999.0

            # Hard cap: skip very stale files regardless of query
            if age_days > self.MAX_CONTEXT_AGE_DAYS:
                continue

            # Read content for scoring (needed even for non-query case for filtering)
            raw = None
            try:
                raw = p.read_text(encoding="utf-8")
            except Exception:
                continue

            # Strip timestamp comment before scoring and injection
            content = re.sub(r"<!--.*?-->\n?", "", raw, flags=re.DOTALL).strip()
            if not content:
                continue

            score = self._score_file(p.name, content, keywords, age_days)

            # When a query is present, skip low-relevance files
            if has_query and score < _RELEVANCE_THRESHOLD:
                logger.debug("Memory skip (score=%.1f): %s", score, p.name)
                continue

            scored.append((score, p, content))

        if not scored:
            return ""

        # Sort: highest score first; tie-break by most recently modified
        scored.sort(key=lambda t: (t[0], t[1].stat().st_mtime), reverse=True)

        included_count = 0
        sections: List[str] = []
        total_chars = 0

        for score, p, content in scored:
            section = f"### {p.name}\n{content}"
            section_chars = len(section)

            if total_chars + section_chars > self.MAX_CONTEXT_CHARS:
                remaining = self.MAX_CONTEXT_CHARS - total_chars - len(f"### {p.name}\n") - 50
                if remaining > 200:
                    section = f"### {p.name}\n{content[:remaining]}\n*(truncated)*"
                    sections.append(section)
                break

            sections.append(section)
            total_chars += section_chars
            included_count += 1

        if not sections:
            return ""

        if has_query:
            logger.debug(
                "Memory context: %d/%d files included (query keywords: %s)",
                included_count, len(raw_paths), ", ".join(sorted(keywords)[:10])
            )

        return (
            "## Agent Memory (persistent knowledge from previous sessions)\n\n"
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
