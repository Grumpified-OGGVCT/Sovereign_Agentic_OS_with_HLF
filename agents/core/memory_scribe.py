"""
Memory Scribe — async SQLite writer with vector search support.
Single-threaded Redis XREADGROUP consumer.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

try:
    import sqlite_vec
except ImportError:
    sqlite_vec = None  # type: ignore[assignment]

import contextlib

import redis as _redis_module

_DB_PATH = Path(os.environ.get("BASE_DIR", "/app")) / "data" / "sqlite" / "memory.db"
_DLQ_STREAM = "memory_scribe_dlq"


def _sql_in(ids: list) -> str:
    """Return a safe SQL ``IN (?, ?, ...)`` placeholder string for *ids*."""
    return f"({','.join('?' * len(ids))})"


def _get_connection() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        conn.enable_load_extension(True)
        if sqlite_vec is not None:
            sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except AttributeError:
        pass  # extension loading may be restricted in some Python builds
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
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
        CREATE INDEX IF NOT EXISTS idx_rolling_context_timestamp ON rolling_context(timestamp);

        CREATE TABLE IF NOT EXISTS fact_store (
            entity_id TEXT NOT NULL,
            vector_embedding TEXT,
            semantic_relationship TEXT,
            confidence_score REAL NOT NULL DEFAULT 0.0
        );
        -- Vector Search Table using sqlite-vec
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_facts USING vec0(
            embedding float[768]
        );
        -- Dream Mode results
        CREATE TABLE IF NOT EXISTS dream_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            cycle_type TEXT NOT NULL DEFAULT 'scheduled',
            hlf_practiced INTEGER DEFAULT 0,
            hlf_passed INTEGER DEFAULT 0,
            context_compressed_chars INTEGER DEFAULT 0,
            context_result_chars INTEGER DEFAULT 0,
            duration_seconds REAL DEFAULT 0,
            summary TEXT
        );
        -- Hat analysis findings
        CREATE TABLE IF NOT EXISTS hat_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dream_cycle_id INTEGER REFERENCES dream_results(id),
            hat TEXT NOT NULL,
            severity TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            recommendation TEXT,
            resolved INTEGER DEFAULT 0,
            timestamp REAL NOT NULL
        );
    """)
    conn.commit()


def _sha256_cache_key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def write_fact(
    conn: sqlite3.Connection,
    entity_id: str,
    vector_embedding: list[float] | None,
    semantic_relationship: str,
    confidence_score: float,
) -> None:
    vec_json = json.dumps(vector_embedding) if vector_embedding else None
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO fact_store (entity_id, vector_embedding, semantic_relationship, confidence_score) "
        "VALUES (?, ?, ?, ?)",
        (entity_id, vec_json, semantic_relationship, confidence_score),
    )
    if vec_json is not None:
        row_id = cur.lastrowid
        cur.execute("INSERT INTO vec_facts(rowid, embedding) VALUES (?, ?)", (row_id, vec_json))
    conn.commit()


# Dynamic Context Pruning — 30-day forgetting curve (Phase 4.3)
# Read at function call time (not module load) so env patches work in tests.
_PRUNE_AGE_DAYS_DEFAULT: int = 30
_PRUNE_CONFIDENCE_DEFAULT: float = 0.2


def prune_old_facts(conn: sqlite3.Connection) -> int:
    """
    Remove low-relevance facts from the Fact_Store that have not been accessed
    for more than FACT_PRUNE_AGE_DAYS days AND whose confidence_score is below
    FACT_PRUNE_CONFIDENCE.

    Removed facts are archived into ``data/cold_archive/`` as a JSON file before
    deletion so the data is never permanently lost.

    Returns the number of rows pruned.

    Notes
    -----
    This function currently uses the SQLite ``rowid`` as a proxy for fact age,
    since no explicit ``last_accessed`` or timestamp column exists yet.
    The ``FACT_PRUNE_AGE_DAYS`` environment variable is therefore interpreted
    as a *percentile* (0–100) over insertion order: low-confidence facts whose
    ``rowid`` falls below that percentile threshold are considered candidates
    for pruning.
    """
    import json as _json

    prune_age_days = float(os.environ.get("FACT_PRUNE_AGE_DAYS", str(_PRUNE_AGE_DAYS_DEFAULT)))
    prune_confidence = float(os.environ.get("FACT_PRUNE_CONFIDENCE", str(_PRUNE_CONFIDENCE_DEFAULT)))

    # Identify stale low-confidence facts.
    # We use the fact_store rowid as a proxy for insertion time because no
    # explicit last_accessed column exists yet. Lower rowid = older.
    # Interpret FACT_PRUNE_AGE_DAYS as a percentile (0–100) over the rowid span.
    prune_percentile = max(0.0, min(100.0, prune_age_days))
    oldest_rowid_cutoff = conn.execute(
        """
        SELECT MAX(rowid) FROM fact_store
        WHERE rowid <= (
            SELECT MIN(rowid) + (MAX(rowid) - MIN(rowid)) * ? / 100.0
            FROM fact_store
        )
        """,
        (prune_percentile,),
    ).fetchone()[0]

    if oldest_rowid_cutoff is None:
        return 0

    rows = conn.execute(
        "SELECT rowid, entity_id, vector_embedding, semantic_relationship, confidence_score "
        "FROM fact_store WHERE rowid <= ? AND confidence_score < ?",
        (oldest_rowid_cutoff, prune_confidence),
    ).fetchall()

    if not rows:
        return 0

    # Archive to cold storage before deleting
    cold_archive = Path(os.environ.get("BASE_DIR", "/app")) / "data" / "cold_archive"
    cold_archive.mkdir(parents=True, exist_ok=True)
    archive_path = cold_archive / f"pruned_facts_{int(time.time())}.json"
    archive_path.write_text(
        _json.dumps(
            [
                {
                    "entity_id": r[1],
                    "vector_embedding": r[2],
                    "semantic_relationship": r[3],
                    "confidence_score": r[4],
                }
                for r in rows
            ]
        )
    )

    rowids = [r[0] for r in rows]
    conn.execute(f"DELETE FROM fact_store WHERE rowid IN {_sql_in(rowids)}", rowids)
    # Remove orphaned vec_facts entries (best-effort; may not exist as virtual table)
    with contextlib.suppress(Exception):
        conn.execute(f"DELETE FROM vec_facts WHERE rowid IN {_sql_in(rowids)}", rowids)
    conn.commit()
    return len(rows)


def run() -> None:
    r = _redis_module.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
    conn = _get_connection()
    _init_schema(conn)

    group = "memory-group"
    stream = "intents"
    with contextlib.suppress(Exception):
        r.xgroup_create(stream, group, id="0", mkstream=True)

    while True:
        messages = r.xreadgroup(group, "memory-1", {stream: ">"}, count=1, block=5000)
        if not messages:
            continue
        for _stream, entries in messages:
            for entry_id, data in entries:
                failures = 0
                while failures < 3:
                    try:
                        payload: dict[str, Any] = json.loads(data.get("data", "{}"))
                        session_id = payload.get("request_id", "unknown")
                        blob = json.dumps(payload)
                        conn.execute(
                            "INSERT INTO rolling_context (session_id, timestamp, fifo_blob, token_count) "
                            "VALUES (?, ?, ?, ?)",
                            (session_id, time.time(), blob, len(blob.split())),
                        )
                        conn.commit()
                        r.xack(stream, group, entry_id)
                        break
                    except Exception as exc:
                        failures += 1
                        if failures >= 3:
                            r.xadd(_DLQ_STREAM, {"data": json.dumps({"error": str(exc), "entry": data})})
                            r.xack(stream, group, entry_id)


if __name__ == "__main__":
    run()
