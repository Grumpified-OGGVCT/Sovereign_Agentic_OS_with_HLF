"""
Dynamic Context Pruner — RAG forgetting curve.

From Phase 4.3 of the Master Build Plan: track fact_store access
patterns and prune vectors untouched for 30+ days with low relevance.
Implements cold storage archival for stale context entries.

Usage:
    pruner = ContextPruner()
    pruner.track_access("fact_001")
    pruner.track_access("fact_002")
    report = pruner.prune_pass()
"""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContextEntry:
    """A fact in the context store with access tracking."""
    entry_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    content: str = ""
    source: str = ""
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0
    relevance: float = 1.0
    pruned: bool = False

    @property
    def idle_days(self) -> float:
        return (time.time() - self.last_accessed) / 86400

    def touch(self) -> None:
        self.access_count += 1
        self.last_accessed = time.time()
        self.relevance = min(1.0, self.relevance + 0.15)


class ContextPruner:
    """Tracks context access and prunes stale entries."""

    def __init__(
        self,
        *,
        max_idle_days: float = 30.0,
        relevance_floor: float = 0.1,
        decay_half_life: float = 10.0,
    ) -> None:
        self._entries: dict[str, ContextEntry] = {}
        self._archive: list[dict[str, Any]] = []
        self._max_idle = max_idle_days
        self._relevance_floor = relevance_floor
        self._half_life = decay_half_life

    @property
    def size(self) -> int:
        return len(self._entries)

    @property
    def archive_size(self) -> int:
        return len(self._archive)

    def add(
        self, content: str, *, source: str = "", entry_id: str = ""
    ) -> ContextEntry:
        eid = entry_id or str(uuid.uuid4())[:8]
        entry = ContextEntry(entry_id=eid, content=content, source=source)
        self._entries[eid] = entry
        return entry

    def track_access(self, entry_id: str) -> bool:
        entry = self._entries.get(entry_id)
        if entry:
            entry.touch()
            return True
        return False

    def decay_pass(self) -> int:
        """Apply exponential decay to all entries, returns count decayed."""
        lam = math.log(2) / self._half_life
        decayed = 0
        for entry in self._entries.values():
            factor = math.exp(-lam * entry.idle_days)
            entry.relevance = entry.relevance * factor
            decayed += 1
        return decayed

    def prune_pass(self) -> dict[str, int]:
        """Prune idle and low-relevance entries."""
        stats = {"pruned_idle": 0, "pruned_low_relevance": 0}
        for eid in list(self._entries.keys()):
            entry = self._entries[eid]
            if entry.idle_days > self._max_idle:
                self._archive_entry(entry)
                stats["pruned_idle"] += 1
            elif entry.relevance < self._relevance_floor:
                self._archive_entry(entry)
                stats["pruned_low_relevance"] += 1
        return stats

    def _archive_entry(self, entry: ContextEntry) -> None:
        entry.pruned = True
        self._archive.append({
            "entry_id": entry.entry_id,
            "content": entry.content,
            "relevance": entry.relevance,
            "idle_days": entry.idle_days,
            "access_count": entry.access_count,
        })
        del self._entries[entry.entry_id]

    def get_stats(self) -> dict[str, Any]:
        avg_rel = (
            sum(e.relevance for e in self._entries.values()) / max(1, self.size)
        )
        return {
            "active": self.size,
            "archived": self.archive_size,
            "avg_relevance": round(avg_rel, 4),
        }
