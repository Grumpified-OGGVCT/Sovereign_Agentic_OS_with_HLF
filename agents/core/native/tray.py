"""
System Tray — Cross-platform system tray for Sovereign OS.

Provides a persistent tray icon with context menu showing agent status,
quick-access tools, and OS controls. Uses pystray (Win/Linux) or rumps (macOS).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any, Callable

from agents.core.native.bridge import TrayMenuItem


class _ServiceState:
    """Tracks a managed subprocess lifecycle."""
    __slots__ = ("name", "process", "running")

    def __init__(self, name: str) -> None:
        self.name = name
        self.process: subprocess.Popen | None = None
        self.running: bool = False

    def start(self, cmd: list[str], *, env: dict | None = None, notify: Callable | None = None) -> None:
        if self.running:
            return
        self.process = subprocess.Popen(cmd, env=env or os.environ.copy())
        self.running = True
        if notify:
            notify(f"{self.name} started.")

    def stop(self, notify: Callable | None = None) -> None:
        if self.running and self.process:
            self.process.terminate()
            self.running = False
            if notify:
                notify(f"{self.name} stopped.")


class SovereignTray:
    """Cross-platform system tray abstraction.

    Provides a unified interface over pystray (Windows/Linux) and
    rumps (macOS). Falls back gracefully when neither is available.
    """

    def __init__(self, tooltip: str = "Sovereign OS") -> None:
        self.tooltip = tooltip
        self._menu_items: list[TrayMenuItem] = []
        self._callbacks: dict[str, Callable[[], None]] = {}
        self._backend: str | None = None
        self._tray: Any = None
        self._running = False

        # Managed services (unified from gui/tray_manager.py)
        self._backend_svc = _ServiceState("OS Backend")
        self._gui_svc = _ServiceState("Command Center GUI")
        self._mcp_svc = _ServiceState("MCP Server")

    # ── Service Lifecycle ────────────────────────────────────────────────

    def _notify(self, message: str) -> None:
        """Send a tray notification if the backend supports it."""
        if self._tray is not None:
            try:
                self._tray.notify(message)
            except Exception:
                pass
        print(f"[Tray] {message}")

    def start_backend(self, profile: str = "hearth") -> None:
        """Start Docker Compose backend with the given profile."""
        env = os.environ.copy()
        env["DEPLOYMENT_TIER"] = profile
        self._backend_svc.start(
            ["docker", "compose", "--profile", profile, "up", "-d"],
            env=env,
            notify=self._notify,
        )

    def stop_backend(self) -> None:
        """Stop Docker Compose backend."""
        if self._backend_svc.running:
            subprocess.Popen(["docker", "compose", "down"])
            self._backend_svc.running = False
            self._notify("OS Backend stopped.")

    def start_gui(self, gui_path: str = "gui/app.py", port: int = 8501) -> None:
        """Start Streamlit GUI and open browser after delay."""
        self._gui_svc.start(
            ["uv", "run", "streamlit", "run", gui_path, "--server.headless", "true"],
            notify=self._notify,
        )

        def _open_browser() -> None:
            import time as _t
            _t.sleep(3)
            webbrowser.open(f"http://localhost:{port}")

        threading.Thread(target=_open_browser, daemon=True).start()

    def stop_gui(self) -> None:
        """Stop Streamlit GUI."""
        self._gui_svc.stop(notify=self._notify)

    def start_mcp(self, mcp_script: str = "mcp/sovereign_mcp_server.py") -> None:
        """Start the MCP server."""
        self._mcp_svc.start(
            ["uv", "run", "python", mcp_script],
            notify=self._notify,
        )

    def stop_mcp(self) -> None:
        """Stop the MCP server."""
        self._mcp_svc.stop(notify=self._notify)

    def stop_all_services(self) -> None:
        """Gracefully shut down all managed services."""
        self.stop_gui()
        self.stop_mcp()
        self.stop_backend()

    def auto_launch_all(self) -> None:
        """Auto-launch backend, GUI, and MCP (called by --auto-launch)."""
        self.start_backend()
        self.start_gui()
        self.start_mcp()

    def register_callback(self, action: str, callback: Callable[[], None]) -> None:
        """Register a callback for a menu action."""
        self._callbacks[action] = callback

    def set_menu(self, items: list[TrayMenuItem]) -> None:
        """Set the tray context menu items."""
        self._menu_items = items

    def _create_default_menu(self) -> list[TrayMenuItem]:
        """Create the default Sovereign OS tray menu."""
        return [
            TrayMenuItem(label="Sovereign OS", action="header", enabled=False),
            TrayMenuItem(label="Status: Active", action="status", enabled=False, separator_before=True),
            TrayMenuItem(label="System Info", action="sysinfo", separator_before=True),
            TrayMenuItem(label="Clipboard History", action="clipboard"),
            TrayMenuItem(label="Notifications", action="notifications"),
            TrayMenuItem(
                label="Agents",
                action="agents",
                separator_before=True,
                children=[
                    TrayMenuItem(label="List Running", action="agents_list"),
                    TrayMenuItem(label="Pause All", action="agents_pause"),
                    TrayMenuItem(label="Resume All", action="agents_resume"),
                ],
            ),
            TrayMenuItem(
                label="Tools",
                action="tools",
                children=[
                    TrayMenuItem(label="Open Workspace", action="open_workspace"),
                    TrayMenuItem(label="Launch GUI", action="launch_gui"),
                    TrayMenuItem(label="View Logs", action="view_logs"),
                    TrayMenuItem(label="Run Preflight", action="run_preflight"),
                ],
            ),
            TrayMenuItem(
                label="Gateway",
                action="gateway",
                children=[
                    TrayMenuItem(label="Start Gateway", action="gateway_start"),
                    TrayMenuItem(label="Stop Gateway", action="gateway_stop"),
                    TrayMenuItem(label="Restart Gateway", action="gateway_restart"),
                    TrayMenuItem(label="Health Check", action="gateway_health"),
                ],
            ),
            TrayMenuItem(label="Settings", action="settings", separator_before=True),
            TrayMenuItem(label="Restart Services", action="restart"),
            TrayMenuItem(label="Quit", action="quit", separator_before=True),
        ]

    def _detect_backend(self) -> str | None:
        """Detect which tray backend is available."""
        if sys.platform == "darwin":
            try:
                import rumps  # noqa: F401
                return "rumps"
            except ImportError:
                pass

        try:
            import pystray  # noqa: F401
            return "pystray"
        except ImportError:
            pass

        return None

    def start(self, blocking: bool = True) -> bool:
        """Start the system tray.

        Args:
            blocking: If True, blocks the calling thread. If False,
                      runs in a background thread.

        Returns:
            True if the tray started successfully.
        """
        self._backend = self._detect_backend()
        if self._backend is None:
            return False

        if not self._menu_items:
            self._menu_items = self._create_default_menu()

        if self._backend == "pystray":
            return self._start_pystray(blocking)
        elif self._backend == "rumps":
            return self._start_rumps(blocking)

        return False

    def stop(self) -> None:
        """Stop the system tray."""
        self._running = False
        if self._tray is not None:
            try:
                if self._backend == "pystray":
                    self._tray.stop()
                elif self._backend == "rumps":
                    from rumps import quit_application
                    quit_application()
            except Exception:
                pass
            self._tray = None

    # ── pystray Backend ──────────────────────────────────────────────────

    def _start_pystray(self, blocking: bool) -> bool:
        """Start using pystray (Windows/Linux)."""
        try:
            import pystray
            from PIL import Image

            # Create a simple shield icon (32x32 green on dark)
            icon_image = self._create_icon_image()

            menu = self._build_pystray_menu(self._menu_items)
            self._tray = pystray.Icon(
                name="sovereign_os",
                icon=icon_image,
                title=self.tooltip,
                menu=menu,
            )
            self._running = True

            if blocking:
                self._tray.run()
            else:
                thread = threading.Thread(target=self._tray.run, daemon=True)
                thread.start()

            return True
        except Exception:
            return False

    def _build_pystray_menu(self, items: list[TrayMenuItem]) -> Any:
        """Convert TrayMenuItems to pystray menu."""
        import pystray

        pystray_items = []
        for item in items:
            if item.children:
                submenu = self._build_pystray_menu(item.children)
                pystray_items.append(
                    pystray.MenuItem(
                        item.label,
                        submenu,
                        enabled=item.enabled,
                    )
                )
            else:
                callback = self._make_pystray_callback(item.action)
                pystray_items.append(
                    pystray.MenuItem(
                        item.label,
                        callback,
                        enabled=item.enabled,
                    )
                )

        return pystray.Menu(*pystray_items)

    def _make_pystray_callback(self, action: str) -> Callable:
        """Create a pystray callback that dispatches to registered callbacks."""
        def _callback(icon: Any, menu_item: Any) -> None:
            if action == "quit":
                self.stop()
                return
            cb = self._callbacks.get(action)
            if cb:
                cb()

        return _callback

    # ── rumps Backend ────────────────────────────────────────────────────

    def _start_rumps(self, blocking: bool) -> bool:
        """Start using rumps (macOS)."""
        try:
            import rumps

            class SovereignApp(rumps.App):
                def __init__(self, tray: SovereignTray):
                    super().__init__("Sovereign OS", quit_button=None)
                    self._tray = tray

            app = SovereignApp(self)

            for item in self._menu_items:
                if item.label == "Quit":
                    app.menu.add(rumps.MenuItem("Quit", callback=lambda _: rumps.quit_application()))
                else:
                    menu_item = rumps.MenuItem(item.label)
                    if not item.enabled:
                        menu_item.set_callback(None)
                    else:
                        action = item.action
                        menu_item.set_callback(lambda sender, a=action: self._callbacks.get(a, lambda: None)())
                    app.menu.add(menu_item)

            self._tray = app
            self._running = True

            if blocking:
                app.run()
            else:
                thread = threading.Thread(target=app.run, daemon=True)
                thread.start()

            return True
        except Exception:
            return False

    # ── Icon Generation ──────────────────────────────────────────────────

    @staticmethod
    def _create_icon_image() -> Any:
        """Create a simple Sovereign OS tray icon using Pillow."""
        try:
            from PIL import Image, ImageDraw

            size = 64
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            # Dark circle background
            draw.ellipse([2, 2, size - 2, size - 2], fill=(30, 30, 46, 255))

            # Shield shape (simplified)
            shield_points = [
                (size // 2, 8),          # top center
                (size - 12, 16),         # top right
                (size - 12, size // 2),  # mid right
                (size // 2, size - 8),   # bottom center
                (12, size // 2),         # mid left
                (12, 16),               # top left
            ]
            draw.polygon(shield_points, fill=(108, 188, 97, 255))  # Sovereign green

            # "S" letter (simplified centerline)
            draw.line(
                [(size // 2 - 6, 22), (size // 2 + 6, 22)],
                fill=(255, 255, 255, 220),
                width=3,
            )
            draw.line(
                [(size // 2 - 6, size // 2), (size // 2 + 6, size // 2)],
                fill=(255, 255, 255, 220),
                width=3,
            )
            draw.line(
                [(size // 2 - 6, size - 22), (size // 2 + 6, size - 22)],
                fill=(255, 255, 255, 220),
                width=3,
            )

            return img
        except ImportError:
            # Pillow not available — return a minimal 1x1 pixel
            from PIL import Image
            return Image.new("RGB", (1, 1), (108, 188, 97))
