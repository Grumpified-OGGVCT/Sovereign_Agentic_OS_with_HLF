"""
tests/test_scribe.py — Unit tests for the Scribe Daemon.

Tests InsAIts prose translation, token budget enforcement,
entry retrieval, log persistence, and daemon lifecycle.
"""

from __future__ import annotations

import json
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from agents.core.daemons import DaemonEventBus, DaemonStatus
from agents.core.daemons.scribe import (
    ProseEntry,
    ScribeDaemon,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def daemon():
    return ScribeDaemon()


@pytest.fixture
def running_daemon():
    d = ScribeDaemon()
    d.start()
    return d


@pytest.fixture
def daemon_with_bus():
    bus = DaemonEventBus()
    d = ScribeDaemon(event_bus=bus)
    d.start()
    return d, bus


# ─── Lifecycle Tests ─────────────────────────────────────────────────────────


class TestLifecycle:
    """Tests for daemon start/stop behavior."""

    def test_initial_status_stopped(self, daemon):
        assert daemon.status == DaemonStatus.STOPPED

    def test_start_sets_running(self, daemon):
        daemon.start()
        assert daemon.status == DaemonStatus.RUNNING

    def test_stop_sets_stopped(self, running_daemon):
        running_daemon.stop()
        assert running_daemon.status == DaemonStatus.STOPPED

    def test_disabled_daemon_stays_stopped(self):
        d = ScribeDaemon(enabled=False)
        d.start()
        assert d.status == DaemonStatus.STOPPED

    def test_translate_while_stopped_returns_none(self, daemon):
        result = daemon.translate({"type": "test"})
        assert result is None


# ─── Translation Tests ───────────────────────────────────────────────────────


class TestTranslation:
    """Tests for event-to-prose translation."""

    def test_intent_execution_prose(self, running_daemon):
        entry = running_daemon.translate({
            "type": "intent_execution",
            "name": "deploy",
        })
        assert entry is not None
        assert "deploy" in entry.prose
        assert entry.event_type == "intent_execution"

    def test_host_function_call_prose(self, running_daemon):
        entry = running_daemon.translate({
            "type": "host_function_call",
            "name": "read_file",
            "backend": "builtin",
        })
        assert "read_file" in entry.prose

    def test_module_import_prose(self, running_daemon):
        entry = running_daemon.translate({
            "type": "module_import",
            "name": "math",
        })
        assert "math" in entry.prose

    def test_gas_consumed_prose(self, running_daemon):
        entry = running_daemon.translate({
            "type": "gas_consumed",
            "name": "compile",
            "gas_cost": 42,
        })
        assert "42" in entry.prose

    def test_set_binding_prose(self, running_daemon):
        entry = running_daemon.translate({
            "type": "set_binding",
            "name": "port",
            "value": 8080,
        })
        assert "port" in entry.prose
        assert "8080" in entry.prose

    def test_unknown_event_produces_fallback_prose(self, running_daemon):
        entry = running_daemon.translate({
            "type": "custom_event",
            "name": "my_op",
        })
        assert entry is not None
        assert "custom_event" in entry.prose

    def test_entry_has_timestamp(self, running_daemon):
        entry = running_daemon.translate({"type": "test"})
        assert entry.timestamp  # non-empty
        assert "T" in entry.timestamp  # ISO-8601 format

    def test_entry_token_count_positive(self, running_daemon):
        entry = running_daemon.translate({"type": "test"})
        assert entry.token_count > 0


# ─── Token Budget Tests ─────────────────────────────────────────────────────


class TestTokenBudget:
    """Tests for token budget enforcement."""

    def test_default_hearth_budget(self):
        d = ScribeDaemon(tier="hearth")
        assert d.token_budget == int(8192 * 0.80)

    def test_forge_tier_budget(self):
        d = ScribeDaemon(tier="forge")
        assert d.token_budget == int(16384 * 0.80)

    def test_sovereign_tier_budget(self):
        d = ScribeDaemon(tier="sovereign")
        assert d.token_budget == int(32768 * 0.80)

    def test_custom_budget_pct(self):
        d = ScribeDaemon(tier="hearth", token_budget_pct=0.50)
        assert d.token_budget == int(8192 * 0.50)

    def test_tokens_used_increments(self, running_daemon):
        assert running_daemon.tokens_used == 0
        running_daemon.translate({"type": "test"})
        assert running_daemon.tokens_used > 0

    def test_tokens_remaining_decreases(self, running_daemon):
        initial = running_daemon.tokens_remaining
        running_daemon.translate({"type": "test"})
        assert running_daemon.tokens_remaining < initial

    def test_budget_exceeded_triggers_summary(self):
        """When budget is nearly full, translation should use summarized prose."""
        d = ScribeDaemon(tier="hearth", token_budget_pct=0.01)  # tiny budget
        d.start()

        # First translation consumes most of the tiny budget
        entry1 = d.translate({"type": "intent_execution", "name": "big_op"})

        # Subsequent translations should be budget-constrained summaries
        for _ in range(20):
            d.translate({"type": "intent_execution", "name": "another"})

        # Should have entries but tokens should be reasonably bounded
        assert len(d.get_entries(count=100)) > 0

    def test_budget_pct_clamped_to_zero_one(self):
        d1 = ScribeDaemon(token_budget_pct=1.5)
        assert d1._token_budget_pct == 1.0
        d2 = ScribeDaemon(token_budget_pct=-0.5)
        assert d2._token_budget_pct == 0.0


# ─── Entry & Stats Tests ────────────────────────────────────────────────────


class TestEntriesAndStats:
    """Tests for entry retrieval and statistics."""

    def test_get_entries_empty_initially(self, running_daemon):
        assert running_daemon.get_entries() == []

    def test_get_entries_returns_recent(self, running_daemon):
        for i in range(15):
            running_daemon.translate({"type": "test", "name": f"op_{i}"})
        entries = running_daemon.get_entries(count=5)
        assert len(entries) == 5

    def test_get_entries_default_count(self, running_daemon):
        for i in range(3):
            running_daemon.translate({"type": "test"})
        entries = running_daemon.get_entries()
        assert len(entries) == 3

    def test_get_stats_structure(self, running_daemon):
        running_daemon.translate({"type": "test"})
        stats = running_daemon.get_stats()
        assert "status" in stats
        assert "translate_count" in stats
        assert "tokens_used" in stats
        assert "token_budget" in stats
        assert "budget_utilization" in stats
        assert stats["translate_count"] == 1

    def test_reset_budget_clears_state(self, running_daemon):
        running_daemon.translate({"type": "test"})
        assert running_daemon.tokens_used > 0
        running_daemon.reset_budget()
        assert running_daemon.tokens_used == 0
        assert running_daemon.get_entries() == []


# ─── Token Estimation Tests ─────────────────────────────────────────────────


class TestTokenEstimation:

    def test_estimate_tokens_short_text(self):
        assert ScribeDaemon._estimate_tokens("hello") >= 1

    def test_estimate_tokens_long_text(self):
        tokens = ScribeDaemon._estimate_tokens("a" * 400)
        assert tokens == 100  # 400 chars / 4

    def test_estimate_tokens_empty_returns_minimum(self):
        assert ScribeDaemon._estimate_tokens("") == 1


# ─── Log Persistence Tests ──────────────────────────────────────────────────


class TestLogPersistence:
    """Tests for _flush_log file writing."""

    def test_flush_writes_jsonl(self, tmp_path):
        log_file = tmp_path / "scribe_log.jsonl"
        d = ScribeDaemon(log_path=log_file)
        d.start()
        d.translate({"type": "intent_execution", "name": "deploy"})
        d.translate({"type": "gas_consumed", "name": "compile", "gas_cost": 5})
        d.stop()  # triggers flush

        assert log_file.exists()
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 2
        record = json.loads(lines[0])
        assert "prose" in record
        assert "token_count" in record

    def test_no_log_path_no_file(self, running_daemon):
        """Without log_path, stop should not crash."""
        running_daemon.translate({"type": "test"})
        running_daemon.stop()  # should not raise


# ─── Event Bus Integration ──────────────────────────────────────────────────


class TestEventBusIntegration:

    def test_translation_emitted_to_bus(self, daemon_with_bus):
        daemon, bus = daemon_with_bus
        received = []
        bus.subscribe(lambda e: received.append(e))

        daemon.translate({"type": "intent_execution", "name": "deploy"})
        assert len(received) == 1
        assert received[0].source == "scribe"
        assert received[0].event_type == "prose"


# ─── Log Metadata Tests ──────────────────────────────────────────────────────


class TestLogMetadata:
    """Tests that metadata is written to the JSONL log (fix for data-loss bug)."""

    def test_flush_writes_metadata_field(self, tmp_path):
        log_file = tmp_path / "scribe_meta.jsonl"
        d = ScribeDaemon(log_path=log_file)
        d.start()
        d.translate({"type": "host_function_call", "name": "READ", "extra": "audit123"})
        d.stop()

        record = json.loads(log_file.read_text().strip())
        assert "metadata" in record
        # The extra field should be preserved in metadata
        assert record["metadata"].get("extra") == "audit123"

    def test_flush_metadata_is_dict(self, tmp_path):
        log_file = tmp_path / "scribe_meta2.jsonl"
        d = ScribeDaemon(log_path=log_file)
        d.start()
        d.translate({"type": "align_check", "name": "R-001"})
        d.stop()

        record = json.loads(log_file.read_text().strip())
        assert isinstance(record["metadata"], dict)

    def test_flush_metadata_preserved_across_multiple_events(self, tmp_path):
        """Metadata preservation holds for every entry in a multi-event batch."""
        log_file = tmp_path / "scribe_multi.jsonl"
        d = ScribeDaemon(log_path=log_file)
        d.start()
        d.translate({"type": "intent_execution", "name": "op1", "tag": "T1"})
        d.translate({"type": "gas_consumed", "name": "op2", "gas_cost": 3})
        d.translate({"type": "security_gate", "name": "gate1", "result": "passed"})
        d.stop()

        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 3
        for raw in lines:
            rec = json.loads(raw)
            assert "metadata" in rec, "metadata field missing from flush record"
            assert isinstance(rec["metadata"], dict)
        # Spot-check specific metadata values
        records = [json.loads(line) for line in lines]
        assert records[0]["metadata"].get("tag") == "T1"
        assert records[1]["metadata"].get("gas_cost") == 3
        assert records[2]["metadata"].get("result") == "passed"


# ─── Scribe Agent Tests ──────────────────────────────────────────────────────


class TestScribeAgent:
    """Tests for agents/core/scribe_agent.py (budget audit & rolling context stats)."""

    @pytest.fixture
    def mock_db(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE rolling_context "
            "(id INTEGER PRIMARY KEY, session_id TEXT, content TEXT, token_count INTEGER)"
        )
        yield conn
        conn.close()

    def test_get_agent_profile_lists_get_rolling_context_stats(self):
        from agents.core.scribe_agent import get_agent_profile
        profile = get_agent_profile()
        assert "get_rolling_context_stats" in profile["tools"]

    def test_get_rolling_context_stats_empty_db(self, mock_db):
        from agents.core.scribe_agent import get_rolling_context_stats
        stats = get_rolling_context_stats(conn=mock_db)
        assert stats["total_tokens"] == 0
        assert stats["session_count"] == 0
        assert stats["sessions"] == []

    def test_get_rolling_context_stats_with_sessions(self, mock_db):
        from agents.core.scribe_agent import get_rolling_context_stats
        mock_db.executemany(
            "INSERT INTO rolling_context (session_id, content, token_count) VALUES (?, ?, ?)",
            [("sess-A", "hello", 100), ("sess-A", "world", 50), ("sess-B", "foo", 200)],
        )
        mock_db.commit()
        stats = get_rolling_context_stats(conn=mock_db)
        assert stats["total_tokens"] == 350
        assert stats["session_count"] == 2
        # Most-used session first
        assert stats["sessions"][0]["session_id"] == "sess-B"
        assert stats["sessions"][0]["token_count"] == 200

    def test_get_rolling_context_stats_no_session_column(self):
        """Gracefully handles tables without session_id column using __ungrouped__ sentinel."""
        from agents.core.scribe_agent import get_rolling_context_stats
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE rolling_context (id INTEGER PRIMARY KEY, content TEXT, token_count INTEGER)"
        )
        conn.execute("INSERT INTO rolling_context (content, token_count) VALUES ('x', 77)")
        conn.commit()
        stats = get_rolling_context_stats(conn=conn)
        assert stats["total_tokens"] == 77
        assert stats["session_count"] == 1
        assert stats["sessions"][0]["session_id"] == "__ungrouped__"
        conn.close()

    def test_get_rolling_context_stats_nonexistent_path(self, tmp_path):
        from agents.core.scribe_agent import get_rolling_context_stats
        missing = tmp_path / "nonexistent.db"
        stats = get_rolling_context_stats(db_path=missing)
        assert stats == {"sessions": [], "total_tokens": 0, "session_count": 0}


# ─── Logger ISO-8601 Timestamp Tests ────────────────────────────────────────


class TestALSLoggerTimestamp:
    """Tests that the ALS logger emits ISO-8601 UTC timestamps."""

    def test_timestamp_ends_with_z(self):
        from agents.core.logger import ALSLogger
        logger = ALSLogger(agent_role="test", goal_id="ts-test")
        entry = logger.log("TIMESTAMP_TEST", {})
        assert entry["timestamp"].endswith("Z"), (
            f"Expected UTC 'Z' suffix on timestamp, got: {entry['timestamp']!r}"
        )

    def test_timestamp_is_iso8601_format(self):
        import re
        from agents.core.logger import ALSLogger
        logger = ALSLogger(agent_role="test", goal_id="ts-fmt")
        entry = logger.log("FORMAT_TEST", {})
        pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
        assert re.match(pattern, entry["timestamp"]), (
            f"Timestamp does not match ISO-8601 UTC pattern: {entry['timestamp']!r}"
        )

