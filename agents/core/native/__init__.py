"""
Native OS Translation Layer — Enterprise-grade platform abstraction.

Provides:
  - Thread-safe singleton bridge per platform
  - Feature flags from config/settings.json
  - Auto-dependency detection with helpful error messages
  - ALSLogger integration for full audit trail

Usage:
    from agents.core.native import get_bridge
    bridge = get_bridge()
    info = bridge.system_info()
    health = bridge.health()
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agents.core.native.bridge import NativeBridge

_bridge_instance: NativeBridge | None = None
_bridge_lock = threading.Lock()

# Config path
_SETTINGS_PATH = Path(__file__).parent.parent.parent.parent / "config" / "settings.json"


def detect_platform() -> str:
    """Detect the host operating system.

    Returns one of: 'windows', 'darwin', 'linux'
    """
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def _load_native_config() -> dict[str, Any]:
    """Load native bridge configuration from settings.json.

    Returns the 'native' section or sensible defaults.
    Feature flags control which subsystems are enabled.
    """
    defaults: dict[str, Any] = {
        "enabled": True,
        "features": {
            "clipboard": True,
            "notifications": True,
            "shell": True,
            "tray": True,
            "sysinfo": True,
            "app_launch": True,
            "process_list": True,
        },
        "shell": {
            "rate_limit_per_minute": 10,
            "timeout_seconds": 30.0,
            "max_stdout_bytes": 10_000,
            "max_stderr_bytes": 5_000,
        },
        "tray": {
            "auto_start": False,
            "ipc_port": 9377,
        },
    }

    try:
        if _SETTINGS_PATH.exists():
            data = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
            native_cfg = data.get("native", {})
            # Deep merge with defaults
            for key, val in defaults.items():
                if key not in native_cfg:
                    native_cfg[key] = val
                elif isinstance(val, dict):
                    for k2, v2 in val.items():
                        if k2 not in native_cfg[key]:
                            native_cfg[key][k2] = v2
            return native_cfg
    except Exception:
        pass

    return defaults


def get_bridge() -> NativeBridge:
    """Get the platform-specific NativeBridge singleton.

    Thread-safe: uses double-checked locking to avoid contention
    after initial creation. The bridge is created once and cached
    for the lifetime of the process.
    """
    global _bridge_instance

    if _bridge_instance is not None:
        return _bridge_instance

    with _bridge_lock:
        # Double-check after acquiring lock
        if _bridge_instance is not None:
            return _bridge_instance

        platform = detect_platform()
        config = _load_native_config()

        if platform == "windows":
            from agents.core.native.windows import WindowsBridge
            _bridge_instance = WindowsBridge(config=config)
        elif platform == "darwin":
            from agents.core.native.darwin import DarwinBridge
            _bridge_instance = DarwinBridge(config=config)
        else:
            from agents.core.native.linux import LinuxBridge
            _bridge_instance = LinuxBridge(config=config)

        return _bridge_instance


def reset_bridge() -> None:
    """Reset the singleton (for testing only)."""
    global _bridge_instance
    with _bridge_lock:
        _bridge_instance = None


def check_dependencies() -> dict[str, bool]:
    """Check which optional native dependencies are installed.

    Returns a dict mapping package name → installed status.
    Useful for setup validation and health dashboards.
    """
    deps: dict[str, bool] = {}

    for pkg_name, import_name in [
        ("psutil", "psutil"),
        ("pyperclip", "pyperclip"),
        ("desktop-notifier", "desktop_notifier"),
        ("pystray", "pystray"),
        ("Pillow", "PIL"),
        ("rumps", "rumps"),
    ]:
        try:
            __import__(import_name)
            deps[pkg_name] = True
        except ImportError:
            deps[pkg_name] = False

    return deps


def install_instructions() -> str:
    """Generate platform-specific install instructions for missing deps."""
    missing = [name for name, installed in check_dependencies().items() if not installed]
    if not missing:
        return "All native dependencies are installed."

    platform = detect_platform()
    lines = ["Missing native dependencies:\n"]

    # Core deps (all platforms)
    core = [d for d in missing if d not in ("rumps",)]
    if core:
        lines.append(f"  pip install {' '.join(core)}")

    # macOS-only
    if platform == "darwin" and "rumps" in missing:
        lines.append("  pip install rumps  # macOS system tray")

    lines.append("\nOr install all at once:")
    if platform == "darwin":
        lines.append("  pip install psutil pyperclip desktop-notifier pystray Pillow rumps")
    else:
        lines.append("  pip install psutil pyperclip desktop-notifier pystray Pillow")

    return "\n".join(lines)


PLATFORM = detect_platform()

__all__ = [
    "get_bridge",
    "reset_bridge",
    "detect_platform",
    "check_dependencies",
    "install_instructions",
    "PLATFORM",
]
