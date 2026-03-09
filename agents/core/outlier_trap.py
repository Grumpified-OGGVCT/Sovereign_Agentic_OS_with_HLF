"""
Semantic Outlier Trap — Anomaly quarantine system.

From Phase 4.1 of the Master Build Plan: if anomaly_score > 0.85,
quarantine the process. Monitors ALS (Audit Log Schema) entries
for anomalous patterns and triggers alerts.

Usage:
    trap = OutlierTrap()
    trap.ingest({"event_type": "action.execute", "severity": "critical", ...})
    if trap.latest_alert:
        print(f"Anomaly detected: {trap.latest_alert.reason}")
"""

from __future__ import annotations

import math
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AnomalyAlert:
    """An alert triggered by an anomalous event."""
    alert_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    event_type: str = ""
    anomaly_score: float = 0.0
    reason: str = ""
    quarantined: bool = False
    timestamp: float = field(default_factory=time.time)
    event_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "event_type": self.event_type,
            "anomaly_score": round(self.anomaly_score, 4),
            "reason": self.reason,
            "quarantined": self.quarantined,
            "timestamp": self.timestamp,
        }


class OutlierTrap:
    """Monitors event streams for anomalous patterns.

    Uses statistical z-score analysis on event frequency and
    severity distributions to detect outliers.
    """

    def __init__(
        self,
        *,
        quarantine_threshold: float = 0.85,
        window_size: int = 100,
        severity_weights: dict[str, float] | None = None,
    ) -> None:
        self._threshold = quarantine_threshold
        self._window_size = window_size
        self._severity_weights = severity_weights or {
            "debug": 0.1,
            "info": 0.2,
            "warning": 0.5,
            "error": 0.8,
            "critical": 1.0,
        }
        self._events: deque[dict[str, Any]] = deque(maxlen=window_size)
        self._type_counts: dict[str, int] = defaultdict(int)
        self._severity_counts: dict[str, int] = defaultdict(int)
        self._alerts: list[AnomalyAlert] = []
        self._quarantine: list[dict[str, Any]] = []

    @property
    def event_count(self) -> int:
        return len(self._events)

    @property
    def alert_count(self) -> int:
        return len(self._alerts)

    @property
    def quarantine_count(self) -> int:
        return len(self._quarantine)

    @property
    def latest_alert(self) -> AnomalyAlert | None:
        return self._alerts[-1] if self._alerts else None

    def ingest(self, event: dict[str, Any]) -> AnomalyAlert | None:
        """Ingest an event and check for anomalies.

        Args:
            event: Dict with at least 'event_type' and optionally 'severity'.

        Returns:
            An AnomalyAlert if anomaly detected, else None.
        """
        event_type = event.get("event_type", "unknown")
        severity = event.get("severity", "info")

        self._events.append(event)
        self._type_counts[event_type] += 1
        self._severity_counts[severity] += 1

        score = self._compute_anomaly_score(event)

        if score >= self._threshold:
            alert = AnomalyAlert(
                event_type=event_type,
                anomaly_score=score,
                reason=self._explain_anomaly(event, score),
                quarantined=True,
                event_data=event,
            )
            self._alerts.append(alert)
            self._quarantine.append(event)
            return alert
        return None

    def _compute_anomaly_score(self, event: dict[str, Any]) -> float:
        """Compute anomaly score based on multiple signals."""
        scores = []

        # 1. Severity weight
        severity = event.get("severity", "info")
        sev_score = self._severity_weights.get(severity, 0.3)
        scores.append(sev_score)

        # 2. Event type rarity (inverse frequency)
        event_type = event.get("event_type", "unknown")
        total = sum(self._type_counts.values())
        type_freq = self._type_counts.get(event_type, 0) / max(1, total)
        rarity_score = 1.0 - type_freq  # Rare events score higher
        scores.append(rarity_score)

        # 3. Burst detection (same type appearing too fast)
        recent_same = sum(
            1 for e in list(self._events)[-10:]
            if e.get("event_type") == event_type
        )
        burst_score = min(1.0, recent_same / 10 * 1.5)  # >6/10 = suspicious
        scores.append(burst_score)

        # 4. Error-adjacent scoring
        error_adjacent = 0.0
        if severity in ("error", "critical"):
            error_adjacent = 0.9
        elif severity == "warning":
            error_adjacent = 0.4
        scores.append(error_adjacent)

        # Weighted combination
        weights = [0.3, 0.2, 0.2, 0.3]
        return sum(s * w for s, w in zip(scores, weights))

    def _explain_anomaly(self, event: dict[str, Any], score: float) -> str:
        severity = event.get("severity", "info")
        event_type = event.get("event_type", "unknown")
        return (
            f"Anomaly detected: event_type='{event_type}' severity='{severity}' "
            f"score={score:.3f} exceeds threshold {self._threshold}"
        )

    def get_quarantined(self) -> list[dict[str, Any]]:
        return list(self._quarantine)

    def get_alerts(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return [a.to_dict() for a in self._alerts[-limit:]]

    def get_stats(self) -> dict[str, Any]:
        return {
            "total_events": self.event_count,
            "total_alerts": self.alert_count,
            "quarantined": self.quarantine_count,
            "event_types": dict(self._type_counts),
            "severity_distribution": dict(self._severity_counts),
            "threshold": self._threshold,
        }
