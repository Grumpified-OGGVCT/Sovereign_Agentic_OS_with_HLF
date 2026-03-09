"""
HLF-Anchored Memory Nodes — Memory segments tagged with HLF intent provenance.

Every memory fragment remembers WHERE it came from: which HLF intent
created it, what confidence the originating agent had, and how many
times it's been accessed. This creates a provenance-traceable memory
system where every fact can be traced back to the HLF pipeline that
produced it.

Features:
  - Memory anchors bind content to HLF intent hashes
  - Forgetting curve (30-day decay, configurable)
  - Access pattern tracking for relevance scoring
  - Cold storage archival for stale nodes
  - Provenance queries: "which intent created this fact?"

Usage:
    store = AnchoredMemoryStore()
    anchor = store.add(
        content="seccomp.json is compliant",
        hlf_intent_hash="sha256:abc123",
        agent_id="sentinel",
    )
    results = store.query_by_provenance("sha256:abc123")
    store.decay_pass()  # Apply forgetting curve
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


# ─── Storage Tier ───────────────────────────────────────────────────────────

class StorageTier(Enum):
    HOT = "hot"          # In-memory, high relevance
    WARM = "warm"        # In-memory but decaying
    COLD = "cold"        # Archived, low relevance
    PRUNED = "pruned"    # Removed from active store


# ─── Memory Anchor ──────────────────────────────────────────────────────────

@dataclass
class MemoryAnchor:
    """A memory node anchored to an HLF intent provenance chain."""

    anchor_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    content: str = ""
    content_hash: str = ""

    # Provenance
    hlf_intent_hash: str = ""       # SHA-256 of the originating HLF intent
    agent_id: str = ""              # Which agent produced this
    pipeline_stage: str = ""        # compile / execute / verify

    # Metadata
    confidence: float = 1.0         # Source confidence [0.0, 1.0]
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0

    # Decay
    relevance_score: float = 1.0    # Decays over time
    tier: StorageTier = StorageTier.HOT
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.content_hash and self.content:
            self.content_hash = hashlib.sha256(
                self.content.encode()
            ).hexdigest()[:16]

    def touch(self) -> None:
        """Record an access, resetting decay."""
        self.access_count += 1
        self.last_accessed = time.time()
        # Access boosts relevance back toward 1.0
        self.relevance_score = min(1.0, self.relevance_score + 0.1)
        if self.tier == StorageTier.WARM:
            self.tier = StorageTier.HOT

    @property
    def age_days(self) -> float:
        return (time.time() - self.created_at) / 86400

    @property
    def idle_days(self) -> float:
        return (time.time() - self.last_accessed) / 86400

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor_id": self.anchor_id,
            "content": self.content,
            "content_hash": self.content_hash,
            "hlf_intent_hash": self.hlf_intent_hash,
            "agent_id": self.agent_id,
            "pipeline_stage": self.pipeline_stage,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
            "access_count": self.access_count,
            "relevance_score": self.relevance_score,
            "tier": self.tier.value,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryAnchor:
        data = dict(data)
        data["tier"] = StorageTier(data.get("tier", "hot"))
        return cls(**data)


# ─── Anchored Memory Store ──────────────────────────────────────────────────

class AnchoredMemoryStore:
    """In-memory store for HLF-anchored memory nodes.

    Supports provenance queries, forgetting curves, and cold archival.
    """

    def __init__(
        self,
        *,
        decay_half_life_days: float = 15.0,
        cold_threshold: float = 0.3,
        prune_threshold: float = 0.05,
        max_idle_days: float = 30.0,
    ) -> None:
        self._nodes: dict[str, MemoryAnchor] = {}
        self._provenance_index: dict[str, list[str]] = {}  # hash → anchor_ids
        self._agent_index: dict[str, list[str]] = {}        # agent → anchor_ids
        self._cold_archive: list[dict[str, Any]] = []

        # Decay config
        self._half_life = decay_half_life_days
        self._cold_threshold = cold_threshold
        self._prune_threshold = prune_threshold
        self._max_idle_days = max_idle_days

    @property
    def size(self) -> int:
        return len(self._nodes)

    @property
    def cold_archive_size(self) -> int:
        return len(self._cold_archive)

    def add(
        self,
        content: str,
        hlf_intent_hash: str,
        agent_id: str = "",
        *,
        confidence: float = 1.0,
        pipeline_stage: str = "execute",
        tags: list[str] | None = None,
    ) -> MemoryAnchor:
        """Add a new memory anchor."""
        anchor = MemoryAnchor(
            content=content,
            hlf_intent_hash=hlf_intent_hash,
            agent_id=agent_id,
            confidence=confidence,
            pipeline_stage=pipeline_stage,
            tags=tags or [],
        )
        self._nodes[anchor.anchor_id] = anchor

        # Index by provenance
        self._provenance_index.setdefault(hlf_intent_hash, []).append(
            anchor.anchor_id
        )
        # Index by agent
        if agent_id:
            self._agent_index.setdefault(agent_id, []).append(
                anchor.anchor_id
            )
        return anchor

    def get(self, anchor_id: str) -> MemoryAnchor | None:
        node = self._nodes.get(anchor_id)
        if node:
            node.touch()
        return node

    def query_by_provenance(
        self, hlf_intent_hash: str
    ) -> list[MemoryAnchor]:
        """Find all memory nodes created by a specific HLF intent."""
        ids = self._provenance_index.get(hlf_intent_hash, [])
        results = []
        for aid in ids:
            node = self._nodes.get(aid)
            if node:
                node.touch()
                results.append(node)
        return results

    def query_by_agent(self, agent_id: str) -> list[MemoryAnchor]:
        """Find all memory nodes created by a specific agent."""
        ids = self._agent_index.get(agent_id, [])
        return [self._nodes[aid] for aid in ids if aid in self._nodes]

    def query_by_tag(self, tag: str) -> list[MemoryAnchor]:
        """Find all memory nodes with a specific tag."""
        return [n for n in self._nodes.values() if tag in n.tags]

    def query_hot(self, *, limit: int = 50) -> list[MemoryAnchor]:
        """Get the most relevant active memories."""
        hot = [
            n for n in self._nodes.values()
            if n.tier in (StorageTier.HOT, StorageTier.WARM)
        ]
        hot.sort(key=lambda n: n.relevance_score, reverse=True)
        return hot[:limit]

    def decay_pass(self) -> dict[str, int]:
        """Apply forgetting curve to all nodes.

        Returns stats: {decayed, demoted_to_warm, demoted_to_cold, pruned}.
        """
        stats = {"decayed": 0, "demoted_warm": 0, "demoted_cold": 0, "pruned": 0}

        for anchor_id in list(self._nodes.keys()):
            node = self._nodes[anchor_id]
            if node.tier == StorageTier.PRUNED:
                continue

            # Exponential decay: relevance = e^(-λt)
            # λ = ln(2) / half_life
            lam = math.log(2) / self._half_life
            idle = node.idle_days
            decay_factor = math.exp(-lam * idle)
            node.relevance_score = node.confidence * decay_factor
            stats["decayed"] += 1

            # Tier transitions
            if node.relevance_score < self._prune_threshold:
                self._archive_node(node)
                stats["pruned"] += 1
            elif node.relevance_score < self._cold_threshold:
                if node.tier != StorageTier.COLD:
                    node.tier = StorageTier.COLD
                    stats["demoted_cold"] += 1
            elif node.relevance_score < 0.7:
                if node.tier == StorageTier.HOT:
                    node.tier = StorageTier.WARM
                    stats["demoted_warm"] += 1

        return stats

    def prune_idle(self) -> int:
        """Force-prune nodes idle beyond max_idle_days."""
        pruned = 0
        for anchor_id in list(self._nodes.keys()):
            node = self._nodes[anchor_id]
            if node.idle_days > self._max_idle_days:
                self._archive_node(node)
                pruned += 1
        return pruned

    def _archive_node(self, node: MemoryAnchor) -> None:
        """Move a node to cold archive and remove from active store."""
        node.tier = StorageTier.PRUNED
        self._cold_archive.append(node.to_dict())
        del self._nodes[node.anchor_id]
        # Clean indices
        prov_list = self._provenance_index.get(node.hlf_intent_hash, [])
        if node.anchor_id in prov_list:
            prov_list.remove(node.anchor_id)
        agent_list = self._agent_index.get(node.agent_id, [])
        if node.anchor_id in agent_list:
            agent_list.remove(node.anchor_id)

    def get_report(self) -> dict[str, Any]:
        """Get store statistics."""
        tiers = {"hot": 0, "warm": 0, "cold": 0}
        for node in self._nodes.values():
            if node.tier.value in tiers:
                tiers[node.tier.value] += 1
        agents = {}
        for node in self._nodes.values():
            agents[node.agent_id] = agents.get(node.agent_id, 0) + 1
        return {
            "total_active": self.size,
            "total_archived": self.cold_archive_size,
            "by_tier": tiers,
            "by_agent": agents,
            "avg_relevance": (
                sum(n.relevance_score for n in self._nodes.values()) / max(1, self.size)
            ),
        }

    def save(self, path: Path | str) -> None:
        data = {
            "nodes": [n.to_dict() for n in self._nodes.values()],
            "cold_archive": self._cold_archive,
        }
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path | str) -> AnchoredMemoryStore:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        store = cls()
        for nd in data.get("nodes", []):
            anchor = MemoryAnchor.from_dict(nd)
            store._nodes[anchor.anchor_id] = anchor
            store._provenance_index.setdefault(
                anchor.hlf_intent_hash, []
            ).append(anchor.anchor_id)
            if anchor.agent_id:
                store._agent_index.setdefault(
                    anchor.agent_id, []
                ).append(anchor.anchor_id)
        store._cold_archive = data.get("cold_archive", [])
        return store
