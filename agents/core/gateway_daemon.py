"""
Gateway Daemon — Manages the model gateway as a background subprocess.

Controls the FastAPI/uvicorn gateway process lifecycle:
    - Start/stop/restart the daemon
    - Health checks via /v1/health
    - Tray menu integration
    - Auto-start on OS boot (optional)

Architecture:
    GatewayDaemon.start() → spawns uvicorn subprocess → listens on port 4000
    GatewayDaemon.stop()  → sends SIGTERM / kills subprocess
    GatewayDaemon.status() → GET /v1/health
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent


# ─── Daemon Config ───────────────────────────────────────────────────────────

@dataclass
class DaemonConfig:
    """Configuration for the gateway daemon process."""

    host: str = "127.0.0.1"
    port: int = 4000
    workers: int = 1
    auto_start: bool = False
    log_file: str = ""
    pid_file: str = ""

    @classmethod
    def from_settings(cls, path: Path | None = None) -> DaemonConfig:
        path = path or (_PROJECT_ROOT / "config" / "settings.json")
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            gw = data.get("gateway", {})
            return cls(
                host=gw.get("host", "127.0.0.1"),
                port=gw.get("port", 4000),
                auto_start=gw.get("auto_start", False),
            )
        except Exception:
            return cls()


# ─── Daemon State ────────────────────────────────────────────────────────────

class DaemonState:
    """Thread-safe daemon state tracker."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._pid: int | None = None
        self._started_at: float = 0
        self._error: str = ""

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    @running.setter
    def running(self, val: bool) -> None:
        with self._lock:
            self._running = val

    @property
    def pid(self) -> int | None:
        with self._lock:
            return self._pid

    @pid.setter
    def pid(self, val: int | None) -> None:
        with self._lock:
            self._pid = val

    @property
    def started_at(self) -> float:
        with self._lock:
            return self._started_at

    @started_at.setter
    def started_at(self, val: float) -> None:
        with self._lock:
            self._started_at = val

    @property
    def error(self) -> str:
        with self._lock:
            return self._error

    @error.setter
    def error(self, val: str) -> None:
        with self._lock:
            self._error = val

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            uptime = time.time() - self._started_at if self._running else 0
            return {
                "running": self._running,
                "pid": self._pid,
                "uptime_seconds": round(uptime, 1),
                "error": self._error,
            }


# ─── Gateway Daemon ─────────────────────────────────────────────────────────

class GatewayDaemon:
    """Manages the model gateway as a background subprocess.

    The daemon starts a uvicorn process serving the FastAPI gateway app.
    It supports start/stop/restart/health operations and integrates
    with the system tray via callback registration.

    Args:
        config: Daemon configuration.
    """

    def __init__(self, config: DaemonConfig | None = None) -> None:
        self._config = config or DaemonConfig()
        self._state = DaemonState()
        self._process: subprocess.Popen | None = None

    def start(self) -> dict[str, Any]:
        """Start the gateway daemon subprocess.

        Returns:
            Dict with 'success', 'pid', 'url'.
        """
        if self._state.running:
            return {
                "success": False,
                "error": "Gateway already running",
                "pid": self._state.pid,
            }

        try:
            # Build uvicorn command
            cmd = [
                sys.executable, "-m", "uvicorn",
                "agents.core.model_gateway:create_app",
                "--factory",
                "--host", self._config.host,
                "--port", str(self._config.port),
                "--workers", str(self._config.workers),
            ]

            # Start subprocess
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(_PROJECT_ROOT),
            )

            self._state.running = True
            self._state.pid = self._process.pid
            self._state.started_at = time.time()
            self._state.error = ""

            # Monitor in background
            thread = threading.Thread(target=self._monitor, daemon=True)
            thread.start()

            logger.info(
                "Gateway started: pid=%d, url=http://%s:%d",
                self._process.pid,
                self._config.host,
                self._config.port,
            )

            return {
                "success": True,
                "pid": self._process.pid,
                "url": f"http://{self._config.host}:{self._config.port}",
            }

        except FileNotFoundError:
            self._state.error = "uvicorn not found — install with: pip install uvicorn"
            return {"success": False, "error": self._state.error}
        except Exception as e:
            self._state.error = str(e)
            return {"success": False, "error": str(e)}

    def stop(self) -> dict[str, Any]:
        """Stop the gateway daemon."""
        if not self._state.running:
            return {"success": False, "error": "Gateway not running"}

        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            except Exception:
                pass
            self._process = None

        self._state.running = False
        self._state.pid = None
        logger.info("Gateway stopped")

        return {"success": True}

    def restart(self) -> dict[str, Any]:
        """Stop then start the gateway."""
        self.stop()
        time.sleep(0.5)
        return self.start()

    def status(self) -> dict[str, Any]:
        """Get current daemon status."""
        state = self._state.to_dict()
        state["config"] = {
            "host": self._config.host,
            "port": self._config.port,
        }
        state["url"] = f"http://{self._config.host}:{self._config.port}"
        return state

    def health_check(self) -> dict[str, Any]:
        """Check if the gateway is healthy by hitting /v1/health."""
        if not self._state.running:
            return {"healthy": False, "error": "not running"}

        try:
            import urllib.request

            url = f"http://{self._config.host}:{self._config.port}/v1/health"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                return {"healthy": True, **data}
        except Exception as e:
            return {"healthy": False, "error": str(e)}

    @property
    def is_running(self) -> bool:
        return self._state.running

    @property
    def config(self) -> DaemonConfig:
        return self._config

    # ── Tray Integration ────────────────────────────────────────────────

    def get_tray_menu_items(self) -> list[dict[str, Any]]:
        """Generate tray menu items for gateway control.

        Returns list of dicts compatible with TrayMenuItem creation.
        """
        status = "Running" if self._state.running else "Stopped"
        return [
            {"label": f"Gateway: {status}", "action": "gateway_status", "enabled": False},
            {"label": "Start Gateway", "action": "gateway_start", "enabled": not self._state.running},
            {"label": "Stop Gateway", "action": "gateway_stop", "enabled": self._state.running},
            {"label": "Restart Gateway", "action": "gateway_restart", "enabled": self._state.running},
            {"label": "Health Check", "action": "gateway_health"},
        ]

    def register_tray_callbacks(self, tray: Any) -> None:
        """Register gateway control callbacks on a SovereignTray instance."""
        tray.register_callback("gateway_start", lambda: self.start())
        tray.register_callback("gateway_stop", lambda: self.stop())
        tray.register_callback("gateway_restart", lambda: self.restart())
        tray.register_callback("gateway_health", lambda: self.health_check())

    # ── Internal ────────────────────────────────────────────────────────

    def _monitor(self) -> None:
        """Monitor the subprocess and update state on exit."""
        if self._process is None:
            return

        self._process.wait()
        self._state.running = False
        self._state.pid = None

        returncode = self._process.returncode
        if returncode != 0:
            self._state.error = f"Gateway exited with code {returncode}"
            logger.warning("Gateway exited: code=%d", returncode)
