"""
Blue Hat — Process & Observability tests for agents/core/logger.py.

Validates:
 - New ``level`` field present in every log entry.
 - Valid log levels are normalised to uppercase; unknown levels fall back to INFO.
 - WARNING / ERROR / CRITICAL entries are mirrored to stderr.
 - DEBUG / INFO entries are NOT written to stderr.
 - Optional ``correlation_id`` appears in the entry only when provided.
 - Merkle chain integrity: successive trace_ids are deterministic SHA-256 hashes.
 - Backward-compatible: callers that do not pass ``level`` or ``correlation_id``
   still receive a valid entry.
"""

from __future__ import annotations

import hashlib
import json
import sys
from io import StringIO
from unittest.mock import patch

import pytest

from agents.core.logger import ALSLogger, LOG_LEVELS, _SEED_HASH, _compute_trace_id


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_logger() -> ALSLogger:
    """Return a fresh ALSLogger with a predictable initial hash."""
    logger = ALSLogger(agent_role="test-agent", goal_id="goal-test")
    return logger


def _capture_log(logger: ALSLogger, **kwargs) -> tuple[dict, str, str]:
    """Call logger.log() and capture stdout + stderr, returning (entry, out, err)."""
    out_buf, err_buf = StringIO(), StringIO()
    with patch("agents.core.logger._read_last_hash", return_value=_SEED_HASH), \
         patch("agents.core.logger._write_last_hash"), \
         patch("sys.stdout", out_buf), \
         patch("sys.stderr", err_buf):
        entry = logger.log(**kwargs)
    return entry, out_buf.getvalue(), err_buf.getvalue()


# ─── Tests: level field ───────────────────────────────────────────────────────

class TestLogLevel:
    def test_default_level_is_info(self):
        logger = _make_logger()
        entry, _, _ = _capture_log(logger, event="agent.start")
        assert entry["level"] == "INFO"

    def test_level_debug(self):
        logger = _make_logger()
        entry, _, _ = _capture_log(logger, event="agent.debug", level="DEBUG")
        assert entry["level"] == "DEBUG"

    def test_level_warning(self):
        logger = _make_logger()
        entry, _, _ = _capture_log(logger, event="agent.warn", level="WARNING")
        assert entry["level"] == "WARNING"

    def test_level_error(self):
        logger = _make_logger()
        entry, _, _ = _capture_log(logger, event="agent.err", level="ERROR")
        assert entry["level"] == "ERROR"

    def test_level_critical(self):
        logger = _make_logger()
        entry, _, _ = _capture_log(logger, event="agent.crit", level="CRITICAL")
        assert entry["level"] == "CRITICAL"

    def test_level_lowercase_normalised(self):
        logger = _make_logger()
        entry, _, _ = _capture_log(logger, event="agent.x", level="warning")
        assert entry["level"] == "WARNING"

    def test_invalid_level_falls_back_to_info(self):
        logger = _make_logger()
        with pytest.warns(UserWarning, match="Invalid log level"):
            entry, _, _ = _capture_log(logger, event="agent.x", level="SUPER_VERBOSE")
        assert entry["level"] == "INFO"

    def test_all_valid_levels_accepted(self):
        logger = _make_logger()
        for lvl in LOG_LEVELS:
            entry, _, _ = _capture_log(logger, event=f"agent.{lvl.lower()}", level=lvl)
            assert entry["level"] == lvl


# ─── Tests: stderr routing ────────────────────────────────────────────────────

class TestStderrRouting:
    @pytest.mark.parametrize("level", ["WARNING", "ERROR", "CRITICAL"])
    def test_high_severity_written_to_stderr(self, level):
        logger = _make_logger()
        entry, out, err = _capture_log(logger, event="alert", level=level)
        assert err.strip(), f"{level} entry should appear on stderr"
        err_entry = json.loads(err.strip())
        assert err_entry["level"] == level

    @pytest.mark.parametrize("level", ["DEBUG", "INFO"])
    def test_low_severity_not_on_stderr(self, level):
        logger = _make_logger()
        _, out, err = _capture_log(logger, event="heartbeat", level=level)
        assert not err.strip(), f"{level} entry must NOT appear on stderr"

    def test_stdout_always_receives_entry(self):
        logger = _make_logger()
        for lvl in LOG_LEVELS:
            _, out, _ = _capture_log(logger, event="x", level=lvl)
            assert out.strip(), f"stdout must always receive an entry (level={lvl})"


# ─── Tests: correlation_id ────────────────────────────────────────────────────

class TestCorrelationId:
    def test_correlation_id_present_when_provided(self):
        logger = _make_logger()
        entry, _, _ = _capture_log(
            logger, event="agent.req", correlation_id="req-abc-123"
        )
        assert entry.get("correlation_id") == "req-abc-123"

    def test_correlation_id_absent_when_empty(self):
        logger = _make_logger()
        entry, _, _ = _capture_log(logger, event="agent.req", correlation_id="")
        assert "correlation_id" not in entry

    def test_correlation_id_absent_when_not_passed(self):
        logger = _make_logger()
        entry, _, _ = _capture_log(logger, event="agent.req")
        assert "correlation_id" not in entry

    def test_correlation_id_in_stdout_json(self):
        logger = _make_logger()
        _, out, _ = _capture_log(
            logger, event="agent.req", correlation_id="trace-xyz"
        )
        parsed = json.loads(out.strip())
        assert parsed.get("correlation_id") == "trace-xyz"


# ─── Tests: Merkle chain integrity ───────────────────────────────────────────

class TestMerkleChain:
    def test_trace_id_is_sha256_of_parent_and_payload(self):
        parent = _SEED_HASH
        event = "agent.test"
        data: dict = {}
        payload = json.dumps({"event": event, "data": data}, sort_keys=True)
        expected = hashlib.sha256(f"{parent}{payload}".encode()).hexdigest()
        assert _compute_trace_id(parent, payload) == expected

    def test_trace_id_in_entry_matches_computation(self):
        logger = _make_logger()
        parent_hash = _SEED_HASH
        event = "agent.chain_test"
        with patch("agents.core.logger._read_last_hash", return_value=parent_hash), \
             patch("agents.core.logger._write_last_hash"), \
             patch("sys.stdout", StringIO()), \
             patch("sys.stderr", StringIO()):
            entry = logger.log(event=event, data={"k": "v"})
        payload = json.dumps({"event": event, "data": {"k": "v"}}, sort_keys=True)
        expected_trace = _compute_trace_id(parent_hash, payload)
        assert entry["trace_id"] == expected_trace
        assert entry["parent_trace_hash"] == parent_hash

    def test_entry_contains_required_fields(self):
        logger = _make_logger()
        entry, _, _ = _capture_log(logger, event="agent.required_fields")
        required = {
            "trace_id", "parent_trace_hash", "timestamp", "goal_id",
            "agent_role", "level", "event", "data",
            "confidence_score", "anomaly_score", "token_cost",
        }
        missing = required - entry.keys()
        assert not missing, f"Missing fields: {missing}"


# ─── Tests: backward compatibility ───────────────────────────────────────────

class TestBackwardCompatibility:
    def test_call_without_level_or_correlation_id(self):
        """Pre-existing callers that pass only (event) still work."""
        logger = _make_logger()
        entry, out, _ = _capture_log(logger, event="legacy.call")
        assert entry["level"] == "INFO"
        assert "correlation_id" not in entry
        assert out.strip()

    def test_call_with_all_original_params(self):
        """Callers passing confidence_score, anomaly_score, token_cost still work."""
        logger = _make_logger()
        entry, _, _ = _capture_log(
            logger,
            event="legacy.full",
            data={"x": 1},
            confidence_score=0.9,
            anomaly_score=0.1,
            token_cost=42,
        )
        assert entry["confidence_score"] == 0.9
        assert entry["anomaly_score"] == 0.1
        assert entry["token_cost"] == 42
        assert entry["level"] == "INFO"
