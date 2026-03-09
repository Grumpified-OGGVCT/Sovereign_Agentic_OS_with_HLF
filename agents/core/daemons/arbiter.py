"""
Arbiter Daemon — Inter-agent dispute resolution via ALIGN adjudication.

When two or more agents disagree on an ALIGN rule interpretation or
a security decision, the Arbiter collects votes, checks ALIGN_LEDGER
precedence, and emits a binding ruling.

Features:
  - Majority vote collection with configurable quorum
  - ALIGN_LEDGER rule precedence checking
  - Dead-man switch: auto-escalate after timeout
  - Structured dispute log (data/arbiter_rulings.jsonl)

Part of the Aegis-Nexus runtime daemon triad (Issue #17).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from agents.core.daemons import DaemonEventBus

_logger = logging.getLogger("aegis.arbiter")


# ─── Dispute Resolution ─────────────────────────────────────────────────────


class DisputeOutcome(Enum):
    """Possible outcomes of a dispute resolution."""
    APPROVED = "approved"
    DENIED = "denied"
    ESCALATED = "escalated"
    TIMED_OUT = "timed_out"


@dataclass
class DisputeVote:
    """A vote from an agent in a dispute."""
    agent: str
    position: str       # "approve" or "deny"
    rationale: str = ""
    timestamp: str = ""


@dataclass
class DisputeRecord:
    """A complete record of a dispute and its resolution."""
    dispute_id: str
    rule: str                              # ALIGN rule in question
    subject: str                           # What is being disputed
    parties: list[str] = field(default_factory=list)
    votes: list[DisputeVote] = field(default_factory=list)
    outcome: DisputeOutcome = DisputeOutcome.ESCALATED
    ruling_rationale: str = ""
    timestamp: str = ""
    resolution_time_ms: float = 0.0


# ─── Arbiter Daemon ─────────────────────────────────────────────────────────


class ArbiterDaemon:
    """
    Inter-agent dispute resolution daemon.

    Listens for ALIGN rule disputes and resolves them via:
      1. Collect votes from involved agents
      2. Check ALIGN_LEDGER rule precedence
      3. Apply majority vote with precedence override
      4. Emit binding ruling to event bus

    Dead-man switch: If a dispute is not resolved within
    escalation_timeout_ms, it auto-escalates to CRITICAL priority.

    Args:
        event_bus: The shared daemon event bus.
        escalation_timeout_ms: Timeout before auto-escalation.
        enabled: Whether the daemon should activate.
        quorum: Minimum votes needed for a valid ruling.
        rulings_path: Path for the rulings log.
    """

    name = "arbiter"

    # ALIGN rules that always take precedence (cannot be overridden by vote)
    _IMMUTABLE_RULES = frozenset({
        "R-001",  # Sovereign-context actions require human approval
        "R-002",  # ALIGN Ledger modifications require dual approval
        "R-003",  # Sensitive data redaction in logs
    })

    def __init__(
        self,
        event_bus: DaemonEventBus | None = None,
        escalation_timeout_ms: int = 12000,
        enabled: bool = True,
        quorum: int = 2,
        rulings_path: Path | None = None,
    ):
        self._event_bus = event_bus
        self._escalation_timeout_ms = escalation_timeout_ms
        self._enabled = enabled
        self._quorum = quorum
        self._rulings_path = rulings_path

        # State
        from agents.core.daemons import DaemonStatus
        self._status = DaemonStatus.STOPPED
        self._active_disputes: dict[str, DisputeRecord] = {}
        self._resolved_disputes: list[DisputeRecord] = []
        self._dispute_counter: int = 0

    @property
    def status(self):
        from agents.core.daemons import DaemonStatus
        return self._status

    @status.setter
    def status(self, value):
        from agents.core.daemons import DaemonStatus
        self._status = value

    def start(self) -> None:
        """Start the Arbiter daemon."""
        from agents.core.daemons import DaemonStatus
        if not self._enabled:
            _logger.info("Arbiter daemon disabled, skipping start")
            return
        self._status = DaemonStatus.RUNNING
        _logger.info(
            "Arbiter daemon started (timeout=%dms, quorum=%d)",
            self._escalation_timeout_ms,
            self._quorum,
        )

    def stop(self) -> None:
        """Stop the Arbiter daemon and flush rulings."""
        from agents.core.daemons import DaemonStatus
        # Auto-escalate any remaining active disputes
        for dispute_id in list(self._active_disputes.keys()):
            self._escalate(dispute_id, reason="daemon_shutdown")
        if self._rulings_path and self._resolved_disputes:
            self._flush_rulings()
        self._status = DaemonStatus.STOPPED
        _logger.info(
            "Arbiter daemon stopped (%d disputes resolved)", len(self._resolved_disputes)
        )

    def open_dispute(
        self,
        rule: str,
        subject: str,
        parties: list[str],
    ) -> str:
        """
        Open a new dispute for adjudication.

        Args:
            rule: The ALIGN rule in question (e.g., "R-004").
            subject: What is being disputed.
            parties: Agent names involved in the dispute.

        Returns:
            Dispute ID for tracking.
        """
        from agents.core.daemons import DaemonStatus
        if self._status != DaemonStatus.RUNNING:
            raise RuntimeError("Arbiter is not running")

        import datetime

        self._dispute_counter += 1
        dispute_id = f"DSP-{self._dispute_counter:04d}"

        record = DisputeRecord(
            dispute_id=dispute_id,
            rule=rule,
            subject=subject,
            parties=parties,
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )
        self._active_disputes[dispute_id] = record

        _logger.info(
            "Dispute %s opened: rule=%s, subject=%s, parties=%s",
            dispute_id,
            rule,
            subject,
            parties,
        )

        if self._event_bus:
            from agents.core.daemons import DaemonEvent
            self._event_bus.emit(DaemonEvent(
                source="arbiter",
                event_type="dispute_opened",
                severity="warning",
                data={"dispute_id": dispute_id, "rule": rule, "subject": subject},
            ))

        return dispute_id

    def cast_vote(
        self,
        dispute_id: str,
        agent: str,
        position: str,
        rationale: str = "",
    ) -> bool:
        """
        Cast a vote in an active dispute.

        Args:
            dispute_id: The dispute to vote on.
            agent: Voting agent name.
            position: "approve" or "deny".
            rationale: Optional explanation.

        Returns:
            True if vote was accepted, False if dispute not found/closed.
        """
        import datetime

        record = self._active_disputes.get(dispute_id)
        if not record:
            _logger.warning("Vote for unknown/closed dispute: %s", dispute_id)
            return False

        if position not in ("approve", "deny"):
            _logger.warning("Invalid vote position: %s", position)
            return False

        # Prevent duplicate votes from same agent
        if any(v.agent == agent for v in record.votes):
            _logger.warning("Duplicate vote from %s in dispute %s", agent, dispute_id)
            return False

        vote = DisputeVote(
            agent=agent,
            position=position,
            rationale=rationale,
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )
        record.votes.append(vote)

        # Check if quorum reached
        if len(record.votes) >= self._quorum:
            self._resolve(dispute_id)

        return True

    def check_timeouts(self) -> list[str]:
        """
        Check for disputes that have exceeded the escalation timeout.

        Returns:
            List of dispute IDs that were auto-escalated.
        """
        import datetime

        escalated = []
        now = time.time()

        for dispute_id, record in list(self._active_disputes.items()):
            try:
                opened_time = datetime.datetime.fromisoformat(record.timestamp)
                elapsed_ms = (now - opened_time.timestamp()) * 1000
                if elapsed_ms > self._escalation_timeout_ms:
                    self._escalate(dispute_id, reason="timeout")
                    escalated.append(dispute_id)
            except (ValueError, TypeError):
                continue

        return escalated

    def get_dispute(self, dispute_id: str) -> DisputeRecord | None:
        """Get a dispute by ID (active or resolved)."""
        if dispute_id in self._active_disputes:
            return self._active_disputes[dispute_id]
        for record in self._resolved_disputes:
            if record.dispute_id == dispute_id:
                return record
        return None

    def get_stats(self) -> dict[str, Any]:
        """Get Arbiter statistics."""
        return {
            "status": self._status.value,
            "active_disputes": len(self._active_disputes),
            "resolved_disputes": len(self._resolved_disputes),
            "total_disputes": self._dispute_counter,
            "outcomes": {
                outcome.value: len([
                    r for r in self._resolved_disputes if r.outcome == outcome
                ])
                for outcome in DisputeOutcome
            },
        }

    # ─── Resolution Logic ────────────────────────────────────────────────

    def _resolve(self, dispute_id: str) -> None:
        """Resolve a dispute based on votes and ALIGN precedence."""
        record = self._active_disputes.get(dispute_id)
        if not record:
            return

        start = time.monotonic()

        # Immutable rules cannot be overridden by vote
        if record.rule in self._IMMUTABLE_RULES:
            record.outcome = DisputeOutcome.DENIED
            record.ruling_rationale = (
                f"ALIGN rule {record.rule} is immutable and cannot be overridden by vote. "
                f"Automatic DENY per ALIGN_LEDGER precedence."
            )
        else:
            # Majority vote
            approve_count = sum(1 for v in record.votes if v.position == "approve")
            deny_count = sum(1 for v in record.votes if v.position == "deny")

            if approve_count > deny_count:
                record.outcome = DisputeOutcome.APPROVED
                record.ruling_rationale = (
                    f"Majority vote: {approve_count} approve vs {deny_count} deny. "
                    f"Rule {record.rule} overridden by agent consensus."
                )
            elif deny_count > approve_count:
                record.outcome = DisputeOutcome.DENIED
                record.ruling_rationale = (
                    f"Majority vote: {deny_count} deny vs {approve_count} approve. "
                    f"Rule {record.rule} enforcement upheld."
                )
            else:
                # Tie goes to enforcement (conservative)
                record.outcome = DisputeOutcome.DENIED
                record.ruling_rationale = (
                    f"Tie vote: {approve_count}-{deny_count}. "
                    f"Tie breaks to enforcement per ALIGN conservative principle."
                )

        record.resolution_time_ms = (time.monotonic() - start) * 1000

        # Move to resolved
        del self._active_disputes[dispute_id]
        self._resolved_disputes.append(record)

        # Emit ruling
        if self._event_bus:
            from agents.core.daemons import DaemonEvent
            self._event_bus.emit(DaemonEvent(
                source="arbiter",
                event_type="ruling",
                severity="info" if record.outcome == DisputeOutcome.APPROVED else "warning",
                data={
                    "dispute_id": dispute_id,
                    "outcome": record.outcome.value,
                    "rationale": record.ruling_rationale,
                },
            ))

        _logger.info(
            "Dispute %s resolved: %s — %s",
            dispute_id,
            record.outcome.value,
            record.ruling_rationale[:80],
        )

    def _escalate(self, dispute_id: str, reason: str = "timeout") -> None:
        """Escalate an unresolved dispute to CRITICAL priority."""
        record = self._active_disputes.get(dispute_id)
        if not record:
            return

        if reason == "timeout":
            record.outcome = DisputeOutcome.TIMED_OUT
            record.ruling_rationale = (
                f"Dispute timed out after {self._escalation_timeout_ms}ms. "
                f"Auto-escalated to CRITICAL priority for human review."
            )
        else:
            record.outcome = DisputeOutcome.ESCALATED
            record.ruling_rationale = f"Escalated: {reason}"

        del self._active_disputes[dispute_id]
        self._resolved_disputes.append(record)

        if self._event_bus:
            from agents.core.daemons import DaemonEvent
            self._event_bus.emit(DaemonEvent(
                source="arbiter",
                event_type="escalation",
                severity="critical",
                data={
                    "dispute_id": dispute_id,
                    "reason": reason,
                    "outcome": record.outcome.value,
                },
            ))

        _logger.warning("Dispute %s escalated: %s", dispute_id, reason)

    def _flush_rulings(self) -> None:
        """Write resolved disputes to the rulings log."""
        if not self._rulings_path:
            return
        try:
            self._rulings_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._rulings_path, "a", encoding="utf-8") as f:
                for record in self._resolved_disputes:
                    f.write(json.dumps({
                        "dispute_id": record.dispute_id,
                        "rule": record.rule,
                        "subject": record.subject,
                        "parties": record.parties,
                        "votes": [
                            {"agent": v.agent, "position": v.position, "rationale": v.rationale}
                            for v in record.votes
                        ],
                        "outcome": record.outcome.value,
                        "ruling_rationale": record.ruling_rationale,
                        "timestamp": record.timestamp,
                        "resolution_time_ms": round(record.resolution_time_ms, 2),
                    }) + "\n")
        except OSError as e:
            _logger.error("Failed to flush Arbiter rulings: %s", e)
