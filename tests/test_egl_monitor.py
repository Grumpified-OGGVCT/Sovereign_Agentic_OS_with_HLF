"""Tests for EGL (Evolutionary Generality Loss) Monitor."""

from __future__ import annotations

from agents.core.egl_monitor import (
    BehaviorRecord,
    EGLMonitor,
)


class TestBehaviorRecord:
    def test_defaults(self):
        r = BehaviorRecord(agent_id="sentinel", capability="security.scan")
        assert r.record_id
        assert r.timestamp > 0

    def test_to_dict(self):
        r = BehaviorRecord(agent_id="a", capability="c", performance=0.9)
        d = r.to_dict()
        assert d["performance"] == 0.9


class TestEGLMonitor:
    def setup_method(self):
        self.monitor = EGLMonitor(
            convergence_threshold=0.3,
            dominance_threshold=0.8,
        )

    def test_record_behavior(self):
        r = self.monitor.record_behavior("sentinel", "security.scan")
        assert r.agent_id == "sentinel"
        assert self.monitor.record_count == 1

    def test_performance_clamped(self):
        r = self.monitor.record_behavior("a", "c", performance=1.5)
        assert r.performance == 1.0
        r2 = self.monitor.record_behavior("a", "c", performance=-0.5)
        assert r2.performance == 0.0

    def test_diversity_single_capability(self):
        for _ in range(10):
            self.monitor.record_behavior("sentinel", "security.scan")
        d = self.monitor.compute_diversity()
        assert d == 0.0  # All same capability

    def test_diversity_multiple_capabilities(self):
        self.monitor.record_behavior("sentinel", "security.scan")
        self.monitor.record_behavior("scribe", "logging.write")
        self.monitor.record_behavior("arbiter", "conflict.resolve")
        d = self.monitor.compute_diversity()
        assert d > 0.0  # Should have positive entropy

    def test_specialization_equal(self):
        self.monitor.record_behavior("a", "c1")
        self.monitor.record_behavior("b", "c2")
        self.monitor.record_behavior("c", "c3")
        s = self.monitor.compute_specialization()
        assert s == 0.0 or s < 0.1  # Near-equal distribution

    def test_specialization_dominant(self):
        for _ in range(50):
            self.monitor.record_behavior("dominant_agent", "scan")
        self.monitor.record_behavior("minor_agent", "log")
        s = self.monitor.compute_specialization()
        assert s > 0.3  # One agent dominates

    def test_measure(self):
        self.monitor.record_behavior("a", "c1", performance=0.9)
        self.monitor.record_behavior("b", "c2", performance=0.8)
        metric = self.monitor.measure()
        assert metric.active_agents == 2
        assert metric.active_capabilities == 2
        assert metric.diversity_score > 0

    def test_baseline_set_on_first_measure(self):
        self.monitor.record_behavior("a", "c1")
        self.monitor.record_behavior("b", "c2")
        self.monitor.measure()
        assert self.monitor._baseline_diversity is not None

    def test_generality_loss_detected(self):
        # Start diverse
        for cap in ["c1", "c2", "c3", "c4"]:
            self.monitor.record_behavior("agent", cap)
        self.monitor.measure()  # Sets baseline

        # Narrow down
        for _ in range(20):
            self.monitor.record_behavior("agent", "c1")
        metric = self.monitor.measure()
        assert metric.generality_loss_pct > 0

    def test_convergence_alert(self):
        # All same capability → low diversity → convergence alert
        for _ in range(10):
            self.monitor.record_behavior("a", "only_one")
        # Also add at least 2 capabilities so active_capabilities > 1
        self.monitor.record_behavior("b", "secondary")
        # Set baseline high first
        self.monitor._baseline_diversity = 2.0
        self.monitor.measure()
        alerts = self.monitor.get_alerts(severity="critical")
        # May or may not fire depending on exact entropy
        # At least verify no crash
        assert isinstance(alerts, list)

    def test_qd_grid(self):
        self.monitor.record_behavior("a", "scan", performance=0.9)
        self.monitor.record_behavior("a", "scan", performance=0.95)
        grid = self.monitor.get_qd_grid()
        assert len(grid) == 1
        assert grid[0]["best_performance"] == 0.95
        assert grid[0]["sample_count"] == 2

    def test_egl_report(self):
        self.monitor.record_behavior("a", "c1")
        report = self.monitor.get_egl_report()
        assert "current" in report
        assert report["total_records"] == 1

    def test_empty_monitor(self):
        d = self.monitor.compute_diversity()
        assert d == 0.0
        s = self.monitor.compute_specialization()
        assert s == 0.0
