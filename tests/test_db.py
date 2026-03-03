"""
Unit tests for the SQL registry (agents.core.db).

All tests use in-memory SQLite — no file cleanup needed.
"""

from __future__ import annotations

import sqlite3

from agents.core.db import (
    TIER_MAP,
    ModelTier,
    add_feedback,
    create_snapshot,
    get_active_policies,
    get_active_snapshot,
    get_agent_template,
    get_current_tier,
    get_equivalents,
    get_feedback,
    get_local_inventory,
    get_models_by_tier,
    promote_snapshot,
    record_tier_change,
    upsert_agent_template,
    upsert_local_inventory,
    upsert_model,
    upsert_model_equivalent,
    upsert_policy_bundle,
)

# ── helpers ──────────────────────────────────────────────────────────────


def _mem_db() -> sqlite3.Connection:
    """Return an initialised in-memory database connection."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        # Re-use the same schema SQL that init_db() executes
        __import__("agents.core.db", fromlist=["_SCHEMA_SQL"])._SCHEMA_SQL
    )
    return conn


# ── Phase 1 tests ────────────────────────────────────────────────────────


def test_init_db_creates_tables() -> None:
    """All 9 tables exist after schema creation."""
    conn = _mem_db()
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    # Filter out sqlite_sequence (auto-created by SQLite for AUTOINCREMENT columns)
    tables = sorted(row["name"] for row in cur.fetchall() if not row["name"].startswith("sqlite_"))
    expected = sorted(
        [
            "snapshots",
            "models",
            "model_tiers",
            "user_local_inventory",
            "local_model_metadata",
            "agent_templates",
            "model_equivalents",
            "policy_bundles",
            "model_feedback",
        ]
    )
    assert tables == expected
    conn.close()


def test_upsert_model_insert_and_update() -> None:
    """Insert a model, then upsert with a changed score — verify update."""
    conn = _mem_db()
    sid = create_snapshot(conn, families=["llama"])
    promote_snapshot(conn, sid)

    # Insert
    upsert_model(conn, sid, "llama3.1:8b", family="llama", raw_score=72.5, tier="A")
    rows = get_models_by_tier(conn, "A", snapshot_id=sid)
    assert len(rows) == 1
    assert rows[0]["raw_score"] == 72.5

    # Update via upsert
    upsert_model(conn, sid, "llama3.1:8b", family="llama", raw_score=80.0, tier="A+")
    rows_a = get_models_by_tier(conn, "A", snapshot_id=sid)
    rows_ap = get_models_by_tier(conn, "A+", snapshot_id=sid)
    assert len(rows_a) == 0  # moved out of A
    assert len(rows_ap) == 1
    assert rows_ap[0]["raw_score"] == 80.0

    conn.close()


def test_get_models_by_tier() -> None:
    """Insert models across tiers, query for one tier only."""
    conn = _mem_db()
    sid = create_snapshot(conn)
    promote_snapshot(conn, sid)

    upsert_model(conn, sid, "model-s", tier="S", raw_score=95.0)
    upsert_model(conn, sid, "model-a", tier="A", raw_score=70.0)
    upsert_model(conn, sid, "model-a2", tier="A", raw_score=75.0)
    upsert_model(conn, sid, "model-c", tier="C", raw_score=40.0)

    tier_a = get_models_by_tier(conn, "A")
    assert len(tier_a) == 2
    # Ordered by raw_score DESC
    assert tier_a[0]["model_id"] == "model-a2"
    assert tier_a[1]["model_id"] == "model-a"

    conn.close()


def test_promote_snapshot() -> None:
    """Create 2 snapshots, promote second, verify first is demoted."""
    conn = _mem_db()
    s1 = create_snapshot(conn, families=["llama"])
    promote_snapshot(conn, s1)
    assert get_active_snapshot(conn)["id"] == s1

    s2 = create_snapshot(conn, families=["qwen", "gemma"])
    promote_snapshot(conn, s2)

    active = get_active_snapshot(conn)
    assert active["id"] == s2
    # s1 should no longer be promoted
    row = conn.execute("SELECT is_promoted FROM snapshots WHERE id = ?", (s1,)).fetchone()
    assert row["is_promoted"] == 0

    conn.close()


def test_local_inventory_upsert() -> None:
    """Insert local model, update last_seen, verify idempotency."""
    conn = _mem_db()

    upsert_local_inventory(conn, "phi3:mini", size_gb=2.3, vram_required_mb=3000)
    inv = get_local_inventory(conn)
    assert len(inv) == 1
    assert inv[0]["model_id"] == "phi3:mini"

    # Upsert again — last_seen should update
    import time

    time.sleep(0.01)  # ensure clock ticks
    upsert_local_inventory(conn, "phi3:mini", size_gb=2.3, vram_required_mb=3100)
    inv2 = get_local_inventory(conn)
    assert len(inv2) == 1
    assert inv2[0]["vram_required_mb"] == 3100

    conn.close()


def test_model_tier_enum_mapping() -> None:
    """All tier strings from scoring.py map to valid ModelTier values."""
    expected_tiers = ["S", "A+", "A", "A-", "B+", "B", "C", "D"]
    for t in expected_tiers:
        assert t in TIER_MAP, f"Tier '{t}' missing from TIER_MAP"
        assert isinstance(TIER_MAP[t], ModelTier)


def test_agent_template_crud() -> None:
    """Create, read, update agent template."""
    conn = _mem_db()

    upsert_agent_template(
        conn,
        "coder",
        required_tier="A",
        system_prompt="You are a coding assistant.",
        tools=["file_read", "file_write"],
        restrictions={"max_tokens": 4096},
    )
    t = get_agent_template(conn, "coder")
    assert t is not None
    assert t["required_tier"] == "A"
    assert "file_read" in t["tools_json"]

    # Update
    upsert_agent_template(
        conn,
        "coder",
        required_tier="S",
        system_prompt="You are an elite coding assistant.",
    )
    t2 = get_agent_template(conn, "coder")
    assert t2["required_tier"] == "S"
    assert "elite" in t2["system_prompt"]

    conn.close()


def test_policy_bundle_crud() -> None:
    """Create active policy, deactivate, verify."""
    conn = _mem_db()

    upsert_policy_bundle(
        conn,
        "no-uncensored",
        rules={"block_uncensored": True},
        active=True,
    )
    active = get_active_policies(conn)
    assert len(active) == 1
    assert active[0]["name"] == "no-uncensored"

    # Deactivate
    upsert_policy_bundle(conn, "no-uncensored", active=False)
    active2 = get_active_policies(conn)
    assert len(active2) == 0

    conn.close()


def test_model_feedback() -> None:
    """Record and retrieve model feedback."""
    conn = _mem_db()
    add_feedback(conn, "llama3.1:8b", rating=5, context="Great response")
    add_feedback(conn, "llama3.1:8b", rating=3, context="Mediocre")
    fb = get_feedback(conn, "llama3.1:8b")
    assert len(fb) == 2
    # ORDER BY ts DESC — most recent first
    # Both inserted in same second so order may be by rowid desc
    ratings = {fb[0]["rating"], fb[1]["rating"]}
    assert ratings == {3, 5}
    conn.close()


def test_tier_history_tracking() -> None:
    """Record tier changes and retrieve current tier."""
    conn = _mem_db()
    record_tier_change(conn, "llama3.1:8b", "B")
    assert get_current_tier(conn, "llama3.1:8b") == "B"

    record_tier_change(conn, "llama3.1:8b", "A")
    assert get_current_tier(conn, "llama3.1:8b") == "A"

    # Should have 2 history records, with the first one closed
    rows = conn.execute("SELECT * FROM model_tiers WHERE model_id = ?", ("llama3.1:8b",)).fetchall()
    assert len(rows) == 2
    assert rows[0]["effective_to"] is not None  # closed
    assert rows[1]["effective_to"] is None  # still current

    conn.close()


def test_model_equivalents() -> None:
    """Map a canonical model to OpenRouter provider ID."""
    conn = _mem_db()
    upsert_model_equivalent(conn, "llama3.1:70b", "openrouter", "meta-llama/llama-3.1-70b-instruct")
    equivs = get_equivalents(conn, "llama3.1:70b")
    assert len(equivs) == 1
    assert equivs[0]["provider_model_id"] == "meta-llama/llama-3.1-70b-instruct"
    conn.close()
