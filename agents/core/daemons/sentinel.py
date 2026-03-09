"""
Sentinel Daemon — Anomaly detection and security monitoring.

Watches for:
  - Privilege escalation patterns (tier violations)
  - Injection signatures (code injection in HLF payloads)
  - Anomalous gas consumption (spikes beyond statistical norm)
  - ALIGN_LEDGER rule violations

Part of the Aegis-Nexus runtime daemon triad (Issue #17).
"""

from __future__ import annotations

import logging
import re
import statistics
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from agents.core.daemons import DaemonEventBus, DaemonStatus

_logger = logging.getLogger("aegis.sentinel")


# ─── Alert Severity ─────────────────────────────────────────────────────────


class AlertSeverity(Enum):
    """Severity levels for Sentinel alerts."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# ─── Sentinel Alert ─────────────────────────────────────────────────────────


@dataclass
class SentinelAlert:
    """An alert raised by the Sentinel daemon."""
    pattern: str                    # Which detector triggered
    severity: AlertSeverity
    source: str = ""                # Where the anomaly was detected
    evidence: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""             # ISO-8601
    recommendation: str = ""        # Suggested remediation


# ─── Injection Patterns ─────────────────────────────────────────────────────


_INJECTION_PATTERNS = [
    re.compile(r"__import__\s*\(", re.IGNORECASE),
    re.compile(r"eval\s*\(", re.IGNORECASE),
    re.compile(r"exec\s*\(", re.IGNORECASE),
    re.compile(r"os\.system\s*\(", re.IGNORECASE),
    re.compile(r"subprocess\.", re.IGNORECASE),
    re.compile(r";\s*DROP\s+TABLE", re.IGNORECASE),
    re.compile(r"<script\b", re.IGNORECASE),
    re.compile(r"\{\{.*\}\}", re.IGNORECASE),  # template injection
]


# ─── Sentinel Daemon ────────────────────────────────────────────────────────


class SentinelDaemon:
    """
    Background anomaly detection daemon.

    Monitors runtime events for privilege escalation, injection,
    and gas consumption anomalies. Emits alerts to the DaemonEventBus.

    Args:
        event_bus: The shared daemon event bus.
        check_interval_ms: How often to run checks (ms).
        enabled: Whether the daemon should activate.
        gas_spike_threshold: Multiplier above mean for gas spike detection.
    """

    name = "sentinel"

    def __init__(
        self,
        event_bus: DaemonEventBus | None = None,
        check_interval_ms: int = 5000,
        enabled: bool = True,
        gas_spike_threshold: float = 3.0,
    ):
        self._event_bus = event_bus
        self._check_interval_ms = check_interval_ms
        self._enabled = enabled
        self._gas_spike_threshold = gas_spike_threshold

        # State
        from agents.core.daemons import DaemonStatus
        self._status: DaemonStatus = DaemonStatus.STOPPED
        self._gas_history: list[int] = []
        self._alerts: list[SentinelAlert] = []
        self._check_count: int = 0
        self._last_check_ms: float = 0.0

    @property
    def status(self):
        """Current daemon status."""
        from agents.core.daemons import DaemonStatus
        return self._status

    @status.setter
    def status(self, value):
        from agents.core.daemons import DaemonStatus
        self._status = value

    def start(self) -> None:
        """Start the Sentinel daemon."""
        from agents.core.daemons import DaemonStatus
        if not self._enabled:
            _logger.info("Sentinel daemon disabled, skipping start")
            return
        self._status = DaemonStatus.RUNNING
        _logger.info("Sentinel daemon started (interval=%dms)", self._check_interval_ms)

    def stop(self) -> None:
        """Stop the Sentinel daemon."""
        from agents.core.daemons import DaemonStatus
        self._status = DaemonStatus.STOPPED
        _logger.info("Sentinel daemon stopped (%d alerts raised)", len(self._alerts))

    def check(self, event: dict[str, Any]) -> list[SentinelAlert]:
        """
        Run anomaly checks against a runtime event.

        Args:
            event: Runtime event dict with keys like 'type', 'payload',
                   'tier', 'gas_used', 'source'.

        Returns:
            List of alerts raised by this check.
        """
        from agents.core.daemons import DaemonStatus
        if self._status != DaemonStatus.RUNNING:
            return []

        start = time.monotonic()
        alerts: list[SentinelAlert] = []

        # Check for privilege escalation
        escalation = self._check_privilege_escalation(event)
        if escalation:
            alerts.append(escalation)

        # Check for injection patterns
        injection = self._check_injection(event)
        if injection:
            alerts.append(injection)

        # Check for gas anomalies
        gas_alert = self._check_gas_anomaly(event)
        if gas_alert:
            alerts.append(gas_alert)

        # Check for ALIGN violations
        align_alert = self._check_align_violation(event)
        if align_alert:
            alerts.append(align_alert)

        # Record and emit
        self._alerts.extend(alerts)
        self._check_count += 1
        self._last_check_ms = (time.monotonic() - start) * 1000

        if alerts and self._event_bus:
            from agents.core.daemons import DaemonEvent
            for alert in alerts:
                self._event_bus.emit(DaemonEvent(
                    source="sentinel",
                    event_type="alert",
                    severity=alert.severity.value,
                    data={
                        "pattern": alert.pattern,
                        "evidence": alert.evidence,
                        "recommendation": alert.recommendation,
                    },
                ))

        return alerts

    def get_alerts(self, severity: AlertSeverity | None = None) -> list[SentinelAlert]:
        """Get all recorded alerts, optionally filtered by severity."""
        if severity:
            return [a for a in self._alerts if a.severity == severity]
        return list(self._alerts)

    def get_stats(self) -> dict[str, Any]:
        """Get Sentinel statistics."""
        return {
            "status": self._status.value,
            "check_count": self._check_count,
            "total_alerts": len(self._alerts),
            "critical_alerts": len([a for a in self._alerts if a.severity == AlertSeverity.CRITICAL]),
            "gas_history_size": len(self._gas_history),
            "last_check_ms": round(self._last_check_ms, 2),
        }

    # ─── Detection Methods ───────────────────────────────────────────────

    def _check_privilege_escalation(self, event: dict[str, Any]) -> SentinelAlert | None:
        """Detect tier boundary violations."""
        event_tier = event.get("tier", "")
        required_tier = event.get("required_tier", "")

        tier_order = {"hearth": 0, "forge": 1, "sovereign": 2}
        if event_tier and required_tier:
            event_level = tier_order.get(event_tier, -1)
            required_level = tier_order.get(required_tier, -1)
            if event_level < required_level:
                return SentinelAlert(
                    pattern="privilege_escalation",
                    severity=AlertSeverity.CRITICAL,
                    source=event.get("source", "unknown"),
                    evidence={"event_tier": event_tier, "required_tier": required_tier},
                    recommendation=f"Upgrade deployment tier to '{required_tier}' or restrict operation.",
                )
        return None

    def _check_injection(self, event: dict[str, Any]) -> SentinelAlert | None:
        """Scan payload for injection signatures."""
        payload = str(event.get("payload", ""))
        for pattern in _INJECTION_PATTERNS:
            match = pattern.search(payload)
            if match:
                return SentinelAlert(
                    pattern="injection_detected",
                    severity=AlertSeverity.CRITICAL,
                    source=event.get("source", "unknown"),
                    evidence={"matched": match.group(), "pattern": pattern.pattern},
                    recommendation="Sanitize input and review ALIGN rules.",
                )
        return None

    def _check_gas_anomaly(self, event: dict[str, Any]) -> SentinelAlert | None:
        """Detect anomalous gas consumption spikes."""
        gas_used = event.get("gas_used")
        if gas_used is None:
            return None

        # Need at least 5 baseline data points before detecting spikes
        if len(self._gas_history) < 5:
            self._gas_history.append(gas_used)
            return None

        # Compute stats on EXISTING history (before appending current value)
        mean = statistics.mean(self._gas_history)
        stdev = statistics.stdev(self._gas_history) if len(self._gas_history) > 1 else 0

        threshold = mean + self._gas_spike_threshold * max(stdev, 1)
        self._gas_history.append(gas_used)

        if gas_used > threshold:
            return SentinelAlert(
                pattern="gas_spike",
                severity=AlertSeverity.WARNING,
                source=event.get("source", "unknown"),
                evidence={
                    "gas_used": gas_used,
                    "mean": round(mean, 2),
                    "stdev": round(stdev, 2),
                    "threshold": round(threshold, 2),
                },
                recommendation="Investigate gas-heavy operation for optimization.",
            )
        return None

    def _check_align_violation(self, event: dict[str, Any]) -> SentinelAlert | None:
        """Detect ALIGN_LEDGER rule violations."""
        violation = event.get("align_violation")
        if not violation:
            return None

        rule_id = violation.get("rule", "unknown")
        return SentinelAlert(
            pattern="align_violation",
            severity=AlertSeverity.WARNING,
            source=event.get("source", "unknown"),
            evidence={"rule": rule_id, "details": violation},
            recommendation=f"Review ALIGN rule {rule_id} compliance.",
        )
