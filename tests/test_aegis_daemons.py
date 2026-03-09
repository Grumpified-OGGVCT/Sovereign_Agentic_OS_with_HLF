"""
Tests for Aegis-Nexus Runtime Daemons.

Covers:
  - Sentinel: pattern detection, alert emission, threshold config
  - Scribe: prose generation, token budget enforcement, log format
  - Arbiter: dispute resolution, vote counting, ALIGN precedence, dead-man switch
  - DaemonManager: start/stop lifecycle, health check
  - DaemonEventBus: event emission, subscription, filtering
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from agents.core.daemons import (
    DaemonEvent,
    DaemonEventBus,
    DaemonManager,
    DaemonStatus,
)
from agents.core.daemons.sentinel import (
    AlertSeverity,
    SentinelAlert,
    SentinelDaemon,
)
from agents.core.daemons.scribe import (
    ProseEntry,
    ScribeDaemon,
)
from agents.core.daemons.arbiter import (
    ArbiterDaemon,
    DisputeOutcome,
    DisputeRecord,
    DisputeVote,
)


# ─── DaemonEventBus ─────────────────────────────────────────────────────────


class TestDaemonEventBus:
    """Test the in-process event bus."""

    def test_emit_and_recent(self):
        bus = DaemonEventBus()
        event = DaemonEvent(source="test", event_type="ping", severity="info")
        bus.emit(event)
        assert len(bus.recent()) == 1
        assert bus.recent()[0].source == "test"

    def test_recent_filter_by_source(self):
        bus = DaemonEventBus()
        bus.emit(DaemonEvent(source="sentinel", event_type="alert", severity="critical"))
        bus.emit(DaemonEvent(source="scribe", event_type="prose", severity="info"))
        bus.emit(DaemonEvent(source="sentinel", event_type="alert", severity="warning"))

        sentinel_events = bus.recent(source="sentinel")
        assert len(sentinel_events) == 2
        scribe_events = bus.recent(source="scribe")
        assert len(scribe_events) == 1

    def test_max_events_cap(self):
        bus = DaemonEventBus(max_events=5)
        for i in range(10):
            bus.emit(DaemonEvent(source="test", event_type=str(i), severity="info"))
        assert len(bus._events) == 5

    def test_subscribe_handler(self):
        bus = DaemonEventBus()
        received = []
        bus.subscribe(lambda e: received.append(e))
        bus.emit(DaemonEvent(source="test", event_type="ping", severity="info"))
        assert len(received) == 1

    def test_clear(self):
        bus = DaemonEventBus()
        bus.emit(DaemonEvent(source="test", event_type="ping", severity="info"))
        bus.clear()
        assert len(bus._events) == 0

    def test_timestamp_auto_filled(self):
        bus = DaemonEventBus()
        event = DaemonEvent(source="test", event_type="ping", severity="info")
        bus.emit(event)
        assert event.timestamp != ""


# ─── Sentinel Daemon ────────────────────────────────────────────────────────


class TestSentinelDaemon:
    """Test the Sentinel anomaly detection daemon."""

    @pytest.fixture
    def bus(self):
        return DaemonEventBus()

    @pytest.fixture
    def sentinel(self, bus):
        s = SentinelDaemon(event_bus=bus, check_interval_ms=1000)
        s.start()
        return s

    def test_start_stop_lifecycle(self, bus):
        s = SentinelDaemon(event_bus=bus)
        assert s.status == DaemonStatus.STOPPED
        s.start()
        assert s.status == DaemonStatus.RUNNING
        s.stop()
        assert s.status == DaemonStatus.STOPPED

    def test_disabled_daemon_skips_start(self, bus):
        s = SentinelDaemon(event_bus=bus, enabled=False)
        s.start()
        assert s.status == DaemonStatus.STOPPED

    def test_check_returns_empty_when_stopped(self, bus):
        s = SentinelDaemon(event_bus=bus)
        result = s.check({"type": "test"})
        assert result == []

    def test_privilege_escalation_detected(self, sentinel):
        alerts = sentinel.check({
            "tier": "hearth",
            "required_tier": "sovereign",
            "source": "test_agent",
        })
        assert len(alerts) == 1
        assert alerts[0].pattern == "privilege_escalation"
        assert alerts[0].severity == AlertSeverity.CRITICAL

    def test_privilege_escalation_same_tier_ok(self, sentinel):
        alerts = sentinel.check({
            "tier": "forge",
            "required_tier": "forge",
        })
        escalation_alerts = [a for a in alerts if a.pattern == "privilege_escalation"]
        assert len(escalation_alerts) == 0

    def test_injection_eval_detected(self, sentinel):
        alerts = sentinel.check({
            "payload": 'some_data; eval("malicious code")',
            "source": "user_input",
        })
        injection_alerts = [a for a in alerts if a.pattern == "injection_detected"]
        assert len(injection_alerts) == 1
        assert injection_alerts[0].severity == AlertSeverity.CRITICAL

    def test_injection_subprocess_detected(self, sentinel):
        alerts = sentinel.check({
            "payload": "import subprocess.run",
        })
        injection_alerts = [a for a in alerts if a.pattern == "injection_detected"]
        assert len(injection_alerts) == 1

    def test_clean_payload_no_injection(self, sentinel):
        alerts = sentinel.check({
            "payload": "normal text content here",
        })
        injection_alerts = [a for a in alerts if a.pattern == "injection_detected"]
        assert len(injection_alerts) == 0

    def test_gas_spike_detected(self, sentinel):
        # Build baseline
        for gas in [10, 12, 11, 13, 10]:
            sentinel.check({"gas_used": gas})

        # Spike
        alerts = sentinel.check({"gas_used": 100})
        gas_alerts = [a for a in alerts if a.pattern == "gas_spike"]
        assert len(gas_alerts) == 1
        assert gas_alerts[0].severity == AlertSeverity.WARNING

    def test_gas_normal_no_alert(self, sentinel):
        for gas in [10, 12, 11, 13, 10, 11]:
            alerts = sentinel.check({"gas_used": gas})
            gas_alerts = [a for a in alerts if a.pattern == "gas_spike"]
            assert len(gas_alerts) == 0

    def test_align_violation_detected(self, sentinel):
        alerts = sentinel.check({
            "align_violation": {"rule": "R-004", "action": "blocked"},
        })
        align_alerts = [a for a in alerts if a.pattern == "align_violation"]
        assert len(align_alerts) == 1

    def test_get_alerts_filtered(self, sentinel):
        sentinel.check({"payload": 'eval("x")'})
        sentinel.check({"align_violation": {"rule": "R-005"}})

        critical = sentinel.get_alerts(severity=AlertSeverity.CRITICAL)
        warning = sentinel.get_alerts(severity=AlertSeverity.WARNING)
        assert len(critical) >= 1
        assert all(a.severity == AlertSeverity.CRITICAL for a in critical)

    def test_get_stats(self, sentinel):
        sentinel.check({"type": "test"})
        stats = sentinel.get_stats()
        assert stats["status"] == "running"
        assert stats["check_count"] == 1

    def test_alerts_emitted_to_bus(self, sentinel, bus):
        sentinel.check({"payload": 'eval("x")'})
        events = bus.recent(source="sentinel")
        assert len(events) >= 1
        assert events[0].event_type == "alert"


# ─── Scribe Daemon ──────────────────────────────────────────────────────────


class TestScribeDaemon:
    """Test the Scribe InsAIts prose translation daemon."""

    @pytest.fixture
    def bus(self):
        return DaemonEventBus()

    @pytest.fixture
    def scribe(self, bus):
        s = ScribeDaemon(event_bus=bus, tier="hearth")
        s.start()
        return s

    def test_start_stop_lifecycle(self, bus):
        s = ScribeDaemon(event_bus=bus)
        assert s.status == DaemonStatus.STOPPED
        s.start()
        assert s.status == DaemonStatus.RUNNING
        s.stop()
        assert s.status == DaemonStatus.STOPPED

    def test_translate_intent_execution(self, scribe):
        entry = scribe.translate({
            "type": "intent_execution",
            "name": "deploy_service",
        })
        assert entry is not None
        assert "deploy_service" in entry.prose
        assert entry.token_count > 0

    def test_translate_host_function_call(self, scribe):
        entry = scribe.translate({
            "type": "host_function_call",
            "name": "HTTP_GET",
            "args": {"url": "https://example.com"},
            "backend": "builtin",
        })
        assert "HTTP_GET" in entry.prose

    def test_translate_unknown_type(self, scribe):
        entry = scribe.translate({
            "type": "custom_event",
            "name": "something",
        })
        assert entry is not None
        assert "custom_event" in entry.prose

    def test_token_budget_enforcement(self, bus):
        scribe = ScribeDaemon(event_bus=bus, tier="hearth", token_budget_pct=0.80)
        scribe.start()
        budget = scribe.token_budget
        assert budget == int(8192 * 0.80)
        assert scribe.tokens_remaining == budget

    def test_token_counting(self, scribe):
        scribe.translate({"type": "test", "name": "op1"})
        assert scribe.tokens_used > 0
        assert scribe.tokens_remaining < scribe.token_budget

    def test_budget_constrained_summarization(self, bus):
        """When budget is nearly exhausted, prose should be summarized."""
        scribe = ScribeDaemon(event_bus=bus, tier="hearth", token_budget_pct=0.01)
        scribe.start()

        # Fill up the budget
        for i in range(100):
            scribe.translate({
                "type": "host_function_call",
                "name": f"FUNC_{i}",
                "args": {"data": "x" * 100},
                "backend": "builtin",
            })

        # Check that budget-constrained entries exist
        entries = scribe.get_entries(count=5)
        has_constrained = any("[budget-constrained]" in e.prose for e in entries)
        assert has_constrained

    def test_get_entries(self, scribe):
        scribe.translate({"type": "test1", "name": "a"})
        scribe.translate({"type": "test2", "name": "b"})
        entries = scribe.get_entries(count=1)
        assert len(entries) == 1
        assert entries[0].event_type == "test2"

    def test_get_stats(self, scribe):
        scribe.translate({"type": "test", "name": "op"})
        stats = scribe.get_stats()
        assert stats["status"] == "running"
        assert stats["translate_count"] == 1
        assert stats["budget_utilization"] > 0

    def test_reset_budget(self, scribe):
        scribe.translate({"type": "test", "name": "op"})
        assert scribe.tokens_used > 0
        scribe.reset_budget()
        assert scribe.tokens_used == 0

    def test_prose_emitted_to_bus(self, scribe, bus):
        scribe.translate({"type": "test", "name": "op"})
        events = bus.recent(source="scribe")
        assert len(events) >= 1
        assert events[0].event_type == "prose"

    def test_translate_returns_none_when_stopped(self, bus):
        scribe = ScribeDaemon(event_bus=bus)
        result = scribe.translate({"type": "test"})
        assert result is None

    def test_log_flush(self, bus, tmp_path):
        log_file = tmp_path / "scribe.jsonl"
        scribe = ScribeDaemon(event_bus=bus, log_path=log_file)
        scribe.start()
        scribe.translate({"type": "test", "name": "op"})
        scribe.stop()

        assert log_file.exists()
        import json
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert "prose" in data


# ─── Arbiter Daemon ──────────────────────────────────────────────────────────


class TestArbiterDaemon:
    """Test the Arbiter dispute resolution daemon."""

    @pytest.fixture
    def bus(self):
        return DaemonEventBus()

    @pytest.fixture
    def arbiter(self, bus):
        a = ArbiterDaemon(event_bus=bus, quorum=2)
        a.start()
        return a

    def test_start_stop_lifecycle(self, bus):
        a = ArbiterDaemon(event_bus=bus)
        assert a.status == DaemonStatus.STOPPED
        a.start()
        assert a.status == DaemonStatus.RUNNING
        a.stop()
        assert a.status == DaemonStatus.STOPPED

    def test_open_dispute(self, arbiter):
        dispute_id = arbiter.open_dispute(
            rule="R-004",
            subject="gas limit override",
            parties=["agent_a", "agent_b"],
        )
        assert dispute_id.startswith("DSP-")
        record = arbiter.get_dispute(dispute_id)
        assert record is not None
        assert record.rule == "R-004"
        assert len(record.parties) == 2

    def test_cast_vote(self, arbiter):
        dispute_id = arbiter.open_dispute(
            rule="R-005", subject="test", parties=["a", "b"],
        )
        result = arbiter.cast_vote(dispute_id, "a", "approve", "seems fine")
        assert result is True

    def test_duplicate_vote_rejected(self, arbiter):
        dispute_id = arbiter.open_dispute(
            rule="R-005", subject="test", parties=["a", "b"],
        )
        arbiter.cast_vote(dispute_id, "a", "approve")
        result = arbiter.cast_vote(dispute_id, "a", "deny")
        assert result is False

    def test_invalid_position_rejected(self, arbiter):
        dispute_id = arbiter.open_dispute(
            rule="R-005", subject="test", parties=["a"],
        )
        result = arbiter.cast_vote(dispute_id, "a", "maybe")
        assert result is False

    def test_majority_approve_resolution(self, arbiter):
        dispute_id = arbiter.open_dispute(
            rule="R-005", subject="test", parties=["a", "b", "c"],
        )
        arbiter.cast_vote(dispute_id, "a", "approve")
        arbiter.cast_vote(dispute_id, "b", "approve")  # quorum reached → auto-resolve

        record = arbiter.get_dispute(dispute_id)
        assert record.outcome == DisputeOutcome.APPROVED

    def test_majority_deny_resolution(self, arbiter):
        dispute_id = arbiter.open_dispute(
            rule="R-005", subject="test", parties=["a", "b", "c"],
        )
        arbiter.cast_vote(dispute_id, "a", "deny")
        arbiter.cast_vote(dispute_id, "b", "deny")

        record = arbiter.get_dispute(dispute_id)
        assert record.outcome == DisputeOutcome.DENIED

    def test_tie_goes_to_enforcement(self, arbiter):
        dispute_id = arbiter.open_dispute(
            rule="R-005", subject="test", parties=["a", "b"],
        )
        arbiter.cast_vote(dispute_id, "a", "approve")
        arbiter.cast_vote(dispute_id, "b", "deny")

        record = arbiter.get_dispute(dispute_id)
        assert record.outcome == DisputeOutcome.DENIED
        assert "Tie" in record.ruling_rationale

    def test_immutable_rule_always_denied(self, arbiter):
        dispute_id = arbiter.open_dispute(
            rule="R-001",  # immutable
            subject="bypass human approval",
            parties=["a", "b"],
        )
        arbiter.cast_vote(dispute_id, "a", "approve")
        arbiter.cast_vote(dispute_id, "b", "approve")

        record = arbiter.get_dispute(dispute_id)
        assert record.outcome == DisputeOutcome.DENIED
        assert "immutable" in record.ruling_rationale.lower()

    def test_get_stats(self, arbiter):
        arbiter.open_dispute(rule="R-005", subject="test1", parties=["a", "b"])
        stats = arbiter.get_stats()
        assert stats["status"] == "running"
        assert stats["active_disputes"] == 1
        assert stats["total_disputes"] == 1

    def test_dispute_emitted_to_bus(self, arbiter, bus):
        arbiter.open_dispute(rule="R-005", subject="test", parties=["a"])
        events = bus.recent(source="arbiter")
        assert len(events) >= 1
        assert events[0].event_type == "dispute_opened"

    def test_ruling_emitted_to_bus(self, arbiter, bus):
        dispute_id = arbiter.open_dispute(
            rule="R-005", subject="test", parties=["a", "b"],
        )
        arbiter.cast_vote(dispute_id, "a", "approve")
        arbiter.cast_vote(dispute_id, "b", "approve")

        ruling_events = [e for e in bus.recent(source="arbiter") if e.event_type == "ruling"]
        assert len(ruling_events) >= 1

    def test_stop_escalates_active_disputes(self, bus):
        arbiter = ArbiterDaemon(event_bus=bus, quorum=3)
        arbiter.start()
        dispute_id = arbiter.open_dispute(rule="R-005", subject="test", parties=["a"])
        arbiter.stop()

        record = arbiter.get_dispute(dispute_id)
        assert record.outcome == DisputeOutcome.ESCALATED

    def test_vote_on_unknown_dispute(self, arbiter):
        result = arbiter.cast_vote("DSP-9999", "a", "approve")
        assert result is False

    def test_open_dispute_when_stopped(self, bus):
        arbiter = ArbiterDaemon(event_bus=bus)
        with pytest.raises(RuntimeError, match="not running"):
            arbiter.open_dispute(rule="R-001", subject="test", parties=[])

    def test_rulings_log_flush(self, bus, tmp_path):
        log_file = tmp_path / "rulings.jsonl"
        arbiter = ArbiterDaemon(event_bus=bus, quorum=2, rulings_path=log_file)
        arbiter.start()
        dispute_id = arbiter.open_dispute(rule="R-005", subject="test", parties=["a", "b"])
        arbiter.cast_vote(dispute_id, "a", "approve")
        arbiter.cast_vote(dispute_id, "b", "approve")
        arbiter.stop()

        assert log_file.exists()
        import json
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["outcome"] == "approved"


# ─── DaemonManager ──────────────────────────────────────────────────────────


class TestDaemonManager:
    """Test the DaemonManager coordinator."""

    @pytest.fixture
    def manager(self):
        config = {
            "sentinel": {"enabled": True, "check_interval_ms": 1000},
            "scribe": {"enabled": True, "token_budget_pct": 0.80},
            "arbiter": {"enabled": True, "escalation_timeout_ms": 5000},
        }
        return DaemonManager(config=config)

    def test_start_all(self, manager):
        results = manager.start_all()
        assert results["sentinel"] == "running"
        assert results["scribe"] == "running"
        assert results["arbiter"] == "running"

    def test_stop_all(self, manager):
        manager.start_all()
        results = manager.stop_all()
        assert results["sentinel"] == "stopped"
        assert results["scribe"] == "stopped"
        assert results["arbiter"] == "stopped"

    def test_status(self, manager):
        status = manager.status()
        assert all(v == "stopped" for v in status.values())
        manager.start_all()
        status = manager.status()
        assert all(v == "running" for v in status.values())

    def test_health_check(self, manager):
        manager.start_all()
        health = manager.health_check()
        assert health["all_healthy"] is True
        assert health["event_bus_size"] == 0

    def test_shared_event_bus(self, manager):
        manager.start_all()
        # Sentinel alert should be visible to manager's bus
        manager.sentinel.check({"payload": 'eval("x")'})
        events = manager.event_bus.recent(source="sentinel")
        assert len(events) >= 1

    def test_disabled_daemon(self):
        config = {
            "sentinel": {"enabled": False},
            "scribe": {"enabled": True},
            "arbiter": {"enabled": True},
        }
        manager = DaemonManager(config=config)
        results = manager.start_all()
        assert results["sentinel"] == "stopped"
        assert results["scribe"] == "running"
