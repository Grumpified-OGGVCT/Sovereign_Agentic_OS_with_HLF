"""
BrowserOS Integration — Discovery, health, launch, and default-browser registration.

BrowserOS is an open-source AI browser (Chromium 145) with:
  - Agent v3 (54 tools), BYOLLM, SOUL.md persona, Cowork filesystem tools
  - MCP server on stable port, 40+ app integrations, workflows, memory
  - Native update mechanism (download-and-replace)

This module detects a local BrowserOS installation, checks its health,
manages default-browser registration, and integrates with the Sovereign OS
setup wizard as an optional addon.

Docs: https://docs.browseros.com/
GitHub: https://github.com/BrowserOS-ai/BrowserOS
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import winreg  # type: ignore[import]  # Windows-only
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents.core.logger import ALSLogger

_logger = ALSLogger(agent_role="browseros-bridge", goal_id="integration")


# ── Constants ────────────────────────────────────────────────────────────────

_BROWSEROS_DOWNLOAD_URL = "https://browseros.com"
_BROWSEROS_DOCS_URL = "https://docs.browseros.com/"
_BROWSEROS_GITHUB_URL = "https://github.com/BrowserOS-ai/BrowserOS"

# Known install paths on Windows
_WINDOWS_INSTALL_PATHS: list[str] = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Chromium", "Application"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "BrowserOS"),
    os.path.join(os.environ.get("PROGRAMFILES", ""), "BrowserOS"),
    os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "BrowserOS"),
]

# BrowserOS data directory (SOUL.md, memory, config)
_BROWSEROS_DATA_DIR = os.path.join(
    os.environ.get("APPDATA", ""), ".browseros"
)
_BROWSEROS_USERDATA_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    "BrowserOS", "BrowserOS", "User Data", ".browseros",
)

# Registry keys for URL protocol and default browser
_HKCU_CLASSES = r"Software\Classes"
_BROWSEROS_PROTOCOL_KEY = r"Software\Classes\browseros"
_HTTP_HANDLER_KEY = r"Software\Classes\http\shell\open\command"
_HTTPS_HANDLER_KEY = r"Software\Classes\https\shell\open\command"

# MCP default port (BrowserOS v0.38+ keeps port stable across restarts)
_BROWSEROS_MCP_PORT = 12007


# ── Data Structures ──────────────────────────────────────────────────────────


@dataclass
class BrowserOSInfo:
    """Detected BrowserOS installation metadata."""

    installed: bool = False
    executable_path: str = ""
    version: str = ""
    data_dir: str = ""
    soul_md_path: str = ""
    memory_dir: str = ""
    mcp_port: int = _BROWSEROS_MCP_PORT
    is_default_browser: bool = False
    is_running: bool = False
    protocol_registered: bool = False


@dataclass
class BrowserOSHealth:
    """BrowserOS health check result."""

    installed: bool = False
    running: bool = False
    mcp_reachable: bool = False
    soul_md_exists: bool = False
    version: str = ""
    default_browser: bool = False
    details: dict[str, str] = field(default_factory=dict)

    @property
    def overall(self) -> str:
        if not self.installed:
            return "not_installed"
        if self.running and self.mcp_reachable:
            return "healthy"
        if self.running:
            return "degraded"
        return "stopped"


# ── Discovery ────────────────────────────────────────────────────────────────


def _read_registry_command(key_path: str) -> str:
    """Read the (Default) value from a registry command key."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "")
            return value
    except (OSError, FileNotFoundError):
        return ""


def _check_protocol_registered() -> bool:
    """Check if the browseros:// URL protocol is registered."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _BROWSEROS_PROTOCOL_KEY):
            return True
    except (OSError, FileNotFoundError):
        return False


def _find_executable() -> str:
    """Find the BrowserOS executable path.

    Checks:
    1. Registry protocol handler (most reliable)
    2. Known install directories
    3. PATH via shutil.which
    """
    # 1. Registry — extract exe path from protocol handler
    cmd_value = _read_registry_command(
        rf"{_BROWSEROS_PROTOCOL_KEY}\shell\open\command"
    )
    if cmd_value:
        # Value looks like: "C:\path\to\chrome.exe" --single-argument %1
        exe_path = cmd_value.split('"')[1] if '"' in cmd_value else cmd_value.split()[0]
        if os.path.isfile(exe_path):
            _logger.log("BROWSEROS_FOUND_VIA_REGISTRY", {"path": exe_path})
            return exe_path

    # 2. Known install paths
    for base_dir in _WINDOWS_INSTALL_PATHS:
        if not os.path.isdir(base_dir):
            continue
        for exe_name in ("BrowserOS.exe", "chrome.exe"):
            candidate = os.path.join(base_dir, exe_name)
            if os.path.isfile(candidate):
                _logger.log("BROWSEROS_FOUND_VIA_PATH", {"path": candidate})
                return candidate
        # Check subdirectories one level deep
        try:
            for child in os.listdir(base_dir):
                child_path = os.path.join(base_dir, child)
                if os.path.isdir(child_path):
                    for exe_name in ("BrowserOS.exe", "chrome.exe"):
                        candidate = os.path.join(child_path, exe_name)
                        if os.path.isfile(candidate):
                            return candidate
        except OSError:
            pass

    # 3. PATH fallback
    which_result = shutil.which("BrowserOS") or shutil.which("browseros")
    if which_result:
        return which_result

    return ""


def _get_version(exe_path: str) -> str:
    """Get BrowserOS version from --version flag or file metadata."""
    if not exe_path:
        return ""
    try:
        result = subprocess.run(
            [exe_path, "--version"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            # Output: "Chromium 145.0.xxxx.xx BrowserOS/0.41.0" or similar
            return result.stdout.strip().split("\n")[0]
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        pass
    return ""


def _find_data_dirs() -> tuple[str, str, str]:
    """Find SOUL.md path, data dir, and memory dir.

    Returns (data_dir, soul_md_path, memory_dir).
    """
    # Check known locations
    for data_dir in (_BROWSEROS_DATA_DIR, _BROWSEROS_USERDATA_DIR):
        if os.path.isdir(data_dir):
            soul_path = os.path.join(data_dir, "SOUL.md")
            memory_dir = os.path.join(data_dir, "memory")
            return data_dir, soul_path, memory_dir

    return "", "", ""


def _is_running() -> bool:
    """Check if BrowserOS is currently running (by process name)."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
            shell=True,
        )
        # BrowserOS runs as chrome.exe — check for the BrowserOS user data dir
        # in the command line to distinguish from regular Chrome
        if "chrome.exe" in result.stdout:
            # More precise: check via WMIC for BrowserOS-specific args
            wmic = subprocess.run(
                ["wmic", "process", "where", "name='chrome.exe'",
                 "get", "CommandLine", "/value"],
                capture_output=True, text=True, timeout=5, shell=True,
            )
            if "BrowserOS" in wmic.stdout or "browseros" in wmic.stdout.lower():
                return True
    except (subprocess.TimeoutExpired, OSError):
        pass
    return False


def _check_is_default_browser(exe_path: str) -> bool:
    """Check if BrowserOS is the current default browser."""
    if not exe_path:
        return False
    http_cmd = _read_registry_command(_HTTP_HANDLER_KEY)
    return exe_path.lower() in http_cmd.lower() if http_cmd else False


def _check_mcp_reachable(port: int = _BROWSEROS_MCP_PORT) -> bool:
    """Check if BrowserOS MCP server is responding."""
    import urllib.request
    try:
        req = urllib.request.Request(
            f"http://localhost:{port}/sse",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status < 500
    except Exception:
        pass
    # Try common alternative ports
    for alt_port in (port + 1, port - 1, 3000, 8080):
        try:
            req = urllib.request.Request(
                f"http://localhost:{alt_port}/sse",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=1) as resp:
                if resp.status < 500:
                    return True
        except Exception:
            pass
    return False


def discover_browseros() -> BrowserOSInfo:
    """Auto-detect BrowserOS installation and return comprehensive info.

    This is the main entry point for Phase 1 discovery.
    """
    exe_path = _find_executable()
    data_dir, soul_path, memory_dir = _find_data_dirs()
    protocol_ok = _check_protocol_registered()

    info = BrowserOSInfo(
        installed=bool(exe_path),
        executable_path=exe_path,
        version=_get_version(exe_path) if exe_path else "",
        data_dir=data_dir,
        soul_md_path=soul_path,
        memory_dir=memory_dir,
        mcp_port=_BROWSEROS_MCP_PORT,
        is_default_browser=_check_is_default_browser(exe_path),
        is_running=_is_running() if exe_path else False,
        protocol_registered=protocol_ok,
    )

    _logger.log("BROWSEROS_DISCOVERY", {
        "installed": info.installed,
        "version": info.version,
        "default_browser": info.is_default_browser,
        "protocol": info.protocol_registered,
        "running": info.is_running,
    })
    return info


# ── Health Check ─────────────────────────────────────────────────────────────


def get_browseros_health() -> BrowserOSHealth:
    """Run a comprehensive health check on BrowserOS."""
    info = discover_browseros()
    details: dict[str, str] = {}

    if not info.installed:
        details["install"] = f"Not found. Download from {_BROWSEROS_DOWNLOAD_URL}"
        return BrowserOSHealth(details=details)

    details["executable"] = info.executable_path
    if info.version:
        details["version"] = info.version
    if info.data_dir:
        details["data_dir"] = info.data_dir

    soul_exists = bool(info.soul_md_path) and os.path.isfile(info.soul_md_path)
    mcp_ok = _check_mcp_reachable(info.mcp_port)

    if not info.is_running:
        details["status"] = "BrowserOS is installed but not running"
    if not mcp_ok and info.is_running:
        details["mcp"] = f"MCP server not responding on port {info.mcp_port}"
    if not soul_exists:
        details["soul"] = "SOUL.md not found — first conversation will create it"

    health = BrowserOSHealth(
        installed=info.installed,
        running=info.is_running,
        mcp_reachable=mcp_ok,
        soul_md_exists=soul_exists,
        version=info.version,
        default_browser=info.is_default_browser,
        details=details,
    )

    _logger.log("BROWSEROS_HEALTH", {
        "overall": health.overall,
        "running": health.running,
        "mcp": health.mcp_reachable,
        "soul": health.soul_md_exists,
    })
    return health


# ── Launch ───────────────────────────────────────────────────────────────────


def launch_browseros(
    url: str | None = None,
    new_window: bool = False,
) -> int | None:
    """Launch BrowserOS, optionally opening a URL.

    Returns the process PID, or None on failure.
    """
    info = discover_browseros()
    if not info.installed:
        _logger.log("BROWSEROS_LAUNCH_FAILED", {"reason": "not_installed"})
        return None

    args = [info.executable_path]
    if new_window:
        args.append("--new-window")
    if url:
        args.append(url)

    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _logger.log("BROWSEROS_LAUNCHED", {
            "pid": proc.pid,
            "url": url or "(homepage)",
        })
        return proc.pid
    except (OSError, FileNotFoundError) as exc:
        _logger.log("BROWSEROS_LAUNCH_ERROR", {"error": str(exc)})
        return None


# ── Default Browser Registration ─────────────────────────────────────────────


def register_as_default_browser() -> tuple[bool, str]:
    """Set BrowserOS as the default browser via Windows registry.

    Writes HTTP/HTTPS handler keys. Returns (success, message).
    This is REVERSIBLE via restore_previous_browser().
    """
    info = discover_browseros()
    if not info.installed:
        return False, "BrowserOS not installed"

    exe_path = info.executable_path
    new_command = f'"{exe_path}" --single-argument %1'

    backup: dict[str, str] = {}

    try:
        # Backup current handlers
        for key_path, label in [
            (_HTTP_HANDLER_KEY, "http"),
            (_HTTPS_HANDLER_KEY, "https"),
        ]:
            current = _read_registry_command(key_path)
            if current:
                backup[label] = current

        # Save backup for restore
        backup_path = os.path.join(
            info.data_dir or os.environ.get("APPDATA", ""),
            ".sovereign_browser_backup.json",
        )
        if backup:
            os.makedirs(os.path.dirname(backup_path), exist_ok=True)
            with open(backup_path, "w") as f:
                json.dump(backup, f, indent=2)

        # Write new handlers
        for key_path in (_HTTP_HANDLER_KEY, _HTTPS_HANDLER_KEY):
            try:
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, new_command)
            except OSError as exc:
                return False, f"Registry write failed for {key_path}: {exc}"

        # Notify Windows of the change
        try:
            import ctypes
            SHCNE_ASSOCCHANGED = 0x08000000
            SHCNF_IDLIST = 0x0000
            ctypes.windll.shell32.SHChangeNotify(
                SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None,
            )
        except Exception:
            pass  # Non-critical — change still takes effect on next restart

        _logger.log("BROWSEROS_SET_DEFAULT", {
            "exe": exe_path,
            "backup_saved": bool(backup),
        })
        return True, f"BrowserOS set as default browser. Backup saved to {backup_path}"

    except Exception as exc:
        return False, f"Failed to set default browser: {exc}"


def restore_previous_browser() -> tuple[bool, str]:
    """Restore the previous default browser from backup."""
    backup_path = os.path.join(
        _BROWSEROS_DATA_DIR or os.environ.get("APPDATA", ""),
        ".sovereign_browser_backup.json",
    )

    if not os.path.isfile(backup_path):
        return False, "No browser backup found"

    try:
        with open(backup_path) as f:
            backup = json.load(f)

        key_map = {"http": _HTTP_HANDLER_KEY, "https": _HTTPS_HANDLER_KEY}
        for label, key_path in key_map.items():
            if label in backup:
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, backup[label])

        os.remove(backup_path)
        _logger.log("BROWSEROS_RESTORED_DEFAULT", {"backup": backup_path})
        return True, "Previous default browser restored"

    except Exception as exc:
        return False, f"Failed to restore: {exc}"


# ── SOUL.md Integration ──────────────────────────────────────────────────────


def read_soul_md() -> str | None:
    """Read BrowserOS SOUL.md content."""
    _, soul_path, _ = _find_data_dirs()
    if soul_path and os.path.isfile(soul_path):
        with open(soul_path, encoding="utf-8") as f:
            return f.read()
    return None


def inject_sovereign_boundaries(soul_content: str) -> str:
    """Inject Sovereign OS ALIGN boundaries into SOUL.md content.

    Only adds the section if not already present.
    """
    marker = "## Sovereign OS Integration"
    if marker in soul_content:
        return soul_content  # Already injected

    sovereign_section = """

## Sovereign OS Integration
- ALWAYS check with the Sovereign OS gateway before:
  - Making purchases or financial transactions
  - Sending emails with attachments to external recipients
  - Downloading or executing binary files
- Route all file operations through ACFS confinement when Cowork is active
- Respect the ALIGN governance ledger — blocked actions should be surfaced, not bypassed
- Log significant browser actions to the Agentic Log Standard (ALS) for audit trail
"""
    return soul_content.rstrip() + "\n" + sovereign_section


def write_soul_md(content: str) -> bool:
    """Write content to BrowserOS SOUL.md."""
    _, soul_path, _ = _find_data_dirs()
    if not soul_path:
        return False
    try:
        os.makedirs(os.path.dirname(soul_path), exist_ok=True)
        with open(soul_path, "w", encoding="utf-8") as f:
            f.write(content)
        _logger.log("BROWSEROS_SOUL_WRITTEN", {"path": soul_path})
        return True
    except OSError as exc:
        _logger.log("BROWSEROS_SOUL_ERROR", {"error": str(exc)})
        return False


# ── Wizard Integration ───────────────────────────────────────────────────────


def get_browseros_wizard_step() -> dict:
    """Generate a setup wizard step for BrowserOS.

    Returns a step dict compatible with cli_tools.get_setup_wizard_steps().
    """
    info = discover_browseros()

    if info.installed and info.is_default_browser:
        return {
            "title": "✅ BrowserOS Ready",
            "description": (
                f"BrowserOS {info.version} — installed and set as default browser. "
                "AI browsing, workflows, and MCP tools are available."
            ),
            "action": None,
            "command": None,
            "priority": "done",
            "category": "browser",
        }
    elif info.installed and not info.is_default_browser:
        return {
            "title": "Set BrowserOS as Default Browser",
            "description": (
                f"BrowserOS {info.version} is installed but not the default browser. "
                "Set it as default to route all web operations through the AI browser."
            ),
            "action": "Set as default browser",
            "command": None,  # Handled programmatically via register_as_default_browser()
            "priority": "optional",
            "category": "browser",
        }
    else:
        return {
            "title": "Install BrowserOS (Optional)",
            "description": (
                "BrowserOS is an open-source AI browser with 54 agent tools, "
                "BYOLLM support, workflows, SOUL.md persona, and MCP integration. "
                "It can serve as the Sovereign OS default browser."
            ),
            "action": f"Download from {_BROWSEROS_DOWNLOAD_URL}",
            "command": None,
            "priority": "optional",
            "category": "browser",
        }


# ── Tool Registration ────────────────────────────────────────────────────────


def register_browseros_tools(registry: Any) -> None:
    """Register BrowserOS management commands into the ToolRegistry."""
    from agents.core.native import _load_native_config

    config = _load_native_config()
    features = config.get("features", {})
    registered = 0

    if features.get("browseros", True):
        def _handle_browseros_status(params: dict) -> dict:
            health = get_browseros_health()
            return {
                "status": "ok",
                "data": {
                    "overall": health.overall,
                    "installed": health.installed,
                    "running": health.running,
                    "mcp_reachable": health.mcp_reachable,
                    "version": health.version,
                    "default_browser": health.default_browser,
                    "soul_md": health.soul_md_exists,
                    "details": health.details,
                },
            }

        registry.register(
            name="browseros.status",
            handler=_handle_browseros_status,
            schema={
                "description": (
                    "Check BrowserOS installation, health, MCP connectivity, "
                    "and default browser status"
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            gas_cost=2,
            permissions=["hearth", "forge", "sovereign"],
            tags=["browseros", "browser", "health"],
        )
        registered += 1

        def _handle_browseros_launch(params: dict) -> dict:
            url = params.get("url")
            pid = launch_browseros(url=url)
            if pid:
                return {"status": "ok", "data": {"pid": pid, "url": url}}
            return {"status": "error", "data": {"message": "BrowserOS not installed or launch failed"}}

        registry.register(
            name="browseros.launch",
            handler=_handle_browseros_launch,
            schema={
                "description": "Launch BrowserOS, optionally opening a URL",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to open"},
                    },
                    "required": [],
                },
            },
            gas_cost=3,
            permissions=["forge", "sovereign"],
            tags=["browseros", "browser", "launch"],
        )
        registered += 1

        def _handle_set_default(params: dict) -> dict:
            ok, msg = register_as_default_browser()
            return {"status": "ok" if ok else "error", "data": {"message": msg}}

        registry.register(
            name="browseros.set_default",
            handler=_handle_set_default,
            schema={
                "description": "Set BrowserOS as the system default browser (reversible)",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            gas_cost=5,
            permissions=["sovereign"],  # Elevated — modifies system settings
            tags=["browseros", "browser", "system"],
        )
        registered += 1

        def _handle_restore_default(params: dict) -> dict:
            ok, msg = restore_previous_browser()
            return {"status": "ok" if ok else "error", "data": {"message": msg}}

        registry.register(
            name="browseros.restore_default",
            handler=_handle_restore_default,
            schema={
                "description": "Restore the previous default browser (undo set_default)",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            gas_cost=5,
            permissions=["sovereign"],
            tags=["browseros", "browser", "system"],
        )
        registered += 1

    _logger.log("BROWSEROS_TOOLS_REGISTERED", {"count": registered})
