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
from typing import Any, Optional


_DB_PATH = Path(os.environ.get("BASE_DIR", "/app")) / "data" / "sqlite" / "memory.db"
_DLQ_STREAM = "memory_scribe_dlq"


import sqlite_vec

def _get_connection() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except AttributeError:
        pass # Depending on python build, extension loading might be restricted
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
    """)
    conn.commit()


def _sha256_cache_key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def write_fact(
    conn: sqlite3.Connection,
    entity_id: str,
    vector_embedding: Optional[list[float]],
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
        cur.execute(
            "INSERT INTO vec_facts(rowid, embedding) VALUES (?, ?)",
            (row_id, vec_json)
        )
    conn.commit()


def run() -> None:
    import redis

    r = redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
    conn = _get_connection()
    _init_schema(conn)

    group = "memory-group"
    stream = "intents"
    try:
        r.xgroup_create(stream, group, id="0", mkstream=True)
    except Exception:
        pass

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
