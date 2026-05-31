"""Oracle-lens tests: prediction logic, memory durability, risk assessment.

Covers the additive Oracle additions to:
  - agents/core/memory_anchor.py
  - agents/core/memory_scribe.py
"""

from __future__ import annotations

import json
import math
import sqlite3
import tempfile
import time

import pytest

from agents.core.memory_anchor import (
    AnchoredMemoryStore,
    MemoryAnchor,
    StorageTier,
)


# ─── Helper ──────────────────────────────────────────────────────────────────

def _make_scribe_db(*, wal: bool = False) -> sqlite3.Connection:
    """Create a DB with the full memory_scribe schema (no redis).

    Uses ``:memory:`` by default.  Pass ``wal=True`` to create a temp file-based
    database so that WAL mode can actually be enabled (SQLite in-memory databases
    always report journal_mode='memory').
    """
    if wal:
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name, check_same_thread=False)
    else:
        conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS fact_store (
            entity_id TEXT NOT NULL,
            vector_embedding TEXT,
            semantic_relationship TEXT,
            confidence_score REAL NOT NULL DEFAULT 0.0,
            last_accessed REAL,
            risk_tag TEXT NOT NULL DEFAULT 'normal'
        );
        CREATE TABLE IF NOT EXISTS vec_facts (
            rowid INTEGER PRIMARY KEY,
            embedding TEXT
        );
    """)
    conn.commit()
    return conn


# ─── MemoryAnchor prediction tests ───────────────────────────────────────────

class TestMemoryAnchorOracle:
    def test_risk_score_default(self):
        anchor = MemoryAnchor(content="test", hlf_intent_hash="h1")
        assert anchor.risk_score == 0.0

    def test_risk_score_updates_on_touch(self):
        anchor = MemoryAnchor(content="test", hlf_intent_hash="h1", confidence=1.0)
        for _ in range(10):
            anchor.touch()
        # After 10 touches demand_weight = min(1.0, 10/10) = 1.0
        assert anchor.risk_score == pytest.approx(1.0, abs=1e-4)

    def test_risk_score_partial_demand(self):
        anchor = MemoryAnchor(content="test", hlf_intent_hash="h1", confidence=0.8)
        anchor.touch()  # access_count = 1 → demand_weight = 0.1
        assert anchor.risk_score == pytest.approx(0.8 * 0.1, abs=1e-4)

    def test_risk_score_in_to_dict(self):
        anchor = MemoryAnchor(content="x", hlf_intent_hash="h")
        anchor.touch()
        d = anchor.to_dict()
        assert "risk_score" in d
        assert isinstance(d["risk_score"], float)

    def test_from_dict_backward_compat_no_risk_score(self):
        """Dicts serialised before the Oracle update must load without error."""
        anchor = MemoryAnchor(content="x", hlf_intent_hash="h")
        d = anchor.to_dict()
        del d["risk_score"]  # simulate old serialisation
        restored = MemoryAnchor.from_dict(d)
        assert restored.risk_score == 0.0  # default applied

    def test_from_dict_roundtrip_preserves_risk_score(self):
        anchor = MemoryAnchor(content="y", hlf_intent_hash="h2")
        anchor.touch()
        anchor.touch()
        restored = MemoryAnchor.from_dict(anchor.to_dict())
        assert restored.risk_score == pytest.approx(anchor.risk_score, abs=1e-6)

    def test_predict_relevance_future(self):
        anchor = MemoryAnchor(content="f", hlf_intent_hash="h", confidence=1.0)
        # Freshly created — idle ≈ 0. In 15 days relevance should be ~0.5.
        pred = anchor.predict_relevance(15.0)
        assert 0.45 < pred < 0.55

    def test_predict_relevance_zero_days(self):
        anchor = MemoryAnchor(content="f", hlf_intent_hash="h", confidence=0.9)
        # zero days ahead ≈ current state (idle ≈ 0 for a fresh anchor)
        pred = anchor.predict_relevance(0.0)
        assert pred == pytest.approx(0.9, abs=0.05)

    def test_predict_relevance_large_horizon(self):
        anchor = MemoryAnchor(content="f", hlf_intent_hash="h", confidence=1.0)
        pred_far = anchor.predict_relevance(90.0)
        assert pred_far < 0.1  # negligible after 90 days

    def test_predict_relevance_monotone_decreasing(self):
        anchor = MemoryAnchor(content="f", hlf_intent_hash="h", confidence=1.0)
        preds = [anchor.predict_relevance(d) for d in [0, 7, 15, 30, 60]]
        assert all(preds[i] > preds[i + 1] for i in range(len(preds) - 1))

    def test_predict_staleness_fresh_anchor(self):
        anchor = MemoryAnchor(content="f", hlf_intent_hash="h", confidence=1.0)
        days = anchor.predict_staleness_days(cold_threshold=0.3)
        # Fresh anchor with confidence=1.0 takes many days to reach 0.3
        assert days > 10.0

    def test_predict_staleness_already_cold(self):
        anchor = MemoryAnchor(content="f", hlf_intent_hash="h", confidence=1.0)
        anchor.relevance_score = 0.1
        anchor.last_accessed = time.time() - (50 * 86400)
        days = anchor.predict_staleness_days(cold_threshold=0.3)
        assert days == 0.0  # already at or below threshold

    def test_predict_staleness_half_life_respected(self):
        anchor = MemoryAnchor(content="f", hlf_intent_hash="h", confidence=1.0)
        # With half_life=15, threshold=0.5, the anchor crosses 0.5 in ~15 days
        days = anchor.predict_staleness_days(cold_threshold=0.5, half_life_days=15.0)
        assert 10.0 < days < 20.0


# ─── AnchoredMemoryStore Oracle API ──────────────────────────────────────────

class TestAnchoredMemoryStoreOracle:
    def setup_method(self):
        self.store = AnchoredMemoryStore(
            decay_half_life_days=15,
            cold_threshold=0.3,
            prune_threshold=0.05,
            max_idle_days=30,
        )

    def test_query_at_risk_empty(self):
        result = self.store.query_at_risk()
        assert result == []

    def test_query_at_risk_no_touches(self):
        self.store.add("fact", "h1", "sentinel")
        # risk_score = 0.0 on freshly added anchor with no touches
        result = self.store.query_at_risk(risk_threshold=0.5)
        assert result == []

    def test_query_at_risk_with_touches(self):
        anchor = self.store.add("fact", "h1", "sentinel", confidence=1.0)
        for _ in range(6):
            anchor.touch()
        # demand_weight = 0.6, confidence = 1.0 → risk_score = 0.6
        result = self.store.query_at_risk(risk_threshold=0.5)
        assert anchor in result

    def test_query_at_risk_threshold_boundary(self):
        anchor = self.store.add("fact", "h1", confidence=0.8)
        for _ in range(5):
            anchor.touch()
        # demand_weight = 0.5, risk_score = 0.8 * 0.5 = 0.4
        above = self.store.query_at_risk(risk_threshold=0.5)
        below = self.store.query_at_risk(risk_threshold=0.3)
        assert anchor not in above
        assert anchor in below

    def test_oracle_trend_report_empty(self):
        report = self.store.oracle_trend_report()
        assert report["total"] == 0
        assert report["at_risk"] == 0
        assert report["avg_risk_score"] == 0.0
        assert report["predicted_cold_7d"] == 0
        assert report["predicted_cold_30d"] == 0
        assert report["high_access_stale"] == []

    def test_oracle_trend_report_counts(self):
        self.store.add("fact1", "h1")
        a2 = self.store.add("fact2", "h2")
        # Make a2 idle for 40 days WITHOUT calling touch() (which would reset last_accessed)
        a2.last_accessed = time.time() - (40 * 86400)
        report = self.store.oracle_trend_report()
        assert report["total"] == 2
        # a2 has been idle 40 days → will be cold in 7d
        assert report["predicted_cold_7d"] >= 1

    def test_oracle_trend_report_high_access_stale(self):
        anchor = self.store.add("fact", "h1")
        # Simulate high access + low relevance
        anchor.access_count = 5
        anchor.relevance_score = 0.3
        report = self.store.oracle_trend_report()
        assert anchor.anchor_id in report["high_access_stale"]

    def test_get_report_includes_oracle_risk(self):
        self.store.add("a", "h1")
        report = self.store.get_report()
        assert "oracle_risk" in report
        assert "avg_risk_score" in report["oracle_risk"]
        assert "at_risk_count" in report["oracle_risk"]

    def test_save_and_load_preserves_risk_score(self, tmp_path):
        anchor = self.store.add("fact", "h1", confidence=1.0)
        for _ in range(8):
            anchor.touch()
        orig_risk = anchor.risk_score
        path = tmp_path / "oracle_memory.json"
        self.store.save(path)
        loaded = AnchoredMemoryStore.load(path)
        # Access directly via _nodes to avoid touch() mutating risk_score
        # (get() calls touch() which increments access_count and recalculates risk)
        loaded_anchor = loaded._nodes.get(anchor.anchor_id)
        assert loaded_anchor is not None
        assert loaded_anchor.risk_score == pytest.approx(orig_risk, abs=1e-6)


# ─── memory_scribe Oracle functions ──────────────────────────────────────────

class TestCheckDurability:
    def test_wal_mode_detected(self):
        from agents.core.memory_scribe import check_durability
        conn = _make_scribe_db(wal=True)
        report = check_durability(conn)
        assert report["wal_mode"] is True

    def test_empty_store_is_durable(self):
        from agents.core.memory_scribe import check_durability
        conn = _make_scribe_db(wal=True)
        report = check_durability(conn)
        assert report["fact_count"] == 0
        assert report["orphaned_vec_entries"] == 0
        assert report["durability_ok"] is True

    def test_unindexed_facts_counted(self):
        from agents.core.memory_scribe import check_durability
        conn = _make_scribe_db()
        conn.execute(
            "INSERT INTO fact_store (entity_id, vector_embedding, semantic_relationship, confidence_score) "
            "VALUES (?, NULL, ?, ?)",
            ("e1", "rel", 0.9),
        )
        conn.commit()
        report = check_durability(conn)
        assert report["unindexed_facts"] == 1
        assert report["fact_count"] == 1

    def test_durability_ok_with_vectorized_fact(self):
        from agents.core.memory_scribe import check_durability
        conn = _make_scribe_db(wal=True)
        vec = json.dumps([0.1] * 768)
        conn.execute(
            "INSERT INTO fact_store (entity_id, vector_embedding, semantic_relationship, confidence_score) "
            "VALUES (?, ?, ?, ?)",
            ("e1", vec, "rel", 0.9),
        )
        conn.commit()
        report = check_durability(conn)
        assert report["fact_count"] == 1
        assert report["unindexed_facts"] == 0
        assert report["durability_ok"] is True


class TestRiskAssessmentFacts:
    def test_empty_store(self):
        from agents.core.memory_scribe import risk_assessment_facts
        conn = _make_scribe_db()
        report = risk_assessment_facts(conn)
        assert report["risk_level"] == "none"
        assert report["fact_count"] == 0

    def test_low_risk_all_high_confidence(self):
        from agents.core.memory_scribe import risk_assessment_facts
        conn = _make_scribe_db()
        vec = json.dumps([0.1] * 768)
        for i in range(5):
            conn.execute(
                "INSERT INTO fact_store (entity_id, vector_embedding, semantic_relationship, confidence_score) "
                "VALUES (?, ?, ?, ?)",
                (f"e{i}", vec, "rel", 0.95),
            )
        conn.commit()
        report = risk_assessment_facts(conn)
        assert report["risk_level"] == "low"
        assert report["low_confidence_ratio"] == 0.0
        assert report["duplicate_entities"] == 0

    def test_high_risk_unvectorized_confident_fact(self):
        from agents.core.memory_scribe import risk_assessment_facts
        conn = _make_scribe_db()
        conn.execute(
            "INSERT INTO fact_store (entity_id, vector_embedding, semantic_relationship, confidence_score) "
            "VALUES (?, NULL, ?, ?)",
            ("e1", "rel", 0.95),  # high confidence but no vector
        )
        conn.commit()
        report = risk_assessment_facts(conn)
        assert report["risk_level"] == "high"
        assert report["high_conf_unvectorized"] == 1

    def test_high_risk_duplicate_entities(self):
        from agents.core.memory_scribe import risk_assessment_facts
        conn = _make_scribe_db()
        for _ in range(2):
            conn.execute(
                "INSERT INTO fact_store (entity_id, vector_embedding, semantic_relationship, confidence_score) "
                "VALUES (?, NULL, ?, ?)",
                ("dup-entity", "rel", 0.5),
            )
        conn.commit()
        report = risk_assessment_facts(conn)
        assert report["duplicate_entities"] == 1
        assert report["risk_level"] == "high"

    def test_medium_risk_moderate_noise(self):
        from agents.core.memory_scribe import risk_assessment_facts
        conn = _make_scribe_db()
        vec = json.dumps([0.1] * 768)
        # 3 high-confidence, 1 low-confidence (25 % noise → medium)
        for i in range(3):
            conn.execute(
                "INSERT INTO fact_store (entity_id, vector_embedding, semantic_relationship, confidence_score) "
                "VALUES (?, ?, ?, ?)",
                (f"good{i}", vec, "rel", 0.9),
            )
        conn.execute(
            "INSERT INTO fact_store (entity_id, vector_embedding, semantic_relationship, confidence_score) "
            "VALUES (?, ?, ?, ?)",
            ("bad0", vec, "rel", 0.1),
        )
        conn.commit()
        report = risk_assessment_facts(conn)
        assert report["risk_level"] == "medium"

    def test_high_risk_majority_low_confidence(self):
        from agents.core.memory_scribe import risk_assessment_facts
        conn = _make_scribe_db()
        vec = json.dumps([0.1] * 768)
        # 1 good, 9 bad → 90 % noise
        conn.execute(
            "INSERT INTO fact_store (entity_id, vector_embedding, semantic_relationship, confidence_score) "
            "VALUES (?, ?, ?, ?)",
            ("good", vec, "rel", 0.9),
        )
        for i in range(9):
            conn.execute(
                "INSERT INTO fact_store (entity_id, vector_embedding, semantic_relationship, confidence_score) "
                "VALUES (?, ?, ?, ?)",
                (f"bad{i}", vec, "rel", 0.05),
            )
        conn.commit()
        report = risk_assessment_facts(conn)
        assert report["risk_level"] == "high"


class TestWriteFactOracle:
    def test_write_fact_stores_last_accessed(self):
        from agents.core.memory_scribe import write_fact
        conn = _make_scribe_db()
        before = time.time()
        write_fact(conn, "e1", None, "rel", 0.8)
        after = time.time()
        row = conn.execute(
            "SELECT last_accessed FROM fact_store WHERE entity_id = 'e1'"
        ).fetchone()
        assert row is not None
        assert before <= row[0] <= after

    def test_write_fact_default_risk_tag(self):
        from agents.core.memory_scribe import write_fact
        conn = _make_scribe_db()
        write_fact(conn, "e1", None, "rel", 0.8)
        row = conn.execute(
            "SELECT risk_tag FROM fact_store WHERE entity_id = 'e1'"
        ).fetchone()
        assert row[0] == "normal"

    def test_write_fact_custom_risk_tag(self):
        from agents.core.memory_scribe import write_fact
        conn = _make_scribe_db()
        write_fact(conn, "e2", None, "rel", 0.5, risk_tag="high")
        row = conn.execute(
            "SELECT risk_tag FROM fact_store WHERE entity_id = 'e2'"
        ).fetchone()
        assert row[0] == "high"

    def test_write_fact_backward_compatible_no_risk_tag(self):
        """Old call sites that omit risk_tag still work via default='normal'."""
        from agents.core.memory_scribe import write_fact
        conn = _make_scribe_db()
        # Omit risk_tag entirely
        write_fact(conn, "e3", None, "rel", 0.7)
        row = conn.execute(
            "SELECT entity_id, risk_tag FROM fact_store WHERE entity_id = 'e3'"
        ).fetchone()
        assert row[0] == "e3"
        assert row[1] == "normal"


class TestMigrateSchema:
    def test_migrate_adds_columns_to_existing_table(self):
        """_migrate_schema must be idempotent and add columns to legacy tables."""
        from agents.core.memory_scribe import _migrate_schema
        # Create a legacy-style fact_store without the new columns
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE fact_store (
                entity_id TEXT NOT NULL,
                vector_embedding TEXT,
                semantic_relationship TEXT,
                confidence_score REAL NOT NULL DEFAULT 0.0
            );
        """)
        conn.commit()
        _migrate_schema(conn)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(fact_store)").fetchall()}
        assert "last_accessed" in cols
        assert "risk_tag" in cols

    def test_migrate_is_idempotent(self):
        """Running _migrate_schema twice must not raise."""
        from agents.core.memory_scribe import _migrate_schema
        conn = _make_scribe_db()
        _migrate_schema(conn)
        _migrate_schema(conn)  # second call should be silent
