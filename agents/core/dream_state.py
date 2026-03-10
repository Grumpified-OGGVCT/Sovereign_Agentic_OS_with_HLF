"""
Dream State Engine — Nightly context compression.

From Phase 4.3 of the Master Build Plan: compress rolling context
into synthesized rules during "sleep" cycles. New rules must not
reduce success rate (regression testing gate).

Usage:
    engine = DreamStateEngine()
    engine.add_experience("Agent Sentinel flagged seccomp violation", outcome="success")
    engine.add_experience("Agent Scribe logged 500 events", outcome="success")
    rules = engine.dream_cycle()
    print(f"Synthesized {len(rules)} rules, saved {engine.last_compression_ratio}% tokens")
"""

from __future__ import annotations

import hashlib
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Experience:
    """A recorded experience from agent operation."""
    exp_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    content: str = ""
    agent_id: str = ""
    outcome: str = "success"       # success | failure | neutral
    tokens: int = 0                # Estimated token count
    timestamp: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)


@dataclass
class SynthesizedRule:
    """A rule distilled from experiences during a dream cycle."""
    rule_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    summary: str = ""
    source_count: int = 0          # How many experiences contributed
    confidence: float = 0.0
    token_savings: int = 0         # Tokens saved by compression
    created_at: float = field(default_factory=time.time)
    source_hashes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "summary": self.summary,
            "source_count": self.source_count,
            "confidence": round(self.confidence, 3),
            "token_savings": self.token_savings,
        }


@dataclass
class DreamCycleReport:
    """Results of a single dream cycle."""
    cycle_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    experiences_processed: int = 0
    rules_synthesized: int = 0
    total_tokens_before: int = 0
    total_tokens_after: int = 0
    compression_ratio: float = 0.0
    regression_pass: bool = True
    timestamp: float = field(default_factory=time.time)
    # DB-persistence fields
    cycle_type: str = "scheduled"
    hlf_practiced: int = 0
    hlf_passed: int = 0
    hat_findings_count: int = 0
    error: str | None = None
    start_time: float = field(default_factory=time.time)
    duration_seconds: float = 0.0
    context_compressed_chars: int = 0
    context_result_chars: int = 0
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "experiences_processed": self.experiences_processed,
            "rules_synthesized": self.rules_synthesized,
            "compression_ratio": round(self.compression_ratio, 2),
            "regression_pass": self.regression_pass,
            "cycle_type": self.cycle_type,
            "hlf_practiced": self.hlf_practiced,
            "hlf_passed": self.hlf_passed,
            "hat_findings_count": self.hat_findings_count,
            "duration_seconds": self.duration_seconds,
            "summary": self.summary,
        }


class DreamStateEngine:
    """Compresses rolling context into synthesized rules.

    Simulates a "sleep" cycle where accumulated experiences are
    distilled into compact rules. Regression testing ensures
    new rules don't reduce success rate.
    """

    def __init__(
        self,
        *,
        min_experiences: int = 5,
        success_rate_floor: float = 0.9,
    ) -> None:
        self._experiences: list[Experience] = []
        self._rules: list[SynthesizedRule] = []
        self._cycle_reports: list[DreamCycleReport] = []
        self._min_experiences = min_experiences
        self._success_floor = success_rate_floor
        self._baseline_success_rate: float | None = None

    @property
    def experience_count(self) -> int:
        return len(self._experiences)

    @property
    def rule_count(self) -> int:
        return len(self._rules)

    @property
    def last_compression_ratio(self) -> float:
        if self._cycle_reports:
            return self._cycle_reports[-1].compression_ratio
        return 0.0

    def add_experience(
        self,
        content: str,
        *,
        agent_id: str = "",
        outcome: str = "success",
        tags: list[str] | None = None,
    ) -> Experience:
        """Add an experience to the rolling context."""
        tokens = len(content.split())  # Simple word-count estimate
        exp = Experience(
            content=content,
            agent_id=agent_id,
            outcome=outcome,
            tokens=tokens,
            tags=tags or [],
        )
        self._experiences.append(exp)
        return exp

    def dream_cycle(self) -> list[SynthesizedRule]:
        """Run a dream cycle: compress experiences into rules.

        Returns synthesized rules (empty if insufficient data or
        regression test fails).
        """
        if len(self._experiences) < self._min_experiences:
            return []

        # Calculate baseline success rate
        success_rate = self._compute_success_rate()
        if self._baseline_success_rate is None:
            self._baseline_success_rate = success_rate

        # Group experiences by topic (using common words)
        groups = self._cluster_experiences()

        # Synthesize rules from groups
        new_rules: list[SynthesizedRule] = []
        tokens_before = sum(e.tokens for e in self._experiences)

        for topic, exps in groups.items():
            if len(exps) < 2:
                continue
            rule = self._synthesize_rule(topic, exps)
            new_rules.append(rule)

        tokens_after = sum(len(r.summary.split()) for r in new_rules)

        # Regression check
        regression_pass = success_rate >= (
            self._baseline_success_rate * self._success_floor
        )

        if not regression_pass:
            # Don't apply rules that would reduce success
            report = DreamCycleReport(
                experiences_processed=len(self._experiences),
                rules_synthesized=0,
                total_tokens_before=tokens_before,
                total_tokens_after=tokens_before,
                compression_ratio=0.0,
                regression_pass=False,
            )
            self._cycle_reports.append(report)
            return []

        # Apply rules
        self._rules.extend(new_rules)
        compression = (
            (1 - tokens_after / max(1, tokens_before)) * 100
        )

        report = DreamCycleReport(
            experiences_processed=len(self._experiences),
            rules_synthesized=len(new_rules),
            total_tokens_before=tokens_before,
            total_tokens_after=tokens_after,
            compression_ratio=compression,
            regression_pass=True,
        )
        self._cycle_reports.append(report)

        # Clear processed experiences
        self._experiences.clear()

        return new_rules

    def _compute_success_rate(self) -> float:
        if not self._experiences:
            return 1.0
        successes = sum(1 for e in self._experiences if e.outcome == "success")
        return successes / len(self._experiences)

    def _cluster_experiences(self) -> dict[str, list[Experience]]:
        """Cluster experiences by common keywords."""
        groups: dict[str, list[Experience]] = {}

        for exp in self._experiences:
            words = exp.content.lower().split()
            # Use most common significant word as cluster key
            word_counts = Counter(
                w for w in words if len(w) > 3
            )
            if word_counts:
                topic = word_counts.most_common(1)[0][0]
            else:
                topic = "general"
            groups.setdefault(topic, []).append(exp)

        return groups

    def _synthesize_rule(
        self, topic: str, experiences: list[Experience]
    ) -> SynthesizedRule:
        """Distill multiple experiences into a single rule."""
        # Combine unique information
        contents = [e.content for e in experiences]
        source_hashes = [
            hashlib.sha256(c.encode()).hexdigest()[:8] for c in contents
        ]

        # Simple synthesis: keep unique phrases
        all_words = set()
        for c in contents:
            all_words.update(c.lower().split())

        summary = f"[{topic}] " + " ".join(sorted(all_words)[:20])
        tokens_saved = sum(e.tokens for e in experiences) - len(summary.split())

        # Confidence based on consistency of outcomes
        outcomes = [e.outcome for e in experiences]
        majority = Counter(outcomes).most_common(1)[0][1]
        confidence = majority / len(outcomes)

        return SynthesizedRule(
            summary=summary,
            source_count=len(experiences),
            confidence=confidence,
            token_savings=max(0, tokens_saved),
            source_hashes=source_hashes,
        )

    def get_rules(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self._rules]

    def get_cycle_reports(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self._cycle_reports]

    def get_stats(self) -> dict[str, Any]:
        total_savings = sum(r.token_savings for r in self._rules)
        return {
            "pending_experiences": self.experience_count,
            "total_rules": self.rule_count,
            "total_cycles": len(self._cycle_reports),
            "total_token_savings": total_savings,
            "baseline_success_rate": self._baseline_success_rate,
        }

    # ------------------------------------------------------------------
    # Catalyst additions — additive enhancements only
    # ------------------------------------------------------------------

    @property
    def experience_tag_frequency(self) -> "Counter[str]":
        """Return a Counter of tag occurrences across all pending experiences.

        Useful for discovering which topics dominate the current experience
        buffer before running a full dream cycle.
        """
        freq: Counter[str] = Counter()
        for exp in self._experiences:
            freq.update(exp.tags)
        return freq

    def rule_lookup(self, keyword: str) -> list[SynthesizedRule]:
        """Search synthesized rules by keyword (case-insensitive substring match).

        Returns every rule whose summary contains *keyword*.  Enables fast
        retrieval of relevant rules without iterating the full rule list
        in calling code.
        """
        kw = keyword.lower()
        return [r for r in self._rules if kw in r.summary.lower()]

    def prune_low_confidence_rules(self, threshold: float = 0.3) -> int:
        """Remove synthesized rules whose confidence has fallen below *threshold*.

        Returns the number of rules pruned.  Keeps the rule set lean and
        prevents stale low-quality rules from polluting future reasoning.
        """
        before = len(self._rules)
        self._rules = [r for r in self._rules if r.confidence >= threshold]
        return before - len(self._rules)

    def decay_rule_confidence(self, decay_factor: float = 0.01) -> None:
        """Apply linear confidence decay to all synthesized rules.

        Reduces each rule's confidence by *decay_factor* (clamped to 0.0).
        Mimics biological forgetting: rules that are not reinforced by fresh
        experiences gradually lose their weight, making them candidates for
        subsequent :meth:`prune_low_confidence_rules` sweeps.
        """
        for rule in self._rules:
            rule.confidence = max(0.0, rule.confidence - decay_factor)


# ---------------------------------------------------------------------------
# Module-level utility: cold archive old rolling_context rows
# ---------------------------------------------------------------------------

_ARCHIVE_AGE_DAYS = 7


def archive_old_traces(
    conn: "sqlite3.Connection",
    age_days: int = _ARCHIVE_AGE_DAYS,
) -> None:
    """Archive rolling_context rows older than *age_days* to cold storage.

    Produces a Parquet file if ``pyarrow`` is available, otherwise JSON.
    Archived rows are deleted from the database after successful write.
    """
    import json as _json
    import os as _os
    import sqlite3 as _sqlite3
    from pathlib import Path as _Path

    cutoff = time.time() - age_days * 86400

    rows = conn.execute(
        "SELECT session_id, timestamp, fifo_blob, token_count "
        "FROM rolling_context WHERE timestamp < ?",
        (cutoff,),
    ).fetchall()

    if not rows:
        return  # nothing to archive

    base_dir = _Path(_os.environ.get("BASE_DIR", "."))
    archive_dir = base_dir / "data" / "cold_archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    stamp = time.strftime("%Y%m%d_%H%M%S")

    # Try Parquet first (fast, columnar, compact)
    try:
        import pyarrow as pa          # type: ignore[import-untyped]
        import pyarrow.parquet as pq  # type: ignore[import-untyped]

        table = pa.table({
            "session_id":  [r[0] for r in rows],
            "timestamp":   [r[1] for r in rows],
            "fifo_blob":   [r[2] for r in rows],
            "token_count": [r[3] for r in rows],
        })
        out_path = archive_dir / f"traces_{stamp}.parquet"
        pq.write_table(table, str(out_path))
    except ImportError:
        # Fallback to JSON
        records = [
            {
                "session_id": r[0],
                "timestamp": r[1],
                "fifo_blob": r[2],
                "token_count": r[3],
            }
            for r in rows
        ]
        out_path = archive_dir / f"traces_{stamp}.json"
        out_path.write_text(_json.dumps(records, indent=2), encoding="utf-8")

    # Delete archived rows
    conn.execute("DELETE FROM rolling_context WHERE timestamp < ?", (cutoff,))
    conn.commit()


# ---------------------------------------------------------------------------
# DB table bootstrapping and dream-result persistence
# ---------------------------------------------------------------------------


def _ensure_dream_tables(conn: "sqlite3.Connection") -> None:
    """Create dream-related tables if they do not exist."""
    conn.executescript("""
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


def _persist_report(
    conn: "sqlite3.Connection",
    report: DreamCycleReport,
) -> int:
    """Persist a DreamCycleReport to the database. Returns the new row id."""
    conn.execute(
        "INSERT INTO dream_results "
        "(timestamp, cycle_type, hlf_practiced, hlf_passed, "
        "context_compressed_chars, context_result_chars, duration_seconds, summary) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            report.start_time,
            report.cycle_type,
            report.hlf_practiced,
            report.hlf_passed,
            report.context_compressed_chars,
            report.context_result_chars,
            report.duration_seconds,
            report.summary,
        ),
    )
    conn.commit()
    row_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return row_id


def get_last_dream_result(
    conn: "sqlite3.Connection",
) -> dict[str, Any] | None:
    """Return the most recent dream result as a dict, or None."""
    cursor = conn.execute(
        "SELECT id, timestamp, cycle_type, hlf_practiced, hlf_passed, "
        "context_compressed_chars, context_result_chars, duration_seconds, summary "
        "FROM dream_results ORDER BY id DESC LIMIT 1"
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "timestamp": row[1],
        "cycle_type": row[2],
        "hlf_practiced": row[3],
        "hlf_passed": row[4],
        "context_compressed_chars": row[5],
        "context_result_chars": row[6],
        "duration_seconds": row[7],
        "summary": row[8],
    }


