"""
sovereign_tray.py — Standalone Sovereign OS System Tray Service.

Entry point for the persistent tray icon that runs independently of
any agent. Communicates with the OS via local IPC (future) and provides
quick-access to Sovereign OS tools, agent status, and system controls.

Usage:
    python sovereign_tray.py            # blocking (foreground)
    python sovereign_tray.py --daemon   # background mode
"""

from __future__ import annotations

import argparse
import sys
import json
import webbrowser
from pathlib import Path

# Ensure project root is on the import path
_project_root = Path(__file__).resolve().parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from agents.core.native import get_bridge, PLATFORM
from agents.core.native.bridge import NotificationRequest, NotificationUrgency
from agents.core.native.tray import SovereignTray


def _on_sysinfo() -> None:
    """Show system info as a notification."""
    bridge = get_bridge()
    info = bridge.system_info()
    body = (
        f"Platform: {info.platform} {info.platform_version}\n"
        f"CPU: {info.cpu_count} cores @ {info.cpu_percent}%\n"
        f"RAM: {info.memory_available_mb}MB / {info.memory_total_mb}MB\n"
        f"Disk: {info.disk_free_gb}GB / {info.disk_total_gb}GB free\n"
        f"Uptime: {info.uptime_seconds / 3600:.1f}h"
    )
    bridge.notify(NotificationRequest(
        title="Sovereign OS — System Info",
        body=body,
        urgency=NotificationUrgency.LOW,
    ))


def _on_clipboard() -> None:
    """Show clipboard contents as a notification."""
    bridge = get_bridge()
    content = bridge.clipboard_read()
    preview = content.text[:200] + "..." if len(content.text) > 200 else content.text
    bridge.notify(NotificationRequest(
        title="Sovereign OS — Clipboard",
        body=preview or "(empty)",
        urgency=NotificationUrgency.LOW,
    ))


def _on_open_workspace() -> None:
    """Open the workspace directory."""
    bridge = get_bridge()
    bridge.open_file(str(_project_root))


def _on_launch_gui() -> None:
    """Launch the Sovereign OS GUI (placeholder)."""
    bridge = get_bridge()
    bridge.notify(NotificationRequest(
        title="Sovereign OS",
        body="GUI launcher not yet implemented. Coming in Phase 3.",
        urgency=NotificationUrgency.NORMAL,
    ))


def _on_view_logs() -> None:
    """Open the logs directory."""
    bridge = get_bridge()
    logs_dir = _project_root / "logs"
    if logs_dir.exists():
        bridge.open_file(str(logs_dir))
    else:
        bridge.notify(NotificationRequest(
            title="Sovereign OS",
            body="No logs directory found.",
            urgency=NotificationUrgency.LOW,
        ))


def _on_settings() -> None:
    """Open settings.json in default editor."""
    bridge = get_bridge()
    settings_path = _project_root / "config" / "settings.json"
    if settings_path.exists():
        bridge.open_file(str(settings_path))


def _on_agents_list() -> None:
    """Show running agents (placeholder)."""
    bridge = get_bridge()
    bridge.notify(NotificationRequest(
        title="Sovereign OS — Agents",
        body="Agent orchestration status will be available when the orchestrator is running.",
        urgency=NotificationUrgency.NORMAL,
    ))


def _on_run_preflight() -> None:
    """Run preflight checks and show notification."""
    bridge = get_bridge()
    bridge.notify(NotificationRequest(
        title="Sovereign OS — Preflight",
        body="Running npm run preflight... Check your terminal.",
        urgency=NotificationUrgency.NORMAL,
    ))
    bridge.shell_exec("npm", ["run", "preflight"], timeout_seconds=120.0, cwd=str(_project_root))


def main() -> None:
    """Main entry point for the Sovereign OS tray service."""
    parser = argparse.ArgumentParser(description="Sovereign OS System Tray")
    parser.add_argument("--daemon", action="store_true", help="Run in background mode")
    args = parser.parse_args()

    print(f"🛡️  Sovereign OS Tray — Platform: {PLATFORM}")

    tray = SovereignTray(tooltip="Sovereign OS — Active")

    # Register callbacks
    tray.register_callback("sysinfo", _on_sysinfo)
    tray.register_callback("clipboard", _on_clipboard)
    tray.register_callback("open_workspace", _on_open_workspace)
    tray.register_callback("launch_gui", _on_launch_gui)
    tray.register_callback("view_logs", _on_view_logs)
    tray.register_callback("settings", _on_settings)
    tray.register_callback("agents_list", _on_agents_list)
    tray.register_callback("run_preflight", _on_run_preflight)

    blocking = not args.daemon
    success = tray.start(blocking=blocking)

    if not success:
        print("⚠️  System tray unavailable. Install pystray + Pillow:")
        print("   pip install pystray Pillow")
        if PLATFORM == "darwin":
            print("   Or: pip install rumps")
        sys.exit(1)

    if args.daemon:
        print("🛡️  Tray running in background. Press Ctrl+C to stop.")
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            tray.stop()
            print("\n🛡️  Tray stopped.")


if __name__ == "__main__":
    main()
