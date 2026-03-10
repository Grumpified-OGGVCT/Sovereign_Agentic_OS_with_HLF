"""
Active Context Tiering subsystem.
Transfers vector clusters between cold SQLite (Fact Store) and hot Redis (Knowledge Sub-Graph).

Silver Hat additions (token & gas optimization):
- Embedding model resolved from ``EMBEDDING_MODEL`` env var (no hardcoded names).
- ``load_hot_graph`` accepts a ``token_budget`` / ``max_clusters`` cap so the hot
  graph never exceeds the per-tier context window.
- ``gas_used`` counter incremented on every tier-promotion (load) and eviction.
- ``get_stats()`` exposes gas and cluster metrics for observability.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
from typing import Any

import redis

logger = logging.getLogger(__name__)

# Redis connection
_REDIS_URL = os.environ.get("REDIS_URL", "redis://redis-broker:6379/0")
# SQLite connection
_DB_PATH = os.path.join(os.environ.get("BASE_DIR", "/app"), "data", "sqlite", "memory.db")

# Gas cost constants (consistent with scribe_agent.py AUDIT_GAS_COST = 1)
_GAS_PER_LOAD: int = 2   # loading a cluster into hot graph costs 2 (IO-bound)
_GAS_PER_EVICT: int = 1  # evicting is cheaper


def _resolve_embedding_model() -> str:
    """Return the embedding model name from the ``EMBEDDING_MODEL`` env var.

    The default fallback (``nomic-embed-text``) is the only permitted hardcoded
    occurrence in this module and exists solely to provide graceful degradation
    when the environment variable is not set.  All other code in this module
    must call this function instead of referencing any model name directly.
    """
    return os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")


def _tier_max_clusters(token_budget: int | None) -> int:
    """Derive a cluster cap from an optional token budget.

    Each cluster is treated as ~256 tokens (a conservative estimate for a
    semantic relationship string).  If no budget is provided, default to 10.
    """
    if token_budget and token_budget > 0:
        return max(1, token_budget // 256)
    return 10


class ContextTierManager:
    """Manages context tiering across SQLite (Cold) and Redis (Hot).

    Silver Hat additions
    --------------------
    * ``gas_used``       — running total of gas consumed by tier operations.
    * ``load_hot_graph`` — now accepts *token_budget* and *max_clusters* to
                           enforce an O(1)-bounded hot graph.
    * ``get_stats()``    — exposes cluster counts and gas consumption.
    """

    def __init__(self) -> None:
        self.r = redis.from_url(_REDIS_URL, decode_responses=True)
        self.gas_used: int = 0
        self._loads: int = 0
        self._evictions: int = 0

    def load_hot_graph(
        self,
        topic_id: str,
        *,
        token_budget: int | None = None,
        max_clusters: int | None = None,
    ) -> None:
        """Pull SQLite vector clusters for a topic into a hot Knowledge Sub-Graph in Redis.

        Uses KNN search against Ollama embeddings.  The number of clusters
        promoted is bounded by *max_clusters* (or derived from *token_budget*)
        to prevent hot-graph bloat.

        Args:
            topic_id:      The topic / entity being loaded.
            token_budget:  Per-tier token budget.  Used to derive *max_clusters*
                           when *max_clusters* is not supplied explicitly.
            max_clusters:  Hard cap on the number of clusters promoted.
        """
        if not os.path.exists(_DB_PATH):
            return

        cap = max_clusters if max_clusters is not None else _tier_max_clusters(token_budget)

        # 1. Generate text embedding for topic via Ollama
        import requests

        embedding_model = _resolve_embedding_model()
        try:
            ollama_host = os.environ.get("OLLAMA_HOST", "http://ollama-matrix:11434")
            resp = requests.post(
                f"{ollama_host}/api/embeddings",
                json={
                    "model": embedding_model,
                    "prompt": topic_id,
                },
                timeout=10,
            )
            resp.raise_for_status()
            embedding = resp.json().get("embedding")
            if not embedding:
                return
        except Exception:
            # Fallback gracefully if embedding service is offline
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

            # 2. KNN Search on vec0 virtual table (capped by *cap*)
            vec_json = json.dumps(embedding)
            rows = conn.execute(
                """
                SELECT f.entity_id, f.semantic_relationship
                FROM fact_store f
                JOIN vec_facts v ON v.rowid = f.rowid
                WHERE v.embedding MATCH ? AND k = ?
                ORDER BY distance
                """,
                (vec_json, cap),
            ).fetchall()

            # 3. Stream clusters to Redis Hot Graph
            pipeline = self.r.pipeline()
            for entity_id, semantic_relationship in rows:
                pipeline.hset(
                    f"hot_graph:{topic_id}:{entity_id}",
                    mapping={"relations": semantic_relationship or ""},
                )
                self.gas_used += _GAS_PER_LOAD
                self._loads += 1
            pipeline.execute()
            conn.close()
        except Exception as exc:
            logger.warning("load_hot_graph DB failure for %s: %s", topic_id, exc)

    def evict_hot_graph(self, topic_id: str) -> None:
        """Evict the Knowledge Sub-Graph from Redis (or just delete if read-only)."""
        keys = self.r.keys(f"hot_graph:{topic_id}:*")
        if keys:
            self.r.delete(*keys)
            self.gas_used += _GAS_PER_EVICT * len(keys)
            self._evictions += len(keys)

    def get_stats(self) -> dict[str, Any]:
        """Return gas and cluster metrics for observability."""
        return {
            "gas_used": self.gas_used,
            "clusters_loaded": self._loads,
            "clusters_evicted": self._evictions,
        }
