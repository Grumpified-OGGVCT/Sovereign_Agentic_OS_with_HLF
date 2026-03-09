"""
tests/test_daemon_bridge.py — Unit tests for the Daemon ↔ SpindleEventBus bridge.

Tests event translation, per-agent gas accounting, bridge lifecycle,
and bidirectional event flow.
"""

from __future__ import annotations

import time
import pytest

from agents.core.event_bus import EventType, SpindleEvent, SpindleEventBus
from agents.core.daemons import DaemonEvent, DaemonEventBus, DaemonManager
from agents.core.daemons.daemon_bridge import (
    AgentGasAccount,
    DaemonBridge,
    _daemon_event_to_spindle,
    _spindle_event_to_daemon,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def spindle_bus():
    return SpindleEventBus(history_limit=100)


@pytest.fixture
def daemon_manager():
    return DaemonManager(config={})


@pytest.fixture
def bridge(spindle_bus, daemon_manager):
    return DaemonBridge(
        spindle_bus, daemon_manager, default_gas_budget=1000
    )


# ─── Event Translation Tests ────────────────────────────────────────────────


class TestEventTranslation:
    """Tests for DaemonEvent ↔ SpindleEvent translation."""

    def test_daemon_to_spindle_sentinel_alert(self):
        """Sentinel alert should map to VALIDATION_REJECTED."""
        event = DaemonEvent(
            source="sentinel",
            event_type="sentinel_alert",
            severity="critical",
            data={"pattern": "injection"},
        )
        result = _daemon_event_to_spindle(event)
        assert result.event_type == EventType.VALIDATION_REJECTED
        assert result.source == "daemon:sentinel"
        assert result.payload["daemon_event_type"] == "sentinel_alert"

    def test_daemon_to_spindle_arbiter_ruling(self):
        """Arbiter ruling should map to REALIGNMENT_TRIGGERED."""
        event = DaemonEvent(
            source="arbiter",
            event_type="arbiter_ruling",
            severity="warning",
            data={"outcome": "escalated"},
        )
        result = _daemon_event_to_spindle(event)
        assert result.event_type == EventType.REALIGNMENT_TRIGGERED

    def test_daemon_to_spindle_unknown_type_defaults(self):
        """Unknown daemon event types should default to NODE_COMPLETED."""
        event = DaemonEvent(
            source="custom",
            event_type="custom_unknown_event",
            severity="info",
            data={},
        )
        result = _daemon_event_to_spindle(event)
        assert result.event_type == EventType.NODE_COMPLETED

    def test_spindle_to_daemon_preserves_payload(self):
        """SpindleEvent → DaemonEvent should preserve payload data."""
        event = SpindleEvent(
            event_type=EventType.NODE_COMPLETED,
            source="agent_a",
            payload={"gas_used": 5, "result": "ok"},
        )
        result = _spindle_event_to_daemon(event)
        assert result.event_type == "spindle:node_completed"
        assert result.source == "agent_a"
        assert result.data["gas_used"] == 5

    def test_spindle_to_daemon_has_severity(self):
        """Translated DaemonEvent should have severity set."""
        event = SpindleEvent(
            event_type=EventType.NODE_STARTED,
            source="test",
            payload={},
        )
        result = _spindle_event_to_daemon(event)
        assert result.severity == "info"


# ─── Gas Accounting Tests ────────────────────────────────────────────────────


class TestGasAccounting:
    """Tests for per-agent gas tracking."""

    def test_gas_account_initial_state(self):
        """New account should start at zero."""
        account = AgentGasAccount(agent_id="agent_1")
        assert account.total_gas == 0
        assert account.operation_count == 0
        assert account.utilization_pct == 0.0
        assert not account.is_over_budget

    def test_gas_account_records(self):
        """Recording gas should update totals."""
        account = AgentGasAccount(agent_id="agent_1", budget=100)
        account.record(10)
        account.record(20)
        assert account.total_gas == 30
        assert account.operation_count == 2
        assert len(account.gas_history) == 2

    def test_gas_account_over_budget(self):
        """Account should report over-budget correctly."""
        account = AgentGasAccount(agent_id="agent_1", budget=50)
        account.record(51)
        assert account.is_over_budget
        assert account.utilization_pct > 100.0

    def test_gas_account_serialization(self):
        """to_dict should produce correct structure."""
        account = AgentGasAccount(agent_id="test_agent", budget=500)
        account.record(100)
        data = account.to_dict()
        assert data["agent_id"] == "test_agent"
        assert data["total_gas"] == 100
        assert data["budget"] == 500
        assert data["utilization_pct"] == 20.0
        assert not data["over_budget"]

    def test_bridge_gas_accounting_on_node_completed(self, bridge, spindle_bus):
        """NODE_COMPLETED events should trigger gas accounting."""
        bridge.start()

        event = SpindleEvent(
            event_type=EventType.NODE_COMPLETED,
            source="agent_alpha",
            payload={"agent_id": "agent_alpha", "gas_used": 15},
        )
        spindle_bus.publish(event)

        account = bridge.get_gas_account("agent_alpha")
        assert account is not None
        assert account.total_gas == 15
        assert account.operation_count == 1

        bridge.stop()

    def test_bridge_gas_multiple_agents(self, bridge, spindle_bus):
        """Gas accounting should track multiple agents independently."""
        bridge.start()

        spindle_bus.publish(SpindleEvent(
            event_type=EventType.NODE_COMPLETED,
            source="a1",
            payload={"agent_id": "a1", "gas_used": 10},
        ))
        spindle_bus.publish(SpindleEvent(
            event_type=EventType.NODE_COMPLETED,
            source="a2",
            payload={"agent_id": "a2", "gas_used": 20},
        ))

        all_accounts = bridge.get_all_gas_accounts()
        assert "a1" in all_accounts
        assert "a2" in all_accounts
        assert all_accounts["a1"]["total_gas"] == 10
        assert all_accounts["a2"]["total_gas"] == 20

        bridge.stop()

    def test_bridge_set_custom_budget(self, bridge):
        """set_gas_budget should override default budget."""
        bridge.set_gas_budget("special_agent", 500)
        account = bridge.get_gas_account("special_agent")
        assert account is not None
        assert account.budget == 500


# ─── Bridge Lifecycle Tests ──────────────────────────────────────────────────


class TestBridgeLifecycle:
    """Tests for bridge start/stop behavior."""

    def test_bridge_starts_and_stops(self, bridge):
        """Bridge should track running state."""
        assert not bridge.is_running
        bridge.start()
        assert bridge.is_running
        bridge.stop()
        assert not bridge.is_running

    def test_double_start_is_idempotent(self, bridge):
        """Starting twice should not cause errors."""
        bridge.start()
        bridge.start()  # no-op
        assert bridge.is_running
        bridge.stop()

    def test_double_stop_is_idempotent(self, bridge):
        """Stopping twice should not cause errors."""
        bridge.start()
        bridge.stop()
        bridge.stop()  # no-op
        assert not bridge.is_running

    def test_daemon_events_forwarded_to_spindle(self, bridge, spindle_bus):
        """Daemon events should appear on SpindleEventBus."""
        bridge.start()
        received: list[SpindleEvent] = []
        spindle_bus.subscribe(
            event_type=None,
            callback=lambda e: received.append(e),
            subscriber_id="test-listener",
        )

        # Emit a daemon event
        bridge.daemon_bus.emit(DaemonEvent(
            source="sentinel",
            event_type="sentinel_alert",
            severity="critical",
            data={"finding": "injection detected"},
        ))

        # Should have been forwarded
        daemon_events = [
            e for e in received if e.source.startswith("daemon:")
        ]
        assert len(daemon_events) >= 1
        assert daemon_events[0].source == "daemon:sentinel"

        bridge.stop()

    def test_spindle_forwarded_events_not_looped_back(self, bridge, spindle_bus):
        """Events forwarded FROM spindle should not loop back infinitely."""
        bridge.start()

        # Publish a spindle event
        spindle_bus.publish(SpindleEvent(
            event_type=EventType.NODE_STARTED,
            source="test_agent",
            payload={"step": 1},
        ))

        # The daemon bus should receive a spindle:node_started event
        # But when it's forwarded back, the spindle: prefix prevents re-forwarding
        # No infinite loop = test passes (with timeout)
        bridge.stop()
