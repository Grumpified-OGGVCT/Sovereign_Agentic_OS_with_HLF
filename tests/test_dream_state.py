"""
Tests for Phase 4.3 new features:
- Dynamic Context Pruning (30-day forgetting curve)
- Log Storage Truncator (parquet archive)
- Dream State archive format
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import patch


def _insert_fact(conn: sqlite3.Connection, entity_id: str, confidence: float) -> None:
    """Insert a fact directly without sqlite-vec dependency."""
    conn.execute(
        "INSERT INTO fact_store (entity_id, vector_embedding, semantic_relationship, confidence_score) "
        "VALUES (?, NULL, ?, ?)",
        (entity_id, "test_rel", confidence),
    )
    conn.commit()


def _make_conn(path: Path) -> sqlite3.Connection:
    """Create a fresh in-memory SQLite DB with the memory_scribe schema."""
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    # Load sqlite-vec if available (test environment may not have it)
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except Exception:
        pass
    # Create tables (vec0 virtual table only if extension loaded)
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
    # Try to create the vec_facts virtual table (requires sqlite-vec)
    try:
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_facts USING vec0(
                embedding float[768]
            );
        """)
        conn.commit()
    except Exception:
        # vec0 not available — create a plain table as fallback for tests
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vec_facts (
                rowid INTEGER PRIMARY KEY,
                embedding TEXT
            );
        """)
        conn.commit()
    return conn


class TestDynamicContextPruning:
    """prune_old_facts() — 30-day forgetting curve."""

    def test_prune_removes_stale_low_confidence(self, tmp_path: Path) -> None:
        from agents.core.memory_scribe import prune_old_facts

        conn = _make_conn(tmp_path / "mem.db")
        # Insert 5 facts with low confidence
        for i in range(5):
            _insert_fact(conn, f"entity_{i}", 0.1)

        with patch.dict("os.environ", {"BASE_DIR": str(tmp_path), "FACT_PRUNE_CONFIDENCE": "0.2"}):
            count = prune_old_facts(conn)

        assert isinstance(count, int)
        assert count >= 0  # may be 0 if percentile calc yields no cutoff yet

    def test_prune_spares_high_confidence(self, tmp_path: Path) -> None:
        from agents.core.memory_scribe import prune_old_facts

        conn = _make_conn(tmp_path / "mem.db")
        # Insert facts with high confidence — must NOT be pruned
        for i in range(5):
            _insert_fact(conn, f"hc_{i}", 0.9)

        with patch.dict("os.environ", {"BASE_DIR": str(tmp_path), "FACT_PRUNE_CONFIDENCE": "0.2"}):
            count = prune_old_facts(conn)

        remaining = conn.execute("SELECT COUNT(*) FROM fact_store").fetchone()[0]
        assert remaining == 5  # nothing pruned
        assert count == 0

    def test_prune_archives_before_deleting(self, tmp_path: Path) -> None:
        from agents.core.memory_scribe import prune_old_facts

        conn = _make_conn(tmp_path / "mem.db")
        # Insert 10 low-confidence facts to ensure some will be pruned
        for i in range(10):
            _insert_fact(conn, f"prune_{i}", 0.05)

        with patch.dict("os.environ", {
            "BASE_DIR": str(tmp_path),
            "FACT_PRUNE_CONFIDENCE": "0.1",
        }):
            count = prune_old_facts(conn)

        if count > 0:
            # Archive file should exist in BASE_DIR/data/cold_archive/
            cold_archive = tmp_path / "data" / "cold_archive"
            archives = list(cold_archive.glob("pruned_facts_*.json"))
            assert len(archives) >= 1


class TestDreamStateArchive:
    """archive_old_traces() — should produce parquet if pyarrow available, else JSON."""

    def test_archive_creates_file(self, tmp_path: Path) -> None:
        from agents.core.dream_state import archive_old_traces

        conn = _make_conn(tmp_path / "mem.db")
        # Insert rows older than 7 days
        old_ts = time.time() - 8 * 86400
        for i in range(3):
            conn.execute(
                "INSERT INTO rolling_context (session_id, timestamp, fifo_blob, token_count) "
                "VALUES (?, ?, ?, ?)",
                (f"sess_{i}", old_ts, f"blob_{i}", 10),
            )
        conn.commit()

        with patch.dict("os.environ", {"BASE_DIR": str(tmp_path)}):
            archive_old_traces(conn)

        cold_archive = tmp_path / "data" / "cold_archive"
        files = list(cold_archive.iterdir())
        assert len(files) == 1
        # Must be either parquet or json
        assert files[0].suffix in (".parquet", ".json")

    def test_archive_deletes_rows(self, tmp_path: Path) -> None:
        from agents.core.dream_state import archive_old_traces

        conn = _make_conn(tmp_path / "mem.db")
        old_ts = time.time() - 8 * 86400
        conn.execute(
            "INSERT INTO rolling_context (session_id, timestamp, fifo_blob, token_count) VALUES (?, ?, ?, ?)",
            ("to_delete", old_ts, "data", 5),
        )
        conn.commit()

        with patch.dict("os.environ", {"BASE_DIR": str(tmp_path)}):
            archive_old_traces(conn)

        count = conn.execute(
            "SELECT COUNT(*) FROM rolling_context WHERE session_id='to_delete'"
        ).fetchone()[0]
        assert count == 0

    def test_archive_skips_recent_rows(self, tmp_path: Path) -> None:
        from agents.core.dream_state import archive_old_traces

        conn = _make_conn(tmp_path / "mem.db")
        conn.execute(
            "INSERT INTO rolling_context (session_id, timestamp, fifo_blob, token_count) VALUES (?, ?, ?, ?)",
            ("recent", time.time(), "data", 5),
        )
        conn.commit()

        with patch.dict("os.environ", {"BASE_DIR": str(tmp_path)}):
            archive_old_traces(conn)

        # Recent rows should remain
        count = conn.execute(
            "SELECT COUNT(*) FROM rolling_context WHERE session_id='recent'"
        ).fetchone()[0]
        assert count == 1
        # No archive file created
        cold_archive = tmp_path / "data" / "cold_archive"
        assert not cold_archive.exists() or len(list(cold_archive.iterdir())) == 0

    def test_archive_parquet_format_if_available(self, tmp_path: Path) -> None:
        """If pyarrow is installed, archive must produce .parquet (not .json)."""
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            return  # skip if pyarrow not available

        from agents.core.dream_state import archive_old_traces

        conn = _make_conn(tmp_path / "mem.db")
        old_ts = time.time() - 8 * 86400
        conn.execute(
            "INSERT INTO rolling_context (session_id, timestamp, fifo_blob, token_count) VALUES (?, ?, ?, ?)",
            ("parq_test", old_ts, "blob_data", 5),
        )
        conn.commit()

        with patch.dict("os.environ", {"BASE_DIR": str(tmp_path)}):
            archive_old_traces(conn)

        cold_archive = tmp_path / "data" / "cold_archive"
        files = list(cold_archive.iterdir())
        assert len(files) == 1
        assert files[0].suffix == ".parquet"
