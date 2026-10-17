"""Applied-changeset history.

Each applied changeset is saved as one JSON file holding the exact "before"
content of every file it changed, so it can be restored later through the
normal approval flow. Retention is per changeset (age + count), independent of
the per-file backup rotation in ConfigurationManager.
"""
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .diffing import content_hash, is_disk_file, items_summary

logger = logging.getLogger(__name__)


def restorable(file_path: str, before: str) -> bool:
    # Created-from-nothing files and new Node-RED flows have no "before" to go back to.
    if not (before or "").strip() or before.strip() == "{}":
        return False
    return is_disk_file(file_path) or file_path == "lovelace.yaml" or file_path.startswith(("lovelace/", "nodered/flow/"))


class ChangesetHistory:
    def __init__(self, directory: str, max_age_days: int = 30, max_records: int = 100):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.max_age_s = max_age_days * 86400
        self.max_records = max_records

    def _path(self, changeset_id: str) -> Path:
        safe = "".join(c for c in changeset_id if c.isalnum() or c in "-_")
        return self.dir / f"{safe}.json"

    def record(self, changeset_id: str, file_changes: List[Dict[str, Any]], applied_files: List[str],
               restored_from: Optional[str] = None) -> None:
        files = []
        for fc in file_changes:
            path = fc["file_path"]
            if path not in applied_files:
                continue
            before = fc.get("original_content", "")
            files.append({
                "file_path": path,
                "before": before,
                "after_hash": content_hash(fc["new_content"]),
                "restorable": restorable(path, before),
                "items": items_summary(path, before, fc["new_content"]),
            })
        if not files:
            return
        rec = {
            "id": changeset_id,
            "applied_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "files": files,
        }
        if restored_from:
            rec["restored_from"] = restored_from
        self._path(changeset_id).write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        self._prune()

    def get(self, changeset_id: str) -> Optional[Dict[str, Any]]:
        p = self._path(changeset_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return None

    def list(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Newest first, without the stored file content."""
        out = []
        for p in sorted(self.dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                continue
            out.append({
                "id": rec["id"],
                "applied_at": rec["applied_at"],
                **({"restored_from": rec["restored_from"]} if rec.get("restored_from") else {}),
                "files": [
                    {k: f[k] for k in ("file_path", "restorable", "items") if f.get(k) is not None}
                    for f in rec["files"]
                ],
            })
        return out

    def _prune(self) -> None:
        now = time.time()
        records = sorted(self.dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
        for i, p in enumerate(records):
            if i >= self.max_records or now - p.stat().st_mtime > self.max_age_s:
                try:
                    p.unlink()
                except OSError as e:
                    logger.warning(f"Could not prune changeset record {p.name}: {e}")
