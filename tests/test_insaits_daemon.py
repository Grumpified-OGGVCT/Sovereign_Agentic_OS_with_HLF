"""Tests for InsAIts V2 Daemon — continuous transparency analysis engine."""

from __future__ import annotations

from unittest.mock import MagicMock

from agents.core.daemons.insaits_daemon import (
    AnalysisCategory,
    AnomalyDetector,
    AuditEntry,
    InsAItsDaemon,
    Severity,
    _categorize_event,
    _generate_prose,
    _severity_for_event,
)

# ─── AuditEntry Tests ───────────────────────────────────────────────────────


class TestAuditEntry:
    def test_to_dict(self):
        entry = AuditEntry(
            timestamp=1000.0,
            category=AnalysisCategory.EXECUTION,
            severity=Severity.INFO,
            prose="Test event",
            agent_id="sentinel",
            event_type="test",
        )
        d = entry.to_dict()
        assert d["category"] == "execution"
        assert d["severity"] == "info"
        assert d["prose"] == "Test event"
        assert d["agent_id"] == "sentinel"

    def test_metadata_default(self):
        entry = AuditEntry(
            timestamp=0, category=AnalysisCategory.GAS,
            severity=Severity.WARNING, prose="test",
        )
        assert entry.metadata == {}


# ─── Prose Generator Tests ──────────────────────────────────────────────────


class TestProseGenerator:
    def test_known_event(self):
        prose = _generate_prose("execution_started", {"agent_id": "forge"})
        assert "Agent execution started" in prose
        assert "forge" in prose

    def test_unknown_event(self):
        prose = _generate_prose("custom_event", {})
        assert "custom_event" in prose

    def test_model_detail(self):
        prose = _generate_prose("model_routed", {"model": "gemini-3"})
        assert "gemini-3" in prose

    def test_gas_detail(self):
        prose = _generate_prose("gas_consumed", {"gas_units": 500})
        assert "500" in prose

    def test_error_detail(self):
        prose = _generate_prose("execution_failed", {"error": "timeout"})
        assert "timeout" in prose

    def test_duration_detail(self):
        prose = _generate_prose("tool_completed", {"duration_ms": 42})
        assert "42ms" in prose

    def test_tool_detail(self):
        prose = _generate_prose("tool_invoked", {"tool": "git_clone"})
        assert "git_clone" in prose


# ─── Categorization Tests ───────────────────────────────────────────────────


class TestCategorization:
    def test_routing_category(self):
        assert _categorize_event("model_routed") == AnalysisCategory.ROUTING
        assert _categorize_event("model_fallback") == AnalysisCategory.ROUTING

    def test_security_category(self):
        assert _categorize_event("align_violation") == AnalysisCategory.SECURITY
        assert _categorize_event("validation_rejected") == AnalysisCategory.SECURITY

    def test_gas_category(self):
        assert _categorize_event("gas_consumed") == AnalysisCategory.GAS

    def test_memory_category(self):
        assert _categorize_event("memory_stored") == AnalysisCategory.MEMORY

    def test_tool_category(self):
        assert _categorize_event("tool_invoked") == AnalysisCategory.TOOL

    def test_lifecycle_category(self):
        assert _categorize_event("daemon_started") == AnalysisCategory.LIFECYCLE

    def test_unknown_defaults_to_execution(self):
        assert _categorize_event("unknown_thing") == AnalysisCategory.EXECUTION


class TestSeverity:
    def test_critical_events(self):
        assert _severity_for_event("align_violation") == Severity.CRITICAL
        assert _severity_for_event("execution_failed") == Severity.CRITICAL

    def test_warning_events(self):
        assert _severity_for_event("model_fallback") == Severity.WARNING

    def test_info_events(self):
        assert _severity_for_event("execution_started") == Severity.INFO
        assert _severity_for_event("unknown") == Severity.INFO


# ─── Anomaly Detector Tests ─────────────────────────────────────────────────


class TestAnomalyDetector:
    def test_no_anomaly_with_few_samples(self):
        detector = AnomalyDetector()
        # First 4 samples: no anomaly possible (need 5 for stats)
        for _i in range(4):
            result = detector.record_gas("agent-a", 100)
            assert result is None

    def test_gas_spike_detected(self):
        detector = AnomalyDetector(gas_sigma=1.5)
        # Establish baseline with consistent values
        for _ in range(10):
            detector.record_gas("agent-a", 100)
        # Spike
        result = detector.record_gas("agent-a", 10000)
        assert result is not None
        assert result.category == AnalysisCategory.ANOMALY
        assert "spike" in result.prose.lower()

    def test_no_spike_for_normal_values(self):
        detector = AnomalyDetector()
        for _ in range(10):
            detector.record_gas("agent-a", 100)
        # Normal value
        result = detector.record_gas("agent-a", 110)
        assert result is None

    def test_repeated_failures(self):
        detector = AnomalyDetector(failure_threshold=3)
        assert detector.record_failure("agent-a") is None
        assert detector.record_failure("agent-a") is None
        result = detector.record_failure("agent-a", "timeout")
        assert result is not None
        assert result.severity == Severity.CRITICAL
        assert "timeout" in result.prose

    def test_success_resets_failures(self):
        detector = AnomalyDetector(failure_threshold=3)
        detector.record_failure("agent-a")
        detector.record_failure("agent-a")
        detector.record_success("agent-a")
        # Counter reset — takes 3 more to trigger
        assert detector.record_failure("agent-a") is None
        assert detector.record_failure("agent-a") is None
        assert detector.record_failure("agent-a") is not None

    def test_model_rejection_pattern(self):
        detector = AnomalyDetector(failure_threshold=3)
        detector.record_rejection("bad-model")
        detector.record_rejection("bad-model")
        result = detector.record_rejection("bad-model")
        assert result is not None
        assert "bad-model" in result.prose

    def test_reset_clears_state(self):
        detector = AnomalyDetector(failure_threshold=2)
        detector.record_failure("agent-a")
        detector.reset()
        assert detector.record_failure("agent-a") is None


# ─── InsAIts Daemon Tests ───────────────────────────────────────────────────


class TestInsAItsDaemon:
    def test_start_stop_lifecycle(self):
        daemon = InsAItsDaemon()
        assert not daemon.is_running
        daemon.start()
        assert daemon.is_running
        assert daemon.event_count >= 1  # start event
        daemon.stop()
        assert not daemon.is_running

    def test_double_start_is_idempotent(self):
        daemon = InsAItsDaemon()
        daemon.start()
        count = daemon.event_count
        daemon.start()
        assert daemon.event_count == count

    def test_process_event(self):
        daemon = InsAItsDaemon()
        daemon.start()
        entry = daemon.process_event("execution_started", {"agent_id": "forge"})
        assert entry.category == AnalysisCategory.EXECUTION
        assert entry.severity == Severity.INFO
        assert "forge" in entry.prose

    def test_process_event_default_data(self):
        daemon = InsAItsDaemon()
        entry = daemon.process_event("health_check")
        assert entry.event_type == "health_check"

    def test_audit_trail(self):
        daemon = InsAItsDaemon()
        daemon.start()
        daemon.process_event("tool_invoked", {"tool": "pytest"})
        daemon.process_event("tool_completed", {"tool": "pytest"})

        trail = daemon.get_audit_trail(limit=10)
        assert len(trail) >= 3  # start + 2 events
        # Newest first
        assert trail[0]["event_type"] == "tool_completed"

    def test_trail_category_filter(self):
        daemon = InsAItsDaemon()
        daemon.process_event("tool_invoked", {"tool": "a"})
        daemon.process_event("gas_consumed", {"gas_units": 100})
        daemon.process_event("tool_completed", {"tool": "a"})

        trail = daemon.get_audit_trail(category=AnalysisCategory.TOOL)
        assert all(e["category"] == "tool" for e in trail)

    def test_trail_severity_filter(self):
        daemon = InsAItsDaemon()
        daemon.process_event("execution_started", {})
        daemon.process_event("execution_failed", {"error": "oops"})

        trail = daemon.get_audit_trail(severity=Severity.CRITICAL)
        assert all(e["severity"] == "critical" for e in trail)

    def test_trail_agent_filter(self):
        daemon = InsAItsDaemon()
        daemon.process_event("execution_started", {"agent_id": "sentinel"})
        daemon.process_event("execution_started", {"agent_id": "scribe"})

        trail = daemon.get_audit_trail(agent_id="sentinel")
        assert all(e["agent_id"] == "sentinel" for e in trail)

    def test_get_report(self):
        daemon = InsAItsDaemon()
        daemon.start()
        daemon.process_event("tool_invoked", {"tool": "git"})
        daemon.process_event("execution_failed", {"error": "e"})

        report = daemon.get_report()
        assert report["status"] == "running"
        assert report["total_events"] >= 3
        assert "category_breakdown" in report
        assert "severity_breakdown" in report
        assert "recent_trail" in report
        assert "critical_events" in report

    def test_prose_summary_running(self):
        daemon = InsAItsDaemon()
        daemon.start()
        daemon.process_event("gas_consumed", {"gas_units": 100, "agent_id": "x"})
        summary = daemon.get_prose_summary()
        assert "InsAIts V2" in summary
        assert "Uptime" in summary

    def test_prose_summary_stopped(self):
        daemon = InsAItsDaemon()
        summary = daemon.get_prose_summary()
        assert "not running" in summary

    def test_anomaly_integration_gas_spike(self):
        daemon = InsAItsDaemon()
        # Build baseline
        for _ in range(10):
            daemon.process_event("gas_consumed", {"gas_units": 100, "agent_id": "a"})
        # Spike
        daemon.process_event("gas_consumed", {"gas_units": 50000, "agent_id": "a"})

        trail = daemon.get_audit_trail(category=AnalysisCategory.ANOMALY)
        assert len(trail) >= 1

    def test_anomaly_integration_repeated_failure(self):
        daemon = InsAItsDaemon()
        for _ in range(3):
            daemon.process_event("execution_failed", {"agent_id": "b", "error": "fail"})

        trail = daemon.get_audit_trail(category=AnalysisCategory.ANOMALY)
        assert len(trail) >= 1

    def test_max_trail_size(self):
        daemon = InsAItsDaemon(max_trail_size=5)
        for i in range(10):
            daemon.process_event("gas_consumed", {"gas_units": i})
        # Trail capped at 5
        trail = daemon.get_audit_trail(limit=100)
        assert len(trail) == 5

    def test_reset(self):
        daemon = InsAItsDaemon()
        daemon.start()
        daemon.process_event("tool_invoked", {"tool": "x"})
        daemon.reset()
        assert daemon.event_count == 0
        assert len(daemon.get_audit_trail()) == 0

    def test_attach_bus(self):
        bus = MagicMock()
        daemon = InsAItsDaemon()
        daemon.attach_bus(bus)
        daemon.start()
        # Should try to subscribe
        assert bus.subscribe.called

    def test_on_event_handles_exception(self):
        daemon = InsAItsDaemon()
        daemon.start()
        # Should not raise even with bad event
        daemon._on_event(None)
        daemon._on_event("raw_string")

    def test_uptime_when_stopped(self):
        daemon = InsAItsDaemon()
        assert daemon.uptime_seconds == 0.0
