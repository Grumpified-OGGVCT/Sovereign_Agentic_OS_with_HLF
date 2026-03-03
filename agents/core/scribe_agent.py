"""
Scribe Agent — Aegis-Nexus Engine (White Hat / Memory & Token).

Responsibilities:
1. Monitor ``rolling_context`` token usage from SQLite.
2. Enforce 80% token-budget gate — publish BUDGET_GATE events when breached.
3. Consume ``scribe_events`` Redis Stream for on-demand audit requests.
4. Publish to ``arbiter_events`` on budget breach.
5. Gas-accounted: each audit costs 1 unit from the per-tier bucket.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents.core.logger import ALSLogger

_logger = ALSLogger(agent_role="scribe-agent", goal_id="memory-audit")

# Redis stream names
SCRIBE_EVENTS_STREAM = "scribe_events"
ARBITER_EVENTS_STREAM = "arbiter_events"
_CONSUMER_GROUP = "scribe-group"

# Gas cost per audit
AUDIT_GAS_COST = 1

# 80% gate threshold (configurable via env)
BUDGET_GATE_PCT: float = float(os.environ.get("SCRIBE_BUDGET_GATE_PCT", "0.80"))

_DB_PATH = Path(os.environ.get("BASE_DIR", "/app")) / "data" / "sqlite" / "memory.db"


@dataclass
class BudgetStatus:
    """Result of a Scribe token-budget audit."""

    tokens_used: int
    budget: int
    pct: float
    gate_blocked: bool


def get_agent_profile() -> dict[str, Any]:
    """Return the Scribe AgentProfile spec for DB template registration."""
    return {
        "name": "scribe",
        "required_tier": "D",
        "system_prompt": (
            "You are the SCRIBE agent — Sovereign OS memory and token auditor. "
            "Monitor rolling context token usage, detect token bloat, and enforce "
            "the 80% budget gate. Return structured JSON findings."
        ),
        "tools": ["audit_budget", "get_rolling_context_stats"],
        "restrictions": {
            "max_tokens": 2048,
            "temperature": 0.0,
            "gas_per_audit": AUDIT_GAS_COST,
            "budget_gate_pct": BUDGET_GATE_PCT,
        },
    }


def audit_budget(
    conn: sqlite3.Connection | None = None,
    db_path: Path | None = None,
    max_context_tokens: int | None = None,
) -> BudgetStatus:
    """
    Read ``rolling_context`` from SQLite and compute token usage vs. budget.

    Returns :class:`BudgetStatus`.  ``gate_blocked=True`` when usage ≥
    :data:`BUDGET_GATE_PCT`.

    Precedence for the DB connection:
      1. *conn* (already-open connection, used as-is)
      2. *db_path* (path to open)
      3. Module-level ``_DB_PATH`` default
    """
    budget = max_context_tokens or int(os.environ.get("MAX_CONTEXT_TOKENS", "8192"))

    def _run(c: sqlite3.Connection) -> BudgetStatus:
        try:
            row = c.execute("SELECT COALESCE(SUM(token_count), 0) FROM rolling_context").fetchone()
            used = int(row[0]) if row else 0
        except Exception:
            used = 0
        pct = used / budget if budget > 0 else 0.0
        blocked = pct >= BUDGET_GATE_PCT
        return BudgetStatus(tokens_used=used, budget=budget, pct=pct, gate_blocked=blocked)

    if conn is not None:
        return _run(conn)

    path = db_path or _DB_PATH
    if not path.exists():
        return BudgetStatus(tokens_used=0, budget=budget, pct=0.0, gate_blocked=False)

    try:
        c = sqlite3.connect(str(path), check_same_thread=False)
        status = _run(c)
        c.close()
        return status
    except Exception as exc:
        _logger.log("SCRIBE_AUDIT_ERROR", {"error": str(exc)}, anomaly_score=0.4)
        return BudgetStatus(tokens_used=0, budget=budget, pct=0.0, gate_blocked=False)


def _publish_budget_alert(r: Any, status: BudgetStatus) -> None:
    """Publish a BUDGET_GATE event to ``arbiter_events``."""
    try:
        r.xadd(
            ARBITER_EVENTS_STREAM,
            {
                "data": json.dumps(
                    {
                        "event_type": "BUDGET_GATE",
                        "source_agent": "scribe",
                        "tokens_used": status.tokens_used,
                        "budget": status.budget,
                        "pct": round(status.pct, 4),
                        "ts": time.time(),
                    }
                ),
            },
        )
    except Exception as exc:
        _logger.log("SCRIBE_PUBLISH_ERROR", {"error": str(exc)}, anomaly_score=0.5)


# --------------------------------------------------------------------------- #
# Background consumer
# --------------------------------------------------------------------------- #


def _consume_loop(stop_event: threading.Event) -> None:
    """Redis XREADGROUP consumer for ``scribe_events``."""
    try:
        import redis  # noqa: PLC0415

        r = redis.from_url(
            os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        )
        with contextlib.suppress(Exception):
            r.xgroup_create(SCRIBE_EVENTS_STREAM, _CONSUMER_GROUP, id="0", mkstream=True)

        tier = os.environ.get("DEPLOYMENT_TIER", "hearth")
        _logger.log("SCRIBE_AGENT_CONSUMER_READY", {"stream": SCRIBE_EVENTS_STREAM})

        from agents.gateway.router import consume_gas  # noqa: PLC0415

        while not stop_event.is_set():
            try:
                messages = r.xreadgroup(
                    _CONSUMER_GROUP,
                    "scribe-1",
                    {SCRIBE_EVENTS_STREAM: ">"},
                    count=1,
                    block=2000,
                )
                if not messages:
                    continue

                for _stream, entries in messages:
                    for entry_id, _data in entries:
                        try:
                            if not consume_gas(tier, AUDIT_GAS_COST, r):
                                _logger.log("SCRIBE_GAS_EXHAUSTED", {}, anomaly_score=0.3)
                                r.xack(SCRIBE_EVENTS_STREAM, _CONSUMER_GROUP, entry_id)
                                continue

                            status = audit_budget()
                            if status.gate_blocked:
                                _publish_budget_alert(r, status)
                            _logger.log(
                                "SCRIBE_AUDIT_COMPLETE",
                                {
                                    "tokens_used": status.tokens_used,
                                    "budget": status.budget,
                                    "pct": round(status.pct, 4),
                                    "gate_blocked": status.gate_blocked,
                                },
                            )
                        except Exception as exc:
                            _logger.log("SCRIBE_CONSUME_ERROR", {"error": str(exc)}, anomaly_score=0.6)
                        finally:
                            r.xack(SCRIBE_EVENTS_STREAM, _CONSUMER_GROUP, entry_id)
            except Exception as exc:
                _logger.log("SCRIBE_LOOP_ERROR", {"error": str(exc)}, anomaly_score=0.5)
                time.sleep(2)
    except Exception as exc:
        _logger.log("SCRIBE_FATAL", {"error": str(exc)}, anomaly_score=1.0)


_stop_event: threading.Event = threading.Event()
_thread: threading.Thread | None = None


def start() -> None:
    """Start the Scribe Agent daemon thread (idempotent)."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(
        target=_consume_loop,
        args=(_stop_event,),
        daemon=True,
        name="scribe-agent",
    )
    _thread.start()
    _logger.log("SCRIBE_AGENT_STARTED", {})


def stop() -> None:
    """Signal the Scribe Agent thread to exit cleanly."""
    _stop_event.set()
    if _thread:
        _thread.join(timeout=10)
    _logger.log("SCRIBE_AGENT_STOPPED", {})
