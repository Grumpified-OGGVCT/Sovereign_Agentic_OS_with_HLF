"""Tests for ALSLogger in-memory ring buffer and get_recent_entries().

These tests cover the transparency features added to agents/core/logger.py:
- _ALS_RING_BUFFER is populated on every ALSLogger.log() call.
- get_recent_entries(n) returns up to n entries, newest first.
- The ring buffer respects its maxlen (200) cap.
"""

from __future__ import annotations

import importlib

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_module():
    """Reload agents.core.logger to get a clean ring buffer for each test."""
    import agents.core.logger as mod
    importlib.reload(mod)
    return mod


# ---------------------------------------------------------------------------
# Ring buffer population
# ---------------------------------------------------------------------------

class TestALSRingBuffer:
    def test_log_populates_ring_buffer(self):
        mod = _fresh_module()
        logger = mod.ALSLogger(agent_role="test-role", goal_id="g1")
        logger.log("TEST_EVENT", {"key": "value"})
        assert len(mod._ALS_RING_BUFFER) == 1

    def test_multiple_logs_accumulate(self):
        mod = _fresh_module()
        logger = mod.ALSLogger(agent_role="test-role", goal_id="g2")
        for i in range(5):
            logger.log(f"EVENT_{i}")
        assert len(mod._ALS_RING_BUFFER) == 5

    def test_ring_buffer_respects_maxlen(self):
        mod = _fresh_module()
        # Temporarily shrink maxlen to test eviction
        import collections
        mod._ALS_RING_BUFFER = collections.deque(maxlen=3)
        logger = mod.ALSLogger(agent_role="overflow-test")
        for i in range(6):
            logger.log(f"EV_{i}")
        # Should only hold last 3 entries
        assert len(mod._ALS_RING_BUFFER) == 3
        events = [e["event"] for e in mod._ALS_RING_BUFFER]
        assert events == ["EV_3", "EV_4", "EV_5"]

    def test_entry_fields_in_buffer(self):
        mod = _fresh_module()
        logger = mod.ALSLogger(agent_role="field-checker", goal_id="g3")
        logger.log("FIELD_TEST", {"x": 1}, confidence_score=0.9, anomaly_score=0.1, token_cost=42)
        entry = list(mod._ALS_RING_BUFFER)[0]
        assert entry["event"] == "FIELD_TEST"
        assert entry["agent_role"] == "field-checker"
        assert entry["goal_id"] == "g3"
        assert entry["confidence_score"] == pytest.approx(0.9)
        assert entry["anomaly_score"] == pytest.approx(0.1)
        assert entry["token_cost"] == 42
        assert "trace_id" in entry
        assert "timestamp" in entry


# ---------------------------------------------------------------------------
# get_recent_entries()
# ---------------------------------------------------------------------------

class TestGetRecentEntries:
    def test_returns_empty_when_buffer_empty(self):
        mod = _fresh_module()
        result = mod.get_recent_entries()
        assert result == []

    def test_returns_entries_newest_first(self):
        mod = _fresh_module()
        logger = mod.ALSLogger(agent_role="order-test")
        logger.log("FIRST")
        logger.log("SECOND")
        logger.log("THIRD")
        entries = mod.get_recent_entries()
        assert entries[0]["event"] == "THIRD"
        assert entries[1]["event"] == "SECOND"
        assert entries[2]["event"] == "FIRST"

    def test_n_limits_returned_count(self):
        mod = _fresh_module()
        logger = mod.ALSLogger(agent_role="limit-test")
        for i in range(20):
            logger.log(f"EV_{i}")
        result = mod.get_recent_entries(5)
        assert len(result) == 5

    def test_n_larger_than_buffer_returns_all(self):
        mod = _fresh_module()
        logger = mod.ALSLogger(agent_role="all-test")
        for i in range(3):
            logger.log(f"E{i}")
        result = mod.get_recent_entries(100)
        assert len(result) == 3

    def test_default_n_is_50(self):
        mod = _fresh_module()
        logger = mod.ALSLogger(agent_role="default-n")
        for i in range(60):
            logger.log(f"E{i}")
        result = mod.get_recent_entries()
        assert len(result) == 50

    def test_does_not_mutate_buffer(self):
        mod = _fresh_module()
        logger = mod.ALSLogger(agent_role="mutation-guard")
        logger.log("EVENT_A")
        before = len(mod._ALS_RING_BUFFER)
        mod.get_recent_entries()
        assert len(mod._ALS_RING_BUFFER) == before


# ---------------------------------------------------------------------------
# Backward-compatibility: module-level `log` convenience function
# ---------------------------------------------------------------------------

class TestModuleLevelLog:
    def test_module_level_log_still_works(self):
        mod = _fresh_module()
        entry = mod.log("COMPAT_TEST", {"source": "module_level"})
        assert entry["event"] == "COMPAT_TEST"
        assert len(mod._ALS_RING_BUFFER) >= 1
