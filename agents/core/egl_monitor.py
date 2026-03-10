"""
EGL (Evolutionary Generality Loss) Monitoring Pipeline.

Tracks whether the agent swarm is becoming too specialized (losing
generality) over time. Inspired by MAP-Elites quality-diversity
algorithms and Yunjue's EGL metric.

Key Concepts:
  - Diversity Score: How varied are agent behaviors across tasks?
  - Specialization Index: Is one agent dominating a capability?
  - Generality Loss %: Trend line showing capability narrowing.
  - Quality-Diversity Grid: MAP-Elites style behavior × performance map.

Scout additions (additive):
  - ThreatRecord: captures security-relevant behavioral events.
  - SecurityCapabilityScore: summarises coverage of a capability class.
  - EGLMonitor.record_threat_event(): log a security threat observation.
  - EGLMonitor.compute_security_coverage(): % of security caps active in window.
  - EGLMonitor.get_security_posture_report(): Scout-level security analysis.
  - Stagnation alert: fires when no security capability has been exercised
    within ``stagnation_window`` records.

Usage:
    monitor = EGLMonitor()
    monitor.record_behavior("sentinel", "security.scan", performance=0.95)
    monitor.record_behavior("scribe", "security.scan", performance=0.72)
    report = monitor.get_egl_report()
"""

from __future__ import annotations

import math
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

# Canonical set of security-related capability prefixes that Scout tracks.
_SECURITY_CAPABILITY_PREFIXES: frozenset[str] = frozenset(
    {
        "security.",
        "threat.",
        "vuln.",
        "scan.",
        "audit.",
        "align.",
        "privesc.",
        "inject.",
        "exfil.",
        "ssrf.",
    }
)


# ─── Data Types ─────────────────────────────────────────────────────────────

@dataclass
class BehaviorRecord:
    """A single observed agent behavior."""
    record_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    agent_id: str = ""
    capability: str = ""          # e.g., "security.scan", "code.generate"
    performance: float = 0.0      # [0.0, 1.0]
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "agent_id": self.agent_id,
            "capability": self.capability,
            "performance": self.performance,
            "timestamp": self.timestamp,
        }


@dataclass
class EGLMetric:
    """A point-in-time EGL measurement."""
    timestamp: float = field(default_factory=time.time)
    diversity_score: float = 0.0        # Shannon entropy of capability distribution
    specialization_index: float = 0.0   # Gini coefficient of agent dominance
    generality_loss_pct: float = 0.0    # % decrease from baseline diversity
    active_capabilities: int = 0
    active_agents: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "diversity_score": round(self.diversity_score, 4),
            "specialization_index": round(self.specialization_index, 4),
            "generality_loss_pct": round(self.generality_loss_pct, 2),
            "active_capabilities": self.active_capabilities,
            "active_agents": self.active_agents,
        }


@dataclass
class QDCell:
    """A cell in the Quality-Diversity grid (MAP-Elites inspired)."""
    capability: str = ""
    agent_id: str = ""
    best_performance: float = 0.0
    sample_count: int = 0
    last_updated: float = field(default_factory=time.time)


# ─── EGL Alert ──────────────────────────────────────────────────────────────

@dataclass
class EGLAlert:
    """An alert triggered by EGL threshold breach."""
    alert_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    alert_type: str = ""          # convergence | dominance | stagnation
    message: str = ""
    severity: str = "warning"     # warning | critical
    metric: EGLMetric | None = None
    timestamp: float = field(default_factory=time.time)


# ─── Scout: Security Tracking ────────────────────────────────────────────────


@dataclass
class ThreatRecord:
    """A security-relevant event observed by the Scout persona."""

    record_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    threat_id: str = ""           # e.g. "THREAT-SSRF", "DEP-002"
    source_agent: str = ""        # Which agent reported it
    capability: str = ""          # Capability domain, e.g. "security.ssrf"
    severity: str = "medium"      # critical | high | medium | low
    blocked: bool = False
    description: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "threat_id": self.threat_id,
            "source_agent": self.source_agent,
            "capability": self.capability,
            "severity": self.severity,
            "blocked": self.blocked,
            "description": self.description,
            "timestamp": self.timestamp,
        }


@dataclass
class SecurityCapabilityScore:
    """Summary score for a single security capability domain."""

    capability: str = ""
    active_agents: int = 0
    observation_count: int = 0
    best_performance: float = 0.0
    last_seen: float = 0.0
    threat_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "active_agents": self.active_agents,
            "observation_count": self.observation_count,
            "best_performance": round(self.best_performance, 4),
            "last_seen": self.last_seen,
            "threat_count": self.threat_count,
        }


# ─── EGL Monitor ────────────────────────────────────────────────────────────

class EGLMonitor:
    """Tracks agent behavior diversity and detects generality loss.

    Uses Shannon entropy for diversity and Gini coefficient for
    specialization dominance detection.
    """

    def __init__(
        self,
        *,
        convergence_threshold: float = 0.3,
        dominance_threshold: float = 0.8,
        window_size: int = 100,
        stagnation_window: int = 50,
    ) -> None:
        self._records: list[BehaviorRecord] = []
        self._metrics_history: list[EGLMetric] = []
        self._alerts: list[EGLAlert] = []
        self._baseline_diversity: float | None = None

        # MAP-Elites grid: (capability, agent) → QDCell
        self._qd_grid: dict[tuple[str, str], QDCell] = {}

        # Config
        self._convergence_threshold = convergence_threshold
        self._dominance_threshold = dominance_threshold
        self._window_size = window_size
        self._stagnation_window = stagnation_window

        # Scout: threat tracking
        self._threat_records: list[ThreatRecord] = []

    @property
    def record_count(self) -> int:
        return len(self._records)

    def record_behavior(
        self,
        agent_id: str,
        capability: str,
        *,
        performance: float = 1.0,
    ) -> BehaviorRecord:
        """Record an observed agent behavior."""
        record = BehaviorRecord(
            agent_id=agent_id,
            capability=capability,
            performance=max(0.0, min(1.0, performance)),
        )
        self._records.append(record)

        # Update QD grid
        key = (capability, agent_id)
        cell = self._qd_grid.get(key)
        if cell is None:
            cell = QDCell(capability=capability, agent_id=agent_id)
            self._qd_grid[key] = cell
        cell.sample_count += 1
        cell.best_performance = max(cell.best_performance, record.performance)
        cell.last_updated = time.time()

        return record

    def compute_diversity(
        self, records: list[BehaviorRecord] | None = None
    ) -> float:
        """Compute Shannon entropy of capability distribution.

        Higher = more diverse. 0 = all behaviors identical.
        """
        recs = records or self._records[-self._window_size:]
        if not recs:
            return 0.0

        counts: dict[str, int] = defaultdict(int)
        for r in recs:
            counts[r.capability] += 1

        total = len(recs)
        entropy = 0.0
        for count in counts.values():
            p = count / total
            if p > 0:
                entropy -= p * math.log2(p)
        return entropy

    def compute_specialization(
        self, records: list[BehaviorRecord] | None = None
    ) -> float:
        """Compute Gini coefficient of agent capability dominance.

        0 = perfectly equal. 1 = one agent does everything.
        """
        recs = records or self._records[-self._window_size:]
        if not recs:
            return 0.0

        agent_counts: dict[str, int] = defaultdict(int)
        for r in recs:
            agent_counts[r.agent_id] += 1

        values = sorted(agent_counts.values())
        n = len(values)
        if n <= 1:
            return 0.0

        total = sum(values)
        cumulative = 0.0
        weighted_sum = 0.0
        for i, v in enumerate(values):
            cumulative += v
            weighted_sum += (i + 1) * v

        gini = (2 * weighted_sum) / (n * total) - (n + 1) / n
        return max(0.0, min(1.0, gini))

    def measure(self) -> EGLMetric:
        """Take a point-in-time EGL measurement."""
        recent = self._records[-self._window_size:]
        diversity = self.compute_diversity(recent)
        specialization = self.compute_specialization(recent)

        # Set baseline on first measurement
        if self._baseline_diversity is None and diversity > 0:
            self._baseline_diversity = diversity

        # Generality loss
        loss_pct = 0.0
        if self._baseline_diversity and self._baseline_diversity > 0:
            loss_pct = max(
                0.0,
                (1 - diversity / self._baseline_diversity) * 100,
            )

        agents = set(r.agent_id for r in recent)
        capabilities = set(r.capability for r in recent)

        metric = EGLMetric(
            diversity_score=diversity,
            specialization_index=specialization,
            generality_loss_pct=loss_pct,
            active_capabilities=len(capabilities),
            active_agents=len(agents),
        )
        self._metrics_history.append(metric)

        # Check thresholds
        self._check_alerts(metric)

        return metric

    def _check_alerts(self, metric: EGLMetric) -> None:
        """Check if the metric breaches any thresholds."""
        if metric.diversity_score < self._convergence_threshold and metric.active_capabilities > 1:
            self._alerts.append(EGLAlert(
                alert_type="convergence",
                message=(
                    f"Diversity score {metric.diversity_score:.3f} below "
                    f"threshold {self._convergence_threshold}"
                ),
                severity="critical",
                metric=metric,
            ))

        if metric.specialization_index > self._dominance_threshold:
            self._alerts.append(EGLAlert(
                alert_type="dominance",
                message=(
                    f"Specialization index {metric.specialization_index:.3f} "
                    f"exceeds threshold {self._dominance_threshold}"
                ),
                severity="warning",
                metric=metric,
            ))

        # Scout: stagnation alert — no security capability exercised recently
        if len(self._records) >= self._stagnation_window:
            recent_window = self._records[-self._stagnation_window:]
            observed_prefixes = {
                prefix
                for r in recent_window
                for prefix in _SECURITY_CAPABILITY_PREFIXES
                if r.capability.startswith(prefix)
            }
            if not observed_prefixes:
                self._alerts.append(EGLAlert(
                    alert_type="stagnation",
                    message=(
                        f"No security capability observed in the last "
                        f"{self._stagnation_window} records"
                    ),
                    severity="warning",
                    metric=metric,
                ))

    def get_qd_grid(self) -> list[dict[str, Any]]:
        """Get the MAP-Elites quality-diversity grid."""
        return [
            {
                "capability": cell.capability,
                "agent_id": cell.agent_id,
                "best_performance": cell.best_performance,
                "sample_count": cell.sample_count,
            }
            for cell in sorted(
                self._qd_grid.values(),
                key=lambda c: c.best_performance,
                reverse=True,
            )
        ]

    def get_alerts(
        self, *, limit: int = 20, severity: str | None = None
    ) -> list[dict[str, Any]]:
        """Get recent alerts."""
        alerts = list(self._alerts)
        if severity:
            alerts = [a for a in alerts if a.severity == severity]
        alerts.reverse()
        return [
            {
                "alert_id": a.alert_id,
                "type": a.alert_type,
                "message": a.message,
                "severity": a.severity,
                "timestamp": a.timestamp,
            }
            for a in alerts[:limit]
        ]

    def get_egl_report(self) -> dict[str, Any]:
        """Get a comprehensive EGL report."""
        metric = self.measure() if self._records else EGLMetric()
        return {
            "current": metric.to_dict(),
            "baseline_diversity": self._baseline_diversity,
            "total_records": self.record_count,
            "qd_grid_size": len(self._qd_grid),
            "alert_count": len(self._alerts),
            "history_length": len(self._metrics_history),
        }

    # ─── Scout Methods ──────────────────────────────────────────────────────

    def record_threat_event(
        self,
        threat_id: str,
        source_agent: str,
        capability: str,
        *,
        severity: str = "medium",
        blocked: bool = False,
        description: str = "",
    ) -> ThreatRecord:
        """
        Record a security-relevant threat event observed by the Scout persona.

        This also implicitly records a behavior for the source_agent / capability
        pair so that EGL metrics reflect security activity.
        """
        record = ThreatRecord(
            threat_id=threat_id,
            source_agent=source_agent,
            capability=capability,
            severity=severity,
            blocked=blocked,
            description=description,
        )
        self._threat_records.append(record)

        # Mirror into behavior records so EGL metrics capture security activity
        perf = 1.0 if blocked else 0.5
        self.record_behavior(source_agent, capability, performance=perf)

        return record

    def compute_security_coverage(self) -> float:
        """
        Scout security-coverage metric.

        Returns the fraction of known security capability prefixes that have
        been observed in the current window (0.0 – 1.0).
        """
        recent = self._records[-self._window_size:]
        if not recent:
            return 0.0

        observed_prefixes: set[str] = set()
        for r in recent:
            for prefix in _SECURITY_CAPABILITY_PREFIXES:
                if r.capability.startswith(prefix):
                    observed_prefixes.add(prefix)

        return len(observed_prefixes) / len(_SECURITY_CAPABILITY_PREFIXES)

    def get_security_posture_report(self) -> dict[str, Any]:
        """
        Scout security-posture report.

        Aggregates threat records and maps them to
        :class:`SecurityCapabilityScore` objects so callers can see which
        security domains are well-covered vs. stale.
        """
        scores: dict[str, SecurityCapabilityScore] = {}
        threat_by_cap: dict[str, int] = defaultdict(int)

        for tr in self._threat_records:
            threat_by_cap[tr.capability] += 1

        windowed_records = self._records[-self._window_size:]
        for r in windowed_records:
            for prefix in _SECURITY_CAPABILITY_PREFIXES:
                if r.capability.startswith(prefix):
                    if r.capability not in scores:
                        scores[r.capability] = SecurityCapabilityScore(
                            capability=r.capability,
                        )
                    sc = scores[r.capability]
                    sc.observation_count += 1
                    sc.best_performance = max(sc.best_performance, r.performance)
                    sc.last_seen = max(sc.last_seen, r.timestamp)
                    break

        # Attach threat counts
        for cap, count in threat_by_cap.items():
            if cap not in scores:
                scores[cap] = SecurityCapabilityScore(capability=cap)
            scores[cap].threat_count = count

        # Count active agents per capability (within window)
        agents_by_cap: dict[str, set[str]] = defaultdict(set)
        for r in windowed_records:
            for prefix in _SECURITY_CAPABILITY_PREFIXES:
                if r.capability.startswith(prefix):
                    agents_by_cap[r.capability].add(r.agent_id)
                    break
        for cap, agents in agents_by_cap.items():
            if cap in scores:
                scores[cap].active_agents = len(agents)

        security_coverage = self.compute_security_coverage()
        stagnation_alerts = [
            a for a in self._alerts if a.alert_type == "stagnation"
        ]

        return {
            "security_coverage": round(security_coverage, 4),
            "security_capabilities_observed": len(scores),
            "total_threat_events": len(self._threat_records),
            "blocked_threats": sum(1 for t in self._threat_records if t.blocked),
            "stagnation_alert_count": len(stagnation_alerts),
            "capability_scores": [sc.to_dict() for sc in sorted(
                scores.values(),
                key=lambda s: s.observation_count,
                reverse=True,
            )],
        }
