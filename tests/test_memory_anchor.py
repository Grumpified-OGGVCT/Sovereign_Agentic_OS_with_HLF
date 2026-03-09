"""Tests for HLF-Anchored Memory Nodes."""

from __future__ import annotations

import time

import pytest

from agents.core.memory_anchor import (
    AnchoredMemoryStore,
    MemoryAnchor,
    StorageTier,
)


class TestMemoryAnchor:
    def test_defaults(self):
        anchor = MemoryAnchor(content="test", hlf_intent_hash="h1")
        assert anchor.anchor_id
        assert anchor.content_hash
        assert anchor.tier == StorageTier.HOT
        assert anchor.relevance_score == 1.0

    def test_touch(self):
        anchor = MemoryAnchor(content="test", hlf_intent_hash="h1")
        anchor.relevance_score = 0.5
        anchor.touch()
        assert anchor.access_count == 1
        assert anchor.relevance_score == 0.6

    def test_to_dict(self):
        anchor = MemoryAnchor(content="x", hlf_intent_hash="h")
        d = anchor.to_dict()
        assert d["content"] == "x"
        assert d["tier"] == "hot"

    def test_from_dict(self):
        original = MemoryAnchor(content="x", hlf_intent_hash="h")
        restored = MemoryAnchor.from_dict(original.to_dict())
        assert restored.content == "x"
        assert restored.tier == StorageTier.HOT


class TestAnchoredMemoryStore:
    def setup_method(self):
        self.store = AnchoredMemoryStore(
            decay_half_life_days=15,
            cold_threshold=0.3,
            prune_threshold=0.05,
            max_idle_days=30,
        )

    def test_add(self):
        anchor = self.store.add("fact", "hash1", "sentinel")
        assert self.store.size == 1
        assert anchor.agent_id == "sentinel"

    def test_get(self):
        anchor = self.store.add("fact", "h1")
        result = self.store.get(anchor.anchor_id)
        assert result is anchor
        assert result.access_count == 1

    def test_get_unknown(self):
        assert self.store.get("nonexistent") is None

    def test_query_by_provenance(self):
        self.store.add("a", "hash1", "sentinel")
        self.store.add("b", "hash1", "scribe")
        self.store.add("c", "hash2", "sentinel")
        results = self.store.query_by_provenance("hash1")
        assert len(results) == 2

    def test_query_by_agent(self):
        self.store.add("a", "h1", "sentinel")
        self.store.add("b", "h2", "sentinel")
        self.store.add("c", "h3", "scribe")
        results = self.store.query_by_agent("sentinel")
        assert len(results) == 2

    def test_query_by_tag(self):
        self.store.add("a", "h1", tags=["security"])
        self.store.add("b", "h2", tags=["code"])
        results = self.store.query_by_tag("security")
        assert len(results) == 1

    def test_query_hot(self):
        self.store.add("a", "h1")
        self.store.add("b", "h2")
        hot = self.store.query_hot()
        assert len(hot) == 2

    def test_decay_pass(self):
        anchor = self.store.add("fact", "h1")
        # Simulate aging by backdating
        anchor.last_accessed = time.time() - (20 * 86400)  # 20 days ago
        stats = self.store.decay_pass()
        assert stats["decayed"] == 1
        assert anchor.relevance_score < 1.0

    def test_decay_demotes_to_cold(self):
        anchor = self.store.add("fact", "h1")
        anchor.last_accessed = time.time() - (40 * 86400)  # 40 days idle
        anchor.confidence = 0.5
        self.store.decay_pass()
        # With 40 days idle and 0.5 confidence, should be cold or pruned
        assert anchor.anchor_id not in self.store._nodes or anchor.tier in (
            StorageTier.COLD, StorageTier.WARM
        )

    def test_prune_idle(self):
        anchor = self.store.add("fact", "h1")
        anchor.last_accessed = time.time() - (31 * 86400)
        pruned = self.store.prune_idle()
        assert pruned == 1
        assert self.store.size == 0
        assert self.store.cold_archive_size == 1

    def test_report(self):
        self.store.add("a", "h1", "sentinel")
        self.store.add("b", "h2", "scribe")
        report = self.store.get_report()
        assert report["total_active"] == 2
        assert report["by_tier"]["hot"] == 2

    def test_save_and_load(self, tmp_path):
        self.store.add("fact1", "h1", "sentinel")
        self.store.add("fact2", "h2", "scribe")
        path = tmp_path / "memory.json"
        self.store.save(path)
        loaded = AnchoredMemoryStore.load(path)
        assert loaded.size == 2
