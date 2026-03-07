"""
Infinite RAG Engine — HLF-Anchored Memory with 3-Tier Storage.

Replaces raw JSON memory blobs with structured HLF ASTs for:
  - Structured Persistence: Memories stored as compiled, validated ASTs
  - Deduplication: SHA-256 content-hash prevents redundant storage
  - Conflict Resolution: Confidence-weighted merge with provenance
  - Persistent Error Correction: Correction chains via parent_hash linking
  - Tiered Storage: Hot (dict/cache) → Warm (SQLite) → Cold (archive)

3-Tier Architecture:
  Hot:  In-memory dict/cache — sub-ms retrieval, eviction on capacity
  Warm: SQLite WAL — persistent, vector-searchable, primary store
  Cold: JSON archive — compressed historical memories, long-term retention

Usage:
    engine = InfiniteRAGEngine(db_path="memory.db")
    engine.init_schema()
    node = HLFMemoryNode.from_hlf_source(source, "entity", "agent1", 0.9)
    engine.store(node)
    results = engine.retrieve("entity", top_k=5)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from hlf.memory_node import HLFMemoryNode, _compute_hash  # noqa: F401 — shared utility for dedup

logger = logging.getLogger(__name__)

# Default configuration
DEFAULT_HOT_CAPACITY = 256       # Max nodes in hot cache
DEFAULT_COLD_AGE_DAYS = 90       # Move to cold after N days without access
DEFAULT_DECAY_FACTOR = 0.95      # Confidence decay per cycle
DEFAULT_DECAY_WINDOW_DAYS = 30   # Decay nodes not accessed in N days


class InfiniteRAGEngine:
    """3-tier HLF-anchored memory engine.

    Hot tier:  In-memory LRU dict for fast retrieval
    Warm tier: SQLite WAL-mode database with full query support
    Cold tier: Archived rows in a separate SQLite table (compressed)
    """

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        hot_capacity: int = DEFAULT_HOT_CAPACITY,
    ) -> None:
        self.db_path = str(db_path)
        self.hot_capacity = hot_capacity
        self._hot_cache: dict[str, HLFMemoryNode] = {}
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------ #
    # Connection & Schema
    # ------------------------------------------------------------------ #

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create SQLite connection with WAL mode."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def init_schema(self) -> None:
        """Create the hlf_memory_nodes and hlf_cold_archive tables."""
        conn = self._get_conn()
        conn.executescript("""
            -- Warm tier: active memory nodes
            CREATE TABLE IF NOT EXISTS hlf_memory_nodes (
                node_id TEXT PRIMARY KEY,
                entity_id TEXT NOT NULL,
                hlf_source TEXT NOT NULL,
                hlf_ast_json TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.5,
                provenance_agent TEXT NOT NULL DEFAULT 'system',
                provenance_ts REAL NOT NULL,
                correction_count INTEGER NOT NULL DEFAULT 0,
                parent_hash TEXT,
                last_accessed REAL NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_hmn_entity
                ON hlf_memory_nodes(entity_id);
            CREATE INDEX IF NOT EXISTS idx_hmn_hash
                ON hlf_memory_nodes(content_hash);
            CREATE INDEX IF NOT EXISTS idx_hmn_confidence
                ON hlf_memory_nodes(confidence DESC);
            CREATE INDEX IF NOT EXISTS idx_hmn_accessed
                ON hlf_memory_nodes(last_accessed);

            -- Cold tier: archived historical memories
            CREATE TABLE IF NOT EXISTS hlf_cold_archive (
                node_id TEXT PRIMARY KEY,
                entity_id TEXT NOT NULL,
                hlf_source TEXT NOT NULL,
                hlf_ast_json TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                confidence REAL NOT NULL,
                provenance_agent TEXT NOT NULL,
                provenance_ts REAL NOT NULL,
                correction_count INTEGER NOT NULL DEFAULT 0,
                parent_hash TEXT,
                archived_at REAL NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_cold_entity
                ON hlf_cold_archive(entity_id);
        """)
        conn.commit()

    def close(self) -> None:
        """Close the SQLite connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------ #
    # Store
    # ------------------------------------------------------------------ #

    def store(self, node: HLFMemoryNode) -> str:
        """Store an HLF memory node. Auto-deduplicates by content_hash.

        Pipeline: Check hot cache → Check warm (SQLite) → Insert if new.

        Returns:
            node_id of the stored (or existing duplicate) node.
        """
        if not node.hlf_source.strip() and not node.hlf_ast:
            raise ValueError("Cannot store empty memory node")

        # Dedup check: hot cache first
        for cached in self._hot_cache.values():
            if cached.content_hash == node.content_hash:
                cached.last_accessed = time.time()
                if node.confidence > cached.confidence:
                    cached.confidence = node.confidence
                return cached.node_id

        # Dedup check: warm tier (SQLite)
        conn = self._get_conn()
        row = conn.execute(
            "SELECT node_id, confidence FROM hlf_memory_nodes WHERE content_hash = ?",
            (node.content_hash,),
        ).fetchone()

        if row:
            # Update confidence if higher, and bump last_accessed
            existing_id = row["node_id"]
            if node.confidence > row["confidence"]:
                conn.execute(
                    "UPDATE hlf_memory_nodes SET confidence = ?, last_accessed = ? WHERE node_id = ?",
                    (node.confidence, time.time(), existing_id),
                )
                conn.commit()
            else:
                conn.execute(
                    "UPDATE hlf_memory_nodes SET last_accessed = ? WHERE node_id = ?",
                    (time.time(), existing_id),
                )
                conn.commit()
            return existing_id

        # Insert new node into warm tier
        conn.execute(
            """INSERT INTO hlf_memory_nodes
               (node_id, entity_id, hlf_source, hlf_ast_json, content_hash,
                confidence, provenance_agent, provenance_ts, correction_count,
                parent_hash, last_accessed, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                node.node_id, node.entity_id, node.hlf_source,
                json.dumps(node.hlf_ast, sort_keys=True),
                node.content_hash, node.confidence, node.provenance_agent,
                node.provenance_ts, node.correction_count, node.parent_hash,
                node.last_accessed, node.created_at,
            ),
        )
        conn.commit()

        # Promote to hot cache
        self._hot_promote(node)

        logger.info(f"Stored memory node {node.node_id[:8]} for entity '{node.entity_id}'")
        return node.node_id

    # ------------------------------------------------------------------ #
    # Retrieve
    # ------------------------------------------------------------------ #

    def retrieve(
        self,
        entity_id: str,
        top_k: int = 5,
        min_confidence: float = 0.0,
    ) -> list[HLFMemoryNode]:
        """Retrieve memory nodes by entity_id, sorted by confidence desc + recency.

        Checks hot cache first, then warm tier. Updates last_accessed timestamps.
        """
        results: list[HLFMemoryNode] = []

        # Hot cache scan
        for node in self._hot_cache.values():
            if node.entity_id == entity_id and node.confidence >= min_confidence:
                node.last_accessed = time.time()
                results.append(node)

        # Warm tier query
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT * FROM hlf_memory_nodes
               WHERE entity_id = ? AND confidence >= ?
               ORDER BY confidence DESC, last_accessed DESC
               LIMIT ?""",
            (entity_id, min_confidence, top_k * 2),  # Over-fetch to merge with hot
        ).fetchall()

        hot_ids = {n.node_id for n in results}
        for row in rows:
            if row["node_id"] not in hot_ids:
                node = self._row_to_node(row)
                results.append(node)

        # Update last_accessed in warm tier
        now = time.time()
        node_ids = [n.node_id for n in results]
        if node_ids:
            placeholders = ",".join("?" for _ in node_ids)
            conn.execute(
                f"UPDATE hlf_memory_nodes SET last_accessed = ? WHERE node_id IN ({placeholders})",
                [now] + node_ids,
            )
            conn.commit()

        # Sort by confidence desc, then recency, and limit
        results.sort(key=lambda n: (n.confidence, n.last_accessed), reverse=True)
        return results[:top_k]

    def retrieve_all(self, top_k: int = 50) -> list[HLFMemoryNode]:
        """Retrieve all memory nodes across all entities."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT * FROM hlf_memory_nodes
               ORDER BY confidence DESC, last_accessed DESC
               LIMIT ?""",
            (top_k,),
        ).fetchall()
        return [self._row_to_node(row) for row in rows]

    # ------------------------------------------------------------------ #
    # Deduplicate
    # ------------------------------------------------------------------ #

    def deduplicate(self) -> int:
        """Scan for duplicate content_hash entries. Keep highest confidence, archive others.

        Returns:
            Number of duplicates archived to cold tier.
        """
        conn = self._get_conn()
        # Find content_hashes that appear more than once
        dupes = conn.execute(
            """SELECT content_hash, COUNT(*) as cnt
               FROM hlf_memory_nodes
               GROUP BY content_hash
               HAVING cnt > 1""",
        ).fetchall()

        archived_count = 0
        for dupe in dupes:
            content_hash = dupe["content_hash"]
            # Get all nodes with this hash, sorted by confidence desc
            rows = conn.execute(
                """SELECT * FROM hlf_memory_nodes
                   WHERE content_hash = ?
                   ORDER BY confidence DESC, last_accessed DESC""",
                (content_hash,),
            ).fetchall()

            # Keep the first (highest confidence), archive the rest
            for row in rows[1:]:
                self._archive_to_cold(row)
                conn.execute(
                    "DELETE FROM hlf_memory_nodes WHERE node_id = ?",
                    (row["node_id"],),
                )
                archived_count += 1

                # Remove from hot cache too
                self._hot_cache.pop(row["node_id"], None)

        if archived_count > 0:
            conn.commit()
            logger.info(f"Deduplicated: archived {archived_count} duplicate nodes to cold tier")

        return archived_count

    # ------------------------------------------------------------------ #
    # Correct (Persistent Error Correction)
    # ------------------------------------------------------------------ #

    def correct(
        self,
        node_id: str,
        corrected_source: str,
        agent: str,
        confidence: float = 0.8,
    ) -> str:
        """Create a correction for an existing memory node.

        The original node's confidence is decayed, and a new node is created
        with parent_hash pointing to the original. This creates an immutable
        correction chain — corrected hallucinations are permanent.

        Args:
            node_id:          ID of the node to correct
            corrected_source: New HLF source with the correction
            agent:            Agent making the correction
            confidence:       Confidence of the corrected version

        Returns:
            node_id of the new corrected node
        """
        conn = self._get_conn()

        # Fetch original node
        row = conn.execute(
            "SELECT * FROM hlf_memory_nodes WHERE node_id = ?",
            (node_id,),
        ).fetchone()

        if not row:
            raise ValueError(f"Node {node_id} not found for correction")

        original_hash = row["content_hash"]
        original_corrections = row["correction_count"]

        # Decay original's confidence
        decayed_confidence = row["confidence"] * 0.5  # Aggressive decay on correction
        conn.execute(
            """UPDATE hlf_memory_nodes
               SET confidence = ?, correction_count = ?
               WHERE node_id = ?""",
            (decayed_confidence, original_corrections + 1, node_id),
        )

        # Create corrected node
        corrected_node = HLFMemoryNode.from_hlf_source(
            source=corrected_source,
            entity_id=row["entity_id"],
            agent=agent,
            confidence=confidence,
        )
        corrected_node.parent_hash = original_hash
        corrected_node.correction_count = 0

        conn.commit()

        # Store the corrected node (will go through dedup check)
        return self.store(corrected_node)

    # ------------------------------------------------------------------ #
    # Confidence Decay (Forgetting Curve)
    # ------------------------------------------------------------------ #

    def decay_confidence(
        self,
        decay_factor: float = DEFAULT_DECAY_FACTOR,
        window_days: int = DEFAULT_DECAY_WINDOW_DAYS,
    ) -> int:
        """Apply confidence decay to nodes not accessed within the window.

        Simulates a forgetting curve — unused memories gradually lose
        confidence, making them candidates for cold archival.

        Returns:
            Number of nodes decayed.
        """
        conn = self._get_conn()
        cutoff = time.time() - (window_days * 86400)

        result = conn.execute(
            """UPDATE hlf_memory_nodes
               SET confidence = confidence * ?
               WHERE last_accessed < ? AND confidence > 0.01""",
            (decay_factor, cutoff),
        )
        decayed = result.rowcount
        conn.commit()

        if decayed > 0:
            logger.info(f"Decayed confidence on {decayed} nodes (factor={decay_factor})")

        return decayed

    # ------------------------------------------------------------------ #
    # Cold Tier Archival
    # ------------------------------------------------------------------ #

    def archive_stale(self, age_days: int = DEFAULT_COLD_AGE_DAYS) -> int:
        """Move nodes older than age_days (by last_accessed) to cold archive.

        Returns:
            Number of nodes archived.
        """
        conn = self._get_conn()
        cutoff = time.time() - (age_days * 86400)

        rows = conn.execute(
            "SELECT * FROM hlf_memory_nodes WHERE last_accessed < ?",
            (cutoff,),
        ).fetchall()

        archived = 0
        for row in rows:
            self._archive_to_cold(row)
            conn.execute(
                "DELETE FROM hlf_memory_nodes WHERE node_id = ?",
                (row["node_id"],),
            )
            self._hot_cache.pop(row["node_id"], None)
            archived += 1

        if archived:
            conn.commit()
            logger.info(f"Archived {archived} stale nodes to cold tier")

        return archived

    def retrieve_cold(self, entity_id: str, top_k: int = 10) -> list[HLFMemoryNode]:
        """Retrieve archived nodes from cold tier."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT * FROM hlf_cold_archive
               WHERE entity_id = ?
               ORDER BY confidence DESC
               LIMIT ?""",
            (entity_id, top_k),
        ).fetchall()

        return [self._cold_row_to_node(row) for row in rows]

    # ------------------------------------------------------------------ #
    # Stats
    # ------------------------------------------------------------------ #

    def stats(self) -> dict[str, Any]:
        """Get memory system statistics."""
        conn = self._get_conn()
        warm_count = conn.execute("SELECT COUNT(*) FROM hlf_memory_nodes").fetchone()[0]
        cold_count = conn.execute("SELECT COUNT(*) FROM hlf_cold_archive").fetchone()[0]

        entity_counts = conn.execute(
            """SELECT entity_id, COUNT(*) as cnt
               FROM hlf_memory_nodes
               GROUP BY entity_id
               ORDER BY cnt DESC
               LIMIT 10""",
        ).fetchall()

        return {
            "hot_count": len(self._hot_cache),
            "hot_capacity": self.hot_capacity,
            "warm_count": warm_count,
            "cold_count": cold_count,
            "total": len(self._hot_cache) + warm_count + cold_count,
            "top_entities": {row["entity_id"]: row["cnt"] for row in entity_counts},
        }

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _hot_promote(self, node: HLFMemoryNode) -> None:
        """Add node to hot cache, evicting oldest if at capacity."""
        if len(self._hot_cache) >= self.hot_capacity:
            # Evict the least-recently-accessed node
            oldest_id = min(self._hot_cache, key=lambda k: self._hot_cache[k].last_accessed)
            del self._hot_cache[oldest_id]
        self._hot_cache[node.node_id] = node

    def _archive_to_cold(self, row: sqlite3.Row) -> None:
        """Move a warm-tier row to cold archive."""
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO hlf_cold_archive
               (node_id, entity_id, hlf_source, hlf_ast_json, content_hash,
                confidence, provenance_agent, provenance_ts, correction_count,
                parent_hash, archived_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["node_id"], row["entity_id"], row["hlf_source"],
                row["hlf_ast_json"], row["content_hash"], row["confidence"],
                row["provenance_agent"], row["provenance_ts"],
                row["correction_count"], row["parent_hash"],
                time.time(), row["created_at"],
            ),
        )

    def _row_to_node(self, row: sqlite3.Row) -> HLFMemoryNode:
        """Convert a warm-tier SQLite row to an HLFMemoryNode."""
        return HLFMemoryNode(
            node_id=row["node_id"],
            entity_id=row["entity_id"],
            hlf_source=row["hlf_source"],
            hlf_ast=json.loads(row["hlf_ast_json"]),
            content_hash=row["content_hash"],
            confidence=row["confidence"],
            provenance_agent=row["provenance_agent"],
            provenance_ts=row["provenance_ts"],
            correction_count=row["correction_count"],
            parent_hash=row["parent_hash"],
            last_accessed=row["last_accessed"],
            created_at=row["created_at"],
        )

    def _cold_row_to_node(self, row: sqlite3.Row) -> HLFMemoryNode:
        """Convert a cold-tier SQLite row to an HLFMemoryNode."""
        return HLFMemoryNode(
            node_id=row["node_id"],
            entity_id=row["entity_id"],
            hlf_source=row["hlf_source"],
            hlf_ast=json.loads(row["hlf_ast_json"]),
            content_hash=row["content_hash"],
            confidence=row["confidence"],
            provenance_agent=row["provenance_agent"],
            provenance_ts=row["provenance_ts"],
            correction_count=row["correction_count"],
            parent_hash=row["parent_hash"],
            last_accessed=row.get("archived_at", time.time()) if hasattr(row, 'get') else time.time(),
            created_at=row["created_at"],
        )
