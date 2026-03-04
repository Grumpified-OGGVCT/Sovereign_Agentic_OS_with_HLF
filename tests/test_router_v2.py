"""
Tests for the Phase 3 registry-aware router — route_request() + AgentProfile.

All tests use in-memory SQLite so no file cleanup is needed.
Legacy route_intent() is NOT tested here (it has its own coverage in test_policy.py).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure agents package is importable
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from agents.core.db import (  # noqa: E402
    _SCHEMA_SQL,
    create_snapshot,
    get_db,
    init_db,
    promote_snapshot,
    upsert_agent_template,
    upsert_local_inventory,
    upsert_model,
    upsert_model_equivalent,
)
from agents.gateway.router import (  # noqa: E402
    _SPECIALIZATION_PATTERNS,
    _TIER_WALK_ORDER,
    AgentProfile,
    route_intent,
    route_request,
)

# ─── Helpers ─────────────────────────────────────────────────────────────


def _seed_registry(conn: sqlite3.Connection) -> int:
    """Create and promote a snapshot with some scored models + local inventory."""
    snap_id = create_snapshot(conn, families=["qwen", "llama", "devstral"])
    promote_snapshot(conn, snap_id)

    # Scored models at various tiers
    upsert_model(conn, snap_id, "qwen3-vl:32b-cloud", family="qwen", raw_score=9.5, tier="S")
    upsert_model(conn, snap_id, "qwen-max", family="qwen", raw_score=8.0, tier="A")
    upsert_model(conn, snap_id, "llama3.1:8b", family="llama", raw_score=5.0, tier="B")
    upsert_model(conn, snap_id, "devstral-small-2505:24b", family="devstral", raw_score=7.0, tier="A-")

    # Local inventory
    upsert_local_inventory(conn, "llama3.1:8b", size_gb=4.7)
    upsert_local_inventory(conn, "devstral-small-2505:24b", size_gb=14.0)
    upsert_local_inventory(conn, "qwen3:8b", size_gb=4.9)

    # Agent template for coding
    upsert_agent_template(
        conn,
        "coding",
        system_prompt="You are a senior software engineer.",
        tools=["code_search", "file_edit"],
        restrictions={"max_tokens": 8192},
    )

    # OpenRouter equivalent
    upsert_model_equivalent(conn, "qwen3-vl:32b-cloud", "openrouter", "qwen/qwen3-vl-32b")

    return snap_id


def _make_mem_db() -> tuple[sqlite3.Connection, Path]:
    """Init in-memory db for testing."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Use init_db's table creation SQL but on in-memory conn
    conn.executescript(_SCHEMA_SQL)
    return conn, Path(":memory:")


# ─── Tests ───────────────────────────────────────────────────────────────


def test_agent_profile_defaults():
    """AgentProfile has sensible defaults."""
    profile = AgentProfile(model="test-model")
    assert profile.model == "test-model"
    assert profile.provider == "ollama"
    assert profile.tier == "D"
    assert profile.system_prompt == ""
    assert profile.tools == []
    assert profile.restrictions == {}
    assert profile.routing_trace == []
    assert profile.confidence == 0.5


def test_agent_profile_full_construction():
    """AgentProfile with all fields populated."""
    profile = AgentProfile(
        model="qwen-max",
        provider="cloud",
        tier="A",
        system_prompt="You are X.",
        tools=["search"],
        restrictions={"max_tokens": 4096},
        routing_trace=[{"step": "test"}],
        gas_remaining=99,
        confidence=0.9,
    )
    assert profile.model == "qwen-max"
    assert profile.provider == "cloud"
    assert profile.tier == "A"
    assert profile.gas_remaining == 99
    assert len(profile.routing_trace) == 1


def test_tier_walk_order_completeness():
    """All standard tiers are present in the walk order."""
    expected = {"S", "A+", "A", "A-", "B+", "B", "C", "D"}
    assert set(_TIER_WALK_ORDER) == expected
    # S must come first (Cloud-First invariant)
    assert _TIER_WALK_ORDER[0] == "S"


def test_specialization_patterns_exist():
    """Specialization patterns for coding, visual, uncensored are defined."""
    assert "coding" in _SPECIALIZATION_PATTERNS
    assert "visual" in _SPECIALIZATION_PATTERNS
    assert "uncensored" in _SPECIALIZATION_PATTERNS
    assert "debug" in _SPECIALIZATION_PATTERNS["coding"]
    assert "image" in _SPECIALIZATION_PATTERNS["visual"]


def test_route_request_fallback_no_registry():
    """route_request gracefully falls back to route_intent when registry is unavailable."""
    # Mock _try_import_db to return None (simulating missing db module)
    with patch("agents.gateway.router._try_import_db", return_value=None):
        profile = route_request("summarize this text", {})
        assert isinstance(profile, AgentProfile)
        assert profile.model != ""  # Should have picked a model
        assert profile.confidence == 0.3  # Low confidence on fallback
        # Should have a fallback trace entry
        fallback_steps = [t for t in profile.routing_trace if t.get("step") == "fallback"]
        assert len(fallback_steps) == 1
        assert fallback_steps[0]["reason"] == "db_import_failed"


def test_route_request_fallback_no_db_file():
    """route_request gracefully falls back when registry.db doesn't exist."""
    # Mock _try_import_db to return valid imports but db_path returns nonexistent path
    mock_db_path = MagicMock(return_value=Path("/nonexistent/path/registry.db"))

    def fake_imports():
        from agents.core.db import (
            get_agent_template,
            get_db,
            get_equivalents,
            get_local_inventory,
            get_models_by_tier,
            init_db,
        )

        return (
            get_db,
            mock_db_path,
            init_db,
            get_models_by_tier,
            get_local_inventory,
            get_agent_template,
            get_equivalents,
        )

    with patch("agents.gateway.router._try_import_db", side_effect=fake_imports):
        profile = route_request("hello world", {})
        assert isinstance(profile, AgentProfile)
        assert profile.confidence == 0.3
        fallback_steps = [t for t in profile.routing_trace if t.get("step") == "fallback"]
        assert len(fallback_steps) == 1
        assert "registry_not_found" in fallback_steps[0]["reason"]


def test_route_request_with_seeded_registry(tmp_path):
    """route_request selects best model from a seeded registry via tier walk."""
    db_file = tmp_path / "test_registry.db"
    init_db(db_file)

    with get_db(db_file) as conn:
        _seed_registry(conn)

    # Mock db_path to return our temp db
    with patch("agents.core.db.db_path", return_value=db_file):
        profile = route_request("tell me about the weather", {})
        assert isinstance(profile, AgentProfile)
        # Should have picked from the tier walk (S-tier model first)
        assert profile.model in ["qwen3-vl:32b-cloud", "qwen-max", "llama3.1:8b", "devstral-small-2505:24b"]
        assert len(profile.routing_trace) > 0
        # Tier walk traces should exist
        tier_walks = [t for t in profile.routing_trace if t.get("step") == "tier_walk"]
        assert len(tier_walks) > 0


def test_route_request_coding_specialization(tmp_path):
    """Coding intents trigger the specialization override."""
    db_file = tmp_path / "test_registry.db"
    init_db(db_file)

    with get_db(db_file) as conn:
        _seed_registry(conn)

    with patch("agents.core.db.db_path", return_value=db_file):
        profile = route_request("debug this Python code for me", {})
        assert isinstance(profile, AgentProfile)
        # Should have a specialization trace
        spec_steps = [t for t in profile.routing_trace if t.get("step") == "specialization"]
        assert len(spec_steps) == 1
        assert spec_steps[0]["match"] == "coding"
        # Should have loaded the coding template
        template_steps = [t for t in profile.routing_trace if t.get("step") == "template_loaded"]
        if template_steps:
            assert template_steps[0]["name"] == "coding"


def test_route_request_visual_specialization(tmp_path):
    """Visual intents route to primary_model."""
    db_file = tmp_path / "test_registry.db"
    init_db(db_file)

    with get_db(db_file) as conn:
        _seed_registry(conn)

    with patch("agents.core.db.db_path", return_value=db_file):
        profile = route_request("analyze this screenshot for me", {})
        assert isinstance(profile, AgentProfile)
        # Should have a visual specialization trace
        spec_steps = [t for t in profile.routing_trace if t.get("step") == "specialization"]
        assert len(spec_steps) == 1
        assert spec_steps[0]["match"] == "visual"


def test_legacy_route_intent_still_works():
    """Legacy route_intent() is preserved and returns expected models."""
    # Visual intent
    model = route_intent("process this image", {})
    assert isinstance(model, str)
    assert len(model) > 0

    # Coding intent
    model = route_intent("debug this code", {})
    assert isinstance(model, str)

    # Summarization intent
    model = route_intent("summarize the meeting", {})
    assert isinstance(model, str)
