"""
InsAIts V2 Daemon — Continuous Transparency Analysis Engine.

Extends the InsAIts decompiler from compile-time-only analysis into a
continuous runtime daemon that:

  1. Subscribes to SpindleEventBus events for live analysis
  2. Produces human-readable prose for every agent action
  3. Maintains a rolling audit trail with routing traces
  4. Detects anomalous patterns (unusual gas spikes, repeated failures)
  5. Exposes a report API for the GUI Cognitive SOC

Architecture:
  SpindleEventBus → InsAItsDaemon → AuditTrail + Alerts + Reports
                                 ↓
                          GUI Transparency Panel

Usage:
    daemon = InsAItsDaemon(event_bus=bus)
    daemon.start()

    # Later:
    report = daemon.get_report()
    trail = daemon.get_audit_trail(limit=50)
"""

from __future__ import annotations

import logging
import time
import statistics
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ─── Analysis Categories ────────────────────────────────────────────────────

class AnalysisCategory(Enum):
    """Categories for InsAIts analysis entries."""
    ROUTING = "routing"          # Model routing decisions
    EXECUTION = "execution"     # Agent/task execution
    SECURITY = "security"       # Security events (ALIGN, gates)
    GAS = "gas"                 # Gas consumption patterns
    MEMORY = "memory"           # Memory store/recall operations
    TOOL = "tool"               # Tool invocations
    LIFECYCLE = "lifecycle"     # Start/stop/health events
    ANOMALY = "anomaly"         # Detected anomalies


class Severity(Enum):
    """Severity levels for analysis entries."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# ─── Audit Entry ────────────────────────────────────────────────────────────

@dataclass
class AuditEntry:
    """A single InsAIts analysis entry in the audit trail."""

    timestamp: float
    category: AnalysisCategory
    severity: Severity
    prose: str
    agent_id: str = ""
    event_type: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "category": self.category.value,
            "severity": self.severity.value,
            "prose": self.prose,
            "agent_id": self.agent_id,
            "event_type": self.event_type,
            "metadata": self.metadata,
        }


# ─── Anomaly Detector ───────────────────────────────────────────────────────

class AnomalyDetector:
    """Detects anomalous patterns in agent behavior.

    Tracks:
      - Gas consumption spikes (> 2 std devs from rolling mean)
      - Repeated failures (> threshold consecutive failures)
      - Unusual routing patterns (same model rejected repeatedly)
    """

    def __init__(
        self,
        *,
        gas_window: int = 50,
        gas_sigma: float = 2.0,
        failure_threshold: int = 3,
    ) -> None:
        self._gas_history: dict[str, deque[int]] = {}
        self._gas_window = gas_window
        self._gas_sigma = gas_sigma
        self._failure_counts: dict[str, int] = {}
        self._failure_threshold = failure_threshold
        self._rejection_counts: dict[str, int] = {}

    def record_gas(self, agent_id: str, gas_units: int) -> AuditEntry | None:
        """Record gas consumption. Returns anomaly entry if spike detected."""
        if agent_id not in self._gas_history:
            self._gas_history[agent_id] = deque(maxlen=self._gas_window)

        history = self._gas_history[agent_id]

        # Need at least 5 samples for meaningful stats
        if len(history) >= 5:
            mean = statistics.mean(history)
            stdev = statistics.stdev(history) if len(history) > 1 else 0
            threshold = mean + (self._gas_sigma * stdev) if stdev > 0 else mean * 2

            if gas_units > threshold and gas_units > 0:
                history.append(gas_units)
                return AuditEntry(
                    timestamp=time.time(),
                    category=AnalysisCategory.ANOMALY,
                    severity=Severity.WARNING,
                    prose=(
                        f"Gas spike detected for '{agent_id}': "
                        f"{gas_units} units (mean: {mean:.0f}, "
                        f"threshold: {threshold:.0f})"
                    ),
                    agent_id=agent_id,
                    event_type="gas_spike",
                    metadata={
                        "gas_units": gas_units,
                        "mean": round(mean, 2),
                        "stdev": round(stdev, 2),
                        "threshold": round(threshold, 2),
                    },
                )

        history.append(gas_units)
        return None

    def record_failure(self, agent_id: str, error: str = "") -> AuditEntry | None:
        """Record a failure. Returns anomaly if threshold exceeded."""
        self._failure_counts[agent_id] = self._failure_counts.get(agent_id, 0) + 1

        if self._failure_counts[agent_id] >= self._failure_threshold:
            count = self._failure_counts[agent_id]
            return AuditEntry(
                timestamp=time.time(),
                category=AnalysisCategory.ANOMALY,
                severity=Severity.CRITICAL,
                prose=(
                    f"Repeated failures for '{agent_id}': "
                    f"{count} consecutive failures"
                    + (f" (latest: {error})" if error else "")
                ),
                agent_id=agent_id,
                event_type="repeated_failure",
                metadata={"count": count, "latest_error": error},
            )

        return None

    def record_success(self, agent_id: str) -> None:
        """Reset failure counter on success."""
        self._failure_counts[agent_id] = 0

    def record_rejection(self, model: str) -> AuditEntry | None:
        """Record a model routing rejection."""
        self._rejection_counts[model] = self._rejection_counts.get(model, 0) + 1

        if self._rejection_counts[model] >= self._failure_threshold:
            count = self._rejection_counts[model]
            return AuditEntry(
                timestamp=time.time(),
                category=AnalysisCategory.ANOMALY,
                severity=Severity.WARNING,
                prose=(
                    f"Model '{model}' rejected {count} times — "
                    f"consider removing from routing pool"
                ),
                agent_id="maestro",
                event_type="model_rejection_pattern",
                metadata={"model": model, "rejection_count": count},
            )

        return None

    def reset(self) -> None:
        """Reset all anomaly tracking state."""
        self._gas_history.clear()
        self._failure_counts.clear()
        self._rejection_counts.clear()


# ─── Prose Generator ────────────────────────────────────────────────────────

# Event type → human-readable prose templates
_EVENT_PROSE: dict[str, str] = {
    "compilation_complete": "HLF program compiled successfully",
    "execution_started": "Agent execution started",
    "execution_complete": "Agent execution completed",
    "execution_failed": "Agent execution failed",
    "tool_invoked": "Tool invoked",
    "tool_completed": "Tool execution completed",
    "model_routed": "Model routing decision made",
    "model_fallback": "Model routing fell back to alternate provider",
    "gas_consumed": "Gas consumed",
    "gas_budget_exceeded": "Gas budget exceeded",
    "memory_stored": "Memory stored",
    "memory_recalled": "Memory recalled",
    "align_check_passed": "ALIGN security check passed",
    "align_violation": "ALIGN security violation detected",
    "validation_rejected": "Input validation rejected",
    "context_invalidated": "Context invalidated — recomputation required",
    "realignment_triggered": "Realignment triggered",
    "daemon_started": "Daemon started",
    "daemon_stopped": "Daemon stopped",
    "health_check": "Health check executed",
}


def _generate_prose(event_type: str, data: dict[str, Any]) -> str:
    """Generate human-readable prose for an event."""
    template = _EVENT_PROSE.get(event_type, f"Event: {event_type}")

    agent = data.get("agent_id", data.get("source", ""))
    details: list[str] = []

    if agent:
        details.append(f"by '{agent}'")

    if "model" in data:
        details.append(f"using model '{data['model']}'")

    if "tool" in data:
        details.append(f"tool '{data['tool']}'")

    if "gas_units" in data:
        details.append(f"({data['gas_units']} gas units)")

    if "duration_ms" in data:
        details.append(f"in {data['duration_ms']}ms")

    if "error" in data:
        details.append(f"— error: {data['error']}")

    if "reason" in data:
        details.append(f"— reason: {data['reason']}")

    if details:
        return f"{template} {' '.join(details)}"
    return template


def _categorize_event(event_type: str) -> AnalysisCategory:
    """Map event types to analysis categories."""
    if event_type in ("model_routed", "model_fallback"):
        return AnalysisCategory.ROUTING
    if event_type in ("execution_started", "execution_complete",
                       "execution_failed", "compilation_complete"):
        return AnalysisCategory.EXECUTION
    if event_type in ("align_check_passed", "align_violation",
                       "validation_rejected", "realignment_triggered"):
        return AnalysisCategory.SECURITY
    if event_type in ("gas_consumed", "gas_budget_exceeded"):
        return AnalysisCategory.GAS
    if event_type in ("memory_stored", "memory_recalled"):
        return AnalysisCategory.MEMORY
    if event_type in ("tool_invoked", "tool_completed"):
        return AnalysisCategory.TOOL
    if event_type in ("daemon_started", "daemon_stopped", "health_check"):
        return AnalysisCategory.LIFECYCLE
    return AnalysisCategory.EXECUTION


def _severity_for_event(event_type: str) -> Severity:
    """Determine severity level for an event type."""
    if event_type in ("align_violation", "gas_budget_exceeded",
                       "execution_failed", "validation_rejected"):
        return Severity.CRITICAL
    if event_type in ("model_fallback", "realignment_triggered",
                       "context_invalidated"):
        return Severity.WARNING
    return Severity.INFO


# ─── InsAIts V2 Daemon ─────────────────────────────────────────────────────

class InsAItsDaemon:
    """Continuous transparency analysis daemon.

    Subscribes to a SpindleEventBus and maintains a rolling audit trail
    with anomaly detection, categorized prose, and report generation.

    Args:
        event_bus: SpindleEventBus to subscribe to (optional — can attach later).
        max_trail_size: Maximum audit trail entries to retain.
        anomaly_detector: Custom AnomalyDetector (auto-created if None).
    """

    def __init__(
        self,
        event_bus: Any = None,
        *,
        max_trail_size: int = 1000,
        anomaly_detector: AnomalyDetector | None = None,
    ) -> None:
        self._bus = event_bus
        self._trail: deque[AuditEntry] = deque(maxlen=max_trail_size)
        self._anomaly = anomaly_detector or AnomalyDetector()
        self._running = False
        self._start_time: float = 0.0
        self._event_count = 0
        self._category_counts: dict[str, int] = {}
        self._severity_counts: dict[str, int] = {
            s.value: 0 for s in Severity
        }
        self._subscription_ids: list[Any] = []

    def attach_bus(self, event_bus: Any) -> None:
        """Attach a SpindleEventBus after construction."""
        self._bus = event_bus

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def event_count(self) -> int:
        return self._event_count

    @property
    def uptime_seconds(self) -> float:
        if not self._running:
            return 0.0
        return time.time() - self._start_time

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the daemon — subscribe to event bus."""
        if self._running:
            return

        self._running = True
        self._start_time = time.time()

        if self._bus is not None:
            self._subscribe()

        self._record(AuditEntry(
            timestamp=time.time(),
            category=AnalysisCategory.LIFECYCLE,
            severity=Severity.INFO,
            prose="InsAIts V2 daemon started — continuous transparency active",
            event_type="daemon_started",
            agent_id="insaits",
        ))

        logger.info("InsAIts V2 daemon started")

    def stop(self) -> None:
        """Stop the daemon — unsubscribe from event bus."""
        if not self._running:
            return

        self._record(AuditEntry(
            timestamp=time.time(),
            category=AnalysisCategory.LIFECYCLE,
            severity=Severity.INFO,
            prose=(
                f"InsAIts V2 daemon stopped after {self.uptime_seconds:.1f}s "
                f"— {self._event_count} events analyzed"
            ),
            event_type="daemon_stopped",
            agent_id="insaits",
        ))

        if self._bus is not None:
            self._unsubscribe()

        self._running = False
        logger.info("InsAIts V2 daemon stopped")

    def _subscribe(self) -> None:
        """Subscribe to all relevant event types on the bus."""
        try:
            sub_id = self._bus.subscribe("*", self._on_event)
            self._subscription_ids.append(sub_id)
        except (AttributeError, TypeError):
            # Bus doesn't support wildcard — subscribe individually
            pass

    def _unsubscribe(self) -> None:
        """Unsubscribe from the event bus."""
        for sub_id in self._subscription_ids:
            try:
                self._bus.unsubscribe(sub_id)
            except (AttributeError, TypeError):
                pass
        self._subscription_ids.clear()

    # ── Event Processing ─────────────────────────────────────────────────

    def process_event(self, event_type: str, data: dict[str, Any] | None = None) -> AuditEntry:
        """Process an event and add it to the audit trail.

        This is the public API for feeding events — can be called directly
        without a bus, useful for testing and manual integration.
        """
        if data is None:
            data = {}

        prose = _generate_prose(event_type, data)
        category = _categorize_event(event_type)
        severity = _severity_for_event(event_type)
        agent_id = data.get("agent_id", data.get("source", ""))

        entry = AuditEntry(
            timestamp=time.time(),
            category=category,
            severity=severity,
            prose=prose,
            agent_id=agent_id,
            event_type=event_type,
            metadata=data,
        )

        self._record(entry)

        # Feed anomaly detector
        self._check_anomalies(event_type, data)

        return entry

    def _on_event(self, event: Any) -> None:
        """Handle an event from SpindleEventBus."""
        try:
            event_type = getattr(event, "event_type", str(event))
            data = getattr(event, "data", {})
            if isinstance(event_type, Enum):
                event_type = event_type.value
            self.process_event(str(event_type), data if isinstance(data, dict) else {})
        except Exception as e:
            logger.warning("InsAIts failed to process event: %s", e)

    def _record(self, entry: AuditEntry) -> None:
        """Add an entry to the audit trail and update counters."""
        self._trail.append(entry)
        self._event_count += 1

        cat = entry.category.value
        self._category_counts[cat] = self._category_counts.get(cat, 0) + 1
        self._severity_counts[entry.severity.value] = (
            self._severity_counts.get(entry.severity.value, 0) + 1
        )

    def _check_anomalies(self, event_type: str, data: dict[str, Any]) -> None:
        """Feed anomaly detector and record any detected anomalies."""
        agent_id = data.get("agent_id", "")

        # Gas anomalies
        if "gas_units" in data:
            anomaly = self._anomaly.record_gas(agent_id, data["gas_units"])
            if anomaly:
                self._record(anomaly)

        # Failure tracking
        if event_type in ("execution_failed", "validation_rejected"):
            error = data.get("error", "")
            anomaly = self._anomaly.record_failure(agent_id, error)
            if anomaly:
                self._record(anomaly)
        elif event_type in ("execution_complete",):
            self._anomaly.record_success(agent_id)

        # Routing rejections
        if event_type == "model_fallback":
            model = data.get("model", "")
            if model:
                anomaly = self._anomaly.record_rejection(model)
                if anomaly:
                    self._record(anomaly)

    # ── Reports ──────────────────────────────────────────────────────────

    def get_audit_trail(
        self,
        *,
        limit: int = 50,
        category: AnalysisCategory | None = None,
        severity: Severity | None = None,
        agent_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get the audit trail, optionally filtered.

        Args:
            limit: Maximum entries to return (most recent first).
            category: Filter by category.
            severity: Filter by minimum severity.
            agent_id: Filter by agent.

        Returns:
            List of audit entry dicts, newest first.
        """
        entries = list(self._trail)

        if category is not None:
            entries = [e for e in entries if e.category == category]

        if severity is not None:
            severity_order = [Severity.INFO, Severity.WARNING, Severity.CRITICAL]
            min_idx = severity_order.index(severity)
            entries = [
                e for e in entries
                if severity_order.index(e.severity) >= min_idx
            ]

        if agent_id is not None:
            entries = [e for e in entries if e.agent_id == agent_id]

        # Return newest first, limited
        entries.reverse()
        return [e.to_dict() for e in entries[:limit]]

    def get_report(self) -> dict[str, Any]:
        """Generate a full InsAIts transparency report.

        Returns:
            Dict with status, statistics, recent trail, and anomalies.
        """
        anomalies = [
            e.to_dict() for e in self._trail
            if e.category == AnalysisCategory.ANOMALY
        ]

        critical_events = [
            e.to_dict() for e in self._trail
            if e.severity == Severity.CRITICAL
        ]

        return {
            "status": "running" if self._running else "stopped",
            "uptime_seconds": round(self.uptime_seconds, 1),
            "total_events": self._event_count,
            "category_breakdown": dict(self._category_counts),
            "severity_breakdown": dict(self._severity_counts),
            "recent_trail": self.get_audit_trail(limit=20),
            "anomalies": anomalies[-10:],
            "critical_events": critical_events[-10:],
            "trail_size": len(self._trail),
        }

    def get_prose_summary(self) -> str:
        """Generate a human-readable prose summary of current state.

        This is the key InsAIts V2 feature — real-time transparency
        in natural language for the GUI panel.
        """
        lines: list[str] = []
        lines.append("═══ InsAIts V2 Analysis Report ═══")
        lines.append("")

        if not self._running:
            lines.append("  ⚠ Daemon is not running.")
            return "\n".join(lines)

        lines.append(f"  Uptime: {self.uptime_seconds:.0f}s")
        lines.append(f"  Events analyzed: {self._event_count}")
        lines.append("")

        # Category breakdown
        if self._category_counts:
            lines.append("  Activity:")
            for cat, count in sorted(
                self._category_counts.items(),
                key=lambda x: x[1],
                reverse=True,
            ):
                lines.append(f"    {cat}: {count}")

        # Severity breakdown
        crit = self._severity_counts.get("critical", 0)
        warn = self._severity_counts.get("warning", 0)
        if crit > 0:
            lines.append(f"\n  ⛔ {crit} critical event(s) detected")
        if warn > 0:
            lines.append(f"  ⚠ {warn} warning(s)")

        # Last 5 events as prose
        recent = list(self._trail)[-5:]
        if recent:
            lines.append("")
            lines.append("  Recent:")
            for entry in recent:
                severity_icon = {
                    Severity.INFO: "  ℹ",
                    Severity.WARNING: "  ⚠",
                    Severity.CRITICAL: "  ⛔",
                }.get(entry.severity, "  ·")
                lines.append(f"  {severity_icon} {entry.prose}")

        lines.append("")
        lines.append("═══════════════════════════════════")
        return "\n".join(lines)

    def reset(self) -> None:
        """Clear all state — useful for testing."""
        self._trail.clear()
        self._event_count = 0
        self._category_counts.clear()
        self._severity_counts = {s.value: 0 for s in Severity}
        self._anomaly.reset()
