"""Tests for Silver Hat additions to agents.core.context_pruner."""

from __future__ import annotations

import time

import pytest

from agents.core.context_pruner import ContextEntry, ContextPruner


# ---------------------------------------------------------------------------
# ContextEntry
# ---------------------------------------------------------------------------

class TestContextEntryTokenEstimate:
    def test_empty_content_returns_one(self) -> None:
        entry = ContextEntry(content="")
        assert entry.token_estimate == 1

    def test_short_content(self) -> None:
        # 8 chars → 8 // 4 = 2 tokens
        entry = ContextEntry(content="abcdefgh")
        assert entry.token_estimate == 2

    def test_long_content(self) -> None:
        content = "x" * 400  # 400 chars → 100 tokens
        entry = ContextEntry(content=content)
        assert entry.token_estimate == 100


# ---------------------------------------------------------------------------
# ContextPruner — new Silver Hat features
# ---------------------------------------------------------------------------

class TestTokenTotal:
    def test_empty_pruner(self) -> None:
        pruner = ContextPruner()
        assert pruner.token_total == 0

    def test_sum_matches_entries(self) -> None:
        pruner = ContextPruner()
        pruner.add("a" * 40)   # 10 tokens
        pruner.add("b" * 80)   # 20 tokens
        assert pruner.token_total == 30


class TestPruneToBudget:
    def test_no_eviction_when_within_budget(self) -> None:
        pruner = ContextPruner()
        pruner.add("x" * 40)   # 10 tokens
        stats = pruner.prune_to_budget(100)
        assert stats["pruned"] == 0
        assert stats["tokens_saved"] == 0
        assert pruner.size == 1

    def test_evicts_lowest_relevance_first(self) -> None:
        pruner = ContextPruner()
        e_high = pruner.add("x" * 400)   # 100 tokens, relevance=1.0
        e_low = pruner.add("y" * 400)    # 100 tokens
        e_low.relevance = 0.2

        # Budget = 110 → need to evict ~90 tokens; low-relevance entry goes first
        stats = pruner.prune_to_budget(110)
        assert stats["pruned"] == 1
        assert e_low.entry_id not in pruner._entries
        assert e_high.entry_id in pruner._entries

    def test_evicts_multiple_until_budget_met(self) -> None:
        pruner = ContextPruner()
        for i in range(5):
            e = pruner.add("z" * 400)    # 100 tokens each
            e.relevance = 0.1 * (i + 1)

        # Total = 500 tokens; budget = 200 → must evict 3
        stats = pruner.prune_to_budget(200)
        assert stats["pruned"] == 3
        assert pruner.token_total <= 200

    def test_gas_incremented_per_eviction(self) -> None:
        pruner = ContextPruner()
        for _ in range(3):
            e = pruner.add("w" * 400)
            e.relevance = 0.1
        pruner.prune_to_budget(50)
        assert pruner.gas_used > 0


class TestTopKByRelevance:
    def test_k_zero_returns_empty(self) -> None:
        pruner = ContextPruner()
        pruner.add("fact")
        assert pruner.top_k_by_relevance(0) == []

    def test_returns_top_k_sorted_descending(self) -> None:
        pruner = ContextPruner()
        entries = []
        for i in range(5):
            e = pruner.add(f"fact {i}")
            e.relevance = 0.1 * (i + 1)
            entries.append(e)

        top2 = pruner.top_k_by_relevance(2)
        assert len(top2) == 2
        # Highest relevance first
        assert top2[0].relevance >= top2[1].relevance
        assert top2[0].relevance == pytest.approx(0.5)

    def test_k_larger_than_size_returns_all(self) -> None:
        pruner = ContextPruner()
        pruner.add("a")
        pruner.add("b")
        result = pruner.top_k_by_relevance(100)
        assert len(result) == 2


class TestGasTracking:
    def test_gas_starts_at_zero(self) -> None:
        pruner = ContextPruner()
        assert pruner.gas_used == 0

    def test_prune_pass_increments_gas(self) -> None:
        pruner = ContextPruner(relevance_floor=0.5)
        e = pruner.add("stale")
        e.relevance = 0.1
        pruner.prune_pass()
        assert pruner.gas_used == 1

    def test_prune_to_budget_increments_gas(self) -> None:
        pruner = ContextPruner()
        e = pruner.add("v" * 400)
        e.relevance = 0.1
        before = pruner.gas_used
        pruner.prune_to_budget(10)
        assert pruner.gas_used > before


class TestGetStatsEnriched:
    def test_stats_includes_token_total_and_gas(self) -> None:
        pruner = ContextPruner(relevance_floor=0.5)
        pruner.add("content with some tokens here")
        e_low = pruner.add("low relevance entry")
        e_low.relevance = 0.1
        pruner.prune_pass()

        stats = pruner.get_stats()
        assert "token_total" in stats
        assert "gas_used" in stats
        assert stats["gas_used"] == 1
        assert stats["token_total"] >= 0

    def test_stats_active_count(self) -> None:
        pruner = ContextPruner()
        pruner.add("a")
        pruner.add("b")
        stats = pruner.get_stats()
        assert stats["active"] == 2


# ---------------------------------------------------------------------------
# Regression — existing behaviour preserved
# ---------------------------------------------------------------------------

class TestExistingBehaviourUnchanged:
    def test_add_and_access(self) -> None:
        pruner = ContextPruner()
        entry = pruner.add("fact about OS")
        assert pruner.size == 1
        assert pruner.track_access(entry.entry_id)

    def test_prune_idle(self) -> None:
        pruner = ContextPruner(max_idle_days=0.0001)
        entry = pruner.add("old fact")
        entry.last_accessed = time.time() - 86400
        stats = pruner.prune_pass()
        assert stats["pruned_idle"] == 1
        assert pruner.size == 0
        assert pruner.archive_size == 1

    def test_prune_low_relevance(self) -> None:
        pruner = ContextPruner(relevance_floor=0.5)
        entry = pruner.add("low rel fact")
        entry.relevance = 0.1
        stats = pruner.prune_pass()
        assert stats["pruned_low_relevance"] == 1

    def test_decay(self) -> None:
        pruner = ContextPruner(decay_half_life=0.01)
        entry = pruner.add("fact")
        entry.last_accessed = time.time() - 86400
        pruner.decay_pass()
        assert entry.relevance < 1.0

    def test_stats_keys(self) -> None:
        pruner = ContextPruner()
        pruner.add("a")
        pruner.add("b")
        stats = pruner.get_stats()
        assert stats["active"] == 2
        assert "avg_relevance" in stats
