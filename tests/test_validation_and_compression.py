"""
Tests for ValidationToken and Context Compression.

ValidationToken covers:
  - HMAC signing and verification
  - Tamper detection
  - is_valid() gate logic
  - Serialization round-trip

Context Compression covers:
  - prune_to_signature() reduction
  - get_context_bundle() with token budget
  - Priority-based pruning (full → signature → name)
  - Budget enforcement
"""

from __future__ import annotations

import time

from agents.core.crew_orchestrator import ValidationToken
from hlf.infinite_rag import InfiniteRAGEngine
from hlf.memory_node import HLFMemoryNode, _compute_hash

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_engine() -> InfiniteRAGEngine:
    engine = InfiniteRAGEngine(db_path=":memory:")
    engine.init_schema()
    engine.init_dependency_graph()
    return engine


def _store(engine: InfiniteRAGEngine, entity: str, source: str, conf: float = 0.8) -> str:
    ast = {"type": "test", "entity": entity}
    now = time.time()
    node = HLFMemoryNode(
        entity_id=entity,
        hlf_source=source,
        hlf_ast=ast,
        content_hash=_compute_hash(ast),
        confidence=conf,
        provenance_agent="test",
        provenance_ts=now,
        last_accessed=now,
        created_at=now,
    )
    return engine.store(node)


# --------------------------------------------------------------------------- #
# ValidationToken
# --------------------------------------------------------------------------- #


class TestValidationToken:
    """HMAC-signed validation token for MERGE gating."""

    def test_sign_and_verify(self) -> None:
        """Token can be signed and verified."""
        token = ValidationToken(
            session_id="abc123",
            spec_hash="deadbeef",
            tests_passed=True,
            lint_clean=True,
            cove_approved=True,
        )
        token.sign()
        assert token.signature != ""
        assert token.verify() is True

    def test_unsigned_token_fails_verify(self) -> None:
        """Token without signature fails verification."""
        token = ValidationToken(
            session_id="abc",
            spec_hash="beef",
            tests_passed=True,
            lint_clean=True,
            cove_approved=True,
        )
        assert token.verify() is False

    def test_tampered_token_fails_verify(self) -> None:
        """Modifying a signed token invalidates the signature."""
        token = ValidationToken(
            session_id="abc",
            spec_hash="original_hash",
            tests_passed=True,
            lint_clean=True,
            cove_approved=True,
        )
        token.sign()
        assert token.verify() is True

        # Tamper
        token.spec_hash = "tampered_hash"
        assert token.verify() is False

    def test_is_valid_all_checks_pass(self) -> None:
        """is_valid() returns True only when all checks pass and signature is valid."""
        token = ValidationToken(
            session_id="abc",
            spec_hash="beef",
            tests_passed=True,
            lint_clean=True,
            cove_approved=True,
        )
        token.sign()
        assert token.is_valid() is True

    def test_is_valid_fails_without_tests(self) -> None:
        """is_valid() fails if tests didn't pass."""
        token = ValidationToken(
            session_id="abc",
            spec_hash="beef",
            tests_passed=False,
            lint_clean=True,
            cove_approved=True,
        )
        token.sign()
        assert token.is_valid() is False

    def test_is_valid_fails_without_lint(self) -> None:
        """is_valid() fails if lint is not clean."""
        token = ValidationToken(
            session_id="abc",
            spec_hash="beef",
            tests_passed=True,
            lint_clean=False,
            cove_approved=True,
        )
        token.sign()
        assert token.is_valid() is False

    def test_is_valid_fails_without_cove(self) -> None:
        """is_valid() fails if CoVE didn't approve."""
        token = ValidationToken(
            session_id="abc",
            spec_hash="beef",
            tests_passed=True,
            lint_clean=True,
            cove_approved=False,
        )
        token.sign()
        assert token.is_valid() is False

    def test_serialization_roundtrip(self) -> None:
        """to_dict → from_dict preserves all fields."""
        token = ValidationToken(
            session_id="abc",
            spec_hash="beef",
            tests_passed=True,
            lint_clean=True,
            cove_approved=True,
        )
        token.sign()

        data = token.to_dict()
        restored = ValidationToken.from_dict(data)
        assert restored.verify() is True
        assert restored.is_valid() is True
        assert restored.session_id == "abc"


# --------------------------------------------------------------------------- #
# Context Compression
# --------------------------------------------------------------------------- #


class TestPruneToSignature:
    """prune_to_signature() node reduction."""

    def test_reduces_to_first_line(self) -> None:
        """Signature is the first line of the source."""
        engine = _make_engine()
        ast = {"type": "test"}
        now = time.time()
        node = HLFMemoryNode(
            entity_id="module_a",
            hlf_source="def process_data(input: list) -> dict:\n    # complex logic\n    return result",
            hlf_ast=ast,
            content_hash=_compute_hash(ast),
            confidence=0.9,
            provenance_agent="test",
            provenance_ts=now,
            last_accessed=now,
            created_at=now,
        )
        sig = engine.prune_to_signature(node)
        assert sig["entity_id"] == "module_a"
        assert sig["signature"] == "def process_data(input: list) -> dict:"
        assert sig["confidence"] == 0.9
        assert sig["token_estimate"] < 20


class TestGetContextBundle:
    """get_context_bundle() token-budgeted retrieval."""

    def test_focus_entity_gets_full_source(self) -> None:
        """Focus entities (depth 0) get full source."""
        engine = _make_engine()
        _store(engine, "auth", "class AuthModule:\n    def login(self):\n        pass")

        bundle = engine.get_context_bundle(["auth"], budget_tokens=1000)
        assert len(bundle["full"]) == 1
        assert bundle["full"][0]["entity_id"] == "auth"
        assert "class AuthModule" in bundle["full"][0]["hlf_source"]

    def test_distant_entities_get_signature(self) -> None:
        """Entities beyond full_depth get signature only."""
        engine = _make_engine()
        _store(engine, "core", "class Core:\n    def run(self):\n        self.engine.start()")
        _store(engine, "mid", "class Middleware:\n    def process(self):\n        pass")
        _store(engine, "leaf", "class LeafHandler:\n    def handle(self):\n        pass")

        engine.link_entities("mid", "core", "depends_on")
        engine.link_entities("leaf", "mid", "depends_on")

        bundle = engine.get_context_bundle(
            ["core"],
            budget_tokens=5000,
            full_depth=0,     # only core gets full
            signature_depth=1,  # mid gets signature
        )

        # Core should be full
        full_entities = {n["entity_id"] for n in bundle["full"]}
        assert "core" in full_entities

        # Mid should be signature (depth 1, beyond full_depth=0)
        sig_entities = {n["entity_id"] for n in bundle["signatures"]}
        assert "mid" in sig_entities

    def test_budget_enforcement(self) -> None:
        """Token budget limits how much content is included."""
        engine = _make_engine()
        # Store a large source
        long_source = "line of code\n" * 100
        _store(engine, "big_module", long_source)

        bundle = engine.get_context_bundle(["big_module"], budget_tokens=10)
        # Should have limited content
        assert bundle["token_estimate"] <= bundle["budget_tokens"] + 50  # some tolerance

    def test_empty_entities(self) -> None:
        """No stored nodes returns empty bundle."""
        engine = _make_engine()
        bundle = engine.get_context_bundle(["phantom"])
        assert len(bundle["full"]) == 0
        assert len(bundle["signatures"]) == 0

    def test_entities_covered_count(self) -> None:
        """Bundle reports how many entities were visited."""
        engine = _make_engine()
        _store(engine, "a", "module a")
        _store(engine, "b", "module b")
        engine.link_entities("b", "a", "depends_on")

        bundle = engine.get_context_bundle(["a"], budget_tokens=5000)
        assert bundle["entities_covered"] >= 2
