"""
Tests for Instinct SDD Lifecycle Enforcement in Crew Orchestrator.

Covers:
  - SDDPhase enum ordering
  - SDDSession phase progression SPECIFY → PLAN → EXECUTE → VERIFY → MERGE
  - Phase skip raises error
  - Backward phase transition raises error (unless override)
  - SDDSession serialization roundtrip
  - Sealed session rejects further transitions
  - Phase history tracking
"""

from __future__ import annotations

import pytest

from agents.core.crew_orchestrator import SDDPhase, SDDSession

# --------------------------------------------------------------------------- #
# SDDPhase Enum
# --------------------------------------------------------------------------- #


class TestSDDPhase:
    """SDDPhase enum has correct ordering and values."""

    def test_phase_count(self) -> None:
        """There are exactly 5 SDD phases."""
        assert len(SDDPhase) == 5

    def test_phase_ordering(self) -> None:
        """Phases are ordered: SPECIFY < PLAN < EXECUTE < VERIFY < MERGE."""
        assert SDDPhase.SPECIFY.value < SDDPhase.PLAN.value
        assert SDDPhase.PLAN.value < SDDPhase.EXECUTE.value
        assert SDDPhase.EXECUTE.value < SDDPhase.VERIFY.value
        assert SDDPhase.VERIFY.value < SDDPhase.MERGE.value

    def test_phase_names(self) -> None:
        """All phases have correct names."""
        names = [p.name for p in SDDPhase]
        assert names == ["SPECIFY", "PLAN", "EXECUTE", "VERIFY", "MERGE"]


# --------------------------------------------------------------------------- #
# SDDSession — Phase Transitions
# --------------------------------------------------------------------------- #


class TestSDDSessionTransitions:
    """SDDSession enforces strict lifecycle ordering."""

    def test_valid_forward_progression(self) -> None:
        """Sequential forward progression works correctly."""
        session = SDDSession(topic="test mission")
        assert session.phase == SDDPhase.SPECIFY

        session.advance_to(SDDPhase.PLAN)
        assert session.phase == SDDPhase.PLAN

        session.advance_to(SDDPhase.EXECUTE)
        assert session.phase == SDDPhase.EXECUTE

        session.advance_to(SDDPhase.VERIFY)
        assert session.phase == SDDPhase.VERIFY

        session.advance_to(SDDPhase.MERGE)
        assert session.phase == SDDPhase.MERGE
        assert session.sealed is True

    def test_skip_raises_error(self) -> None:
        """Skipping a phase raises ValueError."""
        session = SDDSession(topic="test")
        with pytest.raises(ValueError, match="phase skip"):
            session.advance_to(SDDPhase.EXECUTE)  # Skip PLAN

    def test_backward_raises_error(self) -> None:
        """Going backward raises ValueError."""
        session = SDDSession(topic="test")
        session.advance_to(SDDPhase.PLAN)
        with pytest.raises(ValueError, match="backward transition"):
            session.advance_to(SDDPhase.SPECIFY)

    def test_backward_with_override(self) -> None:
        """Backward transition succeeds with override=True."""
        session = SDDSession(topic="test")
        session.advance_to(SDDPhase.PLAN)
        session.advance_to(SDDPhase.SPECIFY, override=True, notes="Rethinking spec")
        assert session.phase == SDDPhase.SPECIFY

    def test_skip_with_override(self) -> None:
        """Phase skip succeeds with override=True."""
        session = SDDSession(topic="test")
        session.advance_to(SDDPhase.EXECUTE, override=True)
        assert session.phase == SDDPhase.EXECUTE

    def test_sealed_session_rejects_transition(self) -> None:
        """Sealed session (after MERGE) rejects all transitions."""
        session = SDDSession(topic="test")
        # Fast-forward to MERGE
        for phase in [SDDPhase.PLAN, SDDPhase.EXECUTE, SDDPhase.VERIFY, SDDPhase.MERGE]:
            session.advance_to(phase)

        assert session.sealed is True
        with pytest.raises(ValueError, match="sealed"):
            session.advance_to(SDDPhase.SPECIFY, override=True)

    def test_same_phase_transition_allowed(self) -> None:
        """Transitioning to the current phase is allowed (no-op progression)."""
        session = SDDSession(topic="test")
        # Same phase should work (it's not backward, not a skip)
        session.advance_to(SDDPhase.SPECIFY)
        assert session.phase == SDDPhase.SPECIFY


# --------------------------------------------------------------------------- #
# SDDSession — Phase History
# --------------------------------------------------------------------------- #


class TestSDDSessionHistory:
    """SDDSession tracks phase history correctly."""

    def test_history_records_transitions(self) -> None:
        """Each advance_to() adds a history entry."""
        session = SDDSession(topic="test")
        session.advance_to(SDDPhase.PLAN, notes="Spec ready")
        session.advance_to(SDDPhase.EXECUTE, notes="DAG built")

        assert len(session.phase_history) == 2
        assert session.phase_history[0]["from"] == "SPECIFY"
        assert session.phase_history[0]["to"] == "PLAN"
        assert session.phase_history[0]["notes"] == "Spec ready"
        assert session.phase_history[1]["from"] == "PLAN"
        assert session.phase_history[1]["to"] == "EXECUTE"

    def test_history_records_override(self) -> None:
        """Override flag is recorded in history."""
        session = SDDSession(topic="test")
        session.advance_to(SDDPhase.PLAN)
        session.advance_to(SDDPhase.SPECIFY, override=True, notes="Rethinking")

        assert session.phase_history[1]["override"] is True
        assert session.phase_history[0]["override"] is False

    def test_history_has_timestamps(self) -> None:
        """Each history entry has a timestamp."""
        session = SDDSession(topic="test")
        session.advance_to(SDDPhase.PLAN)

        assert "timestamp" in session.phase_history[0]
        assert isinstance(session.phase_history[0]["timestamp"], float)


# --------------------------------------------------------------------------- #
# SDDSession — Serialization
# --------------------------------------------------------------------------- #


class TestSDDSessionSerialization:
    """SDDSession serializes and deserializes correctly."""

    def test_to_dict_roundtrip(self) -> None:
        """to_dict() produces a valid dict with all fields."""
        session = SDDSession(topic="auth module upgrade")
        session.spec = {"raw_spec": "Must use mTLS", "topic": "auth"}
        session.advance_to(SDDPhase.PLAN, notes="Spec created")
        session.task_dag = [{"task": "implement mTLS", "deps": []}]

        d = session.to_dict()
        assert d["topic"] == "auth module upgrade"
        assert d["phase"] == "PLAN"
        assert d["spec"]["raw_spec"] == "Must use mTLS"
        assert len(d["task_dag"]) == 1
        assert d["sealed"] is False
        assert d["response_count"] == 0

    def test_to_dict_sealed(self) -> None:
        """Sealed session serializes correctly."""
        session = SDDSession(topic="test")
        for phase in [SDDPhase.PLAN, SDDPhase.EXECUTE, SDDPhase.VERIFY, SDDPhase.MERGE]:
            session.advance_to(phase)

        d = session.to_dict()
        assert d["sealed"] is True
        assert d["phase"] == "MERGE"
        assert len(d["phase_history"]) == 4

    def test_to_dict_with_verification(self) -> None:
        """Verification report is included in serialization."""
        session = SDDSession(topic="test")
        session.verification_report = {"verdict": "APPROVED", "verifier": "cove"}

        d = session.to_dict()
        assert d["verification_report"]["verdict"] == "APPROVED"
