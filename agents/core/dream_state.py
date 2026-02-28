"""
Dream State — scheduled cron job at 03:00.
Compresses rolling context, runs DSPy regression, auto-immune extraction.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path


_DB_PATH = Path(os.environ.get("BASE_DIR", "/app")) / "data" / "sqlite" / "memory.db"
_COLD_ARCHIVE = Path(os.environ.get("BASE_DIR", "/app")) / "data" / "cold_archive"


def compress_rolling_context(conn: sqlite3.Connection) -> None:
    """Map-reduce summarize day's rolling context."""
    from agents.core.fractal_summarization import FractalSummarizer

    cutoff = time.time() - 86400  # 24 hours
    rows = conn.execute(
        "SELECT session_id, fifo_blob FROM rolling_context WHERE timestamp > ?", (cutoff,)
    ).fetchall()
    if not rows:
        return
    combined = " ".join(r[1] for r in rows)
    
    # Use real map-reduce summarization
    summary = FractalSummarizer.summarize_context(combined, target_tokens=1500)
    
    # Fast token count estimation (approx. 4 chars per token)
    token_count = len(summary) // 4
    
    conn.execute(
        "INSERT INTO rolling_context (session_id, timestamp, fifo_blob, token_count) "
        "VALUES (?, ?, ?, ?)",
        ("dream_summary", time.time(), summary, token_count),
    )
    conn.commit()


def archive_old_traces(conn: sqlite3.Connection) -> None:
    """Move traces older than 7 days to cold_archive as JSON, delete raw."""
    cutoff = time.time() - 7 * 86400
    rows = conn.execute(
        "SELECT rowid, session_id, timestamp, fifo_blob FROM rolling_context WHERE timestamp < ?",
        (cutoff,),
    ).fetchall()
    if not rows:
        return
    _COLD_ARCHIVE.mkdir(parents=True, exist_ok=True)
    archive_file = _COLD_ARCHIVE / f"archive_{int(time.time())}.json"
    archive_file.write_text(json.dumps([{"session_id": r[1], "timestamp": r[2], "blob": r[3]} for r in rows]))
    rowids = [r[0] for r in rows]
    conn.execute(f"DELETE FROM rolling_context WHERE rowid IN ({','.join('?' * len(rowids))})", rowids)
    conn.commit()


def run_dream_cycle() -> None:
    if not _DB_PATH.exists():
        return
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    compress_rolling_context(conn)
    archive_old_traces(conn)
    conn.close()


if __name__ == "__main__":
    run_dream_cycle()
