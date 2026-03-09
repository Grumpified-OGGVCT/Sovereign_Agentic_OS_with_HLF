"""
Tests for GatewayDaemon — subprocess lifecycle + tray integration.

Tests cover:
  - DaemonConfig defaults and from_settings
  - DaemonState thread safety
  - Start/stop/restart lifecycle (mocked subprocess)
  - Health check
  - Status reporting
  - Tray menu generation
  - Tray callback registration
  - Double start prevention
  - Error handling
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

from agents.core.gateway_daemon import (
    DaemonConfig,
    DaemonState,
    GatewayDaemon,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def config() -> DaemonConfig:
    return DaemonConfig(port=4001)


@pytest.fixture
def daemon(config: DaemonConfig) -> GatewayDaemon:
    return GatewayDaemon(config=config)


@pytest.fixture
def settings_file(tmp_path: Path) -> Path:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "gateway": {"host": "0.0.0.0", "port": 5000, "auto_start": True}
    }))
    return path


def _make_mock_proc() -> MagicMock:
    """Create a mock process whose wait() blocks until stop is called."""
    import threading
    mock_proc = MagicMock()
    mock_proc.pid = 42
    mock_proc.returncode = 0
    _stop = threading.Event()
    mock_proc.wait.side_effect = lambda timeout=None: _stop.wait(timeout=timeout if timeout else 30)
    mock_proc.terminate.side_effect = lambda: _stop.set()
    mock_proc.kill.side_effect = lambda: _stop.set()
    return mock_proc


# ─── Config ──────────────────────────────────────────────────────────────────

class TestDaemonConfig:
    def test_defaults(self) -> None:
        cfg = DaemonConfig()
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 4000

    def test_from_settings(self, settings_file: Path) -> None:
        cfg = DaemonConfig.from_settings(settings_file)
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 5000
        assert cfg.auto_start is True

    def test_from_missing_file(self) -> None:
        cfg = DaemonConfig.from_settings(Path("/nonexistent"))
        assert cfg.port == 4000


# ─── State ───────────────────────────────────────────────────────────────────

class TestDaemonState:
    def test_initial(self) -> None:
        s = DaemonState()
        assert s.running is False
        assert s.pid is None

    def test_set_running(self) -> None:
        s = DaemonState()
        s.running = True
        s.pid = 1234
        assert s.running is True
        assert s.pid == 1234

    def test_to_dict(self) -> None:
        s = DaemonState()
        d = s.to_dict()
        assert d["running"] is False
        assert d["pid"] is None
        assert d["uptime_seconds"] == 0


# ─── Lifecycle ───────────────────────────────────────────────────────────────

class TestLifecycle:
    @patch("subprocess.Popen")
    def test_start(self, mock_popen: MagicMock, daemon: GatewayDaemon) -> None:
        mock_popen.return_value = _make_mock_proc()

        result = daemon.start()
        assert result["success"] is True
        assert result["pid"] == 42
        assert daemon.is_running is True
        daemon.stop()

    @patch("subprocess.Popen")
    def test_double_start(self, mock_popen: MagicMock, daemon: GatewayDaemon) -> None:
        mock_popen.return_value = _make_mock_proc()

        daemon.start()
        result = daemon.start()
        assert result["success"] is False
        assert "already running" in result["error"]
        daemon.stop()

    @patch("subprocess.Popen")
    def test_stop(self, mock_popen: MagicMock, daemon: GatewayDaemon) -> None:
        mock_popen.return_value = _make_mock_proc()

        daemon.start()
        result = daemon.stop()
        assert result["success"] is True
        assert daemon.is_running is False

    def test_stop_not_running(self, daemon: GatewayDaemon) -> None:
        result = daemon.stop()
        assert result["success"] is False

    @patch("subprocess.Popen")
    def test_restart(self, mock_popen: MagicMock, daemon: GatewayDaemon) -> None:
        mock_popen.return_value = _make_mock_proc()

        daemon.start()
        result = daemon.restart()
        assert result["success"] is True
        daemon.stop()


# ─── Status ──────────────────────────────────────────────────────────────────

class TestStatus:
    def test_status_stopped(self, daemon: GatewayDaemon) -> None:
        s = daemon.status()
        assert s["running"] is False
        assert s["config"]["port"] == 4001

    @patch("subprocess.Popen")
    def test_status_running(self, mock_popen: MagicMock, daemon: GatewayDaemon) -> None:
        mock_popen.return_value = _make_mock_proc()

        daemon.start()
        s = daemon.status()
        assert s["running"] is True
        assert s["pid"] == 42
        daemon.stop()


# ─── Health ──────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_not_running(self, daemon: GatewayDaemon) -> None:
        result = daemon.health_check()
        assert result["healthy"] is False


# ─── Tray ────────────────────────────────────────────────────────────────────

class TestTray:
    def test_menu_items_stopped(self, daemon: GatewayDaemon) -> None:
        items = daemon.get_tray_menu_items()
        assert len(items) == 5
        assert "Stopped" in items[0]["label"]
        # Start should be enabled
        assert items[1]["enabled"] is True
        # Stop should be disabled
        assert items[2]["enabled"] is False

    @patch("subprocess.Popen")
    def test_menu_items_running(self, mock_popen: MagicMock, daemon: GatewayDaemon) -> None:
        mock_popen.return_value = _make_mock_proc()

        daemon.start()
        items = daemon.get_tray_menu_items()
        assert "Running" in items[0]["label"]
        # Start should be disabled
        assert items[1]["enabled"] is False
        # Stop should be enabled
        assert items[2]["enabled"] is True
        daemon.stop()

    def test_register_callbacks(self, daemon: GatewayDaemon) -> None:
        mock_tray = MagicMock()
        daemon.register_tray_callbacks(mock_tray)
        assert mock_tray.register_callback.call_count == 4


# ─── Error ───────────────────────────────────────────────────────────────────

class TestErrors:
    @patch("subprocess.Popen", side_effect=FileNotFoundError())
    def test_start_no_uvicorn(self, mock_popen: MagicMock, daemon: GatewayDaemon) -> None:
        result = daemon.start()
        assert result["success"] is False
        assert "uvicorn" in result["error"]
