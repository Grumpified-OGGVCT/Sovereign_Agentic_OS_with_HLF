"""Tests for the OS Action Menu System (Phase 6)."""

from __future__ import annotations

import pytest

from agents.core.native.action_menu import build_os_action_menu, register_action_menu_callbacks
from agents.core.native.bridge import TrayMenuItem


class TestBuildOSActionMenu:
    """Test build_os_action_menu() returns a well-formed menu."""

    def test_returns_list(self):
        menu = build_os_action_menu()
        assert isinstance(menu, list)

    def test_minimum_item_count(self):
        """Menu should have at least 15 top-level items."""
        menu = build_os_action_menu()
        assert len(menu) >= 15

    def test_all_items_are_tray_menu_items(self):
        menu = build_os_action_menu()
        for item in menu:
            assert isinstance(item, TrayMenuItem)

    def test_first_item_is_header(self):
        menu = build_os_action_menu()
        assert menu[0].action == "header"
        assert "Sovereign" in menu[0].label

    def test_last_item_is_quit(self):
        menu = build_os_action_menu()
        assert menu[-1].action == "quit"

    def test_has_hlf_submenu(self):
        """Must have an HLF Programs submenu."""
        menu = build_os_action_menu()
        hlf = [m for m in menu if m.action == "hlf"]
        assert len(hlf) == 1
        assert hlf[0].children is not None
        assert len(hlf[0].children) >= 3

    def test_has_agents_submenu(self):
        menu = build_os_action_menu()
        agents = [m for m in menu if m.action == "agents"]
        assert len(agents) == 1
        assert agents[0].children is not None
        assert len(agents[0].children) >= 3

    def test_has_security_submenu(self):
        menu = build_os_action_menu()
        sec = [m for m in menu if m.action == "security"]
        assert len(sec) == 1
        assert sec[0].children is not None
        assert len(sec[0].children) >= 3

    def test_has_quick_tools_submenu(self):
        menu = build_os_action_menu()
        qt = [m for m in menu if m.action == "quick_tools"]
        assert len(qt) == 1
        assert qt[0].children is not None

    def test_has_openclaw_submenu(self):
        menu = build_os_action_menu()
        oc = [m for m in menu if m.action == "openclaw"]
        assert len(oc) == 1
        assert oc[0].children is not None

    def test_has_services_submenu(self):
        menu = build_os_action_menu()
        svc = [m for m in menu if m.action == "services"]
        assert len(svc) == 1
        assert svc[0].children is not None
        # Must have start/stop for backend, GUI, MCP + all
        actions = {c.action for c in svc[0].children}
        assert "svc_start_backend" in actions
        assert "svc_stop_backend" in actions
        assert "svc_start_all" in actions
        assert "svc_stop_all" in actions

    def test_has_gateway_submenu(self):
        menu = build_os_action_menu()
        gw = [m for m in menu if m.action == "gateway"]
        assert len(gw) == 1
        assert gw[0].children is not None

    def test_no_duplicate_actions(self):
        """Every action should be unique across the entire menu tree."""
        menu = build_os_action_menu()
        actions = []

        def _collect(items):
            for item in items:
                if item.enabled and item.action not in ("header", "status"):
                    actions.append(item.action)
                if item.children:
                    _collect(item.children)

        _collect(menu)
        # Some parent actions (like 'agents') double as labels
        # Only check leaf actions (no children)
        leaf_actions = []
        def _collect_leaves(items):
            for item in items:
                if item.children:
                    _collect_leaves(item.children)
                elif item.enabled:
                    leaf_actions.append(item.action)
        _collect_leaves(menu)
        assert len(leaf_actions) == len(set(leaf_actions)), f"Duplicate leaf actions: {leaf_actions}"

    def test_total_action_count(self):
        """Should have at least 30 unique leaf actions."""
        menu = build_os_action_menu()
        actions = set()
        def _collect_leaves(items):
            for item in items:
                if item.children:
                    _collect_leaves(item.children)
                elif item.enabled:
                    actions.add(item.action)
        _collect_leaves(menu)
        assert len(actions) >= 30, f"Only {len(actions)} leaf actions, expected >= 30"


class TestRegisterCallbacks:
    """Test register_action_menu_callbacks() wires correctly."""

    def test_registers_callbacks_to_tray(self):
        """All 38 action callbacks should be registered."""
        from agents.core.native.tray import SovereignTray

        tray = SovereignTray()
        initial_count = len(tray._callbacks)

        register_action_menu_callbacks(tray)

        # Should register many more callbacks
        assert len(tray._callbacks) > initial_count
        assert len(tray._callbacks) >= 30

    def test_critical_callbacks_present(self):
        """Key callbacks must be registered."""
        from agents.core.native.tray import SovereignTray

        tray = SovereignTray()
        register_action_menu_callbacks(tray)

        for action in [
            "sysinfo", "clipboard",
            "hlf_run_gallery", "hlf_test",
            "agents_list", "daemon_health",
            "sentinel_status", "arbiter_disputes",
            "tool_gen_password", "tool_hash_file",
            "openclaw_status",
            "svc_start_backend", "svc_stop_all",
            "settings", "quit",
        ]:
            assert action in tray._callbacks, f"Missing callback: {action}"

    def test_service_callbacks_are_tray_methods(self):
        """Service callbacks should delegate to tray methods."""
        from agents.core.native.tray import SovereignTray

        tray = SovereignTray()
        register_action_menu_callbacks(tray)

        # These should be bound to the tray instance
        assert tray._callbacks["svc_start_backend"] == tray.start_backend
        assert tray._callbacks["svc_stop_all"] == tray.stop_all_services
        assert tray._callbacks["svc_start_all"] == tray.auto_launch_all
