"""
Janus Worker — Isolated subprocess for heavy ML operations.

Accepts JSON commands on stdin, returns JSON on stdout.
Loads SentenceTransformer and ChromaDB lazily on first use.
Self-terminates after 5 minutes of inactivity.

Commands:
  {"cmd": "search_archives", "query": "..."}
  {"cmd": "view_thread_history", "url": "..."}
  {"cmd": "shutdown"}

Protocol:
  - One JSON object per line (newline-delimited JSON)
  - Response includes {"status": "ok", "result": ...} or {"status": "error", "error": "..."}
"""

from __future__ import annotations

import json
import os
import select
import sqlite3
import sys
import time
from pathlib import Path

_BASE_DIR = Path(os.environ.get("BASE_DIR", str(Path(__file__).resolve().parent.parent)))
_IDLE_TIMEOUT = 300  # 5 minutes

# Lazy-loaded ML state
_embedder = None
_chroma_client = None
_collection = None


def _init_ml():
    """Lazy-load SentenceTransformer and ChromaDB."""
    global _embedder, _chroma_client, _collection
    if _embedder is not None:
        return

    import chromadb
    from sentence_transformers import SentenceTransformer

    _embedder = SentenceTransformer("all-MiniLM-L6-v2")
    chroma_path = _BASE_DIR.parent / "project_janus" / "data" / "chroma_db"
    _chroma_client = chromadb.PersistentClient(path=str(chroma_path))
    _collection = _chroma_client.get_or_create_collection(name="stolen_history")


def _get_janus_db():
    db_path = _BASE_DIR.parent / "project_janus" / "data" / "vault.db"
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def handle_search_archives(query: str) -> dict:
    """Semantic search over archival data."""
    _init_ml()
    if _collection is None:
        return {"status": "error", "error": "ML models not initialized"}

    vector = _embedder.encode(query).tolist()
    results = _collection.query(query_embeddings=[vector], n_results=5)

    output = "--- RAW ARCHIVAL DATA ---\n"
    for doc, meta in zip(results["documents"][0], results["metadatas"][0], strict=False):
        output += f"[Author: {meta.get('author', 'N/A')} | Source: {meta.get('source', 'N/A')}]\n"
        output += f"{doc}\n-------------------------\n"

    return {"status": "ok", "result": [{"result": output}]}


def handle_view_thread_history(url: str) -> dict:
    """Reconstruct thread history from snapshots."""
    try:
        conn = _get_janus_db()
        cur = conn.cursor()
        rows = cur.execute(
            """
            SELECT p.author, p.content_clean, p.source_type, p.snapshot_date,
                   p.is_deleted
            FROM posts p
            JOIN threads t ON p.thread_id = t.id
            WHERE t.url = ?
            ORDER BY p.post_external_id, p.snapshot_date
            """,
            (url,),
        ).fetchall()
        conn.close()

        if not rows:
            return {"status": "ok", "result": [{"result": f"No history found for thread: {url}"}]}

        output = f"--- THREAD RECONSTRUCTION: {url} ---\n"
        for row in rows:
            author, content, source, date, is_deleted = row
            deleted_marker = " [DELETED]" if is_deleted else ""
            output += f"[{source.upper()} | {date}]{deleted_marker} {author}: {content}\n"

        return {"status": "ok", "result": [{"result": output}]}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def main():
    """Main loop: read JSON commands from stdin, write responses to stdout."""
    last_activity = time.time()

    while True:
        # Check for idle timeout
        if time.time() - last_activity > _IDLE_TIMEOUT:
            sys.stderr.write("janus_worker: idle timeout, shutting down\n")
            break

        # Non-blocking read with 1s timeout (cross-platform)
        try:
            line = sys.stdin.readline()
        except EOFError:
            break

        if not line:
            # EOF or empty — parent closed pipe
            break

        line = line.strip()
        if not line:
            continue

        last_activity = time.time()

        try:
            cmd = json.loads(line)
        except json.JSONDecodeError as e:
            response = {"status": "error", "error": f"Invalid JSON: {e}"}
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
            continue

        cmd_name = cmd.get("cmd", "")

        try:
            if cmd_name == "search_archives":
                response = handle_search_archives(cmd.get("query", ""))
            elif cmd_name == "view_thread_history":
                response = handle_view_thread_history(cmd.get("url", ""))
            elif cmd_name == "shutdown":
                response = {"status": "ok", "result": "shutting down"}
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
                break
            else:
                response = {"status": "error", "error": f"Unknown command: {cmd_name}"}
        except Exception as e:
            response = {"status": "error", "error": str(e)}

        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
