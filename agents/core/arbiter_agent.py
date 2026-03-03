"""
Arbiter Agent — Aegis-Nexus Engine (Blue Hat / Governance).

Responsibilities:
1. Consume ``arbiter_events`` Redis Stream (alerts from Sentinel + Scribe).
2. Adjudicate events against ALIGN Ledger rules.
3. Emit verdicts: ALLOW | ESCALATE | QUARANTINE.
4. Handle exceptions from other agents (dead-letter adjudication).
5. Gas-accounted: each adjudication costs 2 units from the per-tier bucket.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

from agents.core.logger import ALSLogger
from agents.gateway.sentinel_gate import enforce_align

_logger = ALSLogger(agent_role="arbiter-agent", goal_id="governance")

# Redis stream name
ARBITER_EVENTS_STREAM = "arbiter_events"
_CONSUMER_GROUP = "arbiter-group"

# Gas cost per adjudication
ADJUDICATE_GAS_COST = 2

# Verdict constants
VERDICT_ALLOW = "ALLOW"
VERDICT_ESCALATE = "ESCALATE"
VERDICT_QUARANTINE = "QUARANTINE"

# Budget thresholds for Scribe-sourced BUDGET_GATE events
_BUDGET_ESCALATE_PCT: float = float(os.environ.get("ARBITER_BUDGET_ESCALATE_PCT", "0.90"))
_BUDGET_QUARANTINE_PCT: float = float(os.environ.get("ARBITER_BUDGET_QUARANTINE_PCT", "0.98"))


@dataclass
class ArbiterVerdict:
    """Result of an Arbiter adjudication."""

    verdict: str  # ALLOW | ESCALATE | QUARANTINE
    rule_id: str = ""
    justification: str = ""
    event_type: str = ""


def get_agent_profile() -> dict[str, Any]:
    """Return the Arbiter AgentProfile spec for DB template registration."""
    return {
        "name": "arbiter",
        "required_tier": "D",
        "system_prompt": (
            "You are the ARBITER agent — Sovereign OS governance adjudicator. "
            "Evaluate escalations from Sentinel and Scribe agents, apply ALIGN Ledger "
            "rules, and emit authoritative verdicts: ALLOW, ESCALATE, or QUARANTINE."
        ),
        "tools": ["adjudicate", "enforce_align"],
        "restrictions": {
            "max_tokens": 4096,
            "temperature": 0.0,
            "gas_per_adjudication": ADJUDICATE_GAS_COST,
        },
    }


def adjudicate(event_type: str, payload: str | dict[str, Any]) -> ArbiterVerdict:
    """
    Adjudicate an event from Sentinel or Scribe.

    Decision logic:

    * ``SECURITY_ALERT`` (from Sentinel):
        - ALIGN-blocked content → ``QUARANTINE``
        - ``CRITICAL`` severity → ``QUARANTINE``
        - Any other severity → ``ESCALATE``
    * ``BUDGET_GATE`` (from Scribe):
        - pct ≥ 0.98 → ``QUARANTINE`` (emergency)
        - pct ≥ 0.90 → ``ESCALATE``
        - pct < 0.90 → ``ALLOW``
    * Unknown event type → ``ESCALATE`` (fail-safe)

    ALIGN Ledger is always checked first as the authoritative override.
    """
    text = json.dumps(payload) if isinstance(payload, dict) else str(payload)

    # ALIGN Ledger is the authoritative override
    blocked, rule_id = enforce_align(text)
    if blocked:
        _logger.log(
            "ARBITER_ALIGN_BLOCK",
            {"rule_id": rule_id, "event_type": event_type},
            anomaly_score=0.95,
        )
        return ArbiterVerdict(
            verdict=VERDICT_QUARANTINE,
            rule_id=rule_id,
            justification=f"ALIGN Ledger rule {rule_id} matched",
            event_type=event_type,
        )

    if event_type == "SECURITY_ALERT":
        data = payload if isinstance(payload, dict) else {}
        severity = data.get("severity", "HIGH")
        src_rule = data.get("rule_id", "")
        if severity == "CRITICAL":
            return ArbiterVerdict(
                verdict=VERDICT_QUARANTINE,
                rule_id=src_rule,
                justification=f"Critical security alert: {src_rule}",
                event_type=event_type,
            )
        return ArbiterVerdict(
            verdict=VERDICT_ESCALATE,
            rule_id=src_rule,
            justification=f"Security alert escalated: {src_rule}",
            event_type=event_type,
        )

    if event_type == "BUDGET_GATE":
        data = payload if isinstance(payload, dict) else {}
        pct = float(data.get("pct", 0.0))
        if pct >= _BUDGET_QUARANTINE_PCT:
            return ArbiterVerdict(
                verdict=VERDICT_QUARANTINE,
                justification=f"Token budget at {pct:.1%} — emergency quarantine",
                event_type=event_type,
            )
        if pct >= _BUDGET_ESCALATE_PCT:
            return ArbiterVerdict(
                verdict=VERDICT_ESCALATE,
                justification=f"Token budget at {pct:.1%} — escalation required",
                event_type=event_type,
            )
        return ArbiterVerdict(
            verdict=VERDICT_ALLOW,
            justification=f"Token budget at {pct:.1%} — within limits",
            event_type=event_type,
        )

    # Unknown event type — fail-safe escalation
    _logger.log(
        "ARBITER_UNKNOWN_EVENT",
        {"event_type": event_type, "preview": text[:80]},
        anomaly_score=0.3,
    )
    return ArbiterVerdict(
        verdict=VERDICT_ESCALATE,
        justification=f"Unknown event type: {event_type}",
        event_type=event_type,
    )


# --------------------------------------------------------------------------- #
# Background consumer
# --------------------------------------------------------------------------- #


def _consume_loop(stop_event: threading.Event) -> None:
    """Redis XREADGROUP consumer for ``arbiter_events``."""
    try:
        import redis  # noqa: PLC0415

        r = redis.from_url(
            os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        )
        with contextlib.suppress(Exception):
            r.xgroup_create(ARBITER_EVENTS_STREAM, _CONSUMER_GROUP, id="0", mkstream=True)

        tier = os.environ.get("DEPLOYMENT_TIER", "hearth")
        _logger.log("ARBITER_AGENT_CONSUMER_READY", {"stream": ARBITER_EVENTS_STREAM})

        from agents.gateway.router import consume_gas  # noqa: PLC0415

        while not stop_event.is_set():
            try:
                messages = r.xreadgroup(
                    _CONSUMER_GROUP,
                    "arbiter-1",
                    {ARBITER_EVENTS_STREAM: ">"},
                    count=1,
                    block=2000,
                )
                if not messages:
                    continue

                for _stream, entries in messages:
                    for entry_id, data in entries:
                        raw = data.get("data", "{}")
                        try:
                            if not consume_gas(tier, ADJUDICATE_GAS_COST, r):
                                _logger.log("ARBITER_GAS_EXHAUSTED", {}, anomaly_score=0.3)
                                r.xack(ARBITER_EVENTS_STREAM, _CONSUMER_GROUP, entry_id)
                                continue

                            event_data = json.loads(raw) if raw else {}
                            event_type = event_data.get("event_type", "UNKNOWN")
                            verdict = adjudicate(event_type, event_data)
                            _logger.log(
                                "ARBITER_VERDICT",
                                {
                                    "verdict": verdict.verdict,
                                    "rule_id": verdict.rule_id,
                                    "event_type": verdict.event_type,
                                    "justification": verdict.justification,
                                },
                                anomaly_score=0.9 if verdict.verdict == VERDICT_QUARANTINE else 0.0,
                            )
                        except Exception as exc:
                            _logger.log("ARBITER_CONSUME_ERROR", {"error": str(exc)}, anomaly_score=0.6)
                        finally:
                            r.xack(ARBITER_EVENTS_STREAM, _CONSUMER_GROUP, entry_id)
            except Exception as exc:
                _logger.log("ARBITER_LOOP_ERROR", {"error": str(exc)}, anomaly_score=0.5)
                time.sleep(2)
    except Exception as exc:
        _logger.log("ARBITER_FATAL", {"error": str(exc)}, anomaly_score=1.0)


_stop_event: threading.Event = threading.Event()
_thread: threading.Thread | None = None


def start() -> None:
    """Start the Arbiter Agent daemon thread (idempotent)."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(
        target=_consume_loop,
        args=(_stop_event,),
        daemon=True,
        name="arbiter-agent",
    )
    _thread.start()
    _logger.log("ARBITER_AGENT_STARTED", {})


def stop() -> None:
    """Signal the Arbiter Agent thread to exit cleanly."""
    _stop_event.set()
    if _thread:
        _thread.join(timeout=10)
    _logger.log("ARBITER_AGENT_STOPPED", {})
