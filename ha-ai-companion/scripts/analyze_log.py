"""Summarize an HA AI Companion add-on log into per-turn diagnostics.

Usage: python scripts/analyze_log.py <addon.log> [--turns N]

Per turn: start time, duration, model, iterations, tokens, tool calls, tool
errors, retries, and the slowest model wait. Then totals: most-used and
most-failing tools, and grouped warnings/errors.
"""
import argparse
import re
import sys
from collections import Counter
from datetime import datetime

LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3}) - ([\w.]+) - (\w+) - (.*)$")
ITER = re.compile(r"\[ITERATION (\d+)\]")
CTX = re.compile(r"\[CTX\] iter=\d+ system=(\d+) tools=(\d+) history=(\d+) turn=(\d+) max_tool=(\d+)")
CACHED = re.compile(r"\(input: (\d+), output: \d+, cached: (\d+)\)")


def _ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S,%f")


def _norm(msg):
    msg = re.sub(r"'[^']*'", "'…'", msg)
    return re.sub(r"\d+", "N", msg)[:110]


def parse(lines):
    turns, cur, problems = [], None, Counter()
    tool_use, tool_err = Counter(), Counter()
    wait_start = None
    for raw in lines:
        m = LINE.match(raw.rstrip("\n"))
        if not m:
            continue
        ts, level, msg = _ts(m.group(1)), m.group(3), m.group(4)
        if msg.startswith("Agent streaming user message:"):
            cur = {"start": ts, "end": ts, "msg": msg.split(":", 1)[1].strip()[:70], "model": "", "iters": 0,
                   "tools": Counter(), "errors": [], "retries": 0, "tokens": "", "max_wait": 0.0, "done": False,
                   "ctx": None, "cached_pct": None}
            turns.append(cur)
            wait_start = None
            continue
        if level in ("WARNING", "ERROR"):
            problems[f"{level} {_norm(msg)}"] += 1
        if cur is None or cur["done"]:
            continue
        cur["end"] = ts
        it = ITER.search(msg)
        if it:
            cur["iters"] = max(cur["iters"], int(it.group(1)))
        if "Calling OpenAI streaming API" in msg:
            wait_start = ts
        elif wait_start and it and "] Model:" not in msg:
            # "[ITERATION N] Model: ..." is logged before the request goes out; the
            # wait ends at the next iteration line that follows the response.
            cur["max_wait"] = max(cur["max_wait"], (ts - wait_start).total_seconds())
            wait_start = None
        if msg.startswith("[ROUTING]") and "model " in msg:
            cur["model"] = msg.rsplit("model ", 1)[1].split()[0]
        t = re.search(r"Calling tool: (\w+)", msg)
        if t:
            cur["tools"][t.group(1)] += 1
            tool_use[t.group(1)] += 1
        e = re.search(r"Tool '(\w+)' error: (.*)", msg)
        if e and level == "WARNING":
            cur["errors"].append(f"{e.group(1)}: {e.group(2)[:90]}")
            tool_err[e.group(1)] += 1
        x = CTX.search(msg)
        if x:
            # Chars, last iteration wins for the fixed parts; turn/max_tool keep the peak.
            sysc, tools, hist, turn, mt = map(int, x.groups())
            prev = cur["ctx"] or (0, 0, 0, 0, 0)
            cur["ctx"] = (sysc, tools, hist, max(prev[3], turn), max(prev[4], mt))
        if msg.startswith("[RETRY]"):
            cur["retries"] += 1
        c = re.search(r"Agent completed after (\d+) iteration\(s\) - Total tokens: (\d+)", msg)
        if c:
            cur.update(done=True, iters=int(c.group(1)), tokens=c.group(2))
            k = CACHED.search(msg)
            if k and int(k.group(1)):
                cur["cached_pct"] = 100 * int(k.group(2)) // int(k.group(1))
    return turns, tool_use, tool_err, problems


def report(turns, tool_use, tool_err, problems, last=None, out=sys.stdout):
    shown = turns[-last:] if last else turns
    w = out.write
    w(f"{len(turns)} turn(s); showing {len(shown)}\n\n")
    for i, t in enumerate(shown, 1):
        dur = (t["end"] - t["start"]).total_seconds()
        state = "done" if t["done"] else "NOT COMPLETED"
        w(f"#{i} {t['start']:%m-%d %H:%M} {dur:6.0f}s {state:13} iters={t['iters']:<2} tokens={t['tokens'] or '?':>7} "
          f"retries={t['retries']} slowest_model_wait={t['max_wait']:.0f}s {t['model']}\n")
        w(f"    \"{t['msg']}\"\n")
        if t["ctx"] or t["cached_pct"] is not None:
            c = t["ctx"]
            parts = [f"sys={c[0]} tools={c[1]} history={c[2]} turn_peak={c[3]} max_tool={c[4]} chars"] if c else []
            if t["cached_pct"] is not None:
                parts.append(f"cached={t['cached_pct']}%")
            w("    ctx: " + " ".join(parts) + "\n")
        if t["tools"]:
            w("    tools: " + ", ".join(f"{k}×{v}" for k, v in t["tools"].most_common()) + "\n")
        for e in t["errors"][:5]:
            w(f"    ! {e}\n")
    if tool_use:
        w("\nTool calls (errors): " + ", ".join(f"{k} {v} ({tool_err.get(k, 0)})" for k, v in tool_use.most_common(15)) + "\n")
    if problems:
        w("\nWarnings/errors by type:\n")
        for k, v in problems.most_common(20):
            w(f"{v:5} {k}\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("--turns", type=int, default=None, help="only show the last N turns")
    a = ap.parse_args(argv)
    with open(a.log, encoding="utf-8", errors="replace") as f:
        report(*parse(f), last=a.turns)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
