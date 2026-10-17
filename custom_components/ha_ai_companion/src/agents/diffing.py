"""Server-side diffs for proposed changesets.

The diff is computed from the exact content the proposal was built on, so the
UI never has to reconstruct the "before" side from chat history.
"""
import difflib
import hashlib
import json
from io import StringIO
from typing import Any, Dict, List, Optional

from ruamel.yaml import YAML

# Paths that are not plain files under /config — no on-disk hash to compare.
VIRTUAL_PREFIXES = ("nodered/", "devices/", "entities/", "areas/", "lovelace/")


def is_disk_file(file_path: str) -> bool:
    return file_path != "lovelace.yaml" and not file_path.startswith(VIRTUAL_PREFIXES)


def content_hash(text: Optional[str]) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _normalize(text: Optional[str], file_path: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if file_path.endswith(".json") and text.strip()[:1] in ("{", "["):
        try:
            text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        except ValueError:
            pass
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def unified_diff(file_path: str, old: Optional[str], new: Optional[str]) -> str:
    old_n, new_n = _normalize(old, file_path), _normalize(new, file_path)
    lines = difflib.unified_diff(
        old_n.splitlines(keepends=True),
        new_n.splitlines(keepends=True),
        fromfile=f"a/{file_path}" if old_n else "/dev/null",
        tofile=f"b/{file_path}",
        n=3,
    )
    return "".join(lines)


def _load_yaml(text: str) -> Any:
    try:
        return YAML(typ="safe").load(StringIO(text)) if text and text.strip() else None
    except Exception:
        return None


def _keyed(data: Any) -> Optional[Dict[str, Any]]:
    """Map items to a stable key: list items by id/alias, mappings by key."""
    if isinstance(data, dict):
        return {str(k): v for k, v in data.items()}
    if isinstance(data, list) and data and all(isinstance(i, dict) for i in data):
        out = {}
        for i, item in enumerate(data):
            key = item.get("id") or item.get("alias") or f"#{i}"
            out[str(key)] = item
        return out
    return None


def _label(key: str, item: Any) -> str:
    alias = item.get("alias") if isinstance(item, dict) else None
    return f"{alias} ({key})" if alias and alias != key else key


def items_summary(file_path: str, old: Optional[str], new: Optional[str]) -> Optional[Dict[str, List[str]]]:
    """Added/changed/removed items for list- or mapping-shaped YAML files."""
    if not file_path.endswith((".yaml", ".yml")) or file_path == "lovelace.yaml" or file_path.startswith("lovelace/"):
        return None
    old_k, new_k = _keyed(_load_yaml(old or "")), _keyed(_load_yaml(new or ""))
    if old_k is None and new_k is None:
        return None
    old_k, new_k = old_k or {}, new_k or {}
    summary = {
        "added": [_label(k, new_k[k]) for k in new_k if k not in old_k],
        "changed": [_label(k, new_k[k]) for k in new_k if k in old_k and new_k[k] != old_k[k]],
        "removed": [_label(k, old_k[k]) for k in old_k if k not in new_k],
    }
    return summary if any(summary.values()) else None


def describe_change(file_path: str, old: Optional[str], new: Optional[str]) -> Dict[str, Any]:
    """Everything the UI needs to render one file of a changeset."""
    diff = unified_diff(file_path, old, new)
    added = sum(1 for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff.splitlines() if l.startswith("-") and not l.startswith("---"))
    return {
        "file_path": file_path,
        "diff": diff,
        "added": added,
        "removed": removed,
        "is_new_file": not (old or "").strip(),
        "items": items_summary(file_path, old, new),
    }
