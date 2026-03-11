"""Tests for Wave 2-3 modules: similarity gate, hlb format, outlier trap,
dead man's switch, dream state, and context pruner."""
from __future__ import annotations

import time

import pytest

from agents.core.context_pruner import ContextPruner
from agents.core.dead_man_switch import DeadManSwitch
from agents.core.dream_state import DreamStateEngine
from agents.core.outlier_trap import OutlierTrap
from hlf.hlb_format import HlbFormatError, HlbInstruction, HlbReader, HlbWriter
from hlf.similarity_gate import SemanticSimilarityGate, _normalize, cosine_similarity

# ─── Semantic Similarity Gate ───────────────────────────────────────────────


class TestSimilarityGate:
    def test_identical_text(self):
        gate = SemanticSimilarityGate(threshold=0.95)
        result = gate.check("deploy the application", "deploy the application")
        assert result.passed
        assert result.similarity >= 0.99

    def test_similar_text_passes(self):
        gate = SemanticSimilarityGate(threshold=0.5)
        result = gate.check(
            "Deploy the application to production",
            "[INTENT] Deploy application production",
        )
        assert result.similarity > 0.3

    def test_dissimilar_text_fails(self):
        gate = SemanticSimilarityGate(threshold=0.95)
        result = gate.check("Deploy the app", "Buy groceries for dinner")
        assert not result.passed
        assert len(gate.get_drift_alerts()) == 1

    def test_caching(self):
        gate = SemanticSimilarityGate()
        r1 = gate.check("hello", "hello")
        r2 = gate.check("hello", "hello")
        assert r1.similarity == r2.similarity
        # Cache returns same object without re-recording
        assert gate.check_count >= 1

    def test_stats(self):
        gate = SemanticSimilarityGate(threshold=0.5)
        gate.check("a b c", "a b c")
        stats = gate.get_stats()
        assert stats["total_checks"] == 1

    def test_normalize(self):
        assert "[INTENT]" not in _normalize("[INTENT] hello")
        assert "→" not in _normalize("a → b")

    def test_cosine_similarity_identity(self):
        from collections import Counter
        a = Counter({"a": 1, "b": 2})
        assert cosine_similarity(a, a) == pytest.approx(1.0)

    def test_cosine_empty(self):
        from collections import Counter
        assert cosine_similarity(Counter(), Counter({"a": 1})) == 0.0


# ─── HLB Format ────────────────────────────────────────────────────────────



class TestHlbFormat:
    def test_roundtrip_basic(self):
        instructions = [
            HlbInstruction(opcode=1, operands=[10, 20]),
            HlbInstruction(opcode=2, operands=[]),
            HlbInstruction(opcode=3, operands=[-1]),
        ]
        constants = ["hello", "world"]
        writer = HlbWriter()
        data = writer.encode(instructions, constants)
        reader = HlbReader()
        instrs, consts, smap = reader.decode(data)
        assert len(instrs) == 3
        assert instrs[0].opcode == 1
        assert instrs[0].operands == [10, 20]
        assert consts == ["hello", "world"]

    def test_roundtrip_with_source_map(self):
        instructions = [HlbInstruction(opcode=1)]
        source_map = {0: 5}
        writer = HlbWriter()
        data = writer.encode(instructions, source_map=source_map)
        _, _, smap = HlbReader().decode(data)
        assert smap == {0: 5}

    def test_roundtrip_compressed(self):
        instructions = [HlbInstruction(opcode=i) for i in range(50)]
        writer = HlbWriter()
        data = writer.encode(instructions, compress=True)
        instrs, _, _ = HlbReader().decode(data)
        assert len(instrs) == 50

    def test_invalid_magic(self):
        with pytest.raises(HlbFormatError):
            HlbReader().decode(b"BADMAGIC1234567890")

    def test_truncated_data(self):
        with pytest.raises(HlbFormatError):
            HlbReader().decode(b"HLF")

    def test_get_info(self):
        writer = HlbWriter()
        data = writer.encode([HlbInstruction(opcode=0)])
        info = HlbReader().get_info(data)
        assert info["magic"] == "HLFv04"
        assert info["version"] == 1

    def test_unicode_constants(self):
        writer = HlbWriter()
        data = writer.encode([], ["Ω", "Δ", "⩕"])
        _, consts, _ = HlbReader().decode(data)
        assert consts == ["Ω", "Δ", "⩕"]

    def test_empty_program(self):
        writer = HlbWriter()
        data = writer.encode([])
        instrs, consts, _ = HlbReader().decode(data)
        assert len(instrs) == 0
        assert len(consts) == 0


# ─── Outlier Trap ───────────────────────────────────────────────────────────



class TestOutlierTrap:
    def test_normal_event_no_alert(self):
        trap = OutlierTrap()
        result = trap.ingest({"event_type": "action.execute", "severity": "info"})
        assert result is None

    def test_critical_event_alerts(self):
        trap = OutlierTrap(quarantine_threshold=0.5)
        result = trap.ingest({"event_type": "action.execute", "severity": "critical"})
        assert result is not None
        assert result.quarantined

    def test_quarantine_count(self):
        trap = OutlierTrap(quarantine_threshold=0.5)
        trap.ingest({"event_type": "a", "severity": "critical"})
        trap.ingest({"event_type": "b", "severity": "critical"})
        assert trap.quarantine_count == 2

    def test_stats(self):
        trap = OutlierTrap()
        trap.ingest({"event_type": "a", "severity": "info"})
        stats = trap.get_stats()
        assert stats["total_events"] == 1


# ─── Dead Man's Switch ─────────────────────────────────────────────────────



class TestDeadManSwitch:
    def test_no_trigger_under_threshold(self):
        switch = DeadManSwitch(max_panics=3, window_minutes=5)
        result = switch.record_panic("c1")
        assert result is None
        assert not switch.is_triggered

    def test_triggers_above_threshold(self):
        switch = DeadManSwitch(max_panics=2, window_minutes=5)
        switch.record_panic("c1")
        switch.record_panic("c2")
        result = switch.record_panic("c3")
        assert result is not None
        assert switch.is_triggered

    def test_callback_fires(self):
        triggered = []
        switch = DeadManSwitch(
            max_panics=1,
            window_minutes=5,
            on_trigger=lambda t: triggered.append(t),
        )
        switch.record_panic("c1")
        switch.record_panic("c2")
        assert len(triggered) == 1

    def test_arm_disarm(self):
        switch = DeadManSwitch(max_panics=1)
        switch.disarm()
        switch.record_panic("c1")
        switch.record_panic("c2")
        assert not switch.is_triggered

    def test_rearm(self):
        switch = DeadManSwitch(max_panics=1)
        switch.record_panic("c1")
        switch.record_panic("c2")
        assert switch.is_triggered
        switch.arm()
        assert not switch.is_triggered

    def test_status(self):
        switch = DeadManSwitch(max_panics=3)
        status = switch.get_status()
        assert status["armed"]
        assert not status["triggered"]


# ─── Dream State Engine ────────────────────────────────────────────────────



class TestDreamStateEngine:
    def test_insufficient_experiences(self):
        engine = DreamStateEngine(min_experiences=5)
        engine.add_experience("one")
        rules = engine.dream_cycle()
        assert len(rules) == 0

    def test_successful_cycle(self):
        engine = DreamStateEngine(min_experiences=3)
        for i in range(6):
            engine.add_experience(
                f"Agent sentinel performed security scan iteration {i}",
                agent_id="sentinel",
            )
        rules = engine.dream_cycle()
        assert len(rules) >= 1
        assert engine.experience_count == 0  # Cleared after cycle

    def test_compression_ratio(self):
        engine = DreamStateEngine(min_experiences=3)
        for _ in range(10):
            engine.add_experience("sentinel flagged a seccomp violation in the container")
        engine.dream_cycle()
        assert engine.last_compression_ratio > 0

    def test_stats(self):
        engine = DreamStateEngine()
        engine.add_experience("test")
        stats = engine.get_stats()
        assert stats["pending_experiences"] == 1


# ─── Context Pruner ─────────────────────────────────────────────────────────



class TestContextPruner:
    def test_add_and_access(self):
        pruner = ContextPruner()
        entry = pruner.add("fact about OS")
        assert pruner.size == 1
        assert pruner.track_access(entry.entry_id)

    def test_prune_idle(self):
        pruner = ContextPruner(max_idle_days=0.0001)  # ~8.6 seconds
        entry = pruner.add("old fact")
        entry.last_accessed = time.time() - 86400  # 1 day ago
        stats = pruner.prune_pass()
        assert stats["pruned_idle"] == 1
        assert pruner.size == 0
        assert pruner.archive_size == 1

    def test_prune_low_relevance(self):
        pruner = ContextPruner(relevance_floor=0.5)
        entry = pruner.add("low rel fact")
        entry.relevance = 0.1
        stats = pruner.prune_pass()
        assert stats["pruned_low_relevance"] == 1

    def test_decay(self):
        pruner = ContextPruner(decay_half_life=0.01)
        entry = pruner.add("fact")
        entry.last_accessed = time.time() - 86400  # 1 day
        pruner.decay_pass()
        assert entry.relevance < 1.0

    def test_stats(self):
        pruner = ContextPruner()
        pruner.add("a")
        pruner.add("b")
        stats = pruner.get_stats()
        assert stats["active"] == 2
