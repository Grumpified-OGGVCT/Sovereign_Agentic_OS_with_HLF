"""Tests for SQLite memory layer."""
from __future__ import annotations

import json
import sqlite3
import tempfile

import pytest


def _create_test_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS identity_core (
            id TEXT PRIMARY KEY,
            directive_hash TEXT NOT NULL,
            immutable_constraint_blob TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rolling_context (
            session_id TEXT NOT NULL,
            timestamp REAL NOT NULL,
            fifo_blob TEXT NOT NULL,
            token_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS fact_store (
            entity_id TEXT NOT NULL,
            vector_embedding TEXT,
            semantic_relationship TEXT,
            confidence_score REAL NOT NULL DEFAULT 0.0
        );
    """)
    conn.commit()
    return conn


def test_wal_mode_enabled() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    conn = _create_test_db(db_path)
    result = conn.execute("PRAGMA journal_mode").fetchone()
    assert result[0] == "wal", f"Expected WAL mode, got: {result[0]}"
    conn.close()


def test_fact_store_insert_with_vector() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    conn = _create_test_db(db_path)

    # Insert a fact with a FLOAT32[768]-equivalent vector stored as JSON
    vector = [0.1] * 768
    vec_json = json.dumps(vector)
    conn.execute(
        "INSERT INTO fact_store (entity_id, vector_embedding, semantic_relationship, confidence_score) "
        "VALUES (?, ?, ?, ?)",
        ("test-entity-1", vec_json, "is_a:test", 0.95),
    )
    conn.commit()

    row = conn.execute("SELECT entity_id, vector_embedding, confidence_score FROM fact_store").fetchone()
    assert row[0] == "test-entity-1"
    loaded_vec = json.loads(row[1])
    assert len(loaded_vec) == 768
    assert abs(loaded_vec[0] - 0.1) < 1e-6
    assert row[2] == pytest.approx(0.95, abs=1e-6)
    conn.close()


def test_rolling_context_insert() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    conn = _create_test_db(db_path)
    import time

    conn.execute(
        "INSERT INTO rolling_context (session_id, timestamp, fifo_blob, token_count) VALUES (?, ?, ?, ?)",
        ("session-1", time.time(), '{"test": "data"}', 3),
    )
    conn.commit()
    row = conn.execute("SELECT session_id, token_count FROM rolling_context").fetchone()
    assert row[0] == "session-1"
    assert row[1] == 3
    conn.close()


def test_all_three_tables_created() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    conn = _create_test_db(db_path)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "identity_core" in tables
    assert "rolling_context" in tables
    assert "fact_store" in tables
    conn.close()
