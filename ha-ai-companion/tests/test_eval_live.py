import importlib.util
from pathlib import Path

import yaml

_dir = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location("eval_live", _dir / "eval_live.py")
eval_live = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eval_live)


def _ev(kind, **data):
    return {"event": kind, "data": data}


def test_grade_pass_and_fail_reasons():
    task = {"id": "t", "expect_tools": ["get_automation"], "forbid_full_read": True,
            "expect_changeset": True, "answer_any": ["rekuperator"], "max_iterations": 3}
    good = [
        _ev("tool_start", function="get_automation", arguments={"automation": "x"}),
        _ev("tool_result", function="get_automation", result={"success": True}),
        _ev("tool_start", function="upsert_automation", arguments={}),
        _ev("tool_result", function="upsert_automation", result={"success": True, "changeset_id": "c1"}),
        _ev("message_complete", message={"content": "Staged the Rekuperator change."}),
        _ev("complete", iterations=3, usage={"cost_usd": 0.01, "input_tokens": 900}),
    ]
    r = eval_live.grade(task, good, 4.0, 0.5)
    assert r["passed"], r
    assert r["cost_usd"] == 0.01 and r["tool_errors"] == 0

    bad = [
        _ev("tool_start", function="search_config_files", arguments={"full_content": True}),
        _ev("tool_result", function="search_config_files", result={"success": False, "error": "x"}),
        _ev("message_complete", message={"content": "Done."}),
        _ev("error", error="Maximum iteration limit reached."),
        _ev("complete", iterations=5, usage={}),
    ]
    r = eval_live.grade(task, bad, 9.0, 1.0)
    assert not r["passed"] and r["tool_errors"] == 1
    joined = " ".join(r["fails"])
    for part in ("error:", "none of", "full_content", "no changeset", "answer lacks", "5 iterations"):
        assert part in joined, joined


def test_eval_tasks_file_is_valid():
    tasks = yaml.safe_load((_dir / "eval_tasks.yaml").read_text(encoding="utf-8"))
    ids = [t["id"] for t in tasks]
    assert len(ids) == len(set(ids)) and all(t.get("prompt") for t in tasks)
