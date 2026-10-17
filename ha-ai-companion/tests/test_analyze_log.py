import importlib.util
import io
from pathlib import Path

_p = Path(__file__).resolve().parents[1] / "scripts" / "analyze_log.py"
_spec = importlib.util.spec_from_file_location("analyze_log", _p)
analyze_log = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(analyze_log)

LOG = """\
2026-09-05 08:23:39,196 - src.agents.agent_system - INFO - Agent streaming user message: fix the heating notification
2026-09-05 08:23:40,000 - src.agents.agent_system - INFO - [ITERATION 1] Calling OpenAI streaming API
2026-09-05 08:23:40,001 - src.agents.agent_system - INFO - [ITERATION 1] Model: deepseek/deepseek-v4-flash (main)
2026-09-05 08:23:50,000 - src.agents.agent_system - INFO - [ITERATION 1] Calling tool: search_config_files
2026-09-05 08:23:51,000 - src.agents.agent_system - INFO - [ITERATION 2] Calling OpenAI streaming API
2026-09-05 08:23:55,000 - src.agents.agent_system - WARNING - [ITERATION 2] Tool 'patch_config_key' error: Key path 'x' not found
2026-09-05 08:23:55,001 - src.agents.agent_system - INFO - [RETRY] Injecting retry directive 1/2 for 'patch_config_key'
2026-09-05 08:24:00,000 - src.agents.agent_system - INFO - Agent completed after 3 iteration(s) - Total tokens: 1234 (input: 1000, output: 234, cached: 0)
2026-09-05 08:30:00,000 - src.agents.agent_system - INFO - Agent streaming user message: second turn never finishes
garbage line without timestamp
"""


def test_parse_turns_and_report():
    turns, use, err, problems = analyze_log.parse(io.StringIO(LOG))
    assert len(turns) == 2
    t = turns[0]
    assert t["done"] and t["iters"] == 3 and t["tokens"] == "1234" and t["retries"] == 1
    assert t["tools"]["search_config_files"] == 1 and t["max_wait"] == 10
    assert err["patch_config_key"] == 1 and not turns[1]["done"]
    out = io.StringIO()
    analyze_log.report(turns, use, err, problems, out=out)
    assert "NOT COMPLETED" in out.getvalue() and "patch_config_key" in out.getvalue()
