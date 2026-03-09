"""
System Information — Cross-platform system info via psutil + platform stdlib.

Provides CPU, memory, disk, uptime, and platform-specific enrichment.
Falls back gracefully when psutil is not installed.
"""

from __future__ import annotations

import platform
import sys
import time
from typing import Any

from agents.core.native.bridge import ProcessInfo, SystemInfo

# psutil is optional — degrade gracefully
try:
    import psutil

    _HAS_PSUTIL = True
except ImportError:
    psutil = None  # type: ignore[assignment]
    _HAS_PSUTIL = False


def gather_system_info() -> SystemInfo:
    """Gather current system information.

    Uses psutil when available, falls back to platform stdlib.
    """
    plat = "windows" if sys.platform == "win32" else ("darwin" if sys.platform == "darwin" else "linux")

    extra: dict[str, Any] = {}

    if _HAS_PSUTIL:
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/") if plat != "windows" else psutil.disk_usage("C:\\")
        boot_time = psutil.boot_time()
        uptime = time.time() - boot_time

        # Platform-specific extras
        if plat == "windows":
            extra["edition"] = platform.win32_edition() if hasattr(platform, "win32_edition") else "unknown"
        elif plat == "darwin":
            extra["mac_ver"] = platform.mac_ver()[0]

        return SystemInfo(
            platform=plat,
            platform_version=platform.version(),
            hostname=platform.node(),
            cpu_count=psutil.cpu_count(logical=True) or 1,
            cpu_percent=psutil.cpu_percent(interval=0.1),
            memory_total_mb=int(mem.total / (1024 * 1024)),
            memory_available_mb=int(mem.available / (1024 * 1024)),
            disk_total_gb=round(disk.total / (1024**3), 1),
            disk_free_gb=round(disk.free / (1024**3), 1),
            python_version=platform.python_version(),
            uptime_seconds=round(uptime, 1),
            extra=extra,
        )

    # Fallback without psutil
    return SystemInfo(
        platform=plat,
        platform_version=platform.version(),
        hostname=platform.node(),
        cpu_count=1,
        cpu_percent=0.0,
        memory_total_mb=0,
        memory_available_mb=0,
        disk_total_gb=0.0,
        disk_free_gb=0.0,
        python_version=platform.python_version(),
        uptime_seconds=0.0,
        extra={"psutil_available": False},
    )


def list_processes(filter_name: str | None = None) -> list[ProcessInfo]:
    """List running processes, optionally filtered by name.

    Requires psutil. Returns empty list if not available.
    """
    if not _HAS_PSUTIL:
        return []

    result: list[ProcessInfo] = []
    for proc in psutil.process_iter(["pid", "name", "status", "cpu_percent", "memory_info", "cmdline"]):
        try:
            info = proc.info  # type: ignore[attr-defined]
            name = info.get("name", "")
            if filter_name and filter_name.lower() not in name.lower():
                continue

            mem_info = info.get("memory_info")
            mem_mb = round(mem_info.rss / (1024 * 1024), 1) if mem_info else 0.0
            cmdline = " ".join(info.get("cmdline") or [])

            result.append(
                ProcessInfo(
                    pid=info.get("pid", 0),
                    name=name,
                    status=info.get("status", "unknown"),
                    cpu_percent=info.get("cpu_percent", 0.0) or 0.0,
                    memory_mb=mem_mb,
                    cmdline=cmdline[:200],
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    return result
