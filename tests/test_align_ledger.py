"""Tests for ALIGN Live Ledger — Safety Rule Management."""

from __future__ import annotations

import pytest

from governance.align_ledger import (
    AlignLedger,
    AlignRule,
    AlignSeverity,
    EnforcementMode,
    EvaluationResult,
    ProposalStatus,
    RuleProposal,
)


class TestAlignRule:
    def test_to_dict(self):
        rule = AlignRule(
            rule_id="r1", name="test",
            description="desc", severity=AlignSeverity.HIGH,
        )
        d = rule.to_dict()
        assert d["rule_id"] == "r1"
        assert d["severity"] == "high"
        assert d["enforcement"] == "block"


class TestAlignLedger:
    def setup_method(self):
        self.ledger = AlignLedger()

    def test_add_rule(self):
        rule = self.ledger.add_rule(
            "no_exec", "Block exec()", AlignSeverity.CRITICAL,
            pattern="exec(",
        )
        assert rule.rule_id
        assert rule.severity == AlignSeverity.CRITICAL
        assert self.ledger.rule_count == 1

    def test_get_rule(self):
        rule = self.ledger.add_rule("test", "Test", AlignSeverity.LOW)
        result = self.ledger.get_rule(rule.rule_id)
        assert result is rule

    def test_list_active_only(self):
        r1 = self.ledger.add_rule("a", "A", AlignSeverity.HIGH)
        r2 = self.ledger.add_rule("b", "B", AlignSeverity.LOW)
        self.ledger.disable_rule(r2.rule_id)
        active = self.ledger.list_rules(active_only=True)
        assert len(active) == 1
        assert active[0].name == "a"

    def test_list_by_category(self):
        self.ledger.add_rule("a", "A", AlignSeverity.HIGH, category="security")
        self.ledger.add_rule("b", "B", AlignSeverity.LOW, category="general")
        sec = self.ledger.list_rules(category="security")
        assert len(sec) == 1

    def test_list_by_severity(self):
        self.ledger.add_rule("a", "A", AlignSeverity.CRITICAL)
        self.ledger.add_rule("b", "B", AlignSeverity.LOW)
        crit = self.ledger.list_rules(severity=AlignSeverity.CRITICAL)
        assert len(crit) == 1

    def test_disable_and_enable_rule(self):
        rule = self.ledger.add_rule("test", "Test", AlignSeverity.MEDIUM)
        self.ledger.disable_rule(rule.rule_id)
        assert not rule.active
        self.ledger.enable_rule(rule.rule_id)
        assert rule.active

    def test_disable_unknown_raises(self):
        with pytest.raises(ValueError):
            self.ledger.disable_rule("nonexistent")

    def test_evaluate_blocks(self):
        self.ledger.add_rule(
            "no_exec", "Block exec()", AlignSeverity.CRITICAL,
            pattern="exec(",
        )
        result = self.ledger.evaluate("exec(user_input)")
        assert result.blocked
        assert not result.passed
        assert len(result.triggered_rules) == 1

    def test_evaluate_passes(self):
        self.ledger.add_rule(
            "no_exec", "Block exec()", AlignSeverity.CRITICAL,
            pattern="exec(",
        )
        result = self.ledger.evaluate("print('hello')")
        assert result.passed
        assert not result.blocked

    def test_evaluate_warns(self):
        self.ledger.add_rule(
            "caution", "Warn on eval", AlignSeverity.MEDIUM,
            pattern="eval(",
            enforcement=EnforcementMode.WARN,
        )
        result = self.ledger.evaluate("eval(expr)")
        assert result.passed  # Not blocked, just warning
        assert len(result.warnings) == 1

    def test_evaluate_ignores_disabled(self):
        rule = self.ledger.add_rule(
            "test", "Test", AlignSeverity.HIGH,
            pattern="danger",
        )
        self.ledger.disable_rule(rule.rule_id)
        result = self.ledger.evaluate("danger zone")
        assert result.passed

    def test_evaluate_case_insensitive(self):
        self.ledger.add_rule(
            "test", "Test", AlignSeverity.HIGH,
            pattern="exec(",
        )
        result = self.ledger.evaluate("EXEC(something)")
        assert result.blocked


class TestProposalWorkflow:
    def setup_method(self):
        self.ledger = AlignLedger()
        self.rule = self.ledger.add_rule(
            "test_rule", "Desc", AlignSeverity.MEDIUM,
        )

    def test_propose_change(self):
        proposal = self.ledger.propose_change(
            self.rule.rule_id, "update",
            {"description": "New desc"},
            proposed_by="agent-x",
            reason="Clarity",
        )
        assert proposal.is_pending()
        assert proposal.rule_id == self.rule.rule_id

    def test_approve_proposal(self):
        proposal = self.ledger.propose_change(
            self.rule.rule_id, "update",
            {"description": "Updated"},
        )
        rule = self.ledger.approve_proposal(
            proposal.proposal_id, approved_by="gerry",
        )
        assert rule is not None
        assert rule.description == "Updated"
        assert rule.version == 2
        assert proposal.status == ProposalStatus.APPROVED

    def test_reject_proposal(self):
        proposal = self.ledger.propose_change(
            self.rule.rule_id, "disable", {},
        )
        self.ledger.reject_proposal(
            proposal.proposal_id,
            rejected_by="gerry",
            reason="Not needed",
        )
        assert proposal.status == ProposalStatus.REJECTED
        assert proposal.rejection_reason == "Not needed"
        # Rule should still be active
        assert self.rule.active

    def test_approve_already_decided_raises(self):
        proposal = self.ledger.propose_change(
            self.rule.rule_id, "update", {"name": "renamed"},
        )
        self.ledger.approve_proposal(proposal.proposal_id)
        with pytest.raises(ValueError, match="not pending"):
            self.ledger.approve_proposal(proposal.proposal_id)

    def test_invalid_action_raises(self):
        with pytest.raises(ValueError, match="Invalid action"):
            self.ledger.propose_change(self.rule.rule_id, "explode", {})

    def test_unknown_rule_raises(self):
        with pytest.raises(ValueError, match="not found"):
            self.ledger.propose_change("fake", "update", {})

    def test_list_pending_proposals(self):
        self.ledger.propose_change(self.rule.rule_id, "update", {"name": "a"})
        self.ledger.propose_change(self.rule.rule_id, "update", {"name": "b"})
        pending = self.ledger.list_proposals(status=ProposalStatus.PENDING)
        assert len(pending) == 2

    def test_delete_via_proposal(self):
        proposal = self.ledger.propose_change(self.rule.rule_id, "delete", {})
        self.ledger.approve_proposal(proposal.proposal_id)
        assert self.ledger.get_rule(self.rule.rule_id) is None

    def test_severity_update_via_proposal(self):
        proposal = self.ledger.propose_change(
            self.rule.rule_id, "update",
            {"severity": "critical"},
        )
        rule = self.ledger.approve_proposal(proposal.proposal_id)
        assert rule.severity == AlignSeverity.CRITICAL


class TestAuditTrail:
    def test_rule_creation_audited(self):
        ledger = AlignLedger()
        ledger.add_rule("test", "Test", AlignSeverity.LOW)
        trail = ledger.get_audit_trail()
        assert len(trail) == 1
        assert trail[0]["action"] == "rule_created"

    def test_proposal_lifecycle_audited(self):
        ledger = AlignLedger()
        rule = ledger.add_rule("test", "Test", AlignSeverity.LOW)
        proposal = ledger.propose_change(rule.rule_id, "update", {"name": "x"})
        ledger.approve_proposal(proposal.proposal_id)
        trail = ledger.get_audit_trail()
        actions = [e["action"] for e in trail]
        assert "proposal_created" in actions
        assert "proposal_approved" in actions

    def test_audit_limit(self):
        ledger = AlignLedger()
        for i in range(100):
            ledger.add_rule(f"r{i}", f"Rule {i}", AlignSeverity.LOW)
        trail = ledger.get_audit_trail(limit=10)
        assert len(trail) == 10


class TestLedgerReport:
    def test_report(self):
        ledger = AlignLedger()
        ledger.add_rule("a", "A", AlignSeverity.CRITICAL)
        ledger.add_rule("b", "B", AlignSeverity.LOW)
        report = ledger.get_report()
        assert report["total_rules"] == 2
        assert report["active_rules"] == 2
        assert "critical" in report["by_severity"]


class TestLedgerPersistence:
    def test_save_and_load(self, tmp_path):
        ledger = AlignLedger()
        ledger.add_rule(
            "no_exec", "Block exec", AlignSeverity.CRITICAL,
            pattern="exec(",
        )
        path = tmp_path / "ledger.json"
        ledger.save(path)
        loaded = AlignLedger.load(path)
        assert loaded.rule_count == 1
        rules = loaded.list_rules()
        assert rules[0].name == "no_exec"
        assert rules[0].pattern == "exec("
