"""
Tests for SDD Session Persistence and Re-alignment Events.

Covers:
  - SDDSessionStore: save/load round-trip, list_active, list_all, delete
  - SDDSession.from_dict: deserialization fidelity
  - SDDRealignmentEvent: creation, recording, sealed rejection
  - SDDSession.realign: spec mutation, history tracking
  - Resume from mid-phase
"""

from __future__ import annotations

import pytest

from agents.core.crew_orchestrator import (
    SDDPhase,
    SDDRealignmentEvent,
    SDDSession,
    SDDSessionStore,
)

# --------------------------------------------------------------------------- #
# SDDSessionStore — Persistence
# --------------------------------------------------------------------------- #


class TestSDDSessionStore:
    """SQLite-backed SDD session persistence."""

    def _make_store(self) -> SDDSessionStore:
        store = SDDSessionStore(db_path=":memory:")
        store.init_schema()
        return store

    def test_save_and_load_roundtrip(self) -> None:
        """Session survives save→load cycle."""
        store = self._make_store()
        session = SDDSession(topic="auth upgrade")
        store.save(session)

        restored = store.load(session.session_id)
        assert restored is not None
        assert restored.session_id == session.session_id
        assert restored.topic == "auth upgrade"
        assert restored.phase == SDDPhase.SPECIFY

    def test_load_missing_returns_none(self) -> None:
        """Loading a nonexistent session returns None."""
        store = self._make_store()
        assert store.load("nonexistent") is None

    def test_save_updates_on_conflict(self) -> None:
        """Saving again updates the existing record."""
        store = self._make_store()
        session = SDDSession(topic="api refactor")
        store.save(session)

        session.advance_to(SDDPhase.PLAN)
        store.save(session)

        restored = store.load(session.session_id)
        assert restored is not None
        assert restored.phase == SDDPhase.PLAN

    def test_list_active_excludes_sealed(self) -> None:
        """list_active returns only unsealed sessions."""
        store = self._make_store()

        active = SDDSession(topic="active mission")
        store.save(active)

        sealed = SDDSession(topic="done mission")
        sealed.advance_to(SDDPhase.PLAN)
        sealed.advance_to(SDDPhase.EXECUTE)
        sealed.advance_to(SDDPhase.VERIFY)
        sealed.advance_to(SDDPhase.MERGE)
        store.save(sealed)

        active_list = store.list_active()
        assert len(active_list) == 1
        assert active_list[0]["topic"] == "active mission"

    def test_list_all_includes_sealed(self) -> None:
        """list_all returns both active and sealed sessions."""
        store = self._make_store()

        s1 = SDDSession(topic="mission alpha")
        store.save(s1)

        s2 = SDDSession(topic="mission beta")
        s2.advance_to(SDDPhase.PLAN)
        s2.advance_to(SDDPhase.EXECUTE)
        s2.advance_to(SDDPhase.VERIFY)
        s2.advance_to(SDDPhase.MERGE)
        store.save(s2)

        all_list = store.list_all()
        assert len(all_list) == 2

    def test_delete_session(self) -> None:
        """Deleted session is no longer loadable."""
        store = self._make_store()
        session = SDDSession(topic="temporary")
        store.save(session)

        deleted = store.delete(session.session_id)
        assert deleted is True
        assert store.load(session.session_id) is None

    def test_delete_nonexistent_returns_false(self) -> None:
        """Deleting a nonexistent session returns False."""
        store = self._make_store()
        deleted = store.delete("ghost")
        assert deleted is False

    def test_resume_from_verify_phase(self) -> None:
        """Session saved at VERIFY can be loaded and advanced to MERGE."""
        store = self._make_store()
        session = SDDSession(topic="deploy pipeline")
        session.advance_to(SDDPhase.PLAN)
        session.advance_to(SDDPhase.EXECUTE)
        session.advance_to(SDDPhase.VERIFY)
        store.save(session)

        restored = store.load(session.session_id)
        assert restored is not None
        assert restored.phase == SDDPhase.VERIFY
        assert not restored.sealed

        restored.advance_to(SDDPhase.MERGE)
        assert restored.sealed is True

    def test_close_idempotent(self) -> None:
        """Closing the store multiple times doesn't error."""
        store = self._make_store()
        store.close()
        store.close()  # no error


# --------------------------------------------------------------------------- #
# SDDSession.from_dict — Deserialization
# --------------------------------------------------------------------------- #


class TestSDDSessionSerialization:
    """SDDSession serialization/deserialization fidelity."""

    def test_roundtrip_preserves_fields(self) -> None:
        """to_dict → from_dict preserves all core fields."""
        session = SDDSession(topic="roundtrip test")
        session.spec = {"task": "validate", "constraints": ["no-delete"]}
        session.advance_to(SDDPhase.PLAN, notes="initial plan")

        data = session.to_dict()
        restored = SDDSession.from_dict(data)

        assert restored.session_id == session.session_id
        assert restored.topic == "roundtrip test"
        assert restored.phase == SDDPhase.PLAN
        assert restored.spec == {"task": "validate", "constraints": ["no-delete"]}
        assert len(restored.phase_history) == 1

    def test_from_dict_handles_missing_optional_fields(self) -> None:
        """from_dict works with minimal data."""
        minimal = {"phase": "EXECUTE"}
        restored = SDDSession.from_dict(minimal)
        assert restored.phase == SDDPhase.EXECUTE
        assert restored.topic == ""
        assert restored.spec is None

    def test_session_id_stability(self) -> None:
        """Session ID is stable through serialization."""
        session = SDDSession(topic="stable id")
        sid = session.session_id
        data = session.to_dict()
        restored = SDDSession.from_dict(data)
        assert restored.session_id == sid


# --------------------------------------------------------------------------- #
# Re-alignment Events
# --------------------------------------------------------------------------- #


class TestRealignmentEvents:
    """SDDRealignmentEvent creation and SDDSession.realign()."""

    def test_realignment_event_creation(self) -> None:
        """SDDRealignmentEvent stores fields correctly."""
        event = SDDRealignmentEvent(
            triggered_by="sentinel",
            change_type="deprecated_api",
            change_description="OAuth v1 is deprecated, must use v2",
            affected_nodes=["auth_node", "token_node"],
        )
        assert event.triggered_by == "sentinel"
        assert event.change_type == "deprecated_api"
        assert len(event.affected_nodes) == 2
        assert event.timestamp > 0

    def test_realign_records_event(self) -> None:
        """session.realign() records the event in realignment_events."""
        session = SDDSession(topic="api upgrade")
        session.advance_to(SDDPhase.PLAN)

        event = SDDRealignmentEvent(
            triggered_by="scribe",
            change_type="new_constraint",
            change_description="Rate limit discovered: 100 req/min",
        )
        session.realign(event)

        assert len(session.realignment_events) == 1
        assert session.realignment_events[0]["triggered_by"] == "scribe"
        assert session.realignment_events[0]["change_type"] == "new_constraint"

    def test_realign_updates_spec(self) -> None:
        """realign() injects realignment data into the spec."""
        session = SDDSession(topic="spec sync test")
        session.spec = {"task": "build auth"}
        session.advance_to(SDDPhase.PLAN)

        event = SDDRealignmentEvent(
            triggered_by="arbiter",
            change_type="missing_endpoint",
            change_description="/api/v2/users not available",
        )
        session.realign(event)

        assert "_realignments" in session.spec
        assert len(session.spec["_realignments"]) == 1
        assert session.spec["_realignments"][0]["type"] == "missing_endpoint"

    def test_realign_adds_to_phase_history(self) -> None:
        """realign() logs a phase_history entry with REALIGNMENT prefix."""
        session = SDDSession(topic="history test")
        session.advance_to(SDDPhase.PLAN)
        initial_history_len = len(session.phase_history)

        event = SDDRealignmentEvent(
            triggered_by="cove",
            change_type="security_issue",
            change_description="XSS vulnerability in template",
        )
        session.realign(event)

        assert len(session.phase_history) == initial_history_len + 1
        last_entry = session.phase_history[-1]
        assert "REALIGNMENT" in last_entry["notes"]
        assert last_entry["from"] == "PLAN"
        assert last_entry["to"] == "PLAN"  # same phase

    def test_realign_on_sealed_session_raises(self) -> None:
        """Cannot realign a sealed session."""
        session = SDDSession(topic="sealed test")
        session.advance_to(SDDPhase.PLAN)
        session.advance_to(SDDPhase.EXECUTE)
        session.advance_to(SDDPhase.VERIFY)
        session.advance_to(SDDPhase.MERGE)
        assert session.sealed is True

        event = SDDRealignmentEvent(
            triggered_by="sentinel",
            change_type="late_change",
            change_description="too late",
        )
        with pytest.raises(ValueError, match="sealed"):
            session.realign(event)

    def test_multiple_realignments_accumulate(self) -> None:
        """Multiple realignment events stack correctly."""
        session = SDDSession(topic="multi realign")
        session.spec = {"task": "complex build"}
        session.advance_to(SDDPhase.PLAN)

        for i in range(3):
            event = SDDRealignmentEvent(
                triggered_by=f"agent_{i}",
                change_type="constraint_update",
                change_description=f"Constraint {i} discovered",
            )
            session.realign(event)

        assert len(session.realignment_events) == 3
        assert len(session.spec["_realignments"]) == 3

    def test_realignment_persists_through_store(self) -> None:
        """Realignment events survive save→load through SDDSessionStore."""
        store = SDDSessionStore(db_path=":memory:")
        store.init_schema()

        session = SDDSession(topic="persist realign")
        session.spec = {"task": "test"}
        session.advance_to(SDDPhase.PLAN)
        session.realign(SDDRealignmentEvent(
            triggered_by="sentinel",
            change_type="api_change",
            change_description="Endpoint moved to /v3",
        ))
        store.save(session)

        restored = store.load(session.session_id)
        assert restored is not None
        assert len(restored.realignment_events) == 1
        assert restored.realignment_events[0]["change_type"] == "api_change"
        store.close()
