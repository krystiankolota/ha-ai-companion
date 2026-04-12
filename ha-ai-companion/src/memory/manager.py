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

    MAX_CONTEXT_CHARS = 6000   # ~1500 tokens — keep memory injection lean
    MAX_FILES = 25             # Hard cap on total memory files
    MAX_FILE_CHARS = 1500      # Max content chars per file (excluding metadata header)

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

    async def get_context(self) -> str:
        """
        Return memory files sorted by most-recently-updated first, up to
        MAX_CONTEXT_CHARS (~1500 tokens).  Newer memories take priority.
        Files older than MAX_CONTEXT_AGE_DAYS are skipped.
        Timestamp HTML comments are stripped before injection.
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
            files = [p.name for p in paths]
        except Exception:
            files = await self.list_files()
        if not files:
            return ""

        sections: List[str] = []
        total_chars = 0

        for fname in files:
            raw = await self.read_file(fname)
            if not raw:
                continue
            # Strip HTML timestamp comment — it's noise in the AI's context window
            content = re.sub(r"<!--.*?-->\n?", "", raw, flags=re.DOTALL).strip()
            if not content:
                continue
            section = f"### {fname}\n{content}"
            section_chars = len(section)

            if total_chars + section_chars > self.MAX_CONTEXT_CHARS:
                remaining = self.MAX_CONTEXT_CHARS - total_chars - len(f"### {fname}\n") - 50
                if remaining > 200:
                    section = f"### {fname}\n{content[:remaining]}\n*(truncated)*"
                    sections.append(section)
                break

            sections.append(section)
            total_chars += section_chars

        if not sections:
            return ""

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
