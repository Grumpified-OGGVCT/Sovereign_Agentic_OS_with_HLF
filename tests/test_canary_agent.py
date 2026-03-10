"""
tests/test_canary_agent.py — Unit tests for the Canary Agent early-warning system.

Covers:
  - ProbeResult dataclass serialisation
  - CanaryHealthWindow: consecutive_failures, success_rate, latency z-score detection
  - _fire_probe: success/failure/error paths (httpx mocked)
  - _escalate_on_failures: severity levels at 1 / 3 / 5 consecutive failures
  - _check_redis_health / _check_db_health: healthy + unhealthy branches
  - get_stats: returns expected keys
  - _idle_curiosity_scan: missing DB, valid DB with rows, empty result
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import agents.core.canary_agent as ca
from agents.core.canary_agent import (
    CanaryHealthWindow,
    ProbeResult,
    _check_db_health,
    _check_redis_health,
    _escalate_on_failures,
    _idle_curiosity_scan,
    get_stats,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _fresh_window(window_size: int = 20) -> CanaryHealthWindow:
    return CanaryHealthWindow(window_size=window_size)


# ─── ProbeResult ─────────────────────────────────────────────────────────────


class TestProbeResult:
    def test_to_dict_success(self):
        r = ProbeResult(success=True, status_code=202, latency_ms=123.456)
        d = r.to_dict()
        assert d["success"] is True
        assert d["status_code"] == 202
        assert d["latency_ms"] == 123.46  # rounded to 2 dp

    def test_to_dict_failure(self):
        r = ProbeResult(success=False, status_code=503, error="timeout")
        d = r.to_dict()
        assert d["success"] is False
        assert d["error"] == "timeout"

    def test_timestamp_auto_populated(self):
        before = time.time()
        r = ProbeResult(success=True)
        after = time.time()
        assert before <= r.timestamp <= after


# ─── CanaryHealthWindow ───────────────────────────────────────────────────────


class TestCanaryHealthWindow:
    def test_empty_window_defaults(self):
        w = _fresh_window()
        assert w.total_probes == 0
        assert w.consecutive_failures == 0
        assert w.success_rate == 1.0
        assert w.mean_latency_ms == 0.0

    def test_record_success_increments_totals(self):
        w = _fresh_window()
        w.record(ProbeResult(success=True, latency_ms=100))
        assert w.total_probes == 1
        assert w.success_rate == 1.0

    def test_record_failure_decrements_success_rate(self):
        w = _fresh_window()
        w.record(ProbeResult(success=True, latency_ms=100))
        w.record(ProbeResult(success=False))
        assert w.success_rate == pytest.approx(0.5)

    def test_consecutive_failures_none_after_success(self):
        w = _fresh_window()
        w.record(ProbeResult(success=False))
        w.record(ProbeResult(success=True, latency_ms=100))
        assert w.consecutive_failures == 0

    def test_consecutive_failures_accumulate(self):
        w = _fresh_window()
        for _ in range(4):
            w.record(ProbeResult(success=False))
        assert w.consecutive_failures == 4

    def test_consecutive_failures_resets_on_success(self):
        w = _fresh_window()
        w.record(ProbeResult(success=False))
        w.record(ProbeResult(success=False))
        w.record(ProbeResult(success=True, latency_ms=50))
        w.record(ProbeResult(success=False))
        assert w.consecutive_failures == 1

    def test_mean_latency_only_from_successes(self):
        w = _fresh_window()
        w.record(ProbeResult(success=True, latency_ms=100))
        w.record(ProbeResult(success=True, latency_ms=200))
        w.record(ProbeResult(success=False))  # should not affect mean
        assert w.mean_latency_ms == pytest.approx(150.0)

    def test_is_latency_spike_needs_5_baseline_points(self):
        w = _fresh_window()
        for _ in range(4):
            w.record(ProbeResult(success=True, latency_ms=50))
        assert not w.is_latency_spike(5000.0)  # not enough data yet

    def test_is_latency_spike_detected_with_sufficient_baseline(self):
        w = _fresh_window()
        for _ in range(10):
            w.record(ProbeResult(success=True, latency_ms=50))
        assert w.is_latency_spike(5000.0, sigma=2.5)

    def test_is_latency_spike_not_triggered_on_normal_value(self):
        w = _fresh_window()
        for _ in range(10):
            w.record(ProbeResult(success=True, latency_ms=100))
        # With stdev=0, the 1ms variance floor gives threshold = 100 + 2.5*max(0,1) = 102.5.
        # A value equal to the mean (100.0) is well below the threshold and must not spike.
        assert not w.is_latency_spike(100.0, sigma=2.5)

    def test_get_stats_keys(self):
        w = _fresh_window()
        stats = w.get_stats()
        for key in ("total_probes", "consecutive_failures", "success_rate", "mean_latency_ms", "window_size"):
            assert key in stats

    def test_window_bounded_by_maxlen(self):
        w = _fresh_window(window_size=5)
        for i in range(10):
            w.record(ProbeResult(success=True, latency_ms=float(i * 10)))
        assert w.total_probes == 5


# ─── _escalate_on_failures ───────────────────────────────────────────────────


class TestEscalateOnFailures:
    """Verify the escalating-alert logic emits the right log events."""

    def _collect_log_events(self, consecutive: int) -> list[str]:
        events: list[str] = []
        with patch.object(ca._logger, "log", side_effect=lambda ev, *a, **kw: events.append(ev)):
            with patch("agents.core.canary_agent._set_redis_health_flag"):
                _escalate_on_failures(consecutive)
        return events

    def test_zero_failures_no_log(self):
        events = self._collect_log_events(0)
        assert events == []

    def test_one_failure_early_warning(self):
        events = self._collect_log_events(1)
        assert "CANARY_EARLY_WARNING" in events

    def test_two_failures_no_critical_yet(self):
        events = self._collect_log_events(2)
        assert "CANARY_CRITICAL_THRESHOLD" not in events
        assert "CANARY_DEAD_MAN" not in events

    def test_three_failures_critical(self):
        events = self._collect_log_events(3)
        assert "CANARY_CRITICAL_THRESHOLD" in events

    def test_five_failures_dead_man(self):
        events = self._collect_log_events(5)
        assert "CANARY_DEAD_MAN" in events

    def test_six_failures_dead_man(self):
        events = self._collect_log_events(6)
        assert "CANARY_DEAD_MAN" in events

    def test_critical_sets_redis_flag(self):
        with patch.object(ca._logger, "log"):
            with patch("agents.core.canary_agent._set_redis_health_flag") as mock_set:
                _escalate_on_failures(3)
                mock_set.assert_called_once_with("health:gateway:failed", 600)

    def test_dead_man_sets_redis_dead_flag(self):
        with patch.object(ca._logger, "log"):
            with patch("agents.core.canary_agent._set_redis_health_flag") as mock_set:
                _escalate_on_failures(5)
                mock_set.assert_called_once_with("health:gateway:dead", 1800)


# ─── _fire_probe ─────────────────────────────────────────────────────────────


class TestFireProbe:
    """Test _fire_probe with mocked httpx and a fresh module-level state."""

    def setup_method(self):
        # Reset module-level state before each test
        ca._probe_failure_count = 0
        ca._health_window = CanaryHealthWindow()

    def _mock_response(self, status_code: int, text: str = "") -> MagicMock:
        resp = MagicMock()
        resp.status_code = status_code
        resp.text = text
        return resp

    def test_success_returns_true(self):
        with patch("httpx.post", return_value=self._mock_response(202)):
            with patch.object(ca._logger, "log"):
                result = ca._fire_probe()
        assert result is True

    def test_success_resets_failure_count(self):
        ca._probe_failure_count = 3
        with patch("httpx.post", return_value=self._mock_response(202)):
            with patch.object(ca._logger, "log"):
                ca._fire_probe()
        assert ca._probe_failure_count == 0

    def test_failure_status_returns_false(self):
        with patch("httpx.post", return_value=self._mock_response(503)):
            with patch.object(ca._logger, "log"):
                with patch("agents.core.canary_agent._escalate_on_failures"):
                    result = ca._fire_probe()
        assert result is False

    def test_failure_increments_count(self):
        with patch("httpx.post", return_value=self._mock_response(503)):
            with patch.object(ca._logger, "log"):
                with patch("agents.core.canary_agent._escalate_on_failures"):
                    ca._fire_probe()
        assert ca._probe_failure_count == 1

    def test_exception_returns_false(self):
        with patch("httpx.post", side_effect=Exception("conn refused")):
            with patch.object(ca._logger, "log"):
                with patch("agents.core.canary_agent._escalate_on_failures"):
                    result = ca._fire_probe()
        assert result is False

    def test_exception_increments_failure_count(self):
        with patch("httpx.post", side_effect=Exception("conn refused")):
            with patch.object(ca._logger, "log"):
                with patch("agents.core.canary_agent._escalate_on_failures"):
                    ca._fire_probe()
        assert ca._probe_failure_count == 1

    def test_success_records_to_health_window(self):
        with patch("httpx.post", return_value=self._mock_response(202)):
            with patch.object(ca._logger, "log"):
                ca._fire_probe()
        assert ca._health_window.total_probes == 1
        assert ca._health_window.success_rate == 1.0

    def test_failure_records_to_health_window(self):
        with patch("httpx.post", return_value=self._mock_response(500)):
            with patch.object(ca._logger, "log"):
                with patch("agents.core.canary_agent._escalate_on_failures"):
                    ca._fire_probe()
        assert ca._health_window.consecutive_failures == 1


# ─── _check_redis_health ─────────────────────────────────────────────────────


class TestCheckRedisHealth:
    """Test Redis health checks using sys.modules to stub the redis package."""

    def _make_fake_redis(self, ping_raises: Exception | None = None):
        """Build a minimal fake redis module."""
        import sys
        import types

        mock_client = MagicMock()
        if ping_raises:
            mock_client.ping.side_effect = ping_raises
        else:
            mock_client.ping.return_value = True

        fake_redis_mod = types.ModuleType("redis")
        fake_redis_mod.from_url = MagicMock(return_value=mock_client)
        return fake_redis_mod

    def test_healthy_redis_returns_true(self):
        import sys
        fake = self._make_fake_redis()
        with patch.dict(sys.modules, {"redis": fake}):
            assert _check_redis_health() is True

    def test_unhealthy_redis_ping_returns_false(self):
        import sys
        fake = self._make_fake_redis(ping_raises=Exception("connection refused"))
        with patch.dict(sys.modules, {"redis": fake}):
            with patch.object(ca._logger, "log"):
                assert _check_redis_health() is False

    def test_unhealthy_redis_import_returns_false(self):
        import sys
        with patch.dict(sys.modules, {"redis": None}):  # None → ImportError on import
            with patch.object(ca._logger, "log"):
                result = _check_redis_health()
        assert result is False

    def test_unhealthy_redis_logs_event(self):
        import sys
        fake = self._make_fake_redis(ping_raises=Exception("no conn"))
        logged: list[str] = []
        with patch.dict(sys.modules, {"redis": fake}):
            with patch.object(ca._logger, "log", side_effect=lambda ev, *a, **kw: logged.append(ev)):
                _check_redis_health()
        assert "CANARY_REDIS_UNHEALTHY" in logged


# ─── _check_db_health ────────────────────────────────────────────────────────


class TestCheckDbHealth:
    def test_missing_db_returns_true(self, tmp_path):
        absent = tmp_path / "nope.db"
        assert _check_db_health(absent) is True

    def test_healthy_db_returns_true(self, tmp_path):
        db = tmp_path / "mem.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE fact_store (entity_id TEXT, confidence_score REAL)")
        conn.close()
        assert _check_db_health(db) is True

    def test_corrupt_db_returns_false(self, tmp_path):
        db = tmp_path / "bad.db"
        # Write a truncated / corrupted SQLite file that sqlite3 will reject.
        # Real SQLite files start with "SQLite format 3\x00" (16 bytes); a random
        # payload of the same length but wrong content causes a "not a database" error.
        db.write_bytes(b"\xFF" * 4096)
        with patch.object(ca._logger, "log"):
            result = _check_db_health(db)
        assert result is False

    def test_unhealthy_db_logs_event(self, tmp_path):
        db = tmp_path / "locked.db"
        db.write_bytes(b"\xFF" * 4096)
        logged: list[str] = []
        with patch.object(ca._logger, "log", side_effect=lambda ev, *a, **kw: logged.append(ev)):
            _check_db_health(db)
        assert "CANARY_DB_UNHEALTHY" in logged


# ─── get_stats ───────────────────────────────────────────────────────────────


class TestGetStats:
    def setup_method(self):
        ca._probe_failure_count = 0
        ca._health_window = CanaryHealthWindow()

    def test_stats_contains_required_keys(self):
        stats = get_stats()
        for key in ("probe_failure_count", "total_probes", "consecutive_failures",
                    "success_rate", "mean_latency_ms", "window_size"):
            assert key in stats, f"Missing key: {key}"

    def test_stats_reflects_probe_failure_count(self):
        ca._probe_failure_count = 7
        assert get_stats()["probe_failure_count"] == 7

    def test_stats_reflects_window_state(self):
        ca._health_window.record(ProbeResult(success=True, latency_ms=200))
        stats = get_stats()
        assert stats["total_probes"] == 1
        assert stats["success_rate"] == 1.0


# ─── _idle_curiosity_scan ────────────────────────────────────────────────────


class TestIdleCuriosityScan:
    def test_missing_db_returns_empty(self, tmp_path):
        absent = tmp_path / "no.db"
        result = _idle_curiosity_scan(absent)
        assert result == []

    def test_empty_fact_store_returns_empty(self, tmp_path):
        db = tmp_path / "mem.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE fact_store "
            "(entity_id TEXT, semantic_relationship TEXT, confidence_score REAL)"
        )
        conn.close()
        result = _idle_curiosity_scan(db)
        assert result == []

    def test_low_confidence_rows_returned(self, tmp_path):
        db = tmp_path / "mem.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE fact_store "
            "(entity_id TEXT, semantic_relationship TEXT, confidence_score REAL)"
        )
        conn.execute("INSERT INTO fact_store VALUES ('e1', 'related', 0.3)")
        conn.execute("INSERT INTO fact_store VALUES ('e2', 'linked', 0.45)")
        conn.execute("INSERT INTO fact_store VALUES ('e3', 'assoc', 0.9)")  # above threshold
        conn.commit()
        conn.close()
        with patch.object(ca._logger, "log"):
            result = _idle_curiosity_scan(db)
        assert len(result) == 2
        entity_ids = {r["entity_id"] for r in result}
        assert "e1" in entity_ids
        assert "e2" in entity_ids
        assert "e3" not in entity_ids

    def test_result_ordered_by_confidence_asc(self, tmp_path):
        db = tmp_path / "mem.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE fact_store "
            "(entity_id TEXT, semantic_relationship TEXT, confidence_score REAL)"
        )
        conn.execute("INSERT INTO fact_store VALUES ('high', 'r', 0.45)")
        conn.execute("INSERT INTO fact_store VALUES ('low', 'r', 0.1)")
        conn.commit()
        conn.close()
        with patch.object(ca._logger, "log"):
            result = _idle_curiosity_scan(db)
        assert result[0]["entity_id"] == "low"

    def test_result_keys(self, tmp_path):
        db = tmp_path / "mem.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE fact_store "
            "(entity_id TEXT, semantic_relationship TEXT, confidence_score REAL)"
        )
        conn.execute("INSERT INTO fact_store VALUES ('x', 'rel', 0.2)")
        conn.commit()
        conn.close()
        with patch.object(ca._logger, "log"):
            result = _idle_curiosity_scan(db)
        assert "entity_id" in result[0]
        assert "relationship" in result[0]
        assert "confidence" in result[0]
