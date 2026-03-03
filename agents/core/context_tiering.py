"""
Active Context Tiering subsystem.
Transfers vector clusters between cold SQLite (Fact Store) and hot Redis (Knowledge Sub-Graph).
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3

import redis

# Redis connection
_REDIS_URL = os.environ.get("REDIS_URL", "redis://redis-broker:6379/0")
# SQLite connection
_DB_PATH = os.path.join(os.environ.get("BASE_DIR", "/app"), "data", "sqlite", "memory.db")


class ContextTierManager:
    """Manages context tiering across SQLite (Cold) and Redis (Hot)."""

    def __init__(self):
        self.r = redis.from_url(_REDIS_URL, decode_responses=True)

    def load_hot_graph(self, topic_id: str) -> None:
        """
        Pull SQLite vector clusters for a topic and pre-compute a hot Knowledge Sub-Graph in Redis.
        Now uses real sqlite-vec KNN search against Ollama embeddings.
        """
        if not os.path.exists(_DB_PATH):
            return

        # 1. Generate text embedding for topic via Ollama
        import requests

        try:
            ollama_host = os.environ.get("OLLAMA_HOST", "http://ollama-matrix:11434")
            resp = requests.post(
                f"{ollama_host}/api/embeddings",
                json={
                    "model": "nomic-embed-text",  # Adjust model as deployed
                    "prompt": topic_id,
                },
                timeout=10,
            )
            resp.raise_for_status()
            embedding = resp.json().get("embedding")
            if not embedding:
                return
        except Exception:
            # Fallback to empty if embedding offline
            return

        import sqlite_vec

        try:
            conn = sqlite3.connect(_DB_PATH)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.enable_load_extension(True)
            with contextlib.suppress(Exception):
                sqlite_vec.load(conn)
            conn.enable_load_extension(False)

            # 2. KNN Search on vec0 virtual table
            vec_json = json.dumps(embedding)
            rows = conn.execute(
                """
                SELECT f.entity_id, f.semantic_relationship
                FROM fact_store f
                JOIN vec_facts v ON v.rowid = f.rowid
                WHERE v.embedding MATCH ? AND k = 10
                ORDER BY distance
                """,
                (vec_json,),
            ).fetchall()

            # 3. Stream clusters to Redis Hot Graph
            pipeline = self.r.pipeline()
            for entity_id, semantic_relationship in rows:
                pipeline.hset(f"hot_graph:{topic_id}:{entity_id}", mapping={"relations": semantic_relationship or ""})
            pipeline.execute()
            conn.close()
        except Exception as exc:
            # Fallback gracefully for DB locks or extension load errors
            import logging

            logging.getLogger(__name__).warning(f"load_hot_graph DB failure for {topic_id}: {exc}")

    def evict_hot_graph(self, topic_id: str) -> None:
        """
        Evict the Knowledge Sub-Graph from Redis back to SQLite (or just delete if read-only).
        """
        keys = self.r.keys(f"hot_graph:{topic_id}:*")
        if keys:
            self.r.delete(*keys)
