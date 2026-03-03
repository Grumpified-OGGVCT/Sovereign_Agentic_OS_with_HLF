"""
Canary Agent — Phase 4.1 & 4.3 synthetic health monitor.

Responsibilities:
1. **15-minute synthetic probe**: injects a well-known HLF intent into the MoMA Router
   every 15 minutes to prove end-to-end swarm viability.  A probe failure is logged with
   anomaly_score=1.0 which fires the Semantic Outlier Trap and alerts the Sentinel.

2. **Idle Curiosity Protocol**: if no real intent has been received for >60 minutes the
   canary queries the Fact_Store for low-confidence vector clusters and logs them for
   autonomous follow-up research during the Dreaming State.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from agents.core.logger import ALSLogger

_logger = ALSLogger(agent_role="canary-agent", goal_id="synthetic-probe")

# Synthetic probe payload — a minimal valid HLF intent that the gateway can parse
_PROBE_HLF = '[HLF-v2]\n[INTENT] canary_probe "health"\n[EXPECT] "ok"\n[RESULT] code=0 message="canary"\nΩ\n'

_PROBE_INTERVAL_SEC: int = int(os.environ.get("CANARY_PROBE_INTERVAL", "900"))  # 15 min
_IDLE_THRESHOLD_SEC: int = int(os.environ.get("CANARY_IDLE_THRESHOLD", "3600"))  # 60 min
_GATEWAY_URL: str = os.environ.get("GATEWAY_URL", "http://gateway-node:40404")
_DB_PATH = Path(os.environ.get("BASE_DIR", "/app")) / "data" / "sqlite" / "memory.db"


# --------------------------------------------------------------------------- #
# Probe logic
# --------------------------------------------------------------------------- #

_probe_failure_count = 0


def _fire_probe() -> bool:
    """Send a synthetic probe intent to the gateway. Returns True on success."""
    global _probe_failure_count
    try:
        resp = httpx.post(
            f"{_GATEWAY_URL}/api/v1/intent",
            json={"hlf": _PROBE_HLF},
            timeout=15.0,
        )
        if resp.status_code == 202:
            _logger.log(
                "CANARY_PROBE_OK",
                {"status": resp.status_code},
                confidence_score=1.0,
                anomaly_score=0.0,
            )
            _probe_failure_count = 0
            return True

        _probe_failure_count += 1
        _logger.log(
            "CANARY_PROBE_FAIL",
            {"status": resp.status_code, "detail": resp.text[:200], "consecutive_failures": _probe_failure_count},
            confidence_score=0.0,
            anomaly_score=1.0,  # fires Semantic Outlier Trap
        )
        if _probe_failure_count >= 3:
            _logger.log("CANARY_CRITICAL_THRESHOLD", {"failures": _probe_failure_count}, anomaly_score=1.0)
            # Trip a system-wide health signal (e.g., set a Redis key that the router checks)
            try:
                import redis

                r = redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
                r.setex("health:gateway:failed", 600, "true")  # 10 min TTL
            except Exception:
                pass
        return False
    except Exception as exc:
        _probe_failure_count += 1
        _logger.log(
            "CANARY_PROBE_ERROR",
            {"error": str(exc), "consecutive_failures": _probe_failure_count},
            confidence_score=0.0,
            anomaly_score=1.0,
        )
        return False


# --------------------------------------------------------------------------- #
# Idle Curiosity Protocol
# --------------------------------------------------------------------------- #


def _idle_curiosity_scan(db_path: Path | None = None) -> list[dict[str, Any]]:
    """
    Query the Fact_Store for low-confidence vector clusters (confidence < 0.5).
    Returns a list of entity dicts for the Dreaming State to research.
    *db_path* defaults to the module-level ``_DB_PATH``; override in tests.
    """
    path = db_path if db_path is not None else _DB_PATH
    if not path.exists():
        return []
    try:
        import sqlite3

        conn = sqlite3.connect(str(path), check_same_thread=False)
        rows = conn.execute(
            "SELECT entity_id, semantic_relationship, confidence_score "
            "FROM fact_store WHERE confidence_score < 0.5 "
            "ORDER BY confidence_score ASC LIMIT 20"
        ).fetchall()
        conn.close()
        gaps = [
            {
                "entity_id": r[0],
                "relationship": r[1],
                "confidence": r[2],
            }
            for r in rows
        ]
        if gaps:
            _logger.log(
                "IDLE_CURIOSITY_GAPS_FOUND",
                {"count": len(gaps), "lowest_confidence": gaps[0]["confidence"]},
                anomaly_score=0.3,
            )
        return gaps
    except Exception as exc:
        _logger.log("IDLE_CURIOSITY_ERROR", {"error": str(exc)}, anomaly_score=0.5)
        return []


# --------------------------------------------------------------------------- #
# Background loop
# --------------------------------------------------------------------------- #


def _canary_loop(stop_event: threading.Event) -> None:
    """
    Main canary loop.  Runs in a daemon thread.

    - Fires a synthetic probe every CANARY_PROBE_INTERVAL seconds.
    - Checks for idle system every tick; triggers curiosity scan if idle.
    """
    from agents.gateway.router import get_last_intent_timestamp, is_system_idle

    last_probe = 0.0
    while not stop_event.is_set():
        try:
            now = time.time()

            # 15-min probe
            if now - last_probe >= _PROBE_INTERVAL_SEC:
                _fire_probe()
                last_probe = now

            # Idle Curiosity Protocol (60-min zero-intent threshold)
            if is_system_idle(_IDLE_THRESHOLD_SEC):
                gaps = _idle_curiosity_scan()
                if gaps:
                    _logger.log(
                        "IDLE_CURIOSITY_TRIGGERED",
                        {
                            # Use the same last-intent timestamp that is_system_idle() checks
                            "idle_sec": int(now - get_last_intent_timestamp()),
                            "gaps": gaps[:5],  # include top-5 in trace
                        },
                    )
                # Feed gaps to Dream State queue in Redis
                try:
                    import redis

                    r = redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
                    for gap in gaps[:5]:
                        r.xadd(
                            "dream_state_queue", {"entity_id": gap["entity_id"], "confidence": str(gap["confidence"])}
                        )  # noqa: E501
                except Exception:
                    pass
        except Exception as exc:
            _logger.log("CANARY_LOOP_ERROR", {"error": str(exc)}, anomaly_score=0.5)

        stop_event.wait(timeout=60)  # check every 60 s


_stop_event: threading.Event = threading.Event()
_thread: threading.Thread | None = None


def start() -> None:
    """Start the canary daemon thread (idempotent)."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(
        target=_canary_loop,
        args=(_stop_event,),
        daemon=True,
        name="canary-agent",
    )
    _thread.start()
    _logger.log("CANARY_AGENT_STARTED", {"probe_interval_sec": _PROBE_INTERVAL_SEC})


def stop() -> None:
    """Signal the canary thread to exit cleanly."""
    _stop_event.set()
    if _thread:
        _thread.join(timeout=10)
    _logger.log("CANARY_AGENT_STOPPED", {})
