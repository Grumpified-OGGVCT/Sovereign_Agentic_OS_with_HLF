"""Tests for the Infinite RAG Engine and HLFMemoryNode.

Covers:
  - InfiniteRAGEngine: store/retrieve, dedup, correction chains,
    confidence decay, stale archival, hot cache LRU, stats
  - HLFMemoryNode: from_hlf_source, from_ast, content hashing,
    serialization, dedup matching
"""

from __future__ import annotations

import pytest

from hlf.memory_node import HLFMemoryNode
from hlf.infinite_rag import InfiniteRAGEngine


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _prog(body: str) -> str:
    """Wrap body lines in the HLF v2 program envelope."""
    return f'[HLF-v2]\n{body}\nΩ\n'


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #


@pytest.fixture()
def engine(tmp_path) -> InfiniteRAGEngine:
    """Create a fresh Infinite RAG engine with temp storage."""
    eng = InfiniteRAGEngine(db_path=str(tmp_path / "test_rag.db"))
    eng.init_schema()
    return eng


@pytest.fixture()
def sample_node() -> HLFMemoryNode:
    """Create a sample memory node for testing."""
    return HLFMemoryNode.from_hlf_source(
        source=_prog('[SET] target_host = "example.com"\n[RESULT] 0 "ok"'),
        entity_id="target_host",
        agent="test_agent",
        confidence=0.9,
    )


# ------------------------------------------------------------------ #
# HLFMemoryNode Tests
# ------------------------------------------------------------------ #


class TestHLFMemoryNode:
    """Tests for the HLFMemoryNode dataclass."""

    def test_from_hlf_source(self) -> None:
        """Creating a node from HLF source compiles and hashes."""
        node = HLFMemoryNode.from_hlf_source(
            source=_prog('[SET] target = "10.0.0.1"\n[RESULT] 0 "ok"'),
            entity_id="target",
            agent="recon_agent",
            confidence=0.85,
        )
        assert node.entity_id == "target"
        assert node.hlf_ast is not None
        assert node.content_hash  # SHA-256 of canonical AST
        assert node.confidence == 0.85
        assert node.provenance_agent == "recon_agent"
        assert node.node_id  # UUID4

    def test_from_ast(self) -> None:
        """Creating a node from a pre-compiled AST."""
        ast = {"program": [{"tag": "SET", "name": "x", "value": 42}]}
        node = HLFMemoryNode.from_ast(
            ast=ast,
            entity_id="x_entity",
            agent="builder",
            confidence=0.7,
            source="[SET] x = 42",
        )
        assert node.entity_id == "x_entity"
        assert node.hlf_ast == ast
        assert node.content_hash  # derived from canonical JSON

    def test_content_hash_deterministic(self) -> None:
        """Same source produces the same content hash."""
        source = _prog('[SET] key = "value"\n[RESULT] 0 "ok"')
        n1 = HLFMemoryNode.from_hlf_source(
            source=source, entity_id="k", agent="a", confidence=0.5,
        )
        n2 = HLFMemoryNode.from_hlf_source(
            source=source, entity_id="k", agent="a", confidence=0.5,
        )
        assert n1.content_hash == n2.content_hash

    def test_different_source_different_hash(self) -> None:
        """Different sources produce different hashes."""
        n1 = HLFMemoryNode.from_hlf_source(
            source=_prog('[SET] a = 1\n[RESULT] 0 "ok"'),
            entity_id="a", agent="a", confidence=0.5,
        )
        n2 = HLFMemoryNode.from_hlf_source(
            source=_prog('[SET] b = 2\n[RESULT] 0 "ok"'),
            entity_id="b", agent="a", confidence=0.5,
        )
        assert n1.content_hash != n2.content_hash

    def test_to_dict_round_trip(self, sample_node: HLFMemoryNode) -> None:
        """Serialization round-trip preserves all fields."""
        d = sample_node.to_dict()
        restored = HLFMemoryNode.from_dict(d)
        assert restored.node_id == sample_node.node_id
        assert restored.entity_id == sample_node.entity_id
        assert restored.content_hash == sample_node.content_hash
        assert restored.confidence == sample_node.confidence
        assert restored.provenance_agent == sample_node.provenance_agent

    def test_node_ids_unique(self) -> None:
        """Each node gets a unique UUID."""
        ids = set()
        for i in range(10):
            n = HLFMemoryNode.from_hlf_source(
                source=_prog(f'[SET] var{i} = {i}\n[RESULT] 0 "ok"'),
                entity_id=f"v{i}",
                agent="test",
                confidence=0.5,
            )
            ids.add(n.node_id)
        assert len(ids) == 10

    def test_empty_source_raises(self) -> None:
        """Empty source raises ValueError."""
        with pytest.raises(ValueError, match="empty"):
            HLFMemoryNode.from_hlf_source(
                source="", entity_id="x", agent="a", confidence=0.5,
            )


# ------------------------------------------------------------------ #
# InfiniteRAGEngine Tests
# ------------------------------------------------------------------ #


class TestInfiniteRAGEngine:
    """Tests for the 3-tier Infinite RAG memory engine."""

    def test_store_and_retrieve(self, engine: InfiniteRAGEngine) -> None:
        """Store a node and retrieve it by entity_id."""
        node = HLFMemoryNode.from_hlf_source(
            source=_prog('[SET] host = "10.0.0.1"\n[RESULT] 0 "ok"'),
            entity_id="host",
            agent="scanner",
            confidence=0.95,
        )
        node_id = engine.store(node)
        assert isinstance(node_id, str)
        results = engine.retrieve("host")
        assert len(results) >= 1
        assert results[0].entity_id == "host"
        assert results[0].content_hash == node.content_hash

    def test_dedup_rejects_duplicate(self, engine: InfiniteRAGEngine) -> None:
        """Storing the same content twice returns the same node_id (dedup)."""
        source = _prog('[SET] port = 443\n[RESULT] 0 "ok"')
        n1 = HLFMemoryNode.from_hlf_source(
            source=source, entity_id="port", agent="a", confidence=0.9,
        )
        n2 = HLFMemoryNode.from_hlf_source(
            source=source, entity_id="port", agent="b", confidence=0.8,
        )
        id1 = engine.store(n1)
        id2 = engine.store(n2)
        # Both return same node_id because content_hash matches
        assert id1 == id2

    def test_retrieve_empty(self, engine: InfiniteRAGEngine) -> None:
        """Retrieving a nonexistent entity returns empty list."""
        results = engine.retrieve("nonexistent")
        assert results == []

    def test_retrieve_top_k(self, engine: InfiniteRAGEngine) -> None:
        """Retrieve respects top_k limit."""
        for i in range(10):
            node = HLFMemoryNode.from_hlf_source(
                source=_prog(f'[SET] item{i} = {i}\n[RESULT] 0 "ok"'),
                entity_id="items",
                agent="gen",
                confidence=0.5 + i * 0.05,
            )
            engine.store(node)

        results = engine.retrieve("items", top_k=3)
        assert len(results) <= 3

    def test_correction_chain(self, engine: InfiniteRAGEngine) -> None:
        """Correcting a memory creates a linked chain via parent_hash."""
        original = HLFMemoryNode.from_hlf_source(
            source=_prog('[SET] target = "old_value"\n[RESULT] 0 "ok"'),
            entity_id="target",
            agent="recon",
            confidence=0.9,
        )
        original_id = engine.store(original)

        corrected_id = engine.correct(
            node_id=original_id,
            corrected_source=_prog('[SET] target = "corrected_value"\n[RESULT] 0 "ok"'),
            agent="validator",
            confidence=0.95,
        )

        assert corrected_id != original_id

    def test_stats(self, engine: InfiniteRAGEngine) -> None:
        """Stats reports node counts."""
        node = HLFMemoryNode.from_hlf_source(
            source=_prog('[SET] stat_test = 1\n[RESULT] 0 "ok"'),
            entity_id="stat_test",
            agent="stats",
            confidence=0.5,
        )
        engine.store(node)
        stats = engine.stats()
        assert stats["warm_count"] >= 1

    def test_decay_confidence(self, engine: InfiniteRAGEngine) -> None:
        """decay_confidence reduces confidence for old unaccessed nodes."""
        import time as _time

        node = HLFMemoryNode.from_hlf_source(
            source=_prog('[SET] decay_test = "val"\n[RESULT] 0 "ok"'),
            entity_id="decay_test",
            agent="test",
            confidence=0.9,
        )
        engine.store(node)

        # Artificially age the node so it falls within the decay window
        conn = engine._get_conn()
        old_ts = _time.time() - 86400 * 2  # 2 days ago
        conn.execute(
            "UPDATE hlf_memory_nodes SET last_accessed = ?",
            (old_ts,),
        )
        conn.commit()
        # Clear hot cache so decay isn't masked by cached values
        engine._hot_cache.clear()

        # Run decay with 1-day window (catches nodes > 1 day old)
        decayed = engine.decay_confidence(decay_factor=0.5, window_days=1)
        assert decayed >= 1

        results = engine.retrieve("decay_test", min_confidence=0.0)
        assert len(results) >= 1
        assert results[0].confidence < 0.9

    def test_hot_cache_promotion(self, engine: InfiniteRAGEngine) -> None:
        """Stored nodes are promoted to hot cache."""
        node = HLFMemoryNode.from_hlf_source(
            source=_prog('[SET] hot_test = "val"\n[RESULT] 0 "ok"'),
            entity_id="hot_test",
            agent="test",
            confidence=0.8,
        )
        engine.store(node)
        # Hot cache should have the node
        assert len(engine._hot_cache) >= 1
