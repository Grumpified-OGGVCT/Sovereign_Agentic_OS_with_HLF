"""
tray_manager.py — Sovereign OS Tray Manager (thin wrapper).

This module now delegates to the unified SovereignTray class in
agents.core.native.tray.  Preserved for backwards compatibility
and as the default entry point for ``python gui/tray_manager.py``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure project root is importable
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from agents.core.native.tray import SovereignTray  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Sovereign OS Tray Manager")
    parser.add_argument(
        "--auto-launch",
        action="store_true",
        help="Automatically start backend, GUI, and MCP on boot",
    )
    args = parser.parse_args()

    tray = SovereignTray(tooltip="Sovereign Agentic OS")

    # Wire quit action into service shutdown
    tray.register_callback("quit", tray.stop_all_services)

    if args.auto_launch:
        print("[Tray Manager] Auto-launching all OS services…")
        tray.auto_launch_all()

    print("Sovereign OS Tray Manager loading. Check your system tray.")
    success = tray.start(blocking=True)

    if not success:
        print("⚠️  System tray unavailable. Install pystray + Pillow:")
        print("   pip install pystray Pillow")
        sys.exit(1)


if __name__ == "__main__":
    main()
