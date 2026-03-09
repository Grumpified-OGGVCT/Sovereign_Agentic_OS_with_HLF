"""
tests/test_arbiter.py — Unit tests for the Arbiter Daemon.

Tests dispute resolution: open/vote/resolve lifecycle, quorum logic,
immutable rule enforcement, timeout escalation, and rulings log.
"""

from __future__ import annotations

import json
import time
import pytest
from pathlib import Path

from agents.core.daemons import DaemonEventBus, DaemonStatus
from agents.core.daemons.arbiter import (
    ArbiterDaemon,
    DisputeOutcome,
    DisputeRecord,
    DisputeVote,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def daemon():
    return ArbiterDaemon(quorum=2)


@pytest.fixture
def running_daemon():
    d = ArbiterDaemon(quorum=2)
    d.start()
    return d


@pytest.fixture
def daemon_with_bus():
    bus = DaemonEventBus()
    d = ArbiterDaemon(event_bus=bus, quorum=2)
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
        d = ArbiterDaemon(enabled=False)
        d.start()
        assert d.status == DaemonStatus.STOPPED

    def test_open_dispute_while_stopped_raises(self, daemon):
        with pytest.raises(RuntimeError, match="not running"):
            daemon.open_dispute("R-004", "test", ["a", "b"])


# ─── Open Dispute Tests ─────────────────────────────────────────────────────


class TestOpenDispute:
    """Tests for dispute creation."""

    def test_open_returns_id(self, running_daemon):
        dispute_id = running_daemon.open_dispute("R-004", "data access", ["a1", "a2"])
        assert dispute_id.startswith("DSP-")

    def test_sequential_ids(self, running_daemon):
        id1 = running_daemon.open_dispute("R-004", "test1", ["a", "b"])
        id2 = running_daemon.open_dispute("R-005", "test2", ["c", "d"])
        assert id1 == "DSP-0001"
        assert id2 == "DSP-0002"

    def test_dispute_stored_as_active(self, running_daemon):
        dispute_id = running_daemon.open_dispute("R-004", "test", ["a", "b"])
        record = running_daemon.get_dispute(dispute_id)
        assert record is not None
        assert record.rule == "R-004"
        assert record.parties == ["a", "b"]

    def test_dispute_has_timestamp(self, running_daemon):
        dispute_id = running_daemon.open_dispute("R-004", "test", ["a"])
        record = running_daemon.get_dispute(dispute_id)
        assert record.timestamp != ""


# ─── Vote Tests ──────────────────────────────────────────────────────────────


class TestCastVote:
    """Tests for vote casting."""

    def test_valid_vote_accepted(self, running_daemon):
        did = running_daemon.open_dispute("R-004", "test", ["a", "b"])
        result = running_daemon.cast_vote(did, "a", "approve", "I agree")
        assert result is True

    def test_vote_on_unknown_dispute_rejected(self, running_daemon):
        result = running_daemon.cast_vote("DSP-9999", "a", "approve")
        assert result is False

    def test_invalid_position_rejected(self, running_daemon):
        did = running_daemon.open_dispute("R-004", "test", ["a"])
        result = running_daemon.cast_vote(did, "a", "abstain")
        assert result is False

    def test_duplicate_vote_rejected(self, running_daemon):
        did = running_daemon.open_dispute("R-004", "test", ["a", "b", "c"])
        running_daemon.cast_vote(did, "a", "approve")
        result = running_daemon.cast_vote(did, "a", "deny")
        assert result is False

    def test_quorum_triggers_resolution(self, running_daemon):
        did = running_daemon.open_dispute("R-004", "test", ["a", "b"])
        running_daemon.cast_vote(did, "a", "approve")
        running_daemon.cast_vote(did, "b", "approve")

        # Should be resolved now (moved from active to resolved)
        record = running_daemon.get_dispute(did)
        assert record.outcome == DisputeOutcome.APPROVED


# ─── Resolution Logic Tests ─────────────────────────────────────────────────


class TestResolution:
    """Tests for dispute resolution logic."""

    def test_majority_approve(self, running_daemon):
        did = running_daemon.open_dispute("R-004", "test", ["a", "b", "c"])
        running_daemon._quorum = 3
        running_daemon.cast_vote(did, "a", "approve")
        running_daemon.cast_vote(did, "b", "approve")
        running_daemon.cast_vote(did, "c", "deny")

        record = running_daemon.get_dispute(did)
        assert record.outcome == DisputeOutcome.APPROVED

    def test_majority_deny(self, running_daemon):
        did = running_daemon.open_dispute("R-004", "test", ["a", "b", "c"])
        running_daemon._quorum = 3
        running_daemon.cast_vote(did, "a", "deny")
        running_daemon.cast_vote(did, "b", "deny")
        running_daemon.cast_vote(did, "c", "approve")

        record = running_daemon.get_dispute(did)
        assert record.outcome == DisputeOutcome.DENIED

    def test_tie_goes_to_denial(self, running_daemon):
        """Conservative principle: tie breaks to enforcement."""
        did = running_daemon.open_dispute("R-004", "test", ["a", "b"])
        running_daemon.cast_vote(did, "a", "approve")
        running_daemon.cast_vote(did, "b", "deny")

        record = running_daemon.get_dispute(did)
        assert record.outcome == DisputeOutcome.DENIED
        assert "Tie" in record.ruling_rationale

    def test_immutable_rule_always_denied(self, running_daemon):
        """R-001, R-002, R-003 cannot be overridden by votes."""
        did = running_daemon.open_dispute("R-001", "sovereign action", ["a", "b"])
        running_daemon.cast_vote(did, "a", "approve")
        running_daemon.cast_vote(did, "b", "approve")

        record = running_daemon.get_dispute(did)
        assert record.outcome == DisputeOutcome.DENIED
        assert "immutable" in record.ruling_rationale

    def test_resolution_has_time_ms(self, running_daemon):
        did = running_daemon.open_dispute("R-004", "test", ["a", "b"])
        running_daemon.cast_vote(did, "a", "approve")
        running_daemon.cast_vote(did, "b", "approve")

        record = running_daemon.get_dispute(did)
        assert record.resolution_time_ms >= 0


# ─── Timeout & Escalation Tests ─────────────────────────────────────────────


class TestEscalation:
    """Tests for timeout escalation."""

    def test_stop_escalates_active_disputes(self, running_daemon):
        did = running_daemon.open_dispute("R-004", "test", ["a", "b"])
        running_daemon.stop()

        record = running_daemon.get_dispute(did)
        assert record.outcome == DisputeOutcome.ESCALATED
        assert "shutdown" in record.ruling_rationale

    def test_manual_escalation(self, running_daemon):
        did = running_daemon.open_dispute("R-004", "test", ["a", "b"])
        running_daemon._escalate(did, reason="custom_reason")

        record = running_daemon.get_dispute(did)
        assert record.outcome == DisputeOutcome.ESCALATED

    def test_timeout_check_escalates_old_disputes(self, running_daemon):
        """Disputes older than timeout should be auto-escalated."""
        # Use a very short timeout
        running_daemon._escalation_timeout_ms = 1
        did = running_daemon.open_dispute("R-004", "test", ["a"])

        time.sleep(0.01)  # ensure timeout exceeded
        escalated = running_daemon.check_timeouts()
        assert did in escalated

        record = running_daemon.get_dispute(did)
        assert record.outcome == DisputeOutcome.TIMED_OUT


# ─── Stats Tests ─────────────────────────────────────────────────────────────


class TestStats:

    def test_stats_structure(self, running_daemon):
        stats = running_daemon.get_stats()
        assert "status" in stats
        assert "active_disputes" in stats
        assert "resolved_disputes" in stats
        assert "total_disputes" in stats
        assert "outcomes" in stats

    def test_stats_count_disputes(self, running_daemon):
        running_daemon.open_dispute("R-004", "t1", ["a", "b"])
        running_daemon.open_dispute("R-005", "t2", ["c", "d"])
        stats = running_daemon.get_stats()
        assert stats["active_disputes"] == 2
        assert stats["total_disputes"] == 2

    def test_get_dispute_returns_none_for_unknown(self, running_daemon):
        assert running_daemon.get_dispute("NONEXISTENT") is None


# ─── Rulings Log Persistence ────────────────────────────────────────────────


class TestRulingsPersistence:

    def test_flush_writes_jsonl(self, tmp_path):
        log_file = tmp_path / "arbiter_rulings.jsonl"
        d = ArbiterDaemon(quorum=2, rulings_path=log_file)
        d.start()

        did = d.open_dispute("R-004", "test", ["a", "b"])
        d.cast_vote(did, "a", "approve")
        d.cast_vote(did, "b", "approve")
        d.stop()

        assert log_file.exists()
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["dispute_id"] == did
        assert record["outcome"] == "approved"


# ─── Event Bus Integration ──────────────────────────────────────────────────


class TestEventBusIntegration:

    def test_dispute_opened_emitted(self, daemon_with_bus):
        daemon, bus = daemon_with_bus
        received = []
        bus.subscribe(lambda e: received.append(e))

        daemon.open_dispute("R-004", "test", ["a"])
        assert len(received) == 1
        assert received[0].event_type == "dispute_opened"

    def test_ruling_emitted_on_resolution(self, daemon_with_bus):
        daemon, bus = daemon_with_bus
        received = []
        bus.subscribe(lambda e: received.append(e))

        did = daemon.open_dispute("R-004", "test", ["a", "b"])
        daemon.cast_vote(did, "a", "approve")
        daemon.cast_vote(did, "b", "approve")

        ruling_events = [e for e in received if e.event_type == "ruling"]
        assert len(ruling_events) == 1
        assert ruling_events[0].data["outcome"] == "approved"

    def test_escalation_emitted_to_bus(self, daemon_with_bus):
        daemon, bus = daemon_with_bus
        received = []
        bus.subscribe(lambda e: received.append(e))

        did = daemon.open_dispute("R-004", "test", ["a"])
        daemon._escalate(did, reason="timeout")

        esc_events = [e for e in received if e.event_type == "escalation"]
        assert len(esc_events) == 1
        assert esc_events[0].severity == "critical"
