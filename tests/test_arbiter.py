"""
tests/test_arbiter.py — Unit tests for the Arbiter Daemon.

Tests dispute resolution: open/vote/resolve lifecycle, quorum logic,
immutable rule enforcement, timeout escalation, and rulings log.
Also covers arbiter_agent adjudication logic (SECURITY_ALERT, BUDGET_GATE,
DEAD_LETTER, GAS_SPIKE, and ALIGN-blocked payloads).
"""

from __future__ import annotations

import json
import time
import pytest
from pathlib import Path
from unittest.mock import patch

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


# ─── ArbiterDaemon: list_active_disputes ────────────────────────────────────


class TestListActiveDisputes:
    """Tests for the list_active_disputes() convenience method."""

    def test_empty_at_start(self, running_daemon):
        assert running_daemon.list_active_disputes() == []

    def test_returns_open_disputes(self, running_daemon):
        running_daemon.open_dispute("R-004", "a", ["x"])
        running_daemon.open_dispute("R-005", "b", ["y"])
        active = running_daemon.list_active_disputes()
        assert len(active) == 2
        rules = {r.rule for r in active}
        assert rules == {"R-004", "R-005"}

    def test_resolved_not_included(self, running_daemon):
        did = running_daemon.open_dispute("R-004", "test", ["a", "b"])
        running_daemon.cast_vote(did, "a", "approve")
        running_daemon.cast_vote(did, "b", "approve")
        # dispute is now resolved
        assert running_daemon.list_active_disputes() == []

    def test_returns_snapshot_not_live_reference(self, running_daemon):
        """Mutating the returned list must not affect internal state."""
        running_daemon.open_dispute("R-004", "x", ["a"])
        snapshot = running_daemon.list_active_disputes()
        snapshot.clear()
        # Internal dict should be untouched
        assert len(running_daemon.list_active_disputes()) == 1


# ─── ArbiterDaemon: _flush_rulings no-duplicate guarantee ───────────────────


class TestFlushNoDuplicates:
    """_flush_rulings() must not write the same record more than once."""

    def test_double_stop_writes_one_line(self, tmp_path):
        log_file = tmp_path / "arbiter_rulings.jsonl"
        d = ArbiterDaemon(quorum=2, rulings_path=log_file)
        d.start()

        did = d.open_dispute("R-004", "test", ["a", "b"])
        d.cast_vote(did, "a", "approve")
        d.cast_vote(did, "b", "approve")

        # First stop flushes + stops
        d.stop()
        # Second stop should not re-write
        d.start()
        d.stop()

        lines = [ln for ln in log_file.read_text().strip().split("\n") if ln]
        assert len(lines) == 1, f"Expected 1 line, got {len(lines)}"

    def test_incremental_flush(self, tmp_path):
        """Disputes resolved after first flush appear in second flush only."""
        log_file = tmp_path / "arbiter_rulings.jsonl"
        d = ArbiterDaemon(quorum=2, rulings_path=log_file)
        d.start()

        # Resolve dispute #1 and flush via stop()
        did1 = d.open_dispute("R-004", "first", ["a", "b"])
        d.cast_vote(did1, "a", "approve")
        d.cast_vote(did1, "b", "approve")
        d.stop()

        # Reopen daemon and resolve dispute #2
        d.start()
        did2 = d.open_dispute("R-005", "second", ["c", "d"])
        d.cast_vote(did2, "c", "deny")
        d.cast_vote(did2, "d", "deny")
        d.stop()

        lines = [ln for ln in log_file.read_text().strip().split("\n") if ln]
        assert len(lines) == 2
        records = [json.loads(ln) for ln in lines]
        ids = [r["dispute_id"] for r in records]
        assert did1 in ids
        assert did2 in ids


# ─── ArbiterDaemon: additional immutable rules ──────────────────────────────


class TestImmutableRulesComplete:
    """Verify all three immutable rules (R-001, R-002, R-003) are enforced."""

    @pytest.mark.parametrize("rule", ["R-001", "R-002", "R-003"])
    def test_immutable_rule_cannot_be_approved(self, running_daemon, rule):
        did = running_daemon.open_dispute(rule, "sensitive action", ["a", "b"])
        running_daemon.cast_vote(did, "a", "approve")
        running_daemon.cast_vote(did, "b", "approve")

        record = running_daemon.get_dispute(did)
        assert record.outcome == DisputeOutcome.DENIED
        assert "immutable" in record.ruling_rationale.lower()

    def test_non_immutable_rule_can_be_approved(self, running_daemon):
        """R-004 is mutable and should respect majority vote."""
        did = running_daemon.open_dispute("R-004", "data access", ["a", "b"])
        running_daemon.cast_vote(did, "a", "approve")
        running_daemon.cast_vote(did, "b", "approve")

        record = running_daemon.get_dispute(did)
        assert record.outcome == DisputeOutcome.APPROVED


# ─── arbiter_agent.adjudicate() unit tests ─────────────────────────────────


class TestArbiterAgentAdjudicate:
    """
    Unit tests for agents.core.arbiter_agent.adjudicate().

    enforce_align is patched to isolate filesystem access so tests stay fast.
    """

    @pytest.fixture(autouse=True)
    def _patch_align(self):
        """Default: ALIGN does not block."""
        with patch(
            "agents.core.arbiter_agent.enforce_align",
            return_value=(False, ""),
        ):
            yield

    def test_security_alert_critical_quarantine(self):
        from agents.core.arbiter_agent import adjudicate, VERDICT_QUARANTINE
        v = adjudicate("SECURITY_ALERT", {"severity": "CRITICAL", "rule_id": "R-001"})
        assert v.verdict == VERDICT_QUARANTINE
        assert v.event_type == "SECURITY_ALERT"

    def test_security_alert_non_critical_escalate(self):
        from agents.core.arbiter_agent import adjudicate, VERDICT_ESCALATE
        v = adjudicate("SECURITY_ALERT", {"severity": "HIGH", "rule_id": "R-006"})
        assert v.verdict == VERDICT_ESCALATE

    def test_security_alert_default_severity_escalate(self):
        from agents.core.arbiter_agent import adjudicate, VERDICT_ESCALATE
        v = adjudicate("SECURITY_ALERT", {})
        assert v.verdict == VERDICT_ESCALATE

    def test_budget_gate_quarantine_at_threshold(self):
        from agents.core.arbiter_agent import adjudicate, VERDICT_QUARANTINE
        v = adjudicate("BUDGET_GATE", {"pct": 0.99})
        assert v.verdict == VERDICT_QUARANTINE

    def test_budget_gate_escalate_at_threshold(self):
        from agents.core.arbiter_agent import adjudicate, VERDICT_ESCALATE
        v = adjudicate("BUDGET_GATE", {"pct": 0.92})
        assert v.verdict == VERDICT_ESCALATE

    def test_budget_gate_allow_below_threshold(self):
        from agents.core.arbiter_agent import adjudicate, VERDICT_ALLOW
        v = adjudicate("BUDGET_GATE", {"pct": 0.50})
        assert v.verdict == VERDICT_ALLOW

    def test_dead_letter_quarantine(self):
        from agents.core.arbiter_agent import adjudicate, VERDICT_QUARANTINE
        v = adjudicate("DEAD_LETTER", {"origin": "memory-scribe", "retries": 3})
        assert v.verdict == VERDICT_QUARANTINE
        assert v.event_type == "DEAD_LETTER"
        assert "memory-scribe" in v.justification

    def test_dead_letter_unknown_origin(self):
        from agents.core.arbiter_agent import adjudicate, VERDICT_QUARANTINE
        v = adjudicate("DEAD_LETTER", {})
        assert v.verdict == VERDICT_QUARANTINE
        assert "unknown" in v.justification

    def test_gas_spike_escalate(self):
        from agents.core.arbiter_agent import adjudicate, VERDICT_ESCALATE
        v = adjudicate("GAS_SPIKE", {"agent": "tool-forge", "ratio": 4.2})
        assert v.verdict == VERDICT_ESCALATE
        assert v.event_type == "GAS_SPIKE"
        assert "tool-forge" in v.justification

    def test_gas_spike_missing_fields(self):
        from agents.core.arbiter_agent import adjudicate, VERDICT_ESCALATE
        v = adjudicate("GAS_SPIKE", {})
        assert v.verdict == VERDICT_ESCALATE

    def test_unknown_event_escalates(self):
        from agents.core.arbiter_agent import adjudicate, VERDICT_ESCALATE
        v = adjudicate("TOTALLY_UNKNOWN", {})
        assert v.verdict == VERDICT_ESCALATE

    def test_align_block_overrides_all(self):
        """ALIGN Ledger must override even a BUDGET_GATE ALLOW scenario."""
        from agents.core.arbiter_agent import adjudicate, VERDICT_QUARANTINE
        with patch(
            "agents.core.arbiter_agent.enforce_align",
            return_value=(True, "R-007"),
        ):
            v = adjudicate("BUDGET_GATE", {"pct": 0.10})
        assert v.verdict == VERDICT_QUARANTINE
        assert v.rule_id == "R-007"

    def test_verdict_event_type_preserved(self):
        from agents.core.arbiter_agent import adjudicate
        v = adjudicate("DEAD_LETTER", {"origin": "scribe"})
        assert v.event_type == "DEAD_LETTER"

    def test_payload_as_string(self):
        """adjudicate() must handle str payloads gracefully (treated as opaque)."""
        from agents.core.arbiter_agent import adjudicate, VERDICT_ESCALATE
        v = adjudicate("GAS_SPIKE", '{"agent": "x", "ratio": 5.0}')
        assert v.verdict == VERDICT_ESCALATE
        # String payload is treated as non-dict; agent defaults to "unknown"
        assert "unknown" in v.justification
