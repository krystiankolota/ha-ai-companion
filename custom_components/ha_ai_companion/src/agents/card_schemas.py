"""
Schema validation for HACS custom cards in Lovelace configs.

LLMs hallucinate plausible-looking card schemas (e.g. a `buttons:` array for
bubble-card's horizontal-buttons-stack). HA accepts any JSON — the error only
surfaces as a browser-side JS exception the backend can never see. This module
blocks known-bad structures BEFORE they reach the approval flow, and returns
the correct schema in the error message so the LLM can self-correct.

Philosophy mirrors the pre-dispatch loop guard: block before damage, with an
actionable hint. Unknown custom cards are NOT validated (no false positives).
"""
import re
from typing import Any, Dict, List

# Numbered-prop pattern for horizontal-buttons-stack: 1_name, 2_link, 10_icon...
_NUMBERED_PROP = re.compile(r"^\d+_[a-z_]+$")

_HBS_HINT = (
    "horizontal-buttons-stack uses NUMBERED properties, not a 'buttons' array. "
    "Correct schema: {type: custom:bubble-card, card_type: horizontal-buttons-stack, "
    "1_name: Kitchen, 1_icon: mdi:fridge, 1_link: '#kitchen', 2_name: ..., 2_link: ...}"
)

_POPUP_HINT = (
    "pop-up requires a 'hash' property (e.g. hash: '#kitchen') and content cards "
    "nested under 'cards:' (bubble-card >= 3.0 standalone format)."
)


def _iter_cards(node: Any):
    """Recursively yield every card dict in a Lovelace config structure.

    Walks views, sections, cards, and any nested card lists (vertical-stack,
    grid, pop-up content, etc.).
    """
    if isinstance(node, dict):
        if "type" in node and isinstance(node.get("type"), str):
            yield node
        for key in ("views", "sections", "cards", "card"):
            child = node.get(key)
            if isinstance(child, list):
                for item in child:
                    yield from _iter_cards(item)
            elif isinstance(child, dict):
                yield from _iter_cards(child)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_cards(item)


def _validate_bubble_card(card: Dict[str, Any]) -> List[str]:
    """Return list of violation messages for one bubble-card dict."""
    problems: List[str] = []
    card_type = card.get("card_type")

    if not card_type:
        problems.append(
            "bubble-card requires 'card_type' (one of: button, pop-up, separator, "
            "horizontal-buttons-stack, cover, media-player, climate, select, calendar)."
        )
        return problems

    if card_type == "horizontal-buttons-stack":
        if "buttons" in card:
            problems.append(f"Invalid 'buttons' array. {_HBS_HINT}")
        elif not any(_NUMBERED_PROP.match(str(k)) for k in card):
            problems.append(f"No numbered button properties found. {_HBS_HINT}")

    elif card_type == "pop-up":
        if not card.get("hash"):
            problems.append(f"Missing 'hash'. {_POPUP_HINT}")

    return problems


def validate_lovelace_cards(config: Any) -> List[str]:
    """
    Validate custom-card schemas in a parsed Lovelace config.

    Args:
        config: Parsed YAML/JSON Lovelace config (dict with views, or fragment)

    Returns:
        List of human-readable violation strings. Empty list = valid.
        Only known card families are checked; unknown custom cards pass.
    """
    violations: List[str] = []
    for card in _iter_cards(config):
        if card.get("type") == "custom:bubble-card":
            for problem in _validate_bubble_card(card):
                label = card.get("name") or card.get("card_type") or "?"
                violations.append(f"bubble-card '{label}': {problem}")
    return violations


def format_system_log_entries(entries: List[Dict[str, Any]], max_entries: int = 30) -> List[str]:
    """
    Compact, token-cheap rendering of HA system_log entries.

    ERROR entries first, then WARNING; newest first within each level.
    Message truncated to first line / 200 chars.
    """
    def level_rank(e):
        return 0 if e.get("level") == "ERROR" else 1

    ordered = sorted(entries, key=lambda e: (level_rank(e), -(e.get("timestamp") or 0)))
    lines = []
    for e in ordered[:max_entries]:
        msgs = e.get("message") or []
        first = (msgs[0] if isinstance(msgs, list) and msgs else str(msgs)).split("\n")[0][:200]
        count = e.get("count", 1)
        lines.append(f"{e.get('level', '?')} {e.get('name', '?')} (x{count}): {first}")
    return lines


def format_lovelace_resources(resources: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Compact rendering of Lovelace resources: urls + derived card slugs.

    Derives a card slug from the JS filename: '/hacsfiles/Bubble-Card/bubble-card.js?hacstag=1'
    -> 'bubble-card'.
    """
    slugs = []
    items = []
    for r in resources:
        url = r.get("url", "")
        stem = url.split("/")[-1].split("?")[0]
        slug = re.sub(r"(-bundle)?\.js$", "", stem).lower()
        if slug:
            slugs.append(slug)
        items.append({"url": url, "type": r.get("type")})
    return {"resources": items, "loaded_cards": sorted(set(slugs)), "count": len(items)}
