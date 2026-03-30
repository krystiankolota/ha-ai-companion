"""
Conversation session persistence for HA AI Companion.

Saves each conversation as a JSON file in /config/.ai_agent_sessions/.
Sessions are automatically pruned to MAX_SESSIONS most-recent entries.
"""
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_SAFE_ID = re.compile(r'[^a-zA-Z0-9_\-]')


class ConversationManager:
    """Manages persistent conversation sessions stored as JSON files."""

    DEFAULT_MAX_SESSIONS = 50

    def __init__(self, sessions_dir: str, max_sessions: int = DEFAULT_MAX_SESSIONS):
        self.sessions_dir = Path(sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.MAX_SESSIONS = max(1, max_sessions)
        logger.info("ConversationManager initialised at %s (max_sessions=%d)", self.sessions_dir, self.MAX_SESSIONS)
        # Enforce limit immediately so a config change takes effect on restart
        self._prune_sync()

    def _path(self, session_id: str) -> Path:
        safe = _SAFE_ID.sub('', session_id)[:64] or 'session'
        return self.sessions_dir / f"{safe}.json"

    @staticmethod
    def _auto_title(messages: List[Dict]) -> str:
        for msg in messages:
            if msg.get('role') == 'user' and msg.get('content'):
                title = str(msg['content'])[:60].strip()
                if title:
                    return title
        return "New conversation"

    async def list_sessions(self) -> List[Dict]:
        """Return sessions sorted by most-recently updated."""
        sessions = []
        try:
            paths = sorted(
                self.sessions_dir.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )
            for path in paths:
                try:
                    data = json.loads(path.read_text(encoding='utf-8'))
                    visible = [m for m in data.get('messages', []) if m.get('role') in ('user', 'assistant')]
                    sessions.append({
                        'id': data.get('id', path.stem),
                        'title': data.get('title', 'Untitled'),
                        'created_at': data.get('created_at', ''),
                        'updated_at': data.get('updated_at', ''),
                        'message_count': len(visible),
                    })
                except Exception:
                    continue
        except Exception as e:
            logger.error("list_sessions error: %s", e)
        return sessions

    async def save_session(self, session_id: str, title: str, messages: List[Dict]) -> bool:
        """Create or overwrite a session file."""
        path = self._path(session_id)
        try:
            now = datetime.now().isoformat()
            existing_created = now
            if path.exists():
                try:
                    existing_created = json.loads(path.read_text(encoding='utf-8')).get('created_at', now)
                except Exception:
                    pass

            data = {
                'id': session_id,
                'title': title,
                'created_at': existing_created,
                'updated_at': now,
                'messages': messages,
            }
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            await self._prune()
            return True
        except Exception as e:
            logger.error("save_session(%s) error: %s", session_id, e)
            return False

    async def load_session(self, session_id: str) -> Optional[Dict]:
        """Return full session dict or None if not found."""
        path = self._path(session_id)
        try:
            if path.exists():
                return json.loads(path.read_text(encoding='utf-8'))
        except Exception as e:
            logger.error("load_session(%s) error: %s", session_id, e)
        return None

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session file. Returns True if deleted."""
        path = self._path(session_id)
        try:
            if path.exists():
                path.unlink()
                logger.info("Deleted session %s", session_id)
                return True
        except Exception as e:
            logger.error("delete_session(%s) error: %s", session_id, e)
        return False

    def _prune_sync(self):
        """Synchronous prune — called on init so a config change takes effect on restart."""
        try:
            paths = sorted(
                self.sessions_dir.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )
            for old in paths[self.MAX_SESSIONS:]:
                old.unlink(missing_ok=True)
                logger.info("Pruned old session on startup: %s", old.name)
        except Exception:
            pass

    async def _prune(self):
        """Remove oldest sessions beyond MAX_SESSIONS."""
        try:
            paths = sorted(
                self.sessions_dir.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )
            for old in paths[self.MAX_SESSIONS:]:
                old.unlink(missing_ok=True)
                logger.debug("Pruned old session: %s", old.name)
        except Exception:
            pass
