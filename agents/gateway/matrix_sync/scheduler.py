"""Pipeline scheduler — APScheduler background job for automated pipeline runs.

The scheduler is started via the FastAPI lifespan in ``agents/gateway/bus.py``
and fires every ``pipeline_scheduler.interval_hours`` hours (default 6) as
configured in ``config/settings.json``.

Each trigger calls :func:`run_pipeline_scheduled` which persists results to
``registry.db`` and promotes the snapshot to active (configurable via
``pipeline_scheduler.promote`` in settings).
"""
from __future__ import annotations

import json
import logging
import os
import time
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Runtime state (module-level singleton)
# ---------------------------------------------------------------------------

_scheduler: BackgroundScheduler | None = None
_last_run_time: float | None = None
_last_run_status: str = "never_run"   # "ok" | "error" | "never_run"
_last_run_error: str = ""
_next_run_time: float | None = None


def _load_scheduler_settings() -> dict:
    """Load ``pipeline_scheduler`` block from ``config/settings.json``."""
    settings_path = Path(os.environ.get("BASE_DIR", ".")) / "config" / "settings.json"
    defaults: dict = {"enabled": True, "interval_hours": 6, "promote": True}
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            return {**defaults, **data.get("pipeline_scheduler", {})}
        except Exception:
            pass
    return defaults


def _run_job() -> None:
    """Scheduled job: run the pipeline and persist results to registry.db."""
    global _last_run_time, _last_run_status, _last_run_error
    _last_run_time = time.time()
    try:
        cfg = _load_scheduler_settings()
        promote: bool = bool(cfg.get("promote", True))
        logger.info("Scheduler: starting pipeline run (promote=%s)", promote)
        from .pipeline import run_pipeline_scheduled
        run_pipeline_scheduled(promote=promote)
        _last_run_status = "ok"
        _last_run_error = ""
        logger.info("Scheduler: pipeline run completed successfully")
    except Exception as exc:
        _last_run_status = "error"
        _last_run_error = traceback.format_exc()
        logger.error("Scheduler: pipeline run failed: %s", exc)


def _refresh_next_run() -> None:
    """Update the module-level ``_next_run_time`` from the live scheduler."""
    global _next_run_time
    if _scheduler is None:
        _next_run_time = None
        return
    try:
        jobs = _scheduler.get_jobs()
        if jobs:
            next_dt = jobs[0].next_run_time
            _next_run_time = next_dt.timestamp() if next_dt else None
        else:
            _next_run_time = None
    except Exception:
        _next_run_time = None


def start_scheduler() -> None:
    """Start the APScheduler background scheduler.

    Safe to call multiple times; subsequent calls are no-ops if the scheduler
    is already running.
    """
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        logger.debug("Scheduler already running — skipping start")
        return

    cfg = _load_scheduler_settings()
    if not cfg.get("enabled", True):
        logger.info("Scheduler disabled via config (pipeline_scheduler.enabled=false)")
        return

    interval_hours: int = int(cfg.get("interval_hours", 6))

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.warning("apscheduler not installed — pipeline scheduler disabled")
        return

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        _run_job,
        trigger="interval",
        hours=interval_hours,
        id="pipeline_run",
        name="ollama-matrix-sync pipeline",
        replace_existing=True,
    )
    _scheduler.start()
    _refresh_next_run()
    logger.info("Pipeline scheduler started (interval=%dh, promote=%s)", interval_hours, cfg.get("promote", True))


def stop_scheduler() -> None:
    """Stop the scheduler gracefully. Safe to call even if not running."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        try:
            _scheduler.shutdown(wait=False)
            logger.info("Pipeline scheduler stopped")
        except Exception as exc:
            logger.warning("Error stopping scheduler: %s", exc)
    _scheduler = None


def get_scheduler_status() -> dict:
    """Return a JSON-serialisable status dict for the ``/health/scheduler`` endpoint."""
    _refresh_next_run()
    cfg = _load_scheduler_settings()
    return {
        "enabled": cfg.get("enabled", True),
        "running": _scheduler is not None and _scheduler.running,
        "interval_hours": int(cfg.get("interval_hours", 6)),
        "promote": bool(cfg.get("promote", True)),
        "last_run_time": _last_run_time,
        "last_run_status": _last_run_status,
        "last_run_error": _last_run_error,
        "next_run_time": _next_run_time,
    }
