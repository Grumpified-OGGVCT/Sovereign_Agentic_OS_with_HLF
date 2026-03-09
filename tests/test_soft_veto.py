"""Tests for Soft Veto Gate."""

from __future__ import annotations

import time

import pytest

from governance.soft_veto import (
    RuleMatch,
    SoftVetoGate,
    VetoDecision,
    VetoLevel,
)


class TestVetoDecision:
    def test_properties(self):
        d = VetoDecision(level=VetoLevel.SOFT_VETO, confidence=0.5)
        assert d.is_soft_veto
        assert not d.is_pass
        assert not d.is_hard_block

    def test_to_dict(self):
        d = VetoDecision(level=VetoLevel.PASS, confidence=0.1)
        data = d.to_dict()
        assert data["level"] == "pass"


class TestSoftVetoGate:
    def setup_method(self):
        self.gate = SoftVetoGate(
            low_threshold=0.4,
            high_threshold=0.8,
        )

    def test_invalid_thresholds(self):
        with pytest.raises(ValueError):
            SoftVetoGate(low_threshold=0.9, high_threshold=0.3)

    def test_no_matches_passes(self):
        decision = self.gate.evaluate("hello world", [])
        assert decision.is_pass

    def test_low_confidence_passes(self):
        matches = [
            RuleMatch("r1", "test", match_confidence=0.1),
        ]
        decision = self.gate.evaluate("safe input", matches)
        assert decision.is_pass

    def test_medium_confidence_soft_veto(self):
        matches = [
            RuleMatch("r1", "test", match_confidence=0.6),
        ]
        decision = self.gate.evaluate("borderline input", matches)
        assert decision.is_soft_veto
        assert self.gate.pending_count == 1

    def test_high_confidence_hard_block(self):
        matches = [
            RuleMatch("r1", "test", match_confidence=0.95),
        ]
        decision = self.gate.evaluate("dangerous input", matches)
        assert decision.is_hard_block

    def test_multiple_matches_aggregation(self):
        matches = [
            RuleMatch("r1", "rule1", match_confidence=0.5),
            RuleMatch("r2", "rule2", match_confidence=0.6),
            RuleMatch("r3", "rule3", match_confidence=0.55),
        ]
        decision = self.gate.evaluate("mixed input", matches)
        # Combined = 0.7 * max(0.6) + 0.3 * avg(0.55) = 0.42 + 0.165 = 0.585
        assert decision.is_soft_veto

    def test_escalate(self):
        matches = [RuleMatch("r1", "test", match_confidence=0.6)]
        decision = self.gate.evaluate("x", matches)
        self.gate.escalate(decision, queue="ops-review")
        assert decision.escalated
        assert decision.escalation_queue == "ops-review"

    def test_escalate_non_veto_raises(self):
        decision = VetoDecision(level=VetoLevel.PASS)
        with pytest.raises(ValueError, match="soft-veto"):
            self.gate.escalate(decision)

    def test_resolve_approved(self):
        matches = [RuleMatch("r1", "test", match_confidence=0.6)]
        decision = self.gate.evaluate("x", matches)
        assert self.gate.pending_count == 1
        resolved = self.gate.resolve(decision.decision_id, "approved")
        assert resolved is not None
        assert resolved.resolution == "approved"
        assert self.gate.pending_count == 0

    def test_resolve_denied(self):
        matches = [RuleMatch("r1", "test", match_confidence=0.6)]
        decision = self.gate.evaluate("x", matches)
        resolved = self.gate.resolve(decision.decision_id, "denied")
        assert resolved.resolution == "denied"

    def test_resolve_invalid_raises(self):
        with pytest.raises(ValueError, match="approved"):
            self.gate.resolve("fake", "maybe")

    def test_resolve_unknown_returns_none(self):
        assert self.gate.resolve("nonexistent", "approved") is None

    def test_expire_stale(self):
        gate = SoftVetoGate(auto_expire_minutes=0.001)  # ~60ms
        matches = [RuleMatch("r1", "test", match_confidence=0.6)]
        gate.evaluate("x", matches)
        time.sleep(0.1)
        expired = gate.expire_stale()
        assert expired == 1
        assert gate.pending_count == 0

    def test_get_pending(self):
        matches = [RuleMatch("r1", "test", match_confidence=0.6)]
        self.gate.evaluate("a", matches)
        self.gate.evaluate("b", matches)
        pending = self.gate.get_pending()
        assert len(pending) == 2

    def test_get_history(self):
        self.gate.evaluate("safe", [])
        matches = [RuleMatch("r1", "test", match_confidence=0.9)]
        self.gate.evaluate("danger", matches)
        hist = self.gate.get_history()
        assert len(hist) == 2

    def test_get_history_filtered(self):
        self.gate.evaluate("safe", [])
        matches = [RuleMatch("r1", "test", match_confidence=0.9)]
        self.gate.evaluate("danger", matches)
        blocks = self.gate.get_history(level=VetoLevel.HARD_BLOCK)
        assert len(blocks) == 1

    def test_stats(self):
        self.gate.evaluate("safe", [])
        matches = [RuleMatch("r1", "test", match_confidence=0.6)]
        self.gate.evaluate("borderline", matches)
        stats = self.gate.get_stats()
        assert stats["total_decisions"] == 2
        assert stats["by_level"]["pass"] == 1
        assert stats["by_level"]["soft_veto"] == 1

    def test_input_preview_truncated(self):
        long_input = "x" * 200
        decision = self.gate.evaluate(long_input, [])
        assert len(decision.input_preview) == 100
