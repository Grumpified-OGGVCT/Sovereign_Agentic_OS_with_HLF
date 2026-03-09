"""
Daemon ↔ SpindleEventBus Bridge — Connects Aegis-Nexus daemons to the Spindle event system.

Responsibilities:
  1. Translates DaemonEvent → SpindleEvent and vice versa
  2. Subscribes to SpindleEventBus events and feeds them to daemons as runtime events
  3. Maintains per-agent gas accounting by intercepting NODE_COMPLETED events
  4. Bridges alert emissions from daemons back to SpindleEventBus for system-wide visibility

Architecture:
  SpindleEventBus ←→ DaemonBridge ←→ DaemonManager
       ↑ system events               daemon alerts ↓
       └──────────────────────────────────────────┘
"""

from __future__ import annotations

import datetime
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents.core.event_bus import EventType, SpindleEvent, SpindleEventBus
from agents.core.daemons import DaemonEvent, DaemonEventBus, DaemonManager

logger = logging.getLogger(__name__)


# ─── Per-Agent Gas Accounting ────────────────────────────────────────────────


@dataclass
class AgentGasAccount:
    """Track gas usage per agent over time."""

    agent_id: str
    total_gas: int = 0
    operation_count: int = 0
    last_gas_time: float = 0.0
    gas_history: list[int] = field(default_factory=list)
    budget: int = 10_000  # default per-agent gas budget

    @property
    def utilization_pct(self) -> float:
        """Percentage of budget consumed."""
        return (self.total_gas / self.budget * 100) if self.budget else 0.0

    @property
    def is_over_budget(self) -> bool:
        """Whether this agent has exceeded its gas budget."""
        return self.total_gas > self.budget

    def record(self, gas_units: int) -> None:
        """Record a gas expenditure."""
        self.total_gas += gas_units
        self.operation_count += 1
        self.last_gas_time = time.time()
        self.gas_history.append(gas_units)
        # Keep history bounded
        if len(self.gas_history) > 1000:
            self.gas_history = self.gas_history[-500:]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for reporting."""
        return {
            "agent_id": self.agent_id,
            "total_gas": self.total_gas,
            "operations": self.operation_count,
            "budget": self.budget,
            "utilization_pct": round(self.utilization_pct, 2),
            "over_budget": self.is_over_budget,
        }


# ─── Event Type Mapping ─────────────────────────────────────────────────────

# DaemonEvent.event_type → SpindleEventBus.EventType (best-fit mapping)
_DAEMON_TO_SPINDLE: dict[str, EventType] = {
    "sentinel_alert": EventType.VALIDATION_REJECTED,
    "alert": EventType.VALIDATION_REJECTED,
    "scribe_log": EventType.NODE_COMPLETED,
    "prose": EventType.NODE_COMPLETED,
    "arbiter_ruling": EventType.REALIGNMENT_TRIGGERED,
    "ruling": EventType.REALIGNMENT_TRIGGERED,
    "daemon_started": EventType.NODE_STARTED,
    "daemon_stopped": EventType.NODE_COMPLETED,
    "gas_anomaly": EventType.CONTEXT_INVALIDATED,
    "privilege_escalation": EventType.VALIDATION_REJECTED,
    "injection_attempt": EventType.VALIDATION_REJECTED,
    "align_violation": EventType.REALIGNMENT_TRIGGERED,
}


def _daemon_event_to_spindle(event: DaemonEvent) -> SpindleEvent:
    """Translate a DaemonEvent into a SpindleEvent."""
    event_type = _DAEMON_TO_SPINDLE.get(
        event.event_type, EventType.NODE_COMPLETED
    )
    return SpindleEvent(
        event_type=event_type,
        source=f"daemon:{event.source}",
        payload={
            "daemon_event_type": event.event_type,
            "daemon_severity": event.severity,
            "daemon_data": event.data,
            "daemon_ts": event.timestamp,
        },
    )


def _spindle_event_to_daemon(event: SpindleEvent) -> DaemonEvent:
    """Translate a SpindleEvent into a DaemonEvent for daemon consumption."""
    ts = datetime.datetime.fromtimestamp(
        event.timestamp, tz=datetime.timezone.utc
    ).isoformat()
    return DaemonEvent(
        source=event.source,
        event_type=f"spindle:{event.event_type.value}",
        severity="info",
        data=event.payload,
        timestamp=ts,
    )


# ─── Bridge ──────────────────────────────────────────────────────────────────


class DaemonBridge:
    """Bidirectional bridge between SpindleEventBus and DaemonEventBus.

    Features:
      - Forwards SpindleEvents to daemons for monitoring
      - Forwards daemon alerts back to SpindleEventBus
      - Per-agent gas accounting on NODE_COMPLETED events
      - Budget overage alerts via Sentinel
    """

    SUBSCRIBER_ID = "daemon-bridge"

    def __init__(
        self,
        spindle_bus: SpindleEventBus,
        daemon_manager: DaemonManager,
        *,
        default_gas_budget: int = 10_000,
    ) -> None:
        self.spindle_bus = spindle_bus
        self.daemon_manager = daemon_manager
        self.daemon_bus = daemon_manager.event_bus
        self.default_gas_budget = default_gas_budget

        self._gas_accounts: dict[str, AgentGasAccount] = {}
        self._running = False
        # Store handler refs for unsubscribe
        self._spindle_handler = self._on_spindle_event
        self._daemon_handler = self._on_daemon_event

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Wire up event subscriptions in both directions."""
        if self._running:
            return

        # Spindle → Daemons: forward all events to daemon bus
        self.spindle_bus.subscribe(
            event_type=None,  # all events
            callback=self._spindle_handler,
            subscriber_id=self.SUBSCRIBER_ID,
        )

        # Daemons → Spindle: register handler on daemon bus
        # DaemonEventBus uses simple subscribe(handler) API
        self.daemon_bus.subscribe(self._daemon_handler)

        self._running = True
        logger.info("DaemonBridge started — bidirectional event flow active")

    def stop(self) -> None:
        """Tear down event subscriptions."""
        if not self._running:
            return

        # Unsubscribe from spindle bus
        self.spindle_bus.unsubscribe(self.SUBSCRIBER_ID)

        # Remove daemon handler from daemon bus handlers list
        if self._daemon_handler in self.daemon_bus._handlers:
            self.daemon_bus._handlers.remove(self._daemon_handler)

        self._running = False
        logger.info("DaemonBridge stopped")

    @property
    def is_running(self) -> bool:
        """Whether the bridge is currently active."""
        return self._running

    # ── Event Handlers ───────────────────────────────────────────────────

    def _on_spindle_event(self, event: SpindleEvent) -> None:
        """Handle an event from SpindleEventBus → forward to daemons + gas accounting."""
        # Per-agent gas accounting for completed nodes
        if event.event_type == EventType.NODE_COMPLETED:
            agent_id = event.payload.get("agent_id", event.source)
            gas_used = event.payload.get("gas_used", 1)
            self._record_gas(agent_id, gas_used)

        # Forward to daemon bus for monitoring
        daemon_event = _spindle_event_to_daemon(event)
        self.daemon_bus.emit(daemon_event)

    def _on_daemon_event(self, event: DaemonEvent) -> None:
        """Handle an event from DaemonEventBus → forward to SpindleEventBus."""
        # Avoid infinite loops — don't forward events that originated from spindle
        if event.event_type.startswith("spindle:"):
            return

        spindle_event = _daemon_event_to_spindle(event)
        self.spindle_bus.publish(spindle_event)

    # ── Gas Accounting ───────────────────────────────────────────────────

    def _record_gas(self, agent_id: str, gas_units: int) -> None:
        """Record gas consumption for an agent and check budget."""
        if agent_id not in self._gas_accounts:
            self._gas_accounts[agent_id] = AgentGasAccount(
                agent_id=agent_id,
                budget=self.default_gas_budget,
            )

        account = self._gas_accounts[agent_id]
        account.record(gas_units)

        # Check budget threshold alerts
        if account.is_over_budget:
            logger.warning(
                "Agent %s exceeded gas budget: %d/%d (%.1f%%)",
                agent_id,
                account.total_gas,
                account.budget,
                account.utilization_pct,
            )
            # Emit alert via daemon bus
            self.daemon_bus.emit(DaemonEvent(
                source=self.SUBSCRIBER_ID,
                event_type="gas_anomaly",
                severity="critical",
                data={
                    "agent_id": agent_id,
                    "total_gas": account.total_gas,
                    "budget": account.budget,
                    "utilization_pct": account.utilization_pct,
                },
            ))
        elif account.utilization_pct >= 80:
            logger.info(
                "Agent %s approaching gas budget: %.1f%%",
                agent_id,
                account.utilization_pct,
            )

    def get_gas_account(self, agent_id: str) -> AgentGasAccount | None:
        """Retrieve gas accounting for a specific agent."""
        return self._gas_accounts.get(agent_id)

    def get_all_gas_accounts(self) -> dict[str, dict[str, Any]]:
        """Get all agent gas accounts as serializable dicts."""
        return {
            agent_id: account.to_dict()
            for agent_id, account in self._gas_accounts.items()
        }

    def set_gas_budget(self, agent_id: str, budget: int) -> None:
        """Set a custom gas budget for an agent."""
        if agent_id not in self._gas_accounts:
            self._gas_accounts[agent_id] = AgentGasAccount(
                agent_id=agent_id, budget=budget
            )
        else:
            self._gas_accounts[agent_id].budget = budget

    def get_gas_report(self) -> dict[str, Any]:
        """Generate a dashboard-ready gas utilization report.

        Returns:
            Dict with per-agent accounts, aggregate stats, and alerts.
        """
        accounts = self.get_all_gas_accounts()
        total_gas = sum(a.total_gas for a in self._gas_accounts.values())
        total_ops = sum(a.operation_count for a in self._gas_accounts.values())
        over_budget = [
            aid for aid, a in self._gas_accounts.items() if a.is_over_budget
        ]
        return {
            "accounts": accounts,
            "aggregate": {
                "total_agents": len(self._gas_accounts),
                "total_gas": total_gas,
                "total_operations": total_ops,
                "agents_over_budget": over_budget,
            },
            "bridge_running": self._running,
        }

    @classmethod
    def from_config(
        cls,
        spindle_bus: SpindleEventBus,
        daemon_manager: DaemonManager,
        config_path: Path | str | None = None,
    ) -> DaemonBridge:
        """Create a DaemonBridge from settings.json configuration.

        Args:
            spindle_bus: SpindleEventBus instance.
            daemon_manager: DaemonManager instance.
            config_path: Path to settings.json (auto-detected if None).

        Returns:
            Configured DaemonBridge instance.
        """
        budget = 10_000  # default

        if config_path is None:
            # Auto-detect project root
            candidates = [
                Path("config/settings.json"),
                Path(__file__).parent.parent.parent.parent / "config" / "settings.json",
            ]
            for candidate in candidates:
                if candidate.exists():
                    config_path = candidate
                    break

        if config_path and Path(config_path).exists():
            try:
                data = json.loads(Path(config_path).read_text(encoding="utf-8"))
                # Read gas budget from tier config
                tier = data.get("deployment_tier", "hearth")
                gas_buckets = data.get("gas_buckets", {})
                budget = gas_buckets.get(tier, budget)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Failed to load bridge config: %s", e)

        return cls(
            spindle_bus=spindle_bus,
            daemon_manager=daemon_manager,
            default_gas_budget=budget,
        )
