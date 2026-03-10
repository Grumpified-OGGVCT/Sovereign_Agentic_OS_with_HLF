"""
Aegis-Nexus Runtime Daemons — Continuous monitoring for the Sovereign OS.

Three daemon classes that run alongside the OS, providing background
security monitoring, transparency translation, and dispute resolution.

- **Sentinel**: Anomaly detection (privilege escalation, injection, gas spikes)
- **Scribe**: InsAIts V2 prose stream from raw AST/decision events
- **Arbiter**: Inter-agent ALIGN rule dispute adjudication

All daemons follow the same lifecycle: start() → check() → stop()
and emit events via the DaemonEventBus.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agents.core.daemons.sentinel import SentinelDaemon
from agents.core.daemons.scribe import ScribeDaemon
from agents.core.daemons.arbiter import ArbiterDaemon

_logger = logging.getLogger("aegis.daemons")


# ─── Daemon Status ───────────────────────────────────────────────────────────


class DaemonStatus(Enum):
    """Lifecycle status of a daemon."""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


# ─── Daemon Event Bus ────────────────────────────────────────────────────────


@dataclass
class DaemonEvent:
    """An event emitted by a daemon for the Agent Service Bus."""
    source: str           # daemon name: "sentinel", "scribe", "arbiter"
    event_type: str       # e.g. "alert", "prose", "ruling"
    severity: str         # "info", "warning", "critical"
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""   # ISO-8601, filled at emission time


class DaemonEventBus:
    """
    In-process event bus for daemon-to-OS communication.

    In production, this would be backed by Redis Streams (ASB).
    For now, uses an in-memory deque for testability.
    """

    def __init__(self, max_events: int = 1000):
        self._events: list[DaemonEvent] = []
        self._max = max_events
        self._handlers: list[Any] = []

    def emit(self, event: DaemonEvent) -> None:
        """Publish an event to the bus."""
        import datetime
        if not event.timestamp:
            event.timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._events.append(event)
        if len(self._events) > self._max:
            self._events = self._events[-self._max:]
        for handler in self._handlers:
            try:
                handler(event)
            except Exception:
                _logger.debug("Event handler error", exc_info=True)

    def subscribe(self, handler: Any) -> None:
        """Register an event handler callback."""
        self._handlers.append(handler)

    def recent(self, count: int = 10, source: str | None = None) -> list[DaemonEvent]:
        """Get recent events, optionally filtered by source."""
        events = self._events
        if source:
            events = [e for e in events if e.source == source]
        return events[-count:]

    def clear(self) -> None:
        """Clear all events."""
        self._events.clear()


# ─── Daemon Manager ─────────────────────────────────────────────────────────


class DaemonManager:
    """
    Coordinates all Aegis-Nexus runtime daemons.

    Provides unified start/stop/status for the daemon triad,
    with shared event bus for inter-daemon communication.
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        event_bus: DaemonEventBus | None = None,
    ):
        self.config = config or {}
        self.event_bus = event_bus or DaemonEventBus()

        # Initialize daemons
        sentinel_cfg = self.config.get("sentinel", {})
        scribe_cfg = self.config.get("scribe", {})
        arbiter_cfg = self.config.get("arbiter", {})

        self.sentinel = SentinelDaemon(
            event_bus=self.event_bus,
            check_interval_ms=sentinel_cfg.get("check_interval_ms", 5000),
            enabled=sentinel_cfg.get("enabled", True),
        )
        self.scribe = ScribeDaemon(
            event_bus=self.event_bus,
            token_budget_pct=scribe_cfg.get("token_budget_pct", 0.80),
            enabled=scribe_cfg.get("enabled", True),
        )
        self.arbiter = ArbiterDaemon(
            event_bus=self.event_bus,
            escalation_timeout_ms=arbiter_cfg.get("escalation_timeout_ms", 12000),
            enabled=arbiter_cfg.get("enabled", True),
        )

        self._daemons = [self.sentinel, self.scribe, self.arbiter]
        self._bridge = None  # set via attach_bridge()

    def start_all(self) -> dict[str, str]:
        """Start all enabled daemons, wire cross-daemon routing, and start bridge.

        Cross-daemon routing:
          - Sentinel alerts are auto-translated by Scribe into InsAIts prose.
          - Critical Sentinel alerts are auto-escalated to Arbiter for adjudication.
        """
        results = {}
        for daemon in self._daemons:
            name = daemon.name
            try:
                daemon.start()
                results[name] = daemon.status.value
            except Exception as e:
                results[name] = f"error: {e}"
                _logger.error("Failed to start %s: %s", name, e)

        # Wire cross-daemon event routing
        self.event_bus.subscribe(self._route_daemon_event)

        # Auto-start bridge if attached
        if self._bridge is not None:
            try:
                self._bridge.start()
                results["bridge"] = "running"
            except Exception as e:
                results["bridge"] = f"error: {e}"
                _logger.error("Failed to start DaemonBridge: %s", e)

        return results

    def _route_daemon_event(self, event: DaemonEvent) -> None:
        """Route events between daemons for cross-daemon intelligence.

        Sentinel → Scribe:  every alert becomes InsAIts prose.
        Sentinel → Arbiter: critical alerts open a dispute for adjudication.
        """
        # Sentinel alert → Scribe translation
        if event.source == "sentinel" and event.event_type == "alert":
            if self.scribe.status == DaemonStatus.RUNNING:
                self.scribe.translate({
                    "type": f"sentinel_{event.data.get('pattern', 'alert')}",
                    "name": event.data.get("pattern", "unknown"),
                    "source": "sentinel",
                    **event.data.get("evidence", {}),
                })

        # Critical Sentinel alert → Arbiter escalation
        if (
            event.source == "sentinel"
            and event.event_type == "alert"
            and event.severity == "critical"
        ):
            if self.arbiter.status == DaemonStatus.RUNNING:
                try:
                    source_agent = event.data.get("evidence", {}).get("source", "unknown")
                    self.arbiter.open_dispute(
                        rule=event.data.get("pattern", "unknown"),
                        subject=f"Critical security alert: {event.data.get('pattern', '')}",
                        parties=["sentinel", source_agent],
                    )
                except RuntimeError:
                    pass  # Arbiter not running

    def stop_all(self) -> dict[str, str]:
        """Gracefully stop all daemons and the bridge."""
        results = {}

        # Stop bridge first
        if self._bridge is not None:
            try:
                self._bridge.stop()
                results["bridge"] = "stopped"
            except Exception as e:
                results["bridge"] = f"error: {e}"

        for daemon in self._daemons:
            name = daemon.name
            try:
                daemon.stop()
                results[name] = daemon.status.value
            except Exception as e:
                results[name] = f"error: {e}"
        return results

    def status(self) -> dict[str, str]:
        """Get status of all daemons."""
        s = {d.name: d.status.value for d in self._daemons}
        if self._bridge is not None:
            s["bridge"] = "running" if self._bridge.is_running else "stopped"
        return s

    def health_check(self) -> dict[str, Any]:
        """Run health check across all daemons."""
        return {
            "daemons": self.status(),
            "event_bus_size": len(self.event_bus._events),
            "all_healthy": all(
                d.status in (DaemonStatus.RUNNING, DaemonStatus.STOPPED)
                for d in self._daemons
            ),
        }

    def attach_bridge(
        self,
        spindle_bus: Any,
        *,
        default_gas_budget: int = 10_000,
    ) -> Any:
        """Attach a DaemonBridge for SpindleEventBus integration.

        Args:
            spindle_bus: SpindleEventBus instance.
            default_gas_budget: Per-agent gas budget.

        Returns:
            The created DaemonBridge instance.
        """
        from agents.core.daemons.daemon_bridge import DaemonBridge

        self._bridge = DaemonBridge(
            spindle_bus=spindle_bus,
            daemon_manager=self,
            default_gas_budget=default_gas_budget,
        )
        return self._bridge

    @property
    def bridge(self) -> Any:
        """Access the attached DaemonBridge (or None)."""
        return self._bridge

    def get_gas_report(self) -> dict[str, Any]:
        """Get per-agent gas utilization report from the bridge.

        Returns:
            Gas report dict, or empty dict if no bridge attached.
        """
        if self._bridge is not None:
            return self._bridge.get_gas_report()
        return {"error": "no bridge attached"}


__all__ = [
    "DaemonStatus",
    "DaemonEvent",
    "DaemonEventBus",
    "DaemonManager",
    "SentinelDaemon",
    "ScribeDaemon",
    "ArbiterDaemon",
]
