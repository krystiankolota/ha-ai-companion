"""
Usage Manager for HA AI Companion.

Persists one record per LLM API call to an append-only JSONL log so the app
can show what is actually burning tokens / cost (per model, phase, day, session).

Records are cheap to write and cheap to aggregate. The log is rotated when it
grows past MAX_BYTES so it never bloats unbounded.
"""
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _acc(bucket: Dict[str, Dict], key: str, rec: Dict) -> None:
    key = key or "unknown"
    b = bucket.setdefault(key, {
        "input_tokens": 0, "cached_tokens": 0, "output_tokens": 0,
        "cost_usd": 0.0, "calls": 0,
    })
    b["input_tokens"] += rec.get("input_tokens", 0)
    b["cached_tokens"] += rec.get("cached_tokens", 0)
    b["output_tokens"] += rec.get("output_tokens", 0)
    b["cost_usd"] = round(b["cost_usd"] + rec.get("cost_usd", 0.0), 6)
    b["calls"] += 1


class UsageManager:
    """Append-only per-call token/cost log with simple aggregation."""

    MAX_BYTES = 5 * 1024 * 1024  # rotate at 5 MB

    def __init__(self, usage_dir: str):
        self.usage_dir = Path(usage_dir)
        self.usage_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.usage_dir / "usage.jsonl"
        logger.info("UsageManager initialised at %s", self.usage_dir)

    # ------------------------------------------------------------------
    def record(
        self,
        *,
        session_id: Optional[str],
        phase: str,
        model: str,
        iteration: int,
        input_tokens: int,
        cached_tokens: int,
        output_tokens: int,
        cost_usd: float = 0.0,
    ) -> Dict[str, Any]:
        """Append one call record. Never raises — usage logging must not break a run."""
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id or "",
            "phase": phase or "main",
            "model": model or "",
            "iteration": int(iteration or 0),
            "input_tokens": int(input_tokens or 0),
            "cached_tokens": int(cached_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "cost_usd": round(float(cost_usd or 0.0), 6),
        }
        # Skip empty records (no tokens, no cost) — nothing to learn from them.
        if not (rec["input_tokens"] or rec["output_tokens"] or rec["cost_usd"]):
            return rec
        try:
            self._rotate_if_needed()
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception as exc:
            logger.error("UsageManager.record error: %s", exc)
        return rec

    def _rotate_if_needed(self) -> None:
        try:
            if self.path.exists() and self.path.stat().st_size > self.MAX_BYTES:
                bak = self.usage_dir / "usage.1.jsonl"
                if bak.exists():
                    bak.unlink()
                self.path.rename(bak)
                logger.info("UsageManager rotated usage log (>%d bytes)", self.MAX_BYTES)
        except Exception:
            pass

    def _read_all(self) -> List[Dict]:
        recs: List[Dict] = []
        for p in (self.usage_dir / "usage.1.jsonl", self.path):
            if not p.exists():
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            recs.append(json.loads(line))
                        except Exception:
                            continue
            except Exception as exc:
                logger.error("UsageManager._read_all error on %s: %s", p, exc)
        return recs

    def aggregate(self, days: int = 30) -> Dict[str, Any]:
        """Aggregate the log over the last ``days`` into model/phase/day/session buckets."""
        cutoff = time.time() - days * 86400
        recs = self._read_all()
        by_model: Dict[str, Dict] = {}
        by_phase: Dict[str, Dict] = {}
        by_day: Dict[str, Dict] = {}
        by_session: Dict[str, Dict] = {}
        totals = {"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0}

        for r in recs:
            try:
                ts = datetime.fromisoformat(r["ts"]).timestamp()
            except Exception:
                continue
            if ts < cutoff:
                continue
            _acc(by_model, r.get("model", ""), r)
            _acc(by_phase, r.get("phase", ""), r)
            _acc(by_day, (r.get("ts") or "")[:10], r)
            if r.get("session_id"):
                _acc(by_session, r["session_id"], r)
            totals["input_tokens"] += r.get("input_tokens", 0)
            totals["cached_tokens"] += r.get("cached_tokens", 0)
            totals["output_tokens"] += r.get("output_tokens", 0)
            totals["cost_usd"] += r.get("cost_usd", 0.0)
            totals["calls"] += 1

        totals["cost_usd"] = round(totals["cost_usd"], 6)
        return {
            "days": days,
            "totals": totals,
            "by_model": by_model,
            "by_phase": by_phase,
            "by_day": dict(sorted(by_day.items())),
            "by_session": by_session,
        }
