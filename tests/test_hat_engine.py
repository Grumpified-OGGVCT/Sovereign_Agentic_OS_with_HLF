"""
Tests for the Eleven Thinking Hats engine and Dream Mode integration.

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
    """Verify all 11 hats are fully defined."""

    def test_all_eleven_hats_exist(self) -> None:
        from agents.core.hat_engine import HAT_DEFINITIONS

        expected = {"red", "black", "white", "yellow", "green", "blue",
                    "indigo", "cyan", "purple", "orange", "silver"}
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

        raw = json.dumps([
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
        ])
        findings = _parse_findings("green", raw)
        assert len(findings) == 2
        assert findings[0].hat == "green"
        assert findings[0].severity == "HIGH"
        assert findings[0].title == "Redis has no persistence"
        assert findings[1].severity == "MEDIUM"

    def test_parse_json_wrapped_in_markdown(self) -> None:
        from agents.core.hat_engine import _parse_findings

        raw = "Here's my analysis:\n```json\n" + json.dumps([
            {"severity": "LOW", "title": "Minor issue", "description": "x", "recommendation": "y"}
        ]) + "\n```\nThat's all."

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
