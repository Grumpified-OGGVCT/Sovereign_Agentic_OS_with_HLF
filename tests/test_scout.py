"""
Tests for Scout — Autonomous Threat & Dependency Review.

Covers additive Scout enhancements to:
  - agents/gateway/sentinel_gate.py
  - agents/core/egl_monitor.py
"""

from __future__ import annotations

import pytest

from agents.core.egl_monitor import (
    EGLMonitor,
    SecurityCapabilityScore,
    ThreatRecord,
)
from agents.gateway.sentinel_gate import (
    DependencyRiskEntry,
    enforce_align,
    get_threat_summary,
    scan_for_dependency_risks,
    scan_for_threats,
)


# ─── sentinel_gate Scout Tests ───────────────────────────────────────────────


class TestDependencyRiskEntry:
    def test_dataclass_fields(self):
        entry = DependencyRiskEntry(
            pattern_id="DEP-001",
            description="Pickle risk",
            severity="high",
            matched_text="pickle",
            recommendation="Use json.",
        )
        assert entry.pattern_id == "DEP-001"
        assert entry.severity == "high"

    def test_defaults(self):
        entry = DependencyRiskEntry(
            pattern_id="DEP-X",
            description="desc",
            severity="low",
        )
        assert entry.matched_text == ""
        assert entry.recommendation == ""


class TestScanForDependencyRisks:
    def test_pickle_detected(self):
        findings = scan_for_dependency_risks("import pickle\npickle.loads(data)")
        assert any(f.pattern_id == "DEP-001" for f in findings)

    def test_yaml_load_without_loader_detected(self):
        findings = scan_for_dependency_risks("yaml.load(data)")
        ids = [f.pattern_id for f in findings]
        assert "DEP-002" in ids

    def test_yaml_load_with_safe_loader_not_flagged(self):
        # yaml.load(data, Loader=yaml.SafeLoader) should NOT be flagged
        findings = scan_for_dependency_risks("yaml.load(data, Loader=yaml.SafeLoader)")
        ids = [f.pattern_id for f in findings]
        assert "DEP-002" not in ids

    def test_yaml_load_no_space_before_loader_not_flagged(self):
        findings = scan_for_dependency_risks("yaml.load(data,Loader=yaml.SafeLoader)")
        ids = [f.pattern_id for f in findings]
        assert "DEP-002" not in ids

    def test_yaml_load_extra_spaces_not_flagged(self):
        findings = scan_for_dependency_risks("yaml.load(data, Loader = yaml.SafeLoader)")
        ids = [f.pattern_id for f in findings]
        assert "DEP-002" not in ids

    def test_weak_crypto_detected(self):
        findings = scan_for_dependency_risks("hashlib.md5(value)")
        ids = [f.pattern_id for f in findings]
        assert "DEP-005" in ids

    def test_sha1_detected(self):
        findings = scan_for_dependency_risks("hashlib.sha1(data)")
        ids = [f.pattern_id for f in findings]
        assert "DEP-005" in ids

    def test_telnetlib_detected(self):
        findings = scan_for_dependency_risks("import telnetlib")
        ids = [f.pattern_id for f in findings]
        assert "DEP-007" in ids

    def test_clean_code_no_findings(self):
        findings = scan_for_dependency_risks(
            "import json\ndata = json.loads(text)\nresult = hashlib.sha256(data)"
        )
        assert findings == []

    def test_deduplication(self):
        # Same pattern appearing twice should yield one entry
        text = "pickle.loads(a)\npickle.loads(b)"
        findings = scan_for_dependency_risks(text)
        dep001_entries = [f for f in findings if f.pattern_id == "DEP-001"]
        assert len(dep001_entries) == 1

    def test_multiple_patterns_in_one_payload(self):
        text = "import pickle\nimport telnetlib\nhashlib.md5(x)"
        findings = scan_for_dependency_risks(text)
        ids = {f.pattern_id for f in findings}
        assert "DEP-001" in ids
        assert "DEP-007" in ids
        assert "DEP-005" in ids

    def test_severity_field_populated(self):
        findings = scan_for_dependency_risks("import pickle")
        dep001 = next(f for f in findings if f.pattern_id == "DEP-001")
        assert dep001.severity in {"critical", "high", "medium", "low"}

    def test_matched_text_populated(self):
        findings = scan_for_dependency_risks("hashlib.md5(value)")
        dep005 = next(f for f in findings if f.pattern_id == "DEP-005")
        assert dep005.matched_text != ""

    def test_recommendation_populated(self):
        findings = scan_for_dependency_risks("import pickle")
        dep001 = next(f for f in findings if f.pattern_id == "DEP-001")
        assert len(dep001.recommendation) > 5


class TestScanForThreats:
    def test_ssrf_metadata_address_detected(self):
        findings = scan_for_threats("GET http://169.254.169.254/latest/meta-data/")
        assert any(f["threat_id"] == "THREAT-SSRF" for f in findings)

    def test_ssrf_localhost_detected(self):
        findings = scan_for_threats("url=http://localhost:8080/admin")
        assert any(f["threat_id"] == "THREAT-SSRF" for f in findings)

    def test_path_traversal_detected(self):
        findings = scan_for_threats("file=../../etc/passwd")
        assert any(f["threat_id"] == "THREAT-PATH-TRAVERSAL" for f in findings)

    def test_path_traversal_url_encoded_detected(self):
        findings = scan_for_threats("path=%2e%2e%2fetc%2fpasswd")
        assert any(f["threat_id"] == "THREAT-PATH-TRAVERSAL" for f in findings)

    def test_template_injection_detected(self):
        findings = scan_for_threats("name={{7*7}}")
        assert any(f["threat_id"] == "THREAT-TEMPLATE-INJECTION" for f in findings)

    def test_clean_payload_no_threats(self):
        findings = scan_for_threats("Hello, world! This is a normal payload.")
        assert findings == []

    def test_threat_finding_structure(self):
        findings = scan_for_threats("../../etc/passwd")
        assert len(findings) > 0
        f = findings[0]
        assert "threat_id" in f
        assert "description" in f
        assert "severity" in f
        assert "matched" in f

    def test_threat_severity_is_string(self):
        findings = scan_for_threats("../../etc/passwd")
        f = findings[0]
        assert isinstance(f["severity"], str)
        assert f["severity"] in {"critical", "high", "medium", "low"}

    def test_deduplication(self):
        text = "../../etc/../etc/passwd ../../etc/shadow"
        findings = scan_for_threats(text)
        traversal_ids = [f for f in findings if f["threat_id"] == "THREAT-PATH-TRAVERSAL"]
        assert len(traversal_ids) == 1

    def test_windows_path_traversal_detected(self):
        findings = scan_for_threats(r"path=..\..\..\windows\system32")
        assert any(f["threat_id"] == "THREAT-PATH-TRAVERSAL" for f in findings)

    def test_double_encoded_path_traversal_detected(self):
        findings = scan_for_threats("file=%252e%252e%252f")
        assert any(f["threat_id"] == "THREAT-PATH-TRAVERSAL" for f in findings)


class TestGetThreatSummary:
    def test_returns_dict(self):
        summary = get_threat_summary()
        assert isinstance(summary, dict)

    def test_has_required_keys(self):
        summary = get_threat_summary()
        assert "align_rules" in summary
        assert "dependency_risk_patterns" in summary
        assert "threat_patterns" in summary
        assert "total_detectors" in summary

    def test_align_rules_is_list(self):
        summary = get_threat_summary()
        assert isinstance(summary["align_rules"], list)

    def test_align_rules_include_new_rules(self):
        summary = get_threat_summary()
        ids = [r["id"] for r in summary["align_rules"]]
        assert "R-010" in ids
        assert "R-011" in ids
        assert "R-012" in ids

    def test_total_detectors_positive(self):
        summary = get_threat_summary()
        assert summary["total_detectors"] > 0

    def test_dependency_risk_count_positive(self):
        summary = get_threat_summary()
        assert summary["dependency_risk_patterns"] > 0

    def test_threat_pattern_count_positive(self):
        summary = get_threat_summary()
        assert summary["threat_patterns"] > 0


class TestAlignLedgerNewRules:
    """Verify the new R-010/R-011/R-012 rules fire via enforce_align."""

    def test_r010_ssrf_blocked(self):
        blocked, rule_id = enforce_align("http://169.254.169.254/latest/meta-data")
        assert blocked
        assert rule_id == "R-010"

    def test_r011_path_traversal_blocked(self):
        blocked, rule_id = enforce_align("path=../../etc/passwd")
        assert blocked
        assert rule_id == "R-011"

    def test_r012_pickle_loads_blocked(self):
        blocked, rule_id = enforce_align("pickle.loads(user_data)")
        assert blocked
        assert rule_id == "R-012"

    def test_r012_pickle_dumps_not_blocked(self):
        blocked, _ = enforce_align("pickle.dumps(my_object)")
        assert not blocked

    def test_r012_telnetlib_blocked(self):
        blocked, rule_id = enforce_align("import telnetlib")
        assert blocked
        assert rule_id == "R-012"

    def test_clean_payload_still_passes(self):
        blocked, _ = enforce_align("[INTENT] analyze /data/report.json\n[EXPECT] summary\nΩ")
        assert not blocked


# ─── egl_monitor Scout Tests ─────────────────────────────────────────────────


class TestThreatRecord:
    def test_defaults(self):
        tr = ThreatRecord(
            threat_id="THREAT-SSRF",
            source_agent="sentinel",
            capability="security.ssrf",
        )
        assert tr.record_id
        assert not tr.blocked
        assert tr.severity == "medium"
        assert tr.timestamp > 0

    def test_to_dict(self):
        tr = ThreatRecord(
            threat_id="DEP-001",
            source_agent="scout",
            capability="security.dep",
            severity="high",
            blocked=True,
            description="Pickle detected",
        )
        d = tr.to_dict()
        assert d["threat_id"] == "DEP-001"
        assert d["blocked"] is True
        assert d["severity"] == "high"


class TestSecurityCapabilityScore:
    def test_to_dict(self):
        sc = SecurityCapabilityScore(
            capability="security.scan",
            active_agents=2,
            observation_count=10,
            best_performance=0.95,
            threat_count=3,
        )
        d = sc.to_dict()
        assert d["capability"] == "security.scan"
        assert d["active_agents"] == 2
        assert d["threat_count"] == 3


class TestEGLMonitorScoutMethods:
    def setup_method(self):
        self.monitor = EGLMonitor(
            convergence_threshold=0.3,
            dominance_threshold=0.8,
            stagnation_window=10,
        )

    def test_record_threat_event_returns_record(self):
        tr = self.monitor.record_threat_event(
            "THREAT-SSRF", "sentinel", "security.ssrf",
            severity="critical", blocked=True,
        )
        assert isinstance(tr, ThreatRecord)
        assert tr.threat_id == "THREAT-SSRF"
        assert tr.blocked is True

    def test_record_threat_mirrors_to_behavior(self):
        self.monitor.record_threat_event(
            "DEP-001", "scout", "security.dep",
        )
        assert self.monitor.record_count == 1

    def test_record_threat_blocked_performance_higher(self):
        self.monitor.record_threat_event(
            "T1", "agent", "security.scan", blocked=True,
        )
        record = self.monitor._records[-1]
        assert record.performance == 1.0

    def test_record_threat_unblocked_performance_lower(self):
        self.monitor.record_threat_event(
            "T1", "agent", "security.scan", blocked=False,
        )
        record = self.monitor._records[-1]
        assert record.performance == 0.5

    def test_compute_security_coverage_zero_when_no_security(self):
        self.monitor.record_behavior("coder", "code.generate")
        self.monitor.record_behavior("scribe", "logging.write")
        coverage = self.monitor.compute_security_coverage()
        assert coverage == 0.0

    def test_compute_security_coverage_nonzero_with_security(self):
        self.monitor.record_behavior("sentinel", "security.scan")
        coverage = self.monitor.compute_security_coverage()
        assert coverage > 0.0

    def test_compute_security_coverage_range(self):
        for cap in ["security.scan", "threat.detect", "vuln.assess"]:
            self.monitor.record_behavior("agent", cap)
        coverage = self.monitor.compute_security_coverage()
        assert 0.0 <= coverage <= 1.0

    def test_get_security_posture_report_structure(self):
        self.monitor.record_threat_event("T1", "sentinel", "security.scan", blocked=True)
        report = self.monitor.get_security_posture_report()
        assert "security_coverage" in report
        assert "total_threat_events" in report
        assert "blocked_threats" in report
        assert "stagnation_alert_count" in report
        assert "capability_scores" in report
        assert "security_capabilities_observed" in report

    def test_get_security_posture_blocked_count(self):
        self.monitor.record_threat_event("T1", "s", "security.scan", blocked=True)
        self.monitor.record_threat_event("T2", "s", "security.scan", blocked=False)
        report = self.monitor.get_security_posture_report()
        assert report["total_threat_events"] == 2
        assert report["blocked_threats"] == 1

    def test_get_security_posture_capability_scores_populated(self):
        self.monitor.record_threat_event("T1", "sentinel", "security.scan")
        report = self.monitor.get_security_posture_report()
        caps = [c["capability"] for c in report["capability_scores"]]
        assert "security.scan" in caps

    def test_stagnation_alert_no_security_records(self):
        for i in range(15):
            self.monitor.record_behavior("coder", f"code.task{i}")
        self.monitor.measure()
        stagnation_alerts = [
            a for a in self.monitor._alerts if a.alert_type == "stagnation"
        ]
        assert len(stagnation_alerts) >= 1

    def test_stagnation_alert_not_fired_when_security_present(self):
        for i in range(5):
            self.monitor.record_behavior("coder", "code.generate")
        self.monitor.record_behavior("sentinel", "security.scan")
        for i in range(4):
            self.monitor.record_behavior("coder", "code.generate")
        self.monitor.measure()
        stagnation_alerts = [
            a for a in self.monitor._alerts if a.alert_type == "stagnation"
        ]
        assert len(stagnation_alerts) == 0

    def test_security_posture_report_empty_monitor(self):
        report = self.monitor.get_security_posture_report()
        assert report["total_threat_events"] == 0
        assert report["security_coverage"] == 0.0
        assert report["capability_scores"] == []

    def test_threat_record_stored_in_monitor(self):
        self.monitor.record_threat_event("X", "a", "security.x")
        assert len(self.monitor._threat_records) == 1

    def test_multiple_threat_records(self):
        for i in range(5):
            self.monitor.record_threat_event(f"T{i}", "a", "security.scan")
        assert len(self.monitor._threat_records) == 5
        report = self.monitor.get_security_posture_report()
        assert report["total_threat_events"] == 5
