"""
Canary Agent — Phase 4.1 & 4.3 synthetic health monitor.

Responsibilities:
1. **15-minute synthetic probe**: injects a well-known HLF intent into the MoMA Router
   every 15 minutes to prove end-to-end swarm viability.  A probe failure is logged with
   anomaly_score=1.0 which fires the Semantic Outlier Trap and alerts the Sentinel.

2. **Idle Curiosity Protocol**: if no real intent has been received for >60 minutes the
   canary queries the Fact_Store for low-confidence vector clusters and logs them for
   autonomous follow-up research during the Dreaming State.

3. **Early Warning Detection** (Phase 4.3 enhancement): rolling-window latency trending,
   success-rate monitoring, supplementary Redis/DB health checks, and escalating severity
   alerts so degradation is surfaced *before* hard failures.
"""

from __future__ import annotations

import os
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass, field
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

# Early-warning thresholds (all configurable via env)
_LATENCY_WARN_MS: float = float(os.environ.get("CANARY_LATENCY_WARN_MS", "3000"))   # 3 s
_LATENCY_SIGMA: float = float(os.environ.get("CANARY_LATENCY_SIGMA", "2.5"))        # z-score threshold
_SUCCESS_RATE_WARN: float = float(os.environ.get("CANARY_SUCCESS_RATE_WARN", "0.8"))  # 80 %

# Escalation thresholds and TTLs
_EARLY_WARNING_THRESHOLD: int = 1    # consecutive failures before early-warning alert
_CRITICAL_THRESHOLD: int = 3         # consecutive failures before CRITICAL alert
_DEAD_MAN_THRESHOLD: int = 5         # consecutive failures before dead-man signal
_CRITICAL_TTL_SEC: int = 600         # Redis flag TTL for CRITICAL health flag (10 min)
_DEAD_MAN_TTL_SEC: int = 1800        # Redis flag TTL for dead-man health flag (30 min)


# --------------------------------------------------------------------------- #
# Structured probe result
# --------------------------------------------------------------------------- #


@dataclass
class ProbeResult:
    """Structured outcome of a single synthetic probe."""

    success: bool
    status_code: int = 0
    latency_ms: float = 0.0
    error: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "status_code": self.status_code,
            "latency_ms": round(self.latency_ms, 2),
            "error": self.error,
            "timestamp": self.timestamp,
        }


# --------------------------------------------------------------------------- #
# Rolling health window — anomaly detection
# --------------------------------------------------------------------------- #


class CanaryHealthWindow:
    """Rolling window of probe results for statistical early-warning detection.

    Tracks:
      - Probe latencies (z-score spike detection)
      - Consecutive failure streak
      - Success rate over the rolling window
    """

    def __init__(self, *, window_size: int = 20) -> None:
        self._window_size = window_size
        self._results: deque[ProbeResult] = deque(maxlen=window_size)
        self._latencies: deque[float] = deque(maxlen=window_size)

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def record(self, result: ProbeResult) -> None:
        """Add a probe result to the window."""
        self._results.append(result)
        if result.success:
            self._latencies.append(result.latency_ms)

    # ------------------------------------------------------------------
    # Derived metrics
    # ------------------------------------------------------------------

    @property
    def total_probes(self) -> int:
        return len(self._results)

    @property
    def consecutive_failures(self) -> int:
        """Count failures trailing from the most-recent probe."""
        count = 0
        for r in reversed(self._results):
            if not r.success:
                count += 1
            else:
                break
        return count

    @property
    def success_rate(self) -> float:
        """Fraction of successful probes in the current window (0.0–1.0)."""
        if not self._results:
            return 1.0
        return sum(1 for r in self._results if r.success) / len(self._results)

    @property
    def mean_latency_ms(self) -> float:
        if not self._latencies:
            return 0.0
        return statistics.mean(self._latencies)

    def is_latency_spike(self, latency_ms: float, sigma: float = _LATENCY_SIGMA) -> bool:
        """Return True when *latency_ms* exceeds the rolling mean by *sigma* std-devs.

        Requires at least 5 successful probes to build a baseline.

        A minimum variance floor of 1.0 ms is applied to ``stdev`` (via
        ``max(stdev, 1.0)``) so that spike detection remains meaningful even when
        all historical probes have nearly identical latencies (stdev ≈ 0).
        """
        if len(self._latencies) < 5:
            return False
        mean = statistics.mean(self._latencies)
        stdev = statistics.stdev(self._latencies) if len(self._latencies) > 1 else 0.0
        return latency_ms > mean + sigma * max(stdev, 1.0)

    def get_stats(self) -> dict[str, Any]:
        return {
            "total_probes": self.total_probes,
            "consecutive_failures": self.consecutive_failures,
            "success_rate": round(self.success_rate, 4),
            "mean_latency_ms": round(self.mean_latency_ms, 2),
            "window_size": self._window_size,
        }


# --------------------------------------------------------------------------- #
# Module-level health window + failure counter
# --------------------------------------------------------------------------- #

_health_window = CanaryHealthWindow()
_probe_failure_count = 0


# --------------------------------------------------------------------------- #
# Probe logic
# --------------------------------------------------------------------------- #


def _fire_probe() -> bool:
    """Send a synthetic probe intent to the gateway. Returns True on success."""
    global _probe_failure_count
    t_start = time.monotonic()
    try:
        resp = httpx.post(
            f"{_GATEWAY_URL}/api/v1/intent",
            json={"hlf": _PROBE_HLF},
            timeout=15.0,
        )
        latency_ms = (time.monotonic() - t_start) * 1000.0

        if resp.status_code == 202:
            result = ProbeResult(success=True, status_code=resp.status_code, latency_ms=latency_ms)
            _health_window.record(result)

            # Latency early-warning — flag degradation before hard failures
            if _health_window.is_latency_spike(latency_ms):
                _logger.log(
                    "CANARY_LATENCY_SPIKE",
                    {
                        "latency_ms": round(latency_ms, 2),
                        "mean_ms": round(_health_window.mean_latency_ms, 2),
                    },
                    confidence_score=0.7,
                    anomaly_score=0.6,
                )
            elif latency_ms > _LATENCY_WARN_MS:
                _logger.log(
                    "CANARY_LATENCY_HIGH",
                    {"latency_ms": round(latency_ms, 2), "threshold_ms": _LATENCY_WARN_MS},
                    confidence_score=0.8,
                    anomaly_score=0.4,
                )

            # Success-rate early-warning (degraded but not dead)
            if _health_window.total_probes >= 5 and _health_window.success_rate < _SUCCESS_RATE_WARN:
                _logger.log(
                    "CANARY_SUCCESS_RATE_LOW",
                    {
                        "success_rate": round(_health_window.success_rate, 4),
                        "threshold": _SUCCESS_RATE_WARN,
                    },
                    confidence_score=0.6,
                    anomaly_score=0.7,
                )

            _logger.log(
                "CANARY_PROBE_OK",
                {"status": resp.status_code, "latency_ms": round(latency_ms, 2)},
                confidence_score=1.0,
                anomaly_score=0.0,
            )
            _probe_failure_count = 0
            return True

        result = ProbeResult(success=False, status_code=resp.status_code, latency_ms=latency_ms)
        _health_window.record(result)
        _probe_failure_count += 1
        consecutive = _health_window.consecutive_failures
        _logger.log(
            "CANARY_PROBE_FAIL",
            {
                "status": resp.status_code,
                "detail": resp.text[:200],
                "consecutive_failures": _probe_failure_count,
                "latency_ms": round(latency_ms, 2),
            },
            confidence_score=0.0,
            anomaly_score=1.0,  # fires Semantic Outlier Trap
        )
        _escalate_on_failures(consecutive)
        return False
    except Exception as exc:
        latency_ms = (time.monotonic() - t_start) * 1000.0
        result = ProbeResult(success=False, latency_ms=latency_ms, error=str(exc))
        _health_window.record(result)
        _probe_failure_count += 1
        consecutive = _health_window.consecutive_failures
        _logger.log(
            "CANARY_PROBE_ERROR",
            {"error": str(exc), "consecutive_failures": _probe_failure_count},
            confidence_score=0.0,
            anomaly_score=1.0,
        )
        _escalate_on_failures(consecutive)
        return False


def _escalate_on_failures(consecutive: int) -> None:
    """Emit escalating alerts based on consecutive failure count.

    * 1 failure  → WARNING (early-warning, may be transient)
    * 3 failures → CRITICAL (system-wide health flag in Redis)
    * 5+ failures → CRITICAL + dead-man signal
    """
    if consecutive >= _DEAD_MAN_THRESHOLD:
        _logger.log(
            "CANARY_DEAD_MAN",
            {"consecutive_failures": consecutive},
            confidence_score=0.0,
            anomaly_score=1.0,
        )
        _set_redis_health_flag("health:gateway:dead", _DEAD_MAN_TTL_SEC)
    elif consecutive >= _CRITICAL_THRESHOLD:
        _logger.log("CANARY_CRITICAL_THRESHOLD", {"failures": consecutive}, anomaly_score=1.0)
        _set_redis_health_flag("health:gateway:failed", _CRITICAL_TTL_SEC)
    elif consecutive == _EARLY_WARNING_THRESHOLD:
        _logger.log(
            "CANARY_EARLY_WARNING",
            {"consecutive_failures": consecutive},
            confidence_score=0.3,
            anomaly_score=0.5,
        )


def _set_redis_health_flag(key: str, ttl_sec: int) -> None:
    """Write a health flag to Redis; silent on connection errors."""
    try:
        import redis

        r = redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
        r.setex(key, ttl_sec, "true")
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Supplementary service-health checks
# --------------------------------------------------------------------------- #


def _check_redis_health() -> bool:
    """Ping Redis; return True if reachable, False otherwise."""
    try:
        import redis

        r = redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
        r.ping()
        return True
    except Exception as exc:
        _logger.log(
            "CANARY_REDIS_UNHEALTHY",
            {"error": str(exc)},
            confidence_score=0.0,
            anomaly_score=0.8,
        )
        return False


def _check_db_health(db_path: Path | None = None) -> bool:
    """Verify the SQLite memory database is accessible; return True if ok.

    Reads the database's sqlite_master catalogue to confirm the file is a
    valid, non-corrupted SQLite database (not just a pure expression).
    """
    path = db_path if db_path is not None else _DB_PATH
    if not path.exists():
        return True  # DB not yet created — not a health failure
    try:
        import sqlite3

        conn = sqlite3.connect(str(path))
        conn.execute("SELECT * FROM sqlite_master LIMIT 1").fetchall()
        conn.close()
        return True
    except Exception as exc:
        _logger.log(
            "CANARY_DB_UNHEALTHY",
            {"error": str(exc), "path": str(path)},
            confidence_score=0.0,
            anomaly_score=0.8,
        )
        return False


# --------------------------------------------------------------------------- #
# Public statistics API
# --------------------------------------------------------------------------- #


def get_stats() -> dict[str, Any]:
    """Return a snapshot of canary health metrics for monitoring/GUI."""
    return {
        "probe_failure_count": _probe_failure_count,
        **_health_window.get_stats(),
    }


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
    - Runs supplementary Redis + DB health checks on the same cadence.
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

                # Supplementary service checks run alongside the gateway probe
                _check_redis_health()
                _check_db_health()

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
