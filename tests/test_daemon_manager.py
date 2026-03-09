"""
tests/test_daemon_manager.py — Comprehensive tests for DaemonManager lifecycle,
bridge auto-wiring, gas accounting, config-driven enable/disable, and
end-to-end event forwarding through SpindleEventBus.
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.core.daemons import (
    DaemonManager,
    DaemonEventBus,
    DaemonEvent,
    DaemonStatus,
)
from agents.core.daemons.daemon_bridge import (
    DaemonBridge,
    AgentGasAccount,
)
from agents.core.event_bus import SpindleEventBus, SpindleEvent, EventType


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def config():
    """Standard daemon config matching settings.json format."""
    return {
        "sentinel": {"enabled": True, "check_interval_ms": 1000},
        "scribe": {"enabled": True, "token_budget_pct": 0.80},
        "arbiter": {"enabled": True, "escalation_timeout_ms": 5000},
    }


@pytest.fixture
def event_bus():
    return DaemonEventBus()


@pytest.fixture
def manager(config, event_bus):
    return DaemonManager(config=config, event_bus=event_bus)


@pytest.fixture
def spindle_bus():
    return SpindleEventBus()


@pytest.fixture
def bridged_manager(manager, spindle_bus):
    """Manager with bridge attached."""
    manager.attach_bridge(spindle_bus)
    return manager


# ─── DaemonManager Lifecycle ────────────────────────────────────────────────


class TestDaemonManagerLifecycle:

    def test_init_creates_daemons(self, manager):
        assert manager.sentinel is not None
        assert manager.scribe is not None
        assert manager.arbiter is not None

    def test_init_creates_event_bus(self, config):
        mgr = DaemonManager(config=config)
        assert mgr.event_bus is not None

    def test_init_default_config(self):
        mgr = DaemonManager()
        assert mgr.sentinel is not None

    def test_start_all(self, manager):
        results = manager.start_all()
        assert "sentinel" in results
        assert "scribe" in results
        assert "arbiter" in results
        assert results["sentinel"] == DaemonStatus.RUNNING.value

    def test_stop_all(self, manager):
        manager.start_all()
        results = manager.stop_all()
        assert results["sentinel"] == DaemonStatus.STOPPED.value
        assert results["scribe"] == DaemonStatus.STOPPED.value
        assert results["arbiter"] == DaemonStatus.STOPPED.value

    def test_status(self, manager):
        status = manager.status()
        assert "sentinel" in status
        assert status["sentinel"] == DaemonStatus.STOPPED.value

    def test_status_after_start(self, manager):
        manager.start_all()
        status = manager.status()
        assert status["sentinel"] == DaemonStatus.RUNNING.value

    def test_health_check(self, manager):
        health = manager.health_check()
        assert "daemons" in health
        assert "event_bus_size" in health
        assert "all_healthy" in health
        assert health["all_healthy"] is True

    def test_idempotent_start(self, manager):
        """Starting twice should not error."""
        manager.start_all()
        results = manager.start_all()
        assert results["sentinel"] == DaemonStatus.RUNNING.value

    def test_idempotent_stop(self, manager):
        """Stopping without starting should not error."""
        results = manager.stop_all()
        assert results["sentinel"] == DaemonStatus.STOPPED.value


# ─── Config-Driven Enable/Disable ───────────────────────────────────────────


class TestConfigDriven:

    def test_sentinel_config_applied(self, manager):
        assert manager.sentinel._check_interval_ms == 1000

    def test_scribe_config_applied(self, manager):
        assert manager.scribe._token_budget_pct == 0.80

    def test_arbiter_config_applied(self, manager):
        assert manager.arbiter._escalation_timeout_ms == 5000


# ─── Bridge Attachment ───────────────────────────────────────────────────────


class TestBridgeAttachment:

    def test_attach_bridge_creates_bridge(self, manager, spindle_bus):
        bridge = manager.attach_bridge(spindle_bus)
        assert bridge is not None
        assert manager.bridge is bridge

    def test_bridge_none_before_attach(self, manager):
        assert manager.bridge is None

    def test_start_with_bridge(self, bridged_manager):
        results = bridged_manager.start_all()
        assert "bridge" in results
        assert results["bridge"] == "running"
        assert bridged_manager.bridge.is_running

    def test_stop_with_bridge(self, bridged_manager):
        bridged_manager.start_all()
        results = bridged_manager.stop_all()
        assert "bridge" in results
        assert results["bridge"] == "stopped"
        assert not bridged_manager.bridge.is_running

    def test_status_includes_bridge(self, bridged_manager):
        bridged_manager.start_all()
        status = bridged_manager.status()
        assert "bridge" in status
        assert status["bridge"] == "running"


# ─── Gas Accounting ─────────────────────────────────────────────────────────


class TestGasAccounting:

    def test_gas_report_no_bridge(self, manager):
        report = manager.get_gas_report()
        assert "error" in report

    def test_gas_report_with_bridge(self, bridged_manager):
        bridged_manager.start_all()
        report = bridged_manager.get_gas_report()
        assert "accounts" in report
        assert "aggregate" in report
        assert "bridge_running" in report
        assert report["bridge_running"] is True

    def test_gas_recorded_via_spindle(self, bridged_manager, spindle_bus):
        bridged_manager.start_all()

        # Emit a NODE_COMPLETED event with gas_used
        event = SpindleEvent(
            event_type=EventType.NODE_COMPLETED,
            source="test_agent",
            payload={"agent_id": "agent-42", "gas_used": 50},
        )
        spindle_bus.publish(event)

        report = bridged_manager.get_gas_report()
        assert "agent-42" in report["accounts"]
        assert report["accounts"]["agent-42"]["total_gas"] == 50

    def test_gas_report_aggregate(self, bridged_manager, spindle_bus):
        bridged_manager.start_all()

        for i in range(3):
            spindle_bus.publish(SpindleEvent(
                event_type=EventType.NODE_COMPLETED,
                source="src",
                payload={"agent_id": f"agent-{i}", "gas_used": 10},
            ))

        report = bridged_manager.get_gas_report()
        assert report["aggregate"]["total_agents"] == 3
        assert report["aggregate"]["total_gas"] == 30
        assert report["aggregate"]["total_operations"] == 3


# ─── Event Forwarding End-to-End ────────────────────────────────────────────


class TestEventForwarding:

    def test_daemon_event_reaches_spindle(self, bridged_manager, spindle_bus):
        """Daemon alert → SpindleEventBus."""
        bridged_manager.start_all()

        received = []
        spindle_bus.subscribe(
            event_type=None,
            callback=lambda e: received.append(e),
            subscriber_id="test-listener",
        )

        # Emit a daemon alert
        bridged_manager.event_bus.emit(DaemonEvent(
            source="sentinel",
            event_type="sentinel_alert",
            severity="critical",
            data={"threat": "injection_detected"},
        ))

        # Filter out forwarded spindle events (exclude bridge's own re-publishes)
        daemon_events = [
            e for e in received
            if e.source.startswith("daemon:")
        ]
        assert len(daemon_events) >= 1
        assert daemon_events[0].payload["daemon_event_type"] == "sentinel_alert"

    def test_spindle_event_reaches_daemons(self, bridged_manager, spindle_bus):
        """SpindleEvent → DaemonEventBus."""
        bridged_manager.start_all()

        received = []
        bridged_manager.event_bus.subscribe(lambda e: received.append(e))

        spindle_bus.publish(SpindleEvent(
            event_type=EventType.NODE_STARTED,
            source="orchestrator",
            payload={"step": "compile"},
        ))

        # Should have received the translated daemon event
        spindle_events = [
            e for e in received
            if e.event_type.startswith("spindle:")
        ]
        assert len(spindle_events) >= 1


# ─── DaemonBridge.from_config ───────────────────────────────────────────────


class TestFromConfig:

    def test_from_config_reads_settings(self, tmp_path, manager, spindle_bus):
        config_file = tmp_path / "settings.json"
        config_file.write_text(json.dumps({
            "deployment_tier": "forge",
            "gas_buckets": {"hearth": 1000, "forge": 10000, "sovereign": 100000},
        }))

        bridge = DaemonBridge.from_config(
            spindle_bus=spindle_bus,
            daemon_manager=manager,
            config_path=config_file,
        )
        assert bridge.default_gas_budget == 10000

    def test_from_config_default_budget(self, manager, spindle_bus):
        """When no config file exists, uses 10_000 default."""
        bridge = DaemonBridge.from_config(
            spindle_bus=spindle_bus,
            daemon_manager=manager,
            config_path="/nonexistent/path.json",
        )
        assert bridge.default_gas_budget == 10_000

    def test_from_config_hearth_tier(self, tmp_path, manager, spindle_bus):
        config_file = tmp_path / "settings.json"
        config_file.write_text(json.dumps({
            "deployment_tier": "hearth",
            "gas_buckets": {"hearth": 1000, "forge": 10000, "sovereign": 100000},
        }))

        bridge = DaemonBridge.from_config(
            spindle_bus=spindle_bus,
            daemon_manager=manager,
            config_path=config_file,
        )
        assert bridge.default_gas_budget == 1000
