"""
Tests for the Fourteen Thinking Hats engine and Dream Mode integration.

Tests:
  - Hat definition completeness
  - Finding parsing from JSON / malformed responses
  - System context building
  - Report structure validation
  - Database persistence of findings
  - Dream cycle report structure
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helper: create a test DB with dream tables
# ---------------------------------------------------------------------------


def _make_dream_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
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
    return conn


# ===========================================================================
# Hat Engine Tests
# ===========================================================================


class TestHatDefinitions:
    """Verify all 13 hats are fully defined (14 hat numbers, #13 skipped)."""

    def test_all_hats_exist(self) -> None:
        from agents.core.hat_engine import HAT_DEFINITIONS

        expected = {
            "red", "black", "white", "yellow", "green", "blue",
            "indigo", "cyan", "purple", "orange", "silver",
            "azure", "gold",
        }
        assert set(HAT_DEFINITIONS.keys()) == expected

    def test_each_hat_has_required_fields(self) -> None:
        from agents.core.hat_engine import HAT_DEFINITIONS

        for name, defn in HAT_DEFINITIONS.items():
            assert "emoji" in defn, f"{name} missing emoji"
            assert "name" in defn, f"{name} missing name"
            assert "focus" in defn, f"{name} missing focus"
            assert "system_prompt" in defn, f"{name} missing system_prompt"
            assert len(defn["system_prompt"]) > 50, f"{name} system_prompt too short"


class TestFindingParsing:
    """Test JSON parsing of hat responses."""

    def test_parse_valid_json_array(self) -> None:
        from agents.core.hat_engine import _parse_findings

        raw = json.dumps(
            [
                {
                    "severity": "HIGH",
                    "title": "Redis has no persistence",
                    "description": "State lost on crash",
                    "recommendation": "Enable AOF",
                },
                {
                    "severity": "MEDIUM",
                    "title": "No health checks",
                    "description": "Silent failures",
                    "recommendation": "Add /health endpoints",
                },
            ]
        )
        findings = _parse_findings("green", raw)
        assert len(findings) == 2
        assert findings[0].hat == "green"
        assert findings[0].severity == "HIGH"
        assert findings[0].title == "Redis has no persistence"
        assert findings[1].severity == "MEDIUM"

    def test_parse_json_wrapped_in_markdown(self) -> None:
        from agents.core.hat_engine import _parse_findings

        raw = (
            "Here's my analysis:\n```json\n"
            + json.dumps([{"severity": "LOW", "title": "Minor issue", "description": "x", "recommendation": "y"}])
            + "\n```\nThat's all."
        )

        findings = _parse_findings("blue", raw)
        assert len(findings) == 1
        assert findings[0].severity == "LOW"

    def test_parse_malformed_response(self) -> None:
        from agents.core.hat_engine import _parse_findings

        findings = _parse_findings("red", "This is not JSON at all. Just text.")
        assert len(findings) == 1
        assert findings[0].severity == "INFO"
        assert "red" in findings[0].hat

    def test_parse_empty_response(self) -> None:
        from agents.core.hat_engine import _parse_findings

        findings = _parse_findings("black", "")
        assert len(findings) == 0


class TestHatPersistence:
    """Test database persistence of findings."""

    def test_persist_and_retrieve_findings(self, tmp_path: Path) -> None:
        from agents.core.hat_engine import HatFinding, HatReport, get_recent_findings, persist_findings

        conn = _make_dream_db(tmp_path / "test.db")

        # Create a dream cycle first
        conn.execute(
            "INSERT INTO dream_results (timestamp, cycle_type) VALUES (?, ?)",
            (time.time(), "manual"),
        )
        cycle_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Create hat reports with findings
        reports = [
            HatReport(
                hat="red",
                emoji="🔴",
                focus="chaos",
                findings=[
                    HatFinding("red", "HIGH", "Test Finding", "desc", "fix"),
                    HatFinding("red", "LOW", "Minor", "desc2", "fix2"),
                ],
            ),
            HatReport(
                hat="black",
                emoji="⚫",
                focus="security",
                findings=[
                    HatFinding("black", "CRITICAL", "Injection", "desc3", "fix3"),
                ],
            ),
        ]

        count = persist_findings(conn, cycle_id, reports)
        assert count == 3

        # Retrieve
        recent = get_recent_findings(conn, limit=10)
        assert len(recent) == 3
        # Most recent first
        assert recent[0]["hat"] == "black"
        assert recent[0]["severity"] == "CRITICAL"

        conn.close()


class TestSystemContext:
    """Test system context building for hat analysis."""

    def test_context_includes_align_rules(self, tmp_path: Path) -> None:
        from agents.core.hat_engine import _build_system_context

        # Create minimal governance structure
        gov = tmp_path / "governance"
        gov.mkdir()
        (gov / "align_ledger.yaml").write_text("version: 1.0\nrules: []")

        with patch.dict("os.environ", {"BASE_DIR": str(tmp_path)}):
            ctx = _build_system_context()

        assert "ALIGN RULES" in ctx

    def test_context_with_db_stats(self, tmp_path: Path) -> None:
        from agents.core.hat_engine import _build_system_context

        conn = _make_dream_db(tmp_path / "test.db")
        conn.execute(
            "INSERT INTO rolling_context (session_id, timestamp, fifo_blob) VALUES (?, ?, ?)",
            ("s1", time.time(), "blob"),
        )
        conn.commit()

        with patch.dict("os.environ", {"BASE_DIR": str(tmp_path)}):
            ctx = _build_system_context(conn)

        assert "DB STATS" in ctx
        assert "Rolling context rows: 1" in ctx
        conn.close()


# ===========================================================================
# Dream Cycle Report Tests
# ===========================================================================


class TestDreamCycleReport:
    """Test the DreamCycleReport dataclass structure."""

    def test_report_defaults(self) -> None:
        from agents.core.dream_state import DreamCycleReport

        report = DreamCycleReport()
        assert report.cycle_type == "scheduled"
        assert report.hlf_practiced == 0
        assert report.hlf_passed == 0
        assert report.hat_findings_count == 0
        assert report.error is None

    def test_manual_report(self) -> None:
        from agents.core.dream_state import DreamCycleReport

        report = DreamCycleReport(cycle_type="manual", hlf_practiced=5, hlf_passed=3)
        assert report.cycle_type == "manual"
        assert report.hlf_passed == 3


class TestDreamPersistence:
    """Test dream cycle result persistence."""

    def test_persist_and_retrieve(self, tmp_path: Path) -> None:
        from agents.core.dream_state import DreamCycleReport, _persist_report, get_last_dream_result

        conn = _make_dream_db(tmp_path / "test.db")

        report = DreamCycleReport(
            cycle_type="manual",
            start_time=time.time(),
            duration_seconds=12.5,
            hlf_practiced=5,
            hlf_passed=4,
            context_compressed_chars=10000,
            context_result_chars=3000,
            summary="Test cycle completed.",
        )

        cycle_id = _persist_report(conn, report)
        assert cycle_id > 0

        last = get_last_dream_result(conn)
        assert last is not None
        assert last["cycle_type"] == "manual"
        assert last["hlf_practiced"] == 5
        assert last["hlf_passed"] == 4
        assert last["duration_seconds"] == 12.5
        assert "Test cycle" in last["summary"]

        conn.close()

    def test_get_last_result_empty_db(self, tmp_path: Path) -> None:
        from agents.core.dream_state import get_last_dream_result

        conn = _make_dream_db(tmp_path / "test.db")
        result = get_last_dream_result(conn)
        assert result is None
        conn.close()


class TestEnsureDreamTables:
    """Test that dream tables are created on-demand."""

    def test_tables_created(self, tmp_path: Path) -> None:
        from agents.core.dream_state import _ensure_dream_tables

        conn = sqlite3.connect(str(tmp_path / "bare.db"))
        _ensure_dream_tables(conn)

        # Verify tables exist by inserting data
        conn.execute(
            "INSERT INTO dream_results (timestamp, cycle_type) VALUES (?, ?)",
            (time.time(), "test"),
        )
        conn.execute(
            "INSERT INTO hat_findings (dream_cycle_id, hat, severity, title, timestamp) "
            "VALUES (1, 'red', 'HIGH', 'test', ?)",
            (time.time(),),
        )
        conn.commit()
        conn.close()


# ===========================================================================
# Catalyst additions — tests for new hat_engine features
# ===========================================================================


class TestSeverityOrder:
    """SEVERITY_ORDER maps all expected labels to integer sort keys."""

    def test_severity_order_contains_all_levels(self) -> None:
        from agents.core.hat_engine import SEVERITY_ORDER

        for level in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"):
            assert level in SEVERITY_ORDER

    def test_severity_order_is_ascending(self) -> None:
        from agents.core.hat_engine import SEVERITY_ORDER

        assert SEVERITY_ORDER["CRITICAL"] < SEVERITY_ORDER["HIGH"]
        assert SEVERITY_ORDER["HIGH"] < SEVERITY_ORDER["MEDIUM"]
        assert SEVERITY_ORDER["MEDIUM"] < SEVERITY_ORDER["LOW"]
        assert SEVERITY_ORDER["LOW"] < SEVERITY_ORDER["INFO"]


class TestDeduplicateFindings:
    """deduplicate_findings() removes duplicates within a hat."""

    def test_exact_duplicates_removed(self) -> None:
        from agents.core.hat_engine import HatFinding, deduplicate_findings

        findings = [
            HatFinding("red", "HIGH", "Redis crash", "desc", "fix"),
            HatFinding("red", "HIGH", "Redis crash", "desc2", "fix2"),  # duplicate title
            HatFinding("red", "LOW", "Minor issue", "desc3", "fix3"),
        ]
        result = deduplicate_findings(findings)
        assert len(result) == 2
        # first occurrence is kept
        assert result[0].description == "desc"

    def test_same_title_different_hats_kept(self) -> None:
        from agents.core.hat_engine import HatFinding, deduplicate_findings

        findings = [
            HatFinding("red", "HIGH", "SQL injection", "desc1", "fix1"),
            HatFinding("black", "HIGH", "SQL injection", "desc2", "fix2"),
        ]
        result = deduplicate_findings(findings)
        assert len(result) == 2  # different hats → not duplicates

    def test_case_insensitive_dedup(self) -> None:
        from agents.core.hat_engine import HatFinding, deduplicate_findings

        findings = [
            HatFinding("red", "HIGH", "Redis Crash", "d1", "f1"),
            HatFinding("red", "HIGH", "redis crash", "d2", "f2"),
        ]
        result = deduplicate_findings(findings)
        assert len(result) == 1

    def test_empty_list_is_safe(self) -> None:
        from agents.core.hat_engine import deduplicate_findings

        assert deduplicate_findings([]) == []


class TestPrioritizeFindings:
    """prioritize_findings() orders by severity (CRITICAL first)."""

    def test_ordering(self) -> None:
        from agents.core.hat_engine import HatFinding, prioritize_findings

        findings = [
            HatFinding("red", "LOW", "Minor", "d", "r"),
            HatFinding("black", "CRITICAL", "RCE", "d", "r"),
            HatFinding("white", "MEDIUM", "Waste", "d", "r"),
            HatFinding("blue", "HIGH", "Gap", "d", "r"),
        ]
        result = prioritize_findings(findings)
        severities = [f.severity for f in result]
        assert severities == ["CRITICAL", "HIGH", "MEDIUM", "LOW"]

    def test_stable_sort_within_same_severity(self) -> None:
        from agents.core.hat_engine import HatFinding, prioritize_findings

        findings = [
            HatFinding("red", "HIGH", "First", "d", "r"),
            HatFinding("black", "HIGH", "Second", "d", "r"),
        ]
        result = prioritize_findings(findings)
        assert result[0].title == "First"
        assert result[1].title == "Second"

    def test_unknown_severity_goes_last(self) -> None:
        from agents.core.hat_engine import HatFinding, prioritize_findings

        findings = [
            HatFinding("red", "UNKNOWN", "X", "d", "r"),
            HatFinding("black", "CRITICAL", "Y", "d", "r"),
        ]
        result = prioritize_findings(findings)
        assert result[0].severity == "CRITICAL"


class TestSynthesizeCrossHatInsights:
    """synthesize_cross_hat_insights() finds cross-hat recurring themes."""

    def test_recurring_theme_detected(self) -> None:
        from agents.core.hat_engine import HatFinding, HatReport, synthesize_cross_hat_insights

        reports = [
            HatReport(
                hat="red",
                emoji="🔴",
                focus="chaos",
                findings=[HatFinding("red", "HIGH", "Redis failover missing", "d", "r")],
            ),
            HatReport(
                hat="black",
                emoji="⚫",
                focus="security",
                findings=[HatFinding("black", "HIGH", "Redis password not set", "d", "r")],
            ),
        ]
        insights = synthesize_cross_hat_insights(reports)
        # 'redis' appears in both hats → should generate an insight
        themes = [i.title for i in insights]
        assert any("redis" in t.lower() for t in themes)
        assert all(i.hat == "cross_hat" for i in insights)

    def test_no_theme_single_hat(self) -> None:
        from agents.core.hat_engine import HatFinding, HatReport, synthesize_cross_hat_insights

        reports = [
            HatReport(
                hat="red",
                emoji="🔴",
                focus="chaos",
                findings=[
                    HatFinding("red", "LOW", "Unique issue only here", "d", "r"),
                ],
            ),
        ]
        insights = synthesize_cross_hat_insights(reports)
        assert insights == []

    def test_empty_reports_safe(self) -> None:
        from agents.core.hat_engine import synthesize_cross_hat_insights

        assert synthesize_cross_hat_insights([]) == []


class TestRunHatTimed:
    """run_hat_timed() returns both report and metrics."""

    def test_returns_tuple(self) -> None:
        from unittest.mock import patch

        from agents.core.hat_engine import HatRunMetrics, HatReport, run_hat_timed

        with patch("agents.core.hat_engine._call_ollama", return_value="[]"):
            result = run_hat_timed("green")

        assert isinstance(result, tuple)
        report, metrics = result
        assert isinstance(report, HatReport)
        assert isinstance(metrics, HatRunMetrics)
        assert metrics.hat == "green"
        assert metrics.elapsed_seconds >= 0.0

    def test_unknown_hat_has_error(self) -> None:
        from agents.core.hat_engine import run_hat_timed

        _, metrics = run_hat_timed("does_not_exist")
        assert metrics.has_error is True

    def test_metrics_finding_count(self) -> None:
        from unittest.mock import patch

        from agents.core.hat_engine import run_hat_timed

        findings_json = json.dumps([
            {"severity": "HIGH", "title": "Issue A", "description": "d", "recommendation": "r"},
            {"severity": "LOW", "title": "Issue B", "description": "d", "recommendation": "r"},
        ])
        with patch("agents.core.hat_engine._call_ollama", return_value=findings_json):
            _, metrics = run_hat_timed("red")

        assert metrics.finding_count == 2
        assert metrics.has_error is False


class TestMarkFindingResolved:
    """mark_finding_resolved() updates resolved flag in DB."""

    def test_resolves_existing_finding(self, tmp_path: Path) -> None:
        from agents.core.hat_engine import mark_finding_resolved

        conn = _make_dream_db(tmp_path / "test.db")
        conn.execute(
            "INSERT INTO dream_results (timestamp, cycle_type) VALUES (?, ?)",
            (time.time(), "manual"),
        )
        conn.execute(
            "INSERT INTO hat_findings (dream_cycle_id, hat, severity, title, timestamp) "
            "VALUES (1, 'red', 'HIGH', 'Test', ?)",
            (time.time(),),
        )
        conn.commit()
        finding_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        result = mark_finding_resolved(conn, finding_id)
        assert result is True

        resolved = conn.execute(
            "SELECT resolved FROM hat_findings WHERE id = ?", (finding_id,)
        ).fetchone()[0]
        assert resolved == 1
        conn.close()

    def test_returns_false_for_missing_id(self, tmp_path: Path) -> None:
        from agents.core.hat_engine import mark_finding_resolved

        conn = _make_dream_db(tmp_path / "test.db")
        result = mark_finding_resolved(conn, 99999)
        assert result is False
        conn.close()


# ===========================================================================
# Catalyst additions — tests for new dream_state features
# ===========================================================================


class TestDreamStateEngineCatalyst:
    """Tests for the Catalyst additions to DreamStateEngine."""

    def _make_engine_with_rules(self):
        """Helper: return an engine with at least one synthesized rule."""
        from agents.core.dream_state import DreamStateEngine

        engine = DreamStateEngine(min_experiences=3)
        for i in range(6):
            engine.add_experience(
                f"Agent sentinel detected seccomp violation {i}",
                tags=["security", "sentinel"],
            )
        engine.dream_cycle()
        return engine

    # -- experience_tag_frequency --

    def test_tag_frequency_counts_tags(self) -> None:
        from agents.core.dream_state import DreamStateEngine

        engine = DreamStateEngine(min_experiences=3)
        engine.add_experience("experience A", tags=["security", "redis"])
        engine.add_experience("experience B", tags=["security"])
        engine.add_experience("experience C", tags=["redis"])

        freq = engine.experience_tag_frequency
        assert freq["security"] == 2
        assert freq["redis"] == 2

    def test_tag_frequency_empty_experiences(self) -> None:
        from agents.core.dream_state import DreamStateEngine

        engine = DreamStateEngine()
        assert len(engine.experience_tag_frequency) == 0

    # -- rule_lookup --

    def test_rule_lookup_finds_matching_rule(self) -> None:
        engine = self._make_engine_with_rules()
        results = engine.rule_lookup("sentinel")
        # should find the rule containing 'sentinel'
        assert len(results) >= 1
        assert all("sentinel" in r.summary.lower() for r in results)

    def test_rule_lookup_case_insensitive(self) -> None:
        engine = self._make_engine_with_rules()
        lower_results = engine.rule_lookup("sentinel")
        upper_results = engine.rule_lookup("SENTINEL")
        assert len(lower_results) == len(upper_results)

    def test_rule_lookup_no_match_returns_empty(self) -> None:
        engine = self._make_engine_with_rules()
        results = engine.rule_lookup("zzz_nonexistent_keyword_zzz")
        assert results == []

    def test_rule_lookup_empty_engine(self) -> None:
        from agents.core.dream_state import DreamStateEngine

        engine = DreamStateEngine()
        assert engine.rule_lookup("anything") == []

    # -- prune_low_confidence_rules --

    def test_prune_removes_low_confidence(self) -> None:
        from agents.core.dream_state import DreamStateEngine, SynthesizedRule

        engine = DreamStateEngine()
        engine._rules = [
            SynthesizedRule(summary="rule A", confidence=0.9),
            SynthesizedRule(summary="rule B", confidence=0.1),
            SynthesizedRule(summary="rule C", confidence=0.5),
        ]
        pruned = engine.prune_low_confidence_rules(threshold=0.4)
        assert pruned == 1
        assert engine.rule_count == 2
        remaining_summaries = [r.summary for r in engine._rules]
        assert "rule B" not in remaining_summaries

    def test_prune_keeps_all_above_threshold(self) -> None:
        from agents.core.dream_state import DreamStateEngine, SynthesizedRule

        engine = DreamStateEngine()
        engine._rules = [SynthesizedRule(summary="x", confidence=0.9)]
        pruned = engine.prune_low_confidence_rules(threshold=0.5)
        assert pruned == 0
        assert engine.rule_count == 1

    def test_prune_empty_rules_returns_zero(self) -> None:
        from agents.core.dream_state import DreamStateEngine

        engine = DreamStateEngine()
        assert engine.prune_low_confidence_rules() == 0

    # -- decay_rule_confidence --

    def test_decay_reduces_confidence(self) -> None:
        from agents.core.dream_state import DreamStateEngine, SynthesizedRule

        engine = DreamStateEngine()
        engine._rules = [SynthesizedRule(summary="x", confidence=0.8)]
        engine.decay_rule_confidence(decay_factor=0.1)
        assert abs(engine._rules[0].confidence - 0.7) < 1e-9

    def test_decay_clamps_at_zero(self) -> None:
        from agents.core.dream_state import DreamStateEngine, SynthesizedRule

        engine = DreamStateEngine()
        engine._rules = [SynthesizedRule(summary="x", confidence=0.05)]
        engine.decay_rule_confidence(decay_factor=0.2)
        assert engine._rules[0].confidence == 0.0

    def test_decay_empty_rules_safe(self) -> None:
        from agents.core.dream_state import DreamStateEngine

        engine = DreamStateEngine()
        engine.decay_rule_confidence()  # should not raise
