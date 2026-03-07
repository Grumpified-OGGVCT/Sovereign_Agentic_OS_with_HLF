"""
HLF Memory Node — Structured HLF-Anchored Memory for Infinite RAG.

Each memory node stores an agent's memory as a validated, compiled HLF AST
rather than a raw text blob. This enables:
  - Structured Persistence: Memories are mathematically compressed ASTs
  - Deduplication: Content-hash based identification prevents redundant storage
  - Conflict Resolution: Confidence-weighted merge using provenance metadata
  - Persistent Error Correction: Correction chains via parent_hash linking
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from hlf.hlfc import compile as hlfc_compile


@dataclass
class HLFMemoryNode:
    """An HLF-anchored memory node for the Infinite RAG system.

    Each node represents a single memory entry stored as a compiled HLF AST,
    with provenance tracking, confidence scoring, and deduplication support.

    Attributes:
        node_id:          Unique identifier (UUID4)
        entity_id:        The logical entity this memory belongs to (e.g., "session_results")
        hlf_source:       Original HLF source text
        hlf_ast:          Compiled JSON AST (output of hlfc.compile())
        content_hash:     SHA-256 of the canonical JSON AST (for deduplication)
        confidence:       Confidence score [0.0, 1.0] — higher = more reliable
        provenance_agent: Agent that created this memory
        provenance_ts:    Unix timestamp of creation
        correction_count: Number of times this memory has been corrected
        parent_hash:      Content hash of the memory this corrects (None if original)
        last_accessed:    Unix timestamp of last retrieval
        created_at:       Unix timestamp of creation
    """
    node_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    entity_id: str = ""
    hlf_source: str = ""
    hlf_ast: dict = field(default_factory=dict)
    content_hash: str = ""
    confidence: float = 0.5
    provenance_agent: str = "system"
    provenance_ts: float = field(default_factory=time.time)
    correction_count: int = 0
    parent_hash: str | None = None
    last_accessed: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)

    @classmethod
    def from_hlf_source(
        cls,
        source: str,
        entity_id: str,
        agent: str = "system",
        confidence: float = 0.5,
    ) -> HLFMemoryNode:
        """Create a memory node from raw HLF source text.

        Compiles the source through the full 4-pass hlfc pipeline, computes
        a content hash for deduplication, and wraps everything in a node.

        Args:
            source:     HLF source text (must include [HLF-v2] header and Ω terminator)
            entity_id:  Logical entity name (e.g., "session_results", "agent_observations")
            agent:      Name of the agent creating this memory
            confidence: Confidence score [0.0, 1.0]

        Returns:
            HLFMemoryNode with compiled AST and content hash.

        Raises:
            HlfSyntaxError: If the source fails to compile.
        """
        if not source.strip():
            raise ValueError("Cannot create memory node from empty source")

        ast = hlfc_compile(source)
        content_hash = _compute_hash(ast)
        now = time.time()

        return cls(
            entity_id=entity_id,
            hlf_source=source,
            hlf_ast=ast,
            content_hash=content_hash,
            confidence=confidence,
            provenance_agent=agent,
            provenance_ts=now,
            last_accessed=now,
            created_at=now,
        )

    @classmethod
    def from_ast(
        cls,
        ast: dict,
        entity_id: str,
        agent: str = "system",
        confidence: float = 0.5,
        source: str = "",
    ) -> HLFMemoryNode:
        """Create a memory node from a pre-compiled HLF AST.

        Useful when the AST was produced by runtime execution and needs
        to be persisted without re-compilation.

        Args:
            ast:        Compiled HLF AST dict.
            entity_id:  Logical entity name.
            agent:      Name of the agent creating this memory.
            confidence: Confidence score [0.0, 1.0].
            source:     Optional original source (for debugging).

        Returns:
            HLFMemoryNode with content hash computed from AST.
        """
        content_hash = _compute_hash(ast)
        now = time.time()

        return cls(
            entity_id=entity_id,
            hlf_source=source,
            hlf_ast=ast,
            content_hash=content_hash,
            confidence=confidence,
            provenance_agent=agent,
            provenance_ts=now,
            last_accessed=now,
            created_at=now,
        )

    def matches_content(self, other: HLFMemoryNode) -> bool:
        """Check if another node has identical content (dedup check)."""
        return self.content_hash == other.content_hash

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "node_id": self.node_id,
            "entity_id": self.entity_id,
            "hlf_source": self.hlf_source,
            "hlf_ast": self.hlf_ast,
            "content_hash": self.content_hash,
            "confidence": self.confidence,
            "provenance_agent": self.provenance_agent,
            "provenance_ts": self.provenance_ts,
            "correction_count": self.correction_count,
            "parent_hash": self.parent_hash,
            "last_accessed": self.last_accessed,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HLFMemoryNode:
        """Deserialize from a dict."""
        return cls(
            node_id=data.get("node_id", str(uuid.uuid4())),
            entity_id=data.get("entity_id", ""),
            hlf_source=data.get("hlf_source", ""),
            hlf_ast=data.get("hlf_ast", {}),
            content_hash=data.get("content_hash", ""),
            confidence=data.get("confidence", 0.5),
            provenance_agent=data.get("provenance_agent", "system"),
            provenance_ts=data.get("provenance_ts", time.time()),
            correction_count=data.get("correction_count", 0),
            parent_hash=data.get("parent_hash"),
            last_accessed=data.get("last_accessed", time.time()),
            created_at=data.get("created_at", time.time()),
        )

    def __repr__(self) -> str:
        return (
            f"HLFMemoryNode(id={self.node_id[:8]}..., "
            f"entity='{self.entity_id}', "
            f"confidence={self.confidence}, "
            f"agent='{self.provenance_agent}', "
            f"corrections={self.correction_count})"
        )


def _compute_hash(ast: dict) -> str:
    """Compute a deterministic SHA-256 hash of a compiled HLF AST.

    Uses sorted-key canonical JSON to ensure identical ASTs always
    produce the same hash regardless of dict ordering.
    """
    canonical = json.dumps(ast, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
