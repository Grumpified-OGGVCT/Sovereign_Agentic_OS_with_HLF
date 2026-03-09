"""
WindowsBridge — Enterprise Windows 11 native integration.

Extends NativeBridge with:
  - Windows-specific command allowlist (PowerShell, WMIC, etc.)
  - ALSLogger audit trail for all operations
  - Config-driven feature flags
  - os.startfile for file/URL opening
  - WMI/WMIC enrichment for system info
"""

from __future__ import annotations

import os
import subprocess
import sys
import webbrowser
from typing import Any

from agents.core.logger import ALSLogger
from agents.core.native.bridge import (
    ClipboardContent,
    NativeBridge,
    NotificationRequest,
    ProcessInfo,
    ShellResult,
    SubsystemUnavailableError,
    SystemInfo,
)

_logger = ALSLogger(agent_role="native-windows", goal_id="bridge")


class WindowsBridge(NativeBridge):
    """Windows-specific NativeBridge implementation.

    Extends the base allowlist with Windows-specific safe commands
    and provides Windows-native operations for all subsystems.
    """

    _PLATFORM_COMMANDS: frozenset[str] = frozenset({
        "powershell", "pwsh", "cmd",
        "tasklist", "wmic", "systeminfo", "sc",
        "netstat", "ipconfig", "nbtstat",
        "reg", "schtasks",
        "robocopy", "xcopy", "icacls",
        "certutil",
        "clip",
    })

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self._features = self._config.get("features", {})
        # Merge platform-specific commands into base allowlist
        NativeBridge._BASE_COMMAND_ALLOWLIST = (
            NativeBridge._BASE_COMMAND_ALLOWLIST | self._PLATFORM_COMMANDS
        )
        _logger.log("BRIDGE_INIT", {"platform": "windows", "features": self._features})

    # ── System Info ──────────────────────────────────────────────────────

    def system_info(self) -> SystemInfo:
        from agents.core.native.sysinfo import gather_system_info
        info = gather_system_info()
        _logger.log("SYSINFO_READ", {"hostname": info.hostname})
        return info

    def list_processes(self, filter_name: str | None = None) -> list[ProcessInfo]:
        _logger.log("PROCESS_LIST", {"filter": filter_name})
        processes: list[ProcessInfo] = []
        try:
            import psutil
            for proc in psutil.process_iter(["pid", "name", "status", "cpu_percent", "memory_info"]):
                try:
                    pi = proc.info
                    if filter_name and filter_name.lower() not in pi["name"].lower():
                        continue
                    mem = pi.get("memory_info")
                    processes.append(ProcessInfo(
                        pid=pi["pid"],
                        name=pi["name"],
                        status=pi.get("status", "unknown"),
                        cpu_percent=pi.get("cpu_percent", 0.0) or 0.0,
                        memory_mb=round((mem.rss / (1024 * 1024)) if mem else 0.0, 1),
                    ))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except ImportError:
            _logger.log("PROCESS_LIST_FALLBACK", {"reason": "psutil not installed"})
            try:
                result = subprocess.run(
                    ["tasklist", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, timeout=10,
                )
                for line in result.stdout.strip().split("\n")[:50]:
                    parts = [p.strip('"') for p in line.split('","')]
                    if len(parts) >= 5:
                        name = parts[0]
                        if filter_name and filter_name.lower() not in name.lower():
                            continue
                        try:
                            pid = int(parts[1])
                            mem_str = parts[4].replace(",", "").replace(" K", "")
                            mem_mb = int(mem_str) / 1024 if mem_str.isdigit() else 0.0
                        except (ValueError, IndexError):
                            continue
                        processes.append(ProcessInfo(
                            pid=pid, name=name, status="running",
                            cpu_percent=0.0, memory_mb=round(mem_mb, 1),
                        ))
            except Exception:
                pass
        return processes

    # ── Clipboard ────────────────────────────────────────────────────────

    def clipboard_read(self) -> ClipboardContent:
        if not self._features.get("clipboard", True):
            raise SubsystemUnavailableError("clipboard", "disabled by config")
        from agents.core.native.clipboard import clipboard_read
        content = clipboard_read()
        _logger.log("CLIPBOARD_READ", {"length": len(content.text)})
        return content

    def clipboard_write(self, text: str) -> bool:
        if not self._features.get("clipboard", True):
            raise SubsystemUnavailableError("clipboard", "disabled by config")
        from agents.core.native.clipboard import clipboard_write
        result = clipboard_write(text)
        _logger.log("CLIPBOARD_WRITE", {"length": len(text), "success": result})
        return result

    # ── Notifications ────────────────────────────────────────────────────

    def notify(self, request: NotificationRequest) -> bool:
        if not self._features.get("notifications", True):
            raise SubsystemUnavailableError("notifications", "disabled by config")
        from agents.core.native.notifications import send_notification
        result = send_notification(request)
        _logger.log("NOTIFY", {"title": request.title[:50], "success": result})
        return result

    # ── Shell ────────────────────────────────────────────────────────────

    def shell_exec(
        self,
        command: str,
        args: list[str] | None = None,
        timeout_seconds: float = 30.0,
        cwd: str | None = None,
    ) -> ShellResult:
        if not self._features.get("shell", True):
            raise SubsystemUnavailableError("shell", "disabled by config")
        from agents.core.native.shell import shell_exec as _exec
        shell_cfg = self._config.get("shell", {})
        timeout = min(timeout_seconds, shell_cfg.get("timeout_seconds", 30.0))
        return _exec(
            command, args, timeout_seconds=timeout,
            cwd=cwd, bridge=self,
            max_stdout=shell_cfg.get("max_stdout_bytes", 10_000),
            max_stderr=shell_cfg.get("max_stderr_bytes", 5_000),
        )

    # ── Apps & URLs ──────────────────────────────────────────────────────

    def open_file(self, path: str) -> bool:
        if not self._features.get("app_launch", True):
            raise SubsystemUnavailableError("app_launch", "disabled by config")
        try:
            os.startfile(path)  # type: ignore[attr-defined]
            _logger.log("FILE_OPEN", {"path": path[:100]})
            return True
        except Exception as exc:
            _logger.log("FILE_OPEN_FAILED", {"path": path[:100], "error": str(exc)[:80]})
            return False

    def open_url(self, url: str) -> bool:
        try:
            webbrowser.open(url)
            _logger.log("URL_OPEN", {"url": url[:100]})
            return True
        except Exception:
            return False

    def launch_app(self, app_name: str, args: list[str] | None = None) -> int | None:
        if not self._features.get("app_launch", True):
            raise SubsystemUnavailableError("app_launch", "disabled by config")
        try:
            cmd = [app_name] + (args or [])
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            _logger.log("APP_LAUNCH", {"app": app_name, "pid": proc.pid})
            return proc.pid
        except Exception as exc:
            _logger.log("APP_LAUNCH_FAILED", {"app": app_name, "error": str(exc)[:80]})
            return None

    # ── Tray ─────────────────────────────────────────────────────────────

    def tray_available(self) -> bool:
        if not self._features.get("tray", True):
            return False
        try:
            import pystray  # noqa: F401
            return True
        except ImportError:
            return False
