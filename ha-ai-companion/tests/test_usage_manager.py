"""Tests for UsageManager (append-only JSONL token/cost log — no HA required)."""
import json
import pytest

from usage.manager import UsageManager


@pytest.fixture
def um(tmp_path):
    return UsageManager(usage_dir=str(tmp_path / "usage"))


class TestRecord:
    def test_writes_record(self, um):
        um.record(session_id="s1", phase="config", model="claude", iteration=1,
                  input_tokens=100, cached_tokens=10, output_tokens=20, cost_usd=0.003)
        lines = (um.path).read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["model"] == "claude"
        assert rec["input_tokens"] == 100
        assert rec["cost_usd"] == 0.003

    def test_skips_empty_record(self, um):
        um.record(session_id="s1", phase="config", model="claude", iteration=1,
                  input_tokens=0, cached_tokens=0, output_tokens=0, cost_usd=0.0)
        assert not um.path.exists() or um.path.read_text().strip() == ""

    def test_never_raises_on_bad_input(self, um):
        # None tokens should be coerced, not crash
        rec = um.record(session_id=None, phase="main", model="x", iteration=1,
                        input_tokens=None, cached_tokens=None, output_tokens=5, cost_usd=None)
        assert rec["output_tokens"] == 5


class TestAggregate:
    def _seed(self, um):
        um.record(session_id="s1", phase="config", model="claude", iteration=1,
                  input_tokens=100, cached_tokens=0, output_tokens=20, cost_usd=0.01)
        um.record(session_id="s1", phase="suggestion", model="deepseek", iteration=2,
                  input_tokens=50, cached_tokens=0, output_tokens=10, cost_usd=0.002)
        um.record(session_id="s2", phase="config", model="claude", iteration=1,
                  input_tokens=200, cached_tokens=40, output_tokens=30, cost_usd=0.02)

    def test_totals(self, um):
        self._seed(um)
        agg = um.aggregate(days=30)
        t = agg["totals"]
        assert t["calls"] == 3
        assert t["input_tokens"] == 350
        assert t["output_tokens"] == 60
        assert round(t["cost_usd"], 6) == 0.032

    def test_by_model(self, um):
        self._seed(um)
        agg = um.aggregate(days=30)
        assert agg["by_model"]["claude"]["calls"] == 2
        assert agg["by_model"]["claude"]["input_tokens"] == 300
        assert agg["by_model"]["deepseek"]["calls"] == 1

    def test_by_phase(self, um):
        self._seed(um)
        agg = um.aggregate(days=30)
        assert agg["by_phase"]["config"]["calls"] == 2
        assert agg["by_phase"]["suggestion"]["calls"] == 1

    def test_by_session(self, um):
        self._seed(um)
        agg = um.aggregate(days=30)
        assert agg["by_session"]["s1"]["calls"] == 2
        assert agg["by_session"]["s2"]["calls"] == 1

    def test_empty(self, um):
        agg = um.aggregate(days=30)
        assert agg["totals"]["calls"] == 0
        assert agg["by_model"] == {}

    def test_reads_rotated_log(self, um):
        self._seed(um)
        # Force rotation by lowering threshold
        um.MAX_BYTES = 1
        um.record(session_id="s3", phase="config", model="claude", iteration=1,
                  input_tokens=10, cached_tokens=0, output_tokens=1, cost_usd=0.0001)
        assert (um.usage_dir / "usage.1.jsonl").exists()
        # Aggregation spans both rotated + current
        agg = um.aggregate(days=30)
        assert agg["totals"]["calls"] == 4
