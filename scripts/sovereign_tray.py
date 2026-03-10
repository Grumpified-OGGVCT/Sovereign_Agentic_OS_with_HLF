"""
sovereign_tray.py — Standalone Sovereign OS System Tray Service.

Entry point for the persistent tray icon that runs independently of
any agent. Uses the unified SovereignTray class with the full
Phase 6 OS Action Menu system.

Usage:
    python sovereign_tray.py              # blocking (foreground)
    python sovereign_tray.py --daemon     # background mode
    python sovereign_tray.py --auto-launch  # boot all services
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure project root is on the import path
_project_root = Path(__file__).resolve().parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from agents.core.native import PLATFORM
from agents.core.native.tray import SovereignTray
from agents.core.native.action_menu import register_action_menu_callbacks


def main() -> None:
    """Main entry point for the Sovereign OS tray service."""
    parser = argparse.ArgumentParser(description="Sovereign OS System Tray")
    parser.add_argument("--daemon", action="store_true", help="Run in background mode")
    parser.add_argument("--auto-launch", action="store_true", help="Auto-start backend, GUI, MCP")
    args = parser.parse_args()

    print(f"🛡️  Sovereign OS Tray — Platform: {PLATFORM}")

    tray = SovereignTray(tooltip="Sovereign OS — Active")

    # Wire all 38 action callbacks from the OS Action Menu (Phase 6)
    register_action_menu_callbacks(tray)

    if args.auto_launch:
        print("[Tray] Auto-launching all OS services…")
        tray.auto_launch_all()

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
