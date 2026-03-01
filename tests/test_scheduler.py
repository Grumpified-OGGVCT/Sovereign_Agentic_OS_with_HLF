"""Tests for the APScheduler pipeline scheduler module."""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_scheduler_state() -> None:
    """Reset module-level scheduler state between tests."""
    import agents.gateway.matrix_sync.scheduler as sched
    sched._scheduler = None
    sched._last_run_time = None
    sched._last_run_status = "never_run"
    sched._last_run_error = ""
    sched._next_run_time = None


# ---------------------------------------------------------------------------
# _load_scheduler_settings
# ---------------------------------------------------------------------------

class TestLoadSchedulerSettings:
    def test_defaults_when_no_file(self, tmp_path: Path) -> None:
        with patch.dict("os.environ", {"BASE_DIR": str(tmp_path)}):
            from agents.gateway.matrix_sync.scheduler import _load_scheduler_settings
            cfg = _load_scheduler_settings()
        assert cfg["enabled"] is True
        assert cfg["interval_hours"] == 6
        assert cfg["promote"] is True

    def test_reads_from_settings_json(self, tmp_path: Path) -> None:
        import json
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "settings.json").write_text(
            json.dumps({"pipeline_scheduler": {"enabled": False, "interval_hours": 12, "promote": False}}),
            encoding="utf-8",
        )
        with patch.dict("os.environ", {"BASE_DIR": str(tmp_path)}):
            from agents.gateway.matrix_sync.scheduler import _load_scheduler_settings
            cfg = _load_scheduler_settings()
        assert cfg["enabled"] is False
        assert cfg["interval_hours"] == 12
        assert cfg["promote"] is False

    def test_partial_override_fills_defaults(self, tmp_path: Path) -> None:
        import json
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "settings.json").write_text(
            json.dumps({"pipeline_scheduler": {"interval_hours": 3}}),
            encoding="utf-8",
        )
        with patch.dict("os.environ", {"BASE_DIR": str(tmp_path)}):
            from agents.gateway.matrix_sync.scheduler import _load_scheduler_settings
            cfg = _load_scheduler_settings()
        assert cfg["interval_hours"] == 3
        assert cfg["enabled"] is True   # default
        assert cfg["promote"] is True   # default


# ---------------------------------------------------------------------------
# get_scheduler_status
# ---------------------------------------------------------------------------

class TestGetSchedulerStatus:
    def setup_method(self) -> None:
        _reset_scheduler_state()

    def test_initial_status(self) -> None:
        from agents.gateway.matrix_sync.scheduler import get_scheduler_status
        status = get_scheduler_status()
        assert status["running"] is False
        assert status["last_run_status"] == "never_run"
        assert status["last_run_time"] is None
        assert status["next_run_time"] is None

    def test_status_has_all_required_keys(self) -> None:
        from agents.gateway.matrix_sync.scheduler import get_scheduler_status
        status = get_scheduler_status()
        required_keys = {"enabled", "running", "interval_hours", "promote",
                         "last_run_time", "last_run_status", "last_run_error", "next_run_time"}
        assert required_keys.issubset(status.keys())


# ---------------------------------------------------------------------------
# start_scheduler / stop_scheduler
# ---------------------------------------------------------------------------

class TestStartStopScheduler:
    def setup_method(self) -> None:
        _reset_scheduler_state()

    def teardown_method(self) -> None:
        from agents.gateway.matrix_sync.scheduler import stop_scheduler
        stop_scheduler()
        _reset_scheduler_state()

    def test_start_creates_running_scheduler(self, tmp_path: Path) -> None:
        import json
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "settings.json").write_text(
            json.dumps({"pipeline_scheduler": {"enabled": True, "interval_hours": 6}}),
            encoding="utf-8",
        )
        with patch.dict("os.environ", {"BASE_DIR": str(tmp_path)}):
            from agents.gateway.matrix_sync import scheduler as sched
            sched.start_scheduler()
            status = sched.get_scheduler_status()
        assert status["running"] is True

    def test_stop_makes_scheduler_not_running(self, tmp_path: Path) -> None:
        import json
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "settings.json").write_text(
            json.dumps({"pipeline_scheduler": {"enabled": True, "interval_hours": 6}}),
            encoding="utf-8",
        )
        with patch.dict("os.environ", {"BASE_DIR": str(tmp_path)}):
            from agents.gateway.matrix_sync import scheduler as sched
            sched.start_scheduler()
            sched.stop_scheduler()
            status = sched.get_scheduler_status()
        assert status["running"] is False

    def test_start_disabled_does_not_create_scheduler(self, tmp_path: Path) -> None:
        import json
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "settings.json").write_text(
            json.dumps({"pipeline_scheduler": {"enabled": False}}),
            encoding="utf-8",
        )
        with patch.dict("os.environ", {"BASE_DIR": str(tmp_path)}):
            from agents.gateway.matrix_sync import scheduler as sched
            sched.start_scheduler()
            assert sched._scheduler is None

    def test_double_start_is_safe(self, tmp_path: Path) -> None:
        import json
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "settings.json").write_text(
            json.dumps({"pipeline_scheduler": {"enabled": True, "interval_hours": 6}}),
            encoding="utf-8",
        )
        with patch.dict("os.environ", {"BASE_DIR": str(tmp_path)}):
            from agents.gateway.matrix_sync import scheduler as sched
            sched.start_scheduler()
            sched.start_scheduler()  # must not raise
            assert sched._scheduler is not None and sched._scheduler.running

    def test_stop_when_not_running_is_safe(self) -> None:
        from agents.gateway.matrix_sync.scheduler import stop_scheduler
        stop_scheduler()  # must not raise


# ---------------------------------------------------------------------------
# _run_job
# ---------------------------------------------------------------------------

class TestRunJob:
    def setup_method(self) -> None:
        _reset_scheduler_state()

    def test_run_job_on_success_sets_ok_status(self) -> None:
        with patch("agents.gateway.matrix_sync.scheduler._load_scheduler_settings",
                   return_value={"enabled": True, "interval_hours": 6, "promote": True}):
            with patch("agents.gateway.matrix_sync.pipeline.run_pipeline_scheduled") as mock_run:
                from agents.gateway.matrix_sync.scheduler import _run_job
                import agents.gateway.matrix_sync.scheduler as sched
                _run_job()
        assert sched._last_run_status == "ok"
        assert sched._last_run_error == ""
        assert sched._last_run_time is not None
        mock_run.assert_called_once_with(promote=True)

    def test_run_job_on_error_sets_error_status(self) -> None:
        with patch("agents.gateway.matrix_sync.scheduler._load_scheduler_settings",
                   return_value={"enabled": True, "interval_hours": 6, "promote": True}):
            with patch("agents.gateway.matrix_sync.pipeline.run_pipeline_scheduled",
                       side_effect=RuntimeError("network down")):
                from agents.gateway.matrix_sync.scheduler import _run_job
                import agents.gateway.matrix_sync.scheduler as sched
                _run_job()
        assert sched._last_run_status == "error"
        assert "network down" in sched._last_run_error

    def test_run_job_passes_promote_false(self) -> None:
        with patch("agents.gateway.matrix_sync.scheduler._load_scheduler_settings",
                   return_value={"enabled": True, "interval_hours": 6, "promote": False}):
            with patch("agents.gateway.matrix_sync.pipeline.run_pipeline_scheduled") as mock_run:
                from agents.gateway.matrix_sync.scheduler import _run_job
                _run_job()
        mock_run.assert_called_once_with(promote=False)


# ---------------------------------------------------------------------------
# pipeline.run_pipeline_scheduled
# ---------------------------------------------------------------------------

class TestRunPipelineScheduled:
    def test_accepts_promote_true(self) -> None:
        """run_pipeline_scheduled(promote=True) must call run_pipeline with registry_db+promote."""
        with patch("agents.gateway.matrix_sync.pipeline.run_pipeline") as mock_rp:
            from agents.gateway.matrix_sync.pipeline import run_pipeline_scheduled
            run_pipeline_scheduled(promote=True)
        mock_rp.assert_called_once()
        args_ns = mock_rp.call_args[0][0]
        assert args_ns.registry_db is True
        assert args_ns.promote is True

    def test_accepts_promote_false(self) -> None:
        with patch("agents.gateway.matrix_sync.pipeline.run_pipeline") as mock_rp:
            from agents.gateway.matrix_sync.pipeline import run_pipeline_scheduled
            run_pipeline_scheduled(promote=False)
        args_ns = mock_rp.call_args[0][0]
        assert args_ns.registry_db is True
        assert args_ns.promote is False


# ---------------------------------------------------------------------------
# settings.json pipeline_scheduler block
# ---------------------------------------------------------------------------

class TestSettingsJsonSchema:
    def test_settings_json_has_pipeline_scheduler(self, repo_root: Path) -> None:
        import json
        path = repo_root / "config" / "settings.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "pipeline_scheduler" in data, "settings.json must contain pipeline_scheduler block"
        cfg = data["pipeline_scheduler"]
        assert "interval_hours" in cfg
        assert "enabled" in cfg
        assert "promote" in cfg
        assert cfg["interval_hours"] == 6
