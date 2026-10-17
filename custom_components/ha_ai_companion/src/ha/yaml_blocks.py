"""Text-level navigation and editing of top-level YAML items.

Works on raw text so comments, !include/!secret/!input tags and the user's
formatting survive. Only the edited item's lines change.
"""
import textwrap
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

import yaml


class _TolerantLoader(yaml.SafeLoader):
    """SafeLoader that turns any !tag into a plain value instead of failing."""


def _construct_tagged(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return f"!{suffix} {node.value}"
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_mapping(node, deep=True)


_TolerantLoader.add_multi_constructor("!", _construct_tagged)


def load_tolerant(text: str) -> Any:
    return yaml.load(text, Loader=_TolerantLoader)  # noqa: S506 — SafeLoader subclass


@dataclass
class Block:
    comment_start: int  # 0-based index of the first leading comment line (== start when none)
    start: int          # 0-based index of the item's first line
    end: int            # 0-based exclusive end
    value: Any          # parsed item (list item or {key: value})
    key: Optional[str]  # mapping key for mapping files, None for list items


def _is_top_level_start(line: str) -> bool:
    if not line or line[0] in " \t#" or line.startswith(("---", "...")):
        return False
    return line.strip() != ""


def split_blocks(text: str) -> Tuple[str, List[Block]]:
    """Return (kind, blocks). kind is 'list', 'mapping', or 'other'."""
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if _is_top_level_start(ln)]
    if not starts:
        return "other", []
    kind = "list" if lines[starts[0]].startswith("-") else "mapping"
    blocks: List[Block] = []
    for n, s in enumerate(starts):
        cs = s
        while cs > 0 and lines[cs - 1].startswith("#") and (not blocks or cs - 1 > blocks[-1].start):
            cs -= 1
        if blocks and cs < blocks[-1].end:
            blocks[-1].end = cs
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        chunk = "\n".join(lines[s:end])
        try:
            parsed = load_tolerant(chunk)
        except yaml.YAMLError:
            parsed = None
        key = None
        value = parsed
        if kind == "list":
            value = parsed[0] if isinstance(parsed, list) and parsed else None
        elif isinstance(parsed, dict) and len(parsed) == 1:
            key = str(next(iter(parsed)))
        blocks.append(Block(comment_start=cs, start=s, end=end, value=value, key=key))
    # Trailing blank lines stay with their block; that is fine for replace/remove.
    return kind, blocks


def item_label(block: Block) -> str:
    v = block.value
    if block.key is not None:
        inner = v.get(block.key) if isinstance(v, dict) else None
        alias = inner.get("alias") if isinstance(inner, dict) else None
        return f"{block.key} ({alias})" if alias else block.key
    if isinstance(v, dict):
        name = v.get("alias") or v.get("name") or v.get("platform") or ""
        ident = v.get("id")
        if name and ident is not None:
            return f"{name} (id={ident})"
        return str(name or (f"id={ident}" if ident is not None else "item"))
    return "item"


def outline(text: str, depth: int = 1, big_block_lines: int = 40) -> List[dict]:
    """Top-level items with 1-based line ranges. With depth=2, large blocks also list their children."""
    lines = text.splitlines()
    _kind, blocks = split_blocks(text)
    out = []
    for b in blocks:
        entry = {"lines": f"{b.start + 1}-{_last_content_line(lines, b) + 1}", "item": item_label(b)}
        if depth >= 2 and (b.end - b.start) > big_block_lines:
            entry["children"] = _children(lines, b)
        out.append(entry)
    return out


def _last_content_line(lines: List[str], b: Block) -> int:
    i = b.end - 1
    while i > b.start and not lines[i].strip():
        i -= 1
    return i


def _children(lines: List[str], b: Block) -> List[dict]:
    body = [(i, ln) for i, ln in enumerate(lines[b.start + 1:b.end], b.start + 1)
            if ln.strip() and not ln.lstrip().startswith("#")]
    if not body:
        return []
    indent = min(len(ln) - len(ln.lstrip()) for _, ln in body)
    starts = [i for i, ln in body if len(ln) - len(ln.lstrip()) == indent]
    kids = []
    for n, s in enumerate(starts):
        e = (starts[n + 1] - 1) if n + 1 < len(starts) else _last_content_line(lines, b)
        kids.append({"lines": f"{s + 1}-{e + 1}", "item": lines[s].strip()[:80]})
    return kids


def read_range(text: str, start: int, end: int, max_lines: int = 400) -> dict:
    """1-based inclusive line slice with line numbers."""
    lines = text.splitlines()
    total = len(lines)
    start = max(1, int(start))
    end = min(total, int(end), start + max_lines - 1)
    numbered = "\n".join(f"{n}: {lines[n - 1]}" for n in range(start, end + 1))
    return {"start": start, "end": end, "total_lines": total, "content": numbered}


# ---------------------------------------------------------------------------
# List-item editing (automations.yaml, scenes.yaml, merge-list split files)
# ---------------------------------------------------------------------------

def _dedent2(line: str) -> str:
    return line[2:] if line.startswith("  ") else line.lstrip(" ")


def normalize_item_text(item_text: str, new_id: str) -> Tuple[str, dict]:
    """Turn one item (mapping or single-item list) into '- ...' list-item text.

    Adds `id` when missing. Returns (list_item_text, parsed_item).
    Raises ValueError on anything that is not exactly one mapping.
    """
    raw = textwrap.dedent(item_text.replace("\r\n", "\n")).strip("\n")
    parsed = load_tolerant(raw)
    lines = raw.splitlines()
    if isinstance(parsed, list):
        if len(parsed) != 1 or not isinstance(parsed[0], dict):
            raise ValueError("Provide exactly one item (a single mapping), not a list of several.")
        first = next(i for i, ln in enumerate(lines) if ln.strip() and not ln.lstrip().startswith("#"))
        if not lines[first].startswith("- "):
            raise ValueError("List item must start with '- ' at column 0.")
        lines[first] = "  " + lines[first][2:]
        lines = [_dedent2(ln) for ln in lines]
        parsed = parsed[0]
    elif not isinstance(parsed, dict):
        raise ValueError("Item must be a YAML mapping (e.g. 'alias: ...\\ntriggers: ...').")
    if "id" not in parsed:
        lines.insert(0, f"id: '{new_id}'")
        parsed = {"id": new_id, **parsed}
    out = []
    first_done = False
    for ln in lines:
        if not first_done and ln.strip() and not ln.lstrip().startswith("#"):
            out.append("- " + ln)
            first_done = True
        else:
            out.append(("  " + ln) if ln.strip() else "")
    block = "\n".join(out) + "\n"
    check = load_tolerant(block)
    if not (isinstance(check, list) and len(check) == 1 and isinstance(check[0], dict)):
        raise ValueError("Item could not be converted to a single list entry; check its indentation.")
    return block, parsed


def find_item(text: str, item_id: Optional[str] = None, alias: Optional[str] = None) -> Optional[Block]:
    kind, blocks = split_blocks(text)
    if kind != "list":
        return None
    if item_id is not None:
        for b in blocks:
            if isinstance(b.value, dict) and str(b.value.get("id")) == str(item_id):
                return b
    if alias:
        # Alias is only a fallback key: ambiguous matches resolve to nothing.
        matches = [b for b in blocks if isinstance(b.value, dict) and b.value.get("alias") == alias]
        if len(matches) == 1:
            return matches[0]
    return None


def _is_empty_list_file(text: str) -> bool:
    try:
        return load_tolerant(text) in (None, [])
    except yaml.YAMLError:
        return False


def upsert_item(text: str, item_block: str, item_id: str, alias: Optional[str] = None) -> Tuple[str, str]:
    """Replace the item with item_id (or an id-less one with the same alias), else append.

    Returns (new_text, 'replaced'|'added'). Leading comments of a replaced item are kept.
    """
    if "\r\n" in text:
        item_block = item_block.replace("\n", "\r\n")
    if not text.strip() or _is_empty_list_file(text):
        return item_block, "added"
    kind, _ = split_blocks(text)
    if kind != "list":
        raise ValueError("Target file is not a YAML list of items.")
    lines = text.splitlines(keepends=True)
    b = find_item(text, item_id=item_id)
    if b is None and alias:
        cand = find_item(text, alias=alias)
        if cand is not None and "id" not in cand.value:
            b = cand
    if b is None:
        nl = "\r\n" if "\r\n" in text else "\n"
        body = text if text.endswith("\n") else text + nl
        return body + item_block, "added"
    end = b.end
    trailing = []
    while end > b.start + 1 and not lines[end - 1].strip():
        end -= 1
        trailing.append(lines[end])
    return "".join(lines[:b.start]) + item_block + "".join(trailing) + "".join(lines[b.end:]), "replaced"


def remove_item(text: str, item_id: Optional[str] = None, alias: Optional[str] = None) -> Optional[str]:
    """Remove one list item (with its leading comments). None when not found."""
    b = find_item(text, item_id=item_id, alias=alias)
    if b is None:
        return None
    lines = text.splitlines(keepends=True)
    rest = "".join(lines[:b.comment_start] + lines[b.end:])
    return rest if rest.strip() else "[]\n"


def item_text(text: str, block: Block) -> str:
    return "\n".join(text.splitlines()[block.comment_start:block.end]).rstrip() + "\n"
