"""
History diet — shrink old tool payloads in replayed conversation history.

The frontend resends the full conversation history every turn. Tool results
(file reads, entity dumps) and propose_config_changes arguments (entire file
contents) from old turns dominate that payload — tens of KB each — and get
re-billed on every iteration of every later turn.

Rule: messages belonging to the LAST completed turn (after the last user
message) stay verbatim — the model often continues that work ("now add one
more"). Everything older gets its tool content truncated; the model can
re-read files if it genuinely needs them again.
"""
import json
from typing import Any, Dict, List

TOOL_RESULT_MAX_CHARS = 1000
TOOL_ARGS_MAX_CHARS = 1500

_TRUNCATED_SUFFIX = "\n…[truncated — old turn; re-read the file if needed]"


def _last_user_index(history: List[Dict[str, Any]]) -> int:
    for i in range(len(history) - 1, -1, -1):
        if history[i].get("role") == "user":
            return i
    return -1


def _truncate_tool_message(msg: Dict[str, Any]) -> Dict[str, Any]:
    content = msg.get("content")
    if not isinstance(content, str) or len(content) <= TOOL_RESULT_MAX_CHARS:
        return msg
    slim = dict(msg)
    slim["content"] = content[:TOOL_RESULT_MAX_CHARS] + _TRUNCATED_SUFFIX
    return slim


def _truncate_tool_calls(msg: Dict[str, Any]) -> Dict[str, Any]:
    tool_calls = msg.get("tool_calls")
    if not tool_calls:
        return msg
    changed = False
    new_calls = []
    for tc in tool_calls:
        fn = tc.get("function") if isinstance(tc, dict) else None
        args = fn.get("arguments") if isinstance(fn, dict) else None
        if isinstance(args, str) and len(args) > TOOL_ARGS_MAX_CHARS:
            # Keep arguments valid JSON — some providers re-validate history
            stub = json.dumps({"truncated": args[:500] + "…[old turn args truncated]"})
            new_tc = dict(tc)
            new_tc["function"] = {**fn, "arguments": stub}
            new_calls.append(new_tc)
            changed = True
        else:
            new_calls.append(tc)
    if not changed:
        return msg
    slim = dict(msg)
    slim["tool_calls"] = new_calls
    return slim


def truncate_old_tool_content(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Return history with tool payloads truncated for all turns OLDER than the
    last completed turn. Input is not mutated.
    """
    if not history:
        return history
    boundary = _last_user_index(history)
    result = []
    for i, msg in enumerate(history):
        if boundary != -1 and i >= boundary:
            result.append(msg)
        elif msg.get("role") == "tool":
            result.append(_truncate_tool_message(msg))
        elif msg.get("role") == "assistant" and msg.get("tool_calls"):
            result.append(_truncate_tool_calls(msg))
        else:
            result.append(msg)
    return result
