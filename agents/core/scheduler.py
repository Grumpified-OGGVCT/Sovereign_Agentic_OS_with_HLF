"""
Pipeline Scheduler Daemon — APScheduler-based 6-hour sync cycle.

Advanced features:
- APScheduler BackgroundScheduler running the pipeline every 6 hours
- Snapshot diff alerting: compares new vs previous snapshot for tier changes
- Health endpoint: lightweight HTTP server reporting daemon status
- Rollback: reverts to previous snapshot if new one has catastrophic regression
- Telemetry: tracks pipeline run history, durations, error counts
- Graceful shutdown via SIGINT/SIGTERM

Usage::

    python -m agents.core.scheduler          # foreground daemon
    python -m agents.core.scheduler --once   # single run, then exit

Environment:
    PIPELINE_INTERVAL_HOURS  — override interval (default 6)
    PIPELINE_HEALTH_PORT     — health HTTP port (default 8099)
    PIPELINE_AUTO_PROMOTE    — auto-promote snapshots (default true)
    PIPELINE_ROLLBACK_THRESHOLD — max acceptable tier-D increase % (default 20)
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import sys
import threading
import traceback
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

# Ensure project root is importable
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

INTERVAL_HOURS = float(os.getenv("PIPELINE_INTERVAL_HOURS", "6"))
HEALTH_PORT = int(os.getenv("PIPELINE_HEALTH_PORT", "8099"))
AUTO_PROMOTE = os.getenv("PIPELINE_AUTO_PROMOTE", "true").lower() in ("true", "1", "yes")
ROLLBACK_THRESHOLD = float(os.getenv("PIPELINE_ROLLBACK_THRESHOLD", "20"))  # percent


# ---------------------------------------------------------------------------
# Telemetry — tracks pipeline run history
# ---------------------------------------------------------------------------


@dataclass
class RunRecord:
    """Immutable record of a single pipeline execution."""

    run_id: int
    started_at: str
    finished_at: str = ""
    duration_sec: float = 0.0
    status: str = "running"  # running | success | failed | rolled_back
    snapshot_id: int | None = None
    models_upserted: int = 0
    local_synced: int = 0
    promoted: bool = False
    tier_changes: dict = field(default_factory=dict)
    error: str = ""


class Telemetry:
    """Thread-safe run history with bounded retention."""

    MAX_HISTORY = 100

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: list[RunRecord] = []
        self._run_counter = 0
        self._total_errors = 0
        self._consecutive_failures = 0
        self._daemon_started_at = datetime.now(UTC).isoformat()

    def start_run(self) -> RunRecord:
        with self._lock:
            self._run_counter += 1
            rec = RunRecord(
                run_id=self._run_counter,
                started_at=datetime.now(UTC).isoformat(),
            )
            self._runs.append(rec)
            if len(self._runs) > self.MAX_HISTORY:
                self._runs = self._runs[-self.MAX_HISTORY :]
            return rec

    def finish_run(self, rec: RunRecord, status: str, error: str = "") -> None:
        with self._lock:
            rec.finished_at = datetime.now(UTC).isoformat()
            rec.duration_sec = round(
                (datetime.fromisoformat(rec.finished_at) - datetime.fromisoformat(rec.started_at)).total_seconds(), 2
            )
            rec.status = status
            rec.error = error
            if status == "failed":
                self._total_errors += 1
                self._consecutive_failures += 1
            else:
                self._consecutive_failures = 0

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "daemon_started_at": self._daemon_started_at,
                "total_runs": self._run_counter,
                "total_errors": self._total_errors,
                "consecutive_failures": self._consecutive_failures,
                "interval_hours": INTERVAL_HOURS,
                "auto_promote": AUTO_PROMOTE,
                "rollback_threshold_pct": ROLLBACK_THRESHOLD,
                "recent_runs": [asdict(r) for r in self._runs[-10:]],
            }


_telemetry = Telemetry()


# ---------------------------------------------------------------------------
# Snapshot Diffing — detect regressions before promoting
# ---------------------------------------------------------------------------


def _diff_snapshots(conn: sqlite3.Connection, old_snap_id: int, new_snap_id: int) -> dict:
    """Compare two snapshots for tier shifts.

    Returns:
        {
            "promotions": [{"model": ..., "old_tier": ..., "new_tier": ...}, ...],
            "demotions": [...],
            "new_models": [...],
            "removed_models": [...],
            "tier_d_delta": float,  # percentage point change in D-tier models
        }
    """
    old_models = {}
    new_models = {}

    for row in conn.execute("SELECT model_id, tier FROM models WHERE snapshot_id = ?", (old_snap_id,)):
        old_models[row[0]] = row[1]

    for row in conn.execute("SELECT model_id, tier FROM models WHERE snapshot_id = ?", (new_snap_id,)):
        new_models[row[0]] = row[1]

    tier_rank = {"S": 0, "A+": 1, "A": 2, "A-": 3, "B+": 4, "B": 5, "C": 6, "D": 7}

    promotions = []
    demotions = []
    for mid, new_tier in new_models.items():
        old_tier = old_models.get(mid)
        if old_tier is None:
            continue
        if tier_rank.get(new_tier, 99) < tier_rank.get(old_tier, 99):
            promotions.append({"model": mid, "old_tier": old_tier, "new_tier": new_tier})
        elif tier_rank.get(new_tier, 99) > tier_rank.get(old_tier, 99):
            demotions.append({"model": mid, "old_tier": old_tier, "new_tier": new_tier})

    new_names = set(new_models) - set(old_models)
    removed_names = set(old_models) - set(new_models)

    # Tier-D concentration check
    old_d_count = sum(1 for t in old_models.values() if t == "D")
    new_d_count = sum(1 for t in new_models.values() if t == "D")
    old_total = max(len(old_models), 1)
    new_total = max(len(new_models), 1)
    old_d_pct = (old_d_count / old_total) * 100
    new_d_pct = (new_d_count / new_total) * 100

    return {
        "promotions": promotions,
        "demotions": demotions,
        "new_models": sorted(new_names),
        "removed_models": sorted(removed_names),
        "tier_d_delta_pct": round(new_d_pct - old_d_pct, 2),
        "old_d_pct": round(old_d_pct, 2),
        "new_d_pct": round(new_d_pct, 2),
        "summary": (
            f"+{len(promotions)} promotions, "
            f"-{len(demotions)} demotions, "
            f"{len(new_names)} new, "
            f"{len(removed_names)} removed, "
            f"D-tier: {old_d_pct:.1f}% → {new_d_pct:.1f}%"
        ),
    }


# ---------------------------------------------------------------------------
# Rollback logic
# ---------------------------------------------------------------------------


def _rollback_snapshot(conn: sqlite3.Connection, bad_snap_id: int, good_snap_id: int) -> None:
    """Revert active snapshot from bad → good.

    Marks the bad snapshot as inactive and re-promotes the good one.
    Does NOT delete the bad snapshot (preserves audit trail).
    """
    conn.execute(
        "UPDATE snapshots SET status = 'rolled_back' WHERE id = ?",
        (bad_snap_id,),
    )
    conn.execute(
        "UPDATE snapshots SET status = 'active' WHERE id = ?",
        (good_snap_id,),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Core pipeline execution with advanced governance
# ---------------------------------------------------------------------------


def _run_pipeline_cycle() -> None:
    """Execute one pipeline cycle with diffing, rollback, and telemetry."""
    from agents.core.db import db_path, get_active_snapshot, get_db
    from agents.gateway.matrix_sync.pipeline import run_pipeline_scheduled

    rec = _telemetry.start_run()

    try:
        # Capture the current active snapshot BEFORE running
        _db_file = str(db_path())
        prev_snap_id = None
        try:
            with get_db(_db_file) as conn:
                prev = get_active_snapshot(conn)
                if prev:
                    prev_snap_id = prev["id"]
        except Exception:
            pass  # first run — no previous snapshot

        # Run the pipeline (persists to registry.db)
        # Don't auto-promote yet — we'll evaluate the diff first
        run_pipeline_scheduled(promote=False)

        # Post-run: evaluate the new snapshot
        with get_db(_db_file) as conn:
            # Find the newest non-active snapshot
            row = conn.execute("SELECT id FROM snapshots WHERE status != 'active' ORDER BY id DESC LIMIT 1").fetchone()
            new_snap_id = row[0] if row else None

            if new_snap_id and prev_snap_id:
                # Diff the snapshots
                diff = _diff_snapshots(conn, prev_snap_id, new_snap_id)
                rec.tier_changes = diff

                # Rollback gate: if D-tier concentration spiked beyond threshold
                if diff["tier_d_delta_pct"] > ROLLBACK_THRESHOLD:
                    _rollback_snapshot(conn, new_snap_id, prev_snap_id)
                    rec.promoted = False
                    _telemetry.finish_run(
                        rec,
                        "rolled_back",
                        f"D-tier spike: {diff['old_d_pct']:.1f}% → {diff['new_d_pct']:.1f}% "
                        f"(delta {diff['tier_d_delta_pct']:.1f}% > threshold {ROLLBACK_THRESHOLD}%)",
                    )
                    print(
                        f"⚠️  ROLLBACK: Snapshot #{new_snap_id} rolled back. "
                        f"D-tier delta {diff['tier_d_delta_pct']:.1f}% exceeds threshold."
                    )
                    return

                # Safe to promote
                if AUTO_PROMOTE:
                    from agents.core.db import promote_snapshot

                    promote_snapshot(conn, new_snap_id)
                    conn.commit()
                    rec.promoted = True
                    print(f"✅ Promoted snapshot #{new_snap_id}: {diff['summary']}")

            elif new_snap_id:
                # First ever snapshot — promote unconditionally
                if AUTO_PROMOTE:
                    from agents.core.db import promote_snapshot

                    promote_snapshot(conn, new_snap_id)
                    conn.commit()
                    rec.promoted = True
                    print(f"✅ First snapshot #{new_snap_id} promoted.")

            rec.snapshot_id = new_snap_id

        _telemetry.finish_run(rec, "success")

    except Exception as exc:
        _telemetry.finish_run(rec, "failed", str(exc))
        print(f"❌ Pipeline cycle failed: {exc}")
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Health HTTP Server
# ---------------------------------------------------------------------------


class _HealthHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler exposing daemon telemetry as JSON."""

    def do_GET(self) -> None:
        if self.path == "/health":
            data = _telemetry.to_dict()
            data["status"] = "healthy"
            consecutive = data.get("consecutive_failures", 0)
            if consecutive >= 3:
                data["status"] = "degraded"
            if consecutive >= 5:
                data["status"] = "unhealthy"

            body = json.dumps(data, indent=2).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif self.path == "/health/last":
            runs = _telemetry.to_dict().get("recent_runs", [])
            last = runs[-1] if runs else {"status": "no runs yet"}
            body = json.dumps(last, indent=2).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        elif self.path == "/health/trigger":
            # Manual trigger endpoint
            threading.Thread(target=_run_pipeline_cycle, daemon=True).start()
            body = b'{"triggered": true}'
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args) -> None:
        # Suppress default request logging
        pass


def _start_health_server() -> HTTPServer:
    server = HTTPServer(("0.0.0.0", HEALTH_PORT), _HealthHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"🩺 Health endpoint: http://localhost:{HEALTH_PORT}/health")
    return server


# ---------------------------------------------------------------------------
# Scheduler Daemon
# ---------------------------------------------------------------------------

_shutdown_event = threading.Event()


def _signal_handler(signum: int, frame: Any) -> None:
    print(f"\n🛑 Received signal {signum}, shutting down gracefully...")
    _shutdown_event.set()


def run_daemon(once: bool = False) -> None:
    """Main entry point for the scheduler daemon.

    Args:
        once: If True, run one cycle and exit (useful for CI/cron).
    """
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    interval_sec = INTERVAL_HOURS * 3600
    print("=" * 60)
    print("  Sovereign OS — Pipeline Scheduler Daemon")
    print(f"  Interval: {INTERVAL_HOURS}h ({interval_sec:.0f}s)")
    print(f"  Auto-promote: {AUTO_PROMOTE}")
    print(f"  Rollback threshold: {ROLLBACK_THRESHOLD}% D-tier increase")
    print(f"  Health port: {HEALTH_PORT}")
    print("=" * 60)

    health_server = None
    if not once:
        health_server = _start_health_server()

    # Run first cycle immediately
    print(f"\n⏱  Cycle 1 starting at {datetime.now(UTC).isoformat()}")
    _run_pipeline_cycle()

    if once:
        print("🏁 Single-run mode — exiting.")
        return

    # Subsequent cycles on interval
    while not _shutdown_event.is_set():
        next_run = datetime.now(UTC).timestamp() + interval_sec
        next_run_str = datetime.fromtimestamp(next_run, tz=UTC).isoformat()
        print(f"\n💤 Next cycle at {next_run_str} ({INTERVAL_HOURS}h)")

        # Wait in small increments so we can respond to shutdown signals
        while not _shutdown_event.is_set():
            remaining = next_run - datetime.now(UTC).timestamp()
            if remaining <= 0:
                break
            _shutdown_event.wait(min(remaining, 30))

        if _shutdown_event.is_set():
            break

        print(f"\n⏱  Cycle starting at {datetime.now(UTC).isoformat()}")
        _run_pipeline_cycle()

    if health_server:
        health_server.shutdown()
    print("👋 Scheduler daemon stopped.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    # Use defaults from module-level config if not provided
    default_interval = float(os.getenv("PIPELINE_INTERVAL_HOURS", "6"))
    default_port = int(os.getenv("PIPELINE_HEALTH_PORT", "8099"))

    ap = argparse.ArgumentParser(description="Pipeline Scheduler Daemon — runs model sync on a 6-hour cycle.")
    ap.add_argument("--once", action="store_true", help="Run a single pipeline cycle and exit (for CI/cron).")
    ap.add_argument(
        "--interval", type=float, default=None, help=f"Override interval in hours (default: {default_interval})."
    )
    ap.add_argument("--port", type=int, default=None, help=f"Override health endpoint port (default: {default_port}).")
    ap.add_argument(
        "--no-promote",
        dest="promote",
        action="store_false",
        default=True,
        help="Disable auto-promotion of new snapshots.",
    )
    args = ap.parse_args()

    global INTERVAL_HOURS, HEALTH_PORT, AUTO_PROMOTE
    INTERVAL_HOURS = args.interval if args.interval is not None else INTERVAL_HOURS
    HEALTH_PORT = args.port if args.port is not None else HEALTH_PORT
    if not args.promote:
        AUTO_PROMOTE = False

    run_daemon(once=args.once)


if __name__ == "__main__":
    main()
