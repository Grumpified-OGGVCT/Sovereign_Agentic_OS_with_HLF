"""
Tests for Blast Radius — Precision Retrieval via Dependency Graph.

Covers:
  - Entity dependency linking
  - Directed graph queries (outgoing, incoming, both)
  - Blast radius BFS traversal
  - Depth limiting
  - Diamond dependency patterns
  - Confidence filtering
  - Empty graph edge cases
"""

from __future__ import annotations

import time

from hlf.infinite_rag import InfiniteRAGEngine
from hlf.memory_node import HLFMemoryNode, _compute_hash

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_engine() -> InfiniteRAGEngine:
    """Create an in-memory engine with both schemas."""
    engine = InfiniteRAGEngine(db_path=":memory:")
    engine.init_schema()
    engine.init_dependency_graph()
    return engine


def _store_node(engine: InfiniteRAGEngine, entity: str, source: str, conf: float = 0.8) -> str:
    """Store a simple memory node directly (bypasses HLF compiler)."""
    ast = {"type": "test_node", "entity": entity, "content": source}
    content_hash = _compute_hash(ast)
    now = time.time()
    node = HLFMemoryNode(
        entity_id=entity,
        hlf_source=source,
        hlf_ast=ast,
        content_hash=content_hash,
        confidence=conf,
        provenance_agent="test_agent",
        provenance_ts=now,
        last_accessed=now,
        created_at=now,
    )
    return engine.store(node)


# --------------------------------------------------------------------------- #
# Dependency Graph
# --------------------------------------------------------------------------- #


class TestDependencyGraph:
    """Entity dependency linking and querying."""

    def test_link_entities(self) -> None:
        """link_entities creates a dependency record."""
        engine = _make_engine()
        engine.link_entities("auth_module", "user_model", "imports")
        links = engine.get_linked("auth_module", direction="outgoing")
        assert len(links) == 1
        assert links[0]["entity_id"] == "user_model"
        assert links[0]["relationship"] == "imports"

    def test_get_linked_incoming(self) -> None:
        """Incoming direction shows who depends on this entity."""
        engine = _make_engine()
        engine.link_entities("api_handler", "database", "depends_on")
        engine.link_entities("cache_layer", "database", "depends_on")

        incoming = engine.get_linked("database", direction="incoming")
        assert len(incoming) == 2
        entities = {link["entity_id"] for link in incoming}
        assert entities == {"api_handler", "cache_layer"}

    def test_get_linked_both(self) -> None:
        """Both direction returns outgoing + incoming."""
        engine = _make_engine()
        engine.link_entities("service_a", "service_b", "calls")
        engine.link_entities("service_c", "service_a", "imports")

        links = engine.get_linked("service_a", direction="both")
        assert len(links) == 2

    def test_get_linked_empty(self) -> None:
        """No links returns empty list."""
        engine = _make_engine()
        assert engine.get_linked("orphan") == []

    def test_link_with_weight(self) -> None:
        """Weight is captured correctly."""
        engine = _make_engine()
        engine.link_entities("a", "b", weight=0.7)
        links = engine.get_linked("a", direction="outgoing")
        assert links[0]["weight"] == 0.7


# --------------------------------------------------------------------------- #
# Blast Radius Query
# --------------------------------------------------------------------------- #


class TestBlastRadiusQuery:
    """blast_radius_query() returns affected memory nodes."""

    def test_direct_dependency_included(self) -> None:
        """Nodes of direct dependents are in the blast radius."""
        engine = _make_engine()
        _store_node(engine, "base_lib", 'DEFINE base_lib AS "core library"')
        _store_node(engine, "consumer", 'DEFINE consumer AS "uses base_lib"')

        engine.link_entities("consumer", "base_lib", "depends_on")

        # Change to base_lib should affect consumer
        affected = engine.blast_radius_query("base_lib")
        entity_ids = {n.entity_id for n in affected}
        assert "base_lib" in entity_ids
        assert "consumer" in entity_ids

    def test_transitive_dependency(self) -> None:
        """Blast radius traverses transitive deps: A→B→C, change C → affects all."""
        engine = _make_engine()
        _store_node(engine, "C", 'DEFINE C AS "foundation"')
        _store_node(engine, "B", 'DEFINE B AS "middle layer"')
        _store_node(engine, "A", 'DEFINE A AS "top layer"')

        engine.link_entities("A", "B", "depends_on")
        engine.link_entities("B", "C", "depends_on")

        affected = engine.blast_radius_query("C")
        entity_ids = {n.entity_id for n in affected}
        assert entity_ids == {"A", "B", "C"}

    def test_depth_limiting(self) -> None:
        """max_depth=1 limits traversal to direct dependents only."""
        engine = _make_engine()
        _store_node(engine, "core", 'DEFINE core AS "core"')
        _store_node(engine, "mid", 'DEFINE mid AS "mid"')
        _store_node(engine, "leaf", 'DEFINE leaf AS "leaf"')

        engine.link_entities("leaf", "mid", "depends_on")
        engine.link_entities("mid", "core", "depends_on")

        # Depth 1: core change only reaches mid, not leaf
        affected = engine.blast_radius_query("core", max_depth=1)
        entity_ids = {n.entity_id for n in affected}
        assert "core" in entity_ids
        assert "mid" in entity_ids
        assert "leaf" not in entity_ids

    def test_diamond_dependency(self) -> None:
        """Diamond: D depends on B and C, B and C depend on A. Change A → all affected."""
        engine = _make_engine()
        for name in ["A", "B", "C", "D"]:
            _store_node(engine, name, f'DEFINE {name} AS "node {name}"')

        engine.link_entities("B", "A", "depends_on")
        engine.link_entities("C", "A", "depends_on")
        engine.link_entities("D", "B", "depends_on")
        engine.link_entities("D", "C", "depends_on")

        affected = engine.blast_radius_query("A")
        entity_ids = {n.entity_id for n in affected}
        assert entity_ids == {"A", "B", "C", "D"}

    def test_unlinked_entity_not_affected(self) -> None:
        """Entities with no dependency relationship are excluded."""
        engine = _make_engine()
        _store_node(engine, "changed", 'DEFINE changed AS "modified"')
        _store_node(engine, "unrelated", 'DEFINE unrelated AS "separate"')

        affected = engine.blast_radius_query("changed")
        entity_ids = {n.entity_id for n in affected}
        assert "changed" in entity_ids
        assert "unrelated" not in entity_ids

    def test_confidence_filtering(self) -> None:
        """min_confidence filters low-confidence nodes from results."""
        engine = _make_engine()
        _store_node(engine, "lib", 'DEFINE lib AS "library"', conf=0.9)
        _store_node(engine, "weak_dep", 'DEFINE weak AS "weak"', conf=0.1)

        engine.link_entities("weak_dep", "lib", "depends_on")

        affected = engine.blast_radius_query("lib", min_confidence=0.5)
        entity_ids = {n.entity_id for n in affected}
        assert "lib" in entity_ids
        # weak_dep has conf=0.1, below threshold
        assert "weak_dep" not in entity_ids

    def test_empty_graph_returns_changed_only(self) -> None:
        """No dependencies → only the changed entity's nodes returned."""
        engine = _make_engine()
        _store_node(engine, "solo", 'DEFINE solo AS "standalone"')

        affected = engine.blast_radius_query("solo")
        assert len(affected) == 1
        assert affected[0].entity_id == "solo"

    def test_no_nodes_for_entity(self) -> None:
        """Blast radius with no stored nodes returns empty list."""
        engine = _make_engine()
        affected = engine.blast_radius_query("phantom")
        assert affected == []
