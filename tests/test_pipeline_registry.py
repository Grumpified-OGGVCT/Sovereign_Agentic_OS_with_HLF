"""Tests for the pipeline → registry.db bridge."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

# The function under test
from agents.gateway.matrix_sync.pipeline import _persist_to_registry

# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def registry_db(tmp_path: Path) -> Path:
    """Return a path for a fresh temporary registry.db."""
    return tmp_path / "registry.db"


def _make_catalog_rows() -> list[dict[str, Any]]:
    return [
        {
            "official_nomenclature": "qwen3:32b",
            "family": "qwen3",
            "digest": "sha256:abc123",
            "modified_at": "2025-12-01T00:00:00",
            "size_bytes": 17_000_000_000,
            "benchmark_composite": 0.82,
            "confidence_score": 0.9,
        },
        {
            "official_nomenclature": "llama3.1:8b",
            "family": "llama3.1",
            "digest": "sha256:def456",
            "modified_at": "2025-11-15T00:00:00",
            "size_bytes": 4_300_000_000,
            "benchmark_composite": 0.71,
            "confidence_score": 0.75,
        },
    ]


def _make_matrix_rows() -> list[dict[str, Any]]:
    return [
        # qwen3 appears in two categories — best tier should be picked (A+)
        {"official_nomenclature": "qwen3:32b", "tier": "A+", "category": "reasoning", "category_score": 9.2},
        {"official_nomenclature": "qwen3:32b", "tier": "A", "category": "coding", "category_score": 8.5},
        # llama only in one category
        {"official_nomenclature": "llama3.1:8b", "tier": "B+", "category": "general", "category_score": 6.0},
    ]


def _make_dup_rows() -> list[dict[str, Any]]:
    return [
        {
            "local_model": "qwen3:32b",
            "normalized_id": "qwen3-32b",
            "family_guess": "qwen3",
            "digest": "sha256:abc123",
            "modified_at": "2025-12-01T00:00:00",
            "size_bytes": 17_000_000_000,
        },
    ]


# ── Tests ───────────────────────────────────────────────────────────────────

def test_persist_creates_snapshot_and_models(registry_db: Path) -> None:
    """Pipeline results are persisted to registry.db with correct tiers."""
    result = _persist_to_registry(
        catalog_rows=_make_catalog_rows(),
        matrix_rows=_make_matrix_rows(),
        dup_rows=_make_dup_rows(),
        families=["qwen3", "llama3.1"],
        promote=True,
        db_path=str(registry_db),
    )

    assert result["registry_persisted"] is True
    assert result["models_upserted"] == 2
    assert result["local_synced"] == 1
    assert result["promoted"] is True

    # Verify in database directly
    conn = sqlite3.connect(str(registry_db))
    conn.row_factory = sqlite3.Row

    # Snapshot should exist and be promoted
    snap = conn.execute("SELECT * FROM snapshots WHERE id = ?", (result["snapshot_id"],)).fetchone()
    assert snap is not None
    assert snap["is_promoted"] == 1
    assert snap["model_count"] == 2

    # Models should have correct tiers
    models = conn.execute(
        "SELECT model_id, tier, raw_score FROM models WHERE snapshot_id = ?",
        (result["snapshot_id"],),
    ).fetchall()
    model_map = {m["model_id"]: dict(m) for m in models}

    assert "qwen3:32b" in model_map
    assert model_map["qwen3:32b"]["tier"] == "A+"  # best of A+ and A
    assert model_map["llama3.1:8b"]["tier"] == "B+"

    # Local inventory should be populated
    local = conn.execute("SELECT * FROM user_local_inventory").fetchall()
    assert len(local) == 1
    assert local[0]["model_id"] == "qwen3:32b"

    conn.close()


def test_persist_without_promote(registry_db: Path) -> None:
    """Snapshot is created but NOT promoted when promote=False."""
    result = _persist_to_registry(
        catalog_rows=_make_catalog_rows(),
        matrix_rows=_make_matrix_rows(),
        dup_rows=[],
        families=["qwen3"],
        promote=False,
        db_path=str(registry_db),
    )

    assert result["promoted"] is False

    conn = sqlite3.connect(str(registry_db))
    snap = conn.execute("SELECT is_promoted FROM snapshots WHERE id = ?", (result["snapshot_id"],)).fetchone()
    assert snap[0] == 0
    conn.close()


def test_persist_empty_pipeline(registry_db: Path) -> None:
    """Pipeline with no models still creates a valid snapshot."""
    result = _persist_to_registry(
        catalog_rows=[],
        matrix_rows=[],
        dup_rows=[],
        families=[],
        promote=True,
        db_path=str(registry_db),
    )

    assert result["registry_persisted"] is True
    assert result["models_upserted"] == 0
    assert result["local_synced"] == 0
