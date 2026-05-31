"""
Tests for the Crew Orchestrator — multi-persona round-robin engine.

Tests:
  - Registry loading and persona listing
  - Persona prompt building with cross-awareness
  - Consolidation prompt construction
  - JSON extraction from markdown-wrapped responses
  - Database persistence of crew discussions
  - Report structure validation
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch  # noqa: F401 — pre-staged for mock tests

import pytest


@pytest.fixture(autouse=True)
def _reload_registry():
    """Force registry reload before each test to avoid stale cache."""
    from agents.core.crew_orchestrator import reload_registry
    reload_registry()
    yield
    reload_registry()


# ===========================================================================
# Registry Tests
# ===========================================================================


class TestRegistryLoading:
    """Verify agent registry loads correctly."""

    def test_list_personas_returns_all_agents(self) -> None:
        from agents.core.crew_orchestrator import list_personas

        personas = list_personas()
        # Should have at minimum these 7 agents
        expected = {"sentinel", "scribe", "arbiter", "steward", "cove", "palette", "consolidator"}
        assert expected.issubset(set(personas.keys())), (
            f"Missing personas: {expected - set(personas.keys())}"
        )

    def test_each_persona_has_required_fields(self) -> None:
        from agents.core.crew_orchestrator import list_personas

        personas = list_personas()
        for name, meta in personas.items():
            assert "role" in meta, f"{name} missing role"
            assert "hat" in meta, f"{name} missing hat"
            assert "description" in meta, f"{name} missing description"
            assert "model" in meta, f"{name} missing model"

    def test_cross_awareness_graph(self) -> None:
        from agents.core.crew_orchestrator import get_cross_awareness_graph

        graph = get_cross_awareness_graph()
        # Consolidator should have the most cross-awareness links
        assert "consolidator" in graph
        assert len(graph["consolidator"]) >= 4, (
            f"Consolidator should have universal awareness, got {graph['consolidator']}"
        )

    def test_consolidator_has_universal_awareness(self) -> None:
        from agents.core.crew_orchestrator import get_cross_awareness_graph

        graph = get_cross_awareness_graph()
        consolidator_links = set(graph.get("consolidator", []))
        # Consolidator should be aware of sentinel, cove, palette at minimum
        expected_subset = {"sentinel", "cove", "palette"}
        assert expected_subset.issubset(consolidator_links)


# ===========================================================================
# Prompt Building Tests
# ===========================================================================


class TestPromptBuilding:
    """Verify persona prompts are correctly constructed."""

    def test_persona_prompt_includes_role(self) -> None:
        from agents.core.crew_orchestrator import _build_persona_prompt

        prompt = _build_persona_prompt("sentinel", "Test topic")
        assert "Sentinel" in prompt or "sentinel" in prompt.lower()
        assert "Test topic" not in prompt  # Topic goes in user prompt, not system

    def test_persona_prompt_includes_cross_awareness(self) -> None:
        from agents.core.crew_orchestrator import _build_persona_prompt

        prompt = _build_persona_prompt("sentinel", "Test topic")
        assert "Cross-awareness" in prompt or "cross-awareness" in prompt.lower() or "collaborators" in prompt.lower()

    def test_persona_prompt_with_prior_responses(self) -> None:
        from agents.core.crew_orchestrator import PersonaResponse, _build_persona_prompt

        prior = [
            PersonaResponse(
                persona="cove",
                role="CoVE Validator",
                hat="gold",
                model="test",
                content="Found 3 critical security issues in intent_capsule.py",
            )
        ]
        prompt = _build_persona_prompt("sentinel", "Review security", prior)
        assert "Prior perspectives" in prompt
        assert "critical security issues" in prompt

    def test_consolidator_prompt_includes_all_responses(self) -> None:
        from agents.core.crew_orchestrator import PersonaResponse, _build_consolidator_prompt

        responses = [
            PersonaResponse(persona="sentinel", role="Security", hat="black", model="m", content="Found XSS"),
            PersonaResponse(persona="palette", role="UX", hat="green", model="m", content="Color contrast fail"),
        ]
        prompt = _build_consolidator_prompt("Audit", responses)
        assert "sentinel" in prompt.lower()
        assert "palette" in prompt.lower()
        assert "Found XSS" in prompt
        assert "Color contrast fail" in prompt


# ===========================================================================
# JSON Extraction Tests
# ===========================================================================


class TestJSONExtraction:
    """Verify JSON extraction from various response formats."""

    def test_extract_from_markdown_block(self) -> None:
        from agents.core.crew_orchestrator import _extract_json

        text = 'Here is my analysis:\n```json\n{"key": "value"}\n```\nDone.'
        result = _extract_json(text)
        assert result is not None
        parsed = json.loads(result)
        assert parsed["key"] == "value"

    def test_extract_bare_json(self) -> None:
        from agents.core.crew_orchestrator import _extract_json

        text = 'Some text {"agreements": ["A", "B"]} more text'
        result = _extract_json(text)
        assert result is not None
        parsed = json.loads(result)
        assert len(parsed["agreements"]) == 2

    def test_extract_json_array(self) -> None:
        from agents.core.crew_orchestrator import _extract_json

        text = '```json\n[{"severity": "HIGH", "title": "Bug"}]\n```'
        result = _extract_json(text)
        assert result is not None
        parsed = json.loads(result)
        assert parsed[0]["severity"] == "HIGH"

    def test_no_json_returns_none(self) -> None:
        from agents.core.crew_orchestrator import _extract_json

        result = _extract_json("Just plain text with no JSON at all.")
        assert result is None


# ===========================================================================
# Report Structure Tests
# ===========================================================================


class TestReportStructures:
    """Verify data class defaults and structure."""

    def test_persona_response_defaults(self) -> None:
        from agents.core.crew_orchestrator import PersonaResponse

        resp = PersonaResponse(
            persona="test", role="Test", hat="blue", model="m", content="stuff"
        )
        assert resp.duration_seconds == 0.0
        assert resp.token_estimate == 0
        assert resp.timestamp > 0

    def test_crew_report_defaults(self) -> None:
        from agents.core.crew_orchestrator import CrewReport

        report = CrewReport(topic="Test")
        assert report.topic == "Test"
        assert report.personas_used == []
        assert report.responses == []
        assert report.consolidation is None
        assert report.total_duration == 0.0

    def test_consolidation_report_defaults(self) -> None:
        from agents.core.crew_orchestrator import ConsolidationReport

        cr = ConsolidationReport()
        assert cr.agreements == []
        assert cr.disagreements == []
        assert cr.evidence_gaps == []
        assert cr.recommendations == []
        assert cr.confidence == 0.0


# ===========================================================================
# Persistence Tests
# ===========================================================================


def _make_crew_db(path: Path) -> sqlite3.Connection:
    """Create a test database with crew tables."""
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    from agents.core.crew_orchestrator import _ensure_crew_tables
    _ensure_crew_tables(conn)
    return conn


class TestCrewPersistence:
    """Test database persistence of crew discussions."""

    def test_persist_and_retrieve(self, tmp_path: Path) -> None:
        from agents.core.crew_orchestrator import (
            ConsolidationReport,
            CrewReport,
            PersonaResponse,
            _persist_crew_report,
            get_recent_crew_discussions,
        )

        conn = _make_crew_db(tmp_path / "test.db")

        report = CrewReport(
            topic="Security Audit",
            personas_used=["sentinel", "cove"],
            responses=[
                PersonaResponse(
                    persona="sentinel",
                    role="Security",
                    hat="black",
                    model="test",
                    content="Found 2 issues",
                    duration_seconds=1.5,
                ),
                PersonaResponse(
                    persona="cove",
                    role="CoVE",
                    hat="gold",
                    model="test",
                    content="Confirmed issues, found 1 more",
                    duration_seconds=2.0,
                ),
            ],
            consolidation=ConsolidationReport(
                agreements=["Both found XSS"],
                confidence=0.85,
            ),
            total_duration=5.0,
        )

        discussion_id = _persist_crew_report(conn, report)
        assert discussion_id > 0

        recent = get_recent_crew_discussions(conn, limit=10)
        assert len(recent) == 1
        assert recent[0]["topic"] == "Security Audit"
        assert recent[0]["consolidation_confidence"] == 0.85
        assert "sentinel" in recent[0]["personas_used"]

        conn.close()

    def test_empty_db_returns_empty_list(self, tmp_path: Path) -> None:
        from agents.core.crew_orchestrator import get_recent_crew_discussions

        conn = _make_crew_db(tmp_path / "empty.db")
        recent = get_recent_crew_discussions(conn, limit=5)
        assert recent == []
        conn.close()


class TestEnsureCrewTables:
    """Test that crew tables are created on-demand."""

    def test_tables_created(self, tmp_path: Path) -> None:
        from agents.core.crew_orchestrator import _ensure_crew_tables

        conn = sqlite3.connect(str(tmp_path / "bare.db"))
        _ensure_crew_tables(conn)

        # Verify tables exist
        conn.execute(
            "INSERT INTO crew_discussions (timestamp, topic, personas_used) "
            "VALUES (?, ?, ?)",
            (time.time(), "test", '["a"]'),
        )
        conn.execute(
            "INSERT INTO crew_responses (discussion_id, persona, role, hat, "
            "model, content, timestamp) VALUES (1, 'a', 'b', 'c', 'd', 'e', ?)",
            (time.time(),),
        )
        conn.commit()
        conn.close()


# ===========================================================================
# SDD Session Tests
# ===========================================================================


class TestSDDSession:
    """Verify Spec-Driven Development session lifecycle."""

    def test_initial_state(self) -> None:
        from agents.core.crew_orchestrator import SDDPhase, SDDSession

        session = SDDSession(topic="auth upgrade")
        assert session.phase == SDDPhase.SPECIFY
        assert session.sealed is False
        assert session.realignment_events == []
        assert session.phase_history == []

    def test_advance_sequential(self) -> None:
        from agents.core.crew_orchestrator import SDDPhase, SDDSession

        session = SDDSession(topic="test mission")
        session.advance_to(SDDPhase.PLAN)
        assert session.phase == SDDPhase.PLAN
        session.advance_to(SDDPhase.EXECUTE)
        assert session.phase == SDDPhase.EXECUTE

    def test_advance_skip_blocked(self) -> None:
        from agents.core.crew_orchestrator import SDDPhase, SDDSession

        session = SDDSession(topic="skip test")
        with pytest.raises(ValueError, match="phase skip"):
            session.advance_to(SDDPhase.EXECUTE)  # Skip PLAN

    def test_advance_backward_blocked(self) -> None:
        from agents.core.crew_orchestrator import SDDPhase, SDDSession

        session = SDDSession(topic="backward test")
        session.advance_to(SDDPhase.PLAN)
        session.advance_to(SDDPhase.EXECUTE)
        with pytest.raises(ValueError, match="backward transition"):
            session.advance_to(SDDPhase.PLAN)

    def test_advance_override_allows_skip(self) -> None:
        from agents.core.crew_orchestrator import SDDPhase, SDDSession

        session = SDDSession(topic="override test")
        session.advance_to(SDDPhase.EXECUTE, override=True)
        assert session.phase == SDDPhase.EXECUTE

    def test_merge_seals_session(self) -> None:
        from agents.core.crew_orchestrator import SDDPhase, SDDSession

        session = SDDSession(topic="merge test")
        for phase in [SDDPhase.PLAN, SDDPhase.EXECUTE, SDDPhase.VERIFY, SDDPhase.MERGE]:
            session.advance_to(phase)
        assert session.sealed is True

    def test_cannot_advance_sealed_session(self) -> None:
        from agents.core.crew_orchestrator import SDDPhase, SDDSession

        session = SDDSession(topic="sealed test")
        for phase in [SDDPhase.PLAN, SDDPhase.EXECUTE, SDDPhase.VERIFY, SDDPhase.MERGE]:
            session.advance_to(phase)
        with pytest.raises(ValueError, match="sealed"):
            session.advance_to(SDDPhase.SPECIFY, override=True)

    def test_phase_history_populated(self) -> None:
        from agents.core.crew_orchestrator import SDDPhase, SDDSession

        session = SDDSession(topic="history test")
        session.advance_to(SDDPhase.PLAN, notes="planning")
        assert len(session.phase_history) == 1
        entry = session.phase_history[0]
        assert entry["from"] == "SPECIFY"
        assert entry["to"] == "PLAN"
        assert entry["notes"] == "planning"

    def test_to_dict_roundtrip(self) -> None:
        from agents.core.crew_orchestrator import SDDPhase, SDDSession

        session = SDDSession(topic="roundtrip test")
        session.advance_to(SDDPhase.PLAN, notes="round-trip")
        d = session.to_dict()
        assert d["phase"] == "PLAN"
        assert d["topic"] == "roundtrip test"

        restored = SDDSession.from_dict(d)
        assert restored.phase == SDDPhase.PLAN
        assert restored.topic == "roundtrip test"
        assert restored.sealed is False


class TestSDDRealignment:
    """Verify SDDSession realignment records events correctly."""

    def test_realign_records_event(self) -> None:
        from agents.core.crew_orchestrator import (
            SDDPhase,
            SDDRealignmentEvent,
            SDDSession,
        )

        session = SDDSession(topic="realign test")
        session.advance_to(SDDPhase.PLAN)
        event = SDDRealignmentEvent(
            triggered_by="sentinel",
            change_type="deprecated_api",
            change_description="requests 2.x removed",
            affected_nodes=["node-1"],
        )
        session.realign(event)

        assert len(session.realignment_events) == 1
        rec = session.realignment_events[0]
        assert rec["triggered_by"] == "sentinel"
        assert rec["change_type"] == "deprecated_api"

    def test_realign_blocked_on_sealed(self) -> None:
        from agents.core.crew_orchestrator import (
            SDDPhase,
            SDDRealignmentEvent,
            SDDSession,
        )

        session = SDDSession(topic="sealed realign test")
        for phase in [SDDPhase.PLAN, SDDPhase.EXECUTE, SDDPhase.VERIFY, SDDPhase.MERGE]:
            session.advance_to(phase)
        event = SDDRealignmentEvent(
            triggered_by="catalyst", change_type="perf", change_description="slow path"
        )
        with pytest.raises(ValueError, match="sealed"):
            session.realign(event)

    def test_realign_updates_spec(self) -> None:
        from agents.core.crew_orchestrator import (
            SDDPhase,
            SDDRealignmentEvent,
            SDDSession,
        )

        session = SDDSession(topic="spec realign test")
        session.spec = {"topic": "test"}
        session.advance_to(SDDPhase.PLAN)
        event = SDDRealignmentEvent(
            triggered_by="herald",
            change_type="doc_drift",
            change_description="README outdated",
        )
        session.realign(event)
        assert "_realignments" in session.spec
        assert len(session.spec["_realignments"]) == 1


class TestValidationToken:
    """Verify the cryptographic validation gate token."""

    def test_sign_and_verify(self) -> None:
        from agents.core.crew_orchestrator import ValidationToken

        token = ValidationToken(
            session_id="abc123",
            spec_hash="deadbeef",
            tests_passed=True,
            lint_clean=True,
            cove_approved=True,
        )
        token.sign()
        assert token.signature != ""
        assert token.verify() is True

    def test_is_valid_all_pass(self) -> None:
        from agents.core.crew_orchestrator import ValidationToken

        token = ValidationToken(
            session_id="s1",
            spec_hash="h1",
            tests_passed=True,
            lint_clean=True,
            cove_approved=True,
        )
        token.sign()
        assert token.is_valid() is True

    def test_is_valid_fails_if_tests_not_passed(self) -> None:
        from agents.core.crew_orchestrator import ValidationToken

        token = ValidationToken(
            session_id="s2",
            spec_hash="h2",
            tests_passed=False,
            lint_clean=True,
            cove_approved=True,
        )
        token.sign()
        assert token.is_valid() is False

    def test_tampered_token_fails_verify(self) -> None:
        from agents.core.crew_orchestrator import ValidationToken

        token = ValidationToken(
            session_id="s3",
            spec_hash="h3",
            tests_passed=True,
            lint_clean=True,
            cove_approved=True,
        )
        token.sign()
        token.tests_passed = False  # Tamper
        assert token.verify() is False

    def test_to_dict_roundtrip(self) -> None:
        from agents.core.crew_orchestrator import ValidationToken

        token = ValidationToken(
            session_id="s4",
            spec_hash="h4",
            tests_passed=True,
            lint_clean=True,
            cove_approved=True,
        )
        token.sign()
        d = token.to_dict()
        restored = ValidationToken.from_dict(d)
        assert restored.verify() is True
        assert restored.session_id == "s4"


class TestSDDSessionStore:
    """Verify SQLite persistence of SDD sessions."""

    def test_save_and_load(self, tmp_path: Path) -> None:
        from agents.core.crew_orchestrator import SDDPhase, SDDSession, SDDSessionStore

        store = SDDSessionStore(tmp_path / "sdd.db")
        store.init_schema()

        session = SDDSession(topic="persist test")
        session.advance_to(SDDPhase.PLAN, notes="step 1")
        store.save(session)

        loaded = store.load(session.session_id)
        assert loaded is not None
        assert loaded.topic == "persist test"
        assert loaded.phase == SDDPhase.PLAN

        store.close()

    def test_load_missing_returns_none(self, tmp_path: Path) -> None:
        from agents.core.crew_orchestrator import SDDSessionStore

        store = SDDSessionStore(tmp_path / "missing.db")
        store.init_schema()
        assert store.load("nonexistent-id") is None
        store.close()

    def test_list_active_excludes_sealed(self, tmp_path: Path) -> None:
        from agents.core.crew_orchestrator import (
            SDDPhase,
            SDDSession,
            SDDSessionStore,
        )

        store = SDDSessionStore(tmp_path / "active.db")
        store.init_schema()

        active = SDDSession(topic="active mission")
        active.advance_to(SDDPhase.PLAN)
        store.save(active)

        sealed = SDDSession(topic="sealed mission")
        for phase in [SDDPhase.PLAN, SDDPhase.EXECUTE, SDDPhase.VERIFY, SDDPhase.MERGE]:
            sealed.advance_to(phase)
        store.save(sealed)

        active_list = store.list_active()
        ids = [r["session_id"] for r in active_list]
        assert active.session_id in ids
        assert sealed.session_id not in ids

        store.close()

    def test_delete_session(self, tmp_path: Path) -> None:
        from agents.core.crew_orchestrator import SDDSession, SDDSessionStore

        store = SDDSessionStore(tmp_path / "del.db")
        store.init_schema()

        session = SDDSession(topic="delete me")
        store.save(session)
        assert store.load(session.session_id) is not None

        deleted = store.delete(session.session_id)
        assert deleted is True
        assert store.load(session.session_id) is None

        store.close()

    def test_upsert_updates_existing(self, tmp_path: Path) -> None:
        from agents.core.crew_orchestrator import SDDPhase, SDDSession, SDDSessionStore

        store = SDDSessionStore(tmp_path / "upsert.db")
        store.init_schema()

        session = SDDSession(topic="upsert test")
        store.save(session)
        session.advance_to(SDDPhase.PLAN, notes="updated")
        store.save(session)  # Should upsert, not duplicate

        all_sessions = store.list_all()
        assert sum(1 for s in all_sessions if s["session_id"] == session.session_id) == 1
        loaded = store.load(session.session_id)
        assert loaded.phase == SDDPhase.PLAN

        store.close()


class TestGetSystemStatus:
    """Verify get_system_status returns a complete observability snapshot."""

    def test_system_status_keys(self) -> None:
        from agents.core.crew_orchestrator import get_system_status

        status = get_system_status()
        required_keys = {
            "persona_roster",
            "cross_awareness_graph",
            "default_persona_order",
            "hat_to_persona_map",
            "registry_hash",
            "total_personas",
        }
        assert required_keys.issubset(status.keys())

    def test_default_persona_order_contains_weaver(self) -> None:
        from agents.core.crew_orchestrator import get_system_status

        status = get_system_status()
        assert "weaver" in status["default_persona_order"]

    def test_total_personas_positive(self) -> None:
        from agents.core.crew_orchestrator import get_system_status

        status = get_system_status()
        assert status["total_personas"] > 0
