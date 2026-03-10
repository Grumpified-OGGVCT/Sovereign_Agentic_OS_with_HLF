"""
Tests for agents.core.native.browseros — BrowserOS integration module.

Tests cover:
  - Discovery: registry, paths, fallback
  - Health check: status aggregation
  - SOUL.md: read, inject, write
  - Default browser: register/restore
  - Wizard step: all three states
  - Tool registration: 4 tools registered
  - MCP health check: reachability probing
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pytest


# ── Helper: stub winreg for non-Windows ──────────────────────────────────────

class _FakeWinregKey:
    """Minimal winreg key context manager stub."""
    def __enter__(self):
        return self
    def __exit__(self, *a):
        pass

_FAKE_WINREG = MagicMock()
_FAKE_WINREG.HKEY_CURRENT_USER = 0x80000001
_FAKE_WINREG.REG_SZ = 1
_FAKE_WINREG.OpenKey = MagicMock(return_value=_FakeWinregKey())
_FAKE_WINREG.CreateKey = MagicMock(return_value=_FakeWinregKey())
_FAKE_WINREG.QueryValueEx = MagicMock(return_value=("", 1))
_FAKE_WINREG.SetValueEx = MagicMock()


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _patch_winreg():
    """Ensure winreg is available on all platforms."""
    import sys
    if "winreg" not in sys.modules:
        sys.modules["winreg"] = _FAKE_WINREG
    yield


@pytest.fixture
def tmp_data_dir(tmp_path):
    """Create a temporary BrowserOS data directory."""
    data_dir = tmp_path / ".browseros"
    data_dir.mkdir()
    return data_dir


@pytest.fixture
def soul_content():
    return """# SOUL.md
I am a helpful AI assistant.
I prefer concise answers.
"""


# ── Discovery Tests ──────────────────────────────────────────────────────────


class TestDiscovery:
    """Test discover_browseros() and its sub-functions."""

    @patch("agents.core.native.browseros._find_executable", return_value="")
    @patch("agents.core.native.browseros._find_data_dirs", return_value=("", "", ""))
    @patch("agents.core.native.browseros._check_protocol_registered", return_value=False)
    def test_not_installed(self, mock_proto, mock_dirs, mock_exe):
        from agents.core.native.browseros import discover_browseros
        info = discover_browseros()
        assert not info.installed
        assert info.executable_path == ""
        assert info.version == ""

    @patch("agents.core.native.browseros._is_running", return_value=True)
    @patch("agents.core.native.browseros._check_is_default_browser", return_value=True)
    @patch("agents.core.native.browseros._get_version", return_value="Chromium 145.0")
    @patch("agents.core.native.browseros._check_protocol_registered", return_value=True)
    @patch("agents.core.native.browseros._find_data_dirs", return_value=("/data", "/data/SOUL.md", "/data/memory"))
    @patch("agents.core.native.browseros._find_executable", return_value="C:\\chrome.exe")
    def test_fully_installed(self, mock_exe, mock_dirs, mock_proto, mock_ver, mock_def, mock_run):
        from agents.core.native.browseros import discover_browseros
        info = discover_browseros()
        assert info.installed
        assert info.executable_path == "C:\\chrome.exe"
        assert info.version == "Chromium 145.0"
        assert info.is_default_browser
        assert info.is_running
        assert info.protocol_registered

    @patch("agents.core.native.browseros._read_registry_command", return_value="")
    @patch("shutil.which", return_value=None)
    @patch("os.path.isdir", return_value=False)
    def test_find_executable_not_found(self, mock_isdir, mock_which, mock_reg):
        from agents.core.native.browseros import _find_executable
        assert _find_executable() == ""

    @patch("agents.core.native.browseros._read_registry_command",
           return_value='"C:\\BrowserOS\\chrome.exe" --single-argument %1')
    @patch("os.path.isfile", return_value=True)
    def test_find_executable_via_registry(self, mock_isfile, mock_reg):
        from agents.core.native.browseros import _find_executable
        result = _find_executable()
        assert "chrome.exe" in result


class TestProtocol:
    """Test protocol registration checks."""

    @patch("agents.core.native.browseros.winreg")
    def test_protocol_registered(self, mock_wr):
        mock_wr.HKEY_CURRENT_USER = 0x80000001
        mock_wr.OpenKey.return_value = _FakeWinregKey()
        from agents.core.native.browseros import _check_protocol_registered
        assert _check_protocol_registered() is True

    @patch("agents.core.native.browseros.winreg")
    def test_protocol_not_registered(self, mock_wr):
        mock_wr.HKEY_CURRENT_USER = 0x80000001
        mock_wr.OpenKey.side_effect = OSError("not found")
        from agents.core.native.browseros import _check_protocol_registered
        assert _check_protocol_registered() is False


# ── Health Check Tests ───────────────────────────────────────────────────────


class TestHealth:
    """Test get_browseros_health() and BrowserOSHealth dataclass."""

    @patch("agents.core.native.browseros.discover_browseros")
    def test_health_not_installed(self, mock_discover):
        from agents.core.native.browseros import get_browseros_health, BrowserOSInfo
        mock_discover.return_value = BrowserOSInfo(installed=False)
        health = get_browseros_health()
        assert health.overall == "not_installed"
        assert not health.installed

    @patch("agents.core.native.browseros._check_mcp_reachable", return_value=True)
    @patch("os.path.isfile", return_value=True)
    @patch("agents.core.native.browseros.discover_browseros")
    def test_health_healthy(self, mock_discover, mock_isfile, mock_mcp):
        from agents.core.native.browseros import get_browseros_health, BrowserOSInfo
        mock_discover.return_value = BrowserOSInfo(
            installed=True,
            executable_path="C:\\chrome.exe",
            version="145",
            data_dir="C:\\data",
            soul_md_path="C:\\data\\SOUL.md",
            is_running=True,
        )
        health = get_browseros_health()
        assert health.overall == "healthy"
        assert health.running
        assert health.mcp_reachable

    @patch("agents.core.native.browseros._check_mcp_reachable", return_value=False)
    @patch("os.path.isfile", return_value=False)
    @patch("agents.core.native.browseros.discover_browseros")
    def test_health_stopped(self, mock_discover, mock_isfile, mock_mcp):
        from agents.core.native.browseros import get_browseros_health, BrowserOSInfo
        mock_discover.return_value = BrowserOSInfo(
            installed=True,
            executable_path="C:\\chrome.exe",
            is_running=False,
        )
        health = get_browseros_health()
        assert health.overall == "stopped"

    @patch("agents.core.native.browseros._check_mcp_reachable", return_value=False)
    @patch("os.path.isfile", return_value=False)
    @patch("agents.core.native.browseros.discover_browseros")
    def test_health_degraded_mcp_down(self, mock_discover, mock_isfile, mock_mcp):
        from agents.core.native.browseros import get_browseros_health, BrowserOSInfo
        mock_discover.return_value = BrowserOSInfo(
            installed=True,
            executable_path="C:\\chrome.exe",
            is_running=True,
        )
        health = get_browseros_health()
        assert health.overall == "degraded"
        assert "mcp" in health.details or "MCP" in str(health.details)


# ── MCP Reachability Tests ───────────────────────────────────────────────────


class TestMCP:
    """Test MCP server reachability probing."""

    @patch("urllib.request.urlopen")
    def test_mcp_reachable(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__enter__ = lambda s: mock_response
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        from agents.core.native.browseros import _check_mcp_reachable
        assert _check_mcp_reachable(12007) is True

    @patch("urllib.request.urlopen", side_effect=Exception("connection refused"))
    def test_mcp_unreachable(self, mock_urlopen):
        from agents.core.native.browseros import _check_mcp_reachable
        assert _check_mcp_reachable(12007) is False


# ── SOUL.md Tests ────────────────────────────────────────────────────────────


class TestSoulMd:
    """Test SOUL.md read/write/inject operations."""

    def test_inject_boundaries_new(self, soul_content):
        from agents.core.native.browseros import inject_sovereign_boundaries
        result = inject_sovereign_boundaries(soul_content)
        assert "## Sovereign OS Integration" in result
        assert "ALIGN governance ledger" in result
        assert "ACFS confinement" in result

    def test_inject_boundaries_idempotent(self, soul_content):
        from agents.core.native.browseros import inject_sovereign_boundaries
        first = inject_sovereign_boundaries(soul_content)
        second = inject_sovereign_boundaries(first)
        # Should not duplicate the section
        assert second.count("## Sovereign OS Integration") == 1

    @patch("agents.core.native.browseros._find_data_dirs")
    def test_read_soul_md_exists(self, mock_dirs, tmp_data_dir, soul_content):
        soul_path = tmp_data_dir / "SOUL.md"
        soul_path.write_text(soul_content, encoding="utf-8")
        mock_dirs.return_value = (str(tmp_data_dir), str(soul_path), "")

        from agents.core.native.browseros import read_soul_md
        content = read_soul_md()
        assert content is not None
        assert "helpful AI assistant" in content

    @patch("agents.core.native.browseros._find_data_dirs", return_value=("", "", ""))
    def test_read_soul_md_missing(self, mock_dirs):
        from agents.core.native.browseros import read_soul_md
        assert read_soul_md() is None

    @patch("agents.core.native.browseros._find_data_dirs")
    def test_write_soul_md(self, mock_dirs, tmp_data_dir):
        soul_path = tmp_data_dir / "SOUL.md"
        mock_dirs.return_value = (str(tmp_data_dir), str(soul_path), "")

        from agents.core.native.browseros import write_soul_md
        assert write_soul_md("# Test content") is True
        assert soul_path.read_text(encoding="utf-8") == "# Test content"


# ── Wizard Step Tests ────────────────────────────────────────────────────────


class TestWizardStep:
    """Test get_browseros_wizard_step() for all states."""

    @patch("agents.core.native.browseros.discover_browseros")
    def test_step_not_installed(self, mock_discover):
        from agents.core.native.browseros import get_browseros_wizard_step, BrowserOSInfo
        mock_discover.return_value = BrowserOSInfo(installed=False)
        step = get_browseros_wizard_step()
        assert step["priority"] == "optional"
        assert "Install" in step["title"]
        assert "category" in step
        assert step["category"] == "browser"

    @patch("agents.core.native.browseros.discover_browseros")
    def test_step_installed_not_default(self, mock_discover):
        from agents.core.native.browseros import get_browseros_wizard_step, BrowserOSInfo
        mock_discover.return_value = BrowserOSInfo(
            installed=True, version="0.41.0", is_default_browser=False,
        )
        step = get_browseros_wizard_step()
        assert step["priority"] == "optional"
        assert "Default" in step["title"] or "default" in step["description"]

    @patch("agents.core.native.browseros.discover_browseros")
    def test_step_installed_and_default(self, mock_discover):
        from agents.core.native.browseros import get_browseros_wizard_step, BrowserOSInfo
        mock_discover.return_value = BrowserOSInfo(
            installed=True, version="0.41.0", is_default_browser=True,
        )
        step = get_browseros_wizard_step()
        assert step["priority"] == "done"
        assert "✅" in step["title"]


# ── Default Browser Registration Tests ───────────────────────────────────────


class TestDefaultBrowser:
    """Test register/restore default browser."""

    @patch("agents.core.native.browseros.discover_browseros")
    def test_register_not_installed(self, mock_discover):
        from agents.core.native.browseros import register_as_default_browser, BrowserOSInfo
        mock_discover.return_value = BrowserOSInfo(installed=False)
        ok, msg = register_as_default_browser()
        assert not ok
        assert "not installed" in msg.lower()

    @patch("agents.core.native.browseros.os.path.isfile", return_value=False)
    def test_restore_no_backup(self, mock_isfile):
        from agents.core.native.browseros import restore_previous_browser
        ok, msg = restore_previous_browser()
        assert not ok
        assert "backup" in msg.lower()


# ── Launch Tests ─────────────────────────────────────────────────────────────


class TestLaunch:
    """Test launch_browseros()."""

    @patch("agents.core.native.browseros.discover_browseros")
    def test_launch_not_installed(self, mock_discover):
        from agents.core.native.browseros import launch_browseros, BrowserOSInfo
        mock_discover.return_value = BrowserOSInfo(installed=False)
        pid = launch_browseros()
        assert pid is None

    @patch("subprocess.Popen")
    @patch("agents.core.native.browseros.discover_browseros")
    def test_launch_success(self, mock_discover, mock_popen):
        from agents.core.native.browseros import launch_browseros, BrowserOSInfo
        mock_discover.return_value = BrowserOSInfo(
            installed=True, executable_path="C:\\chrome.exe",
        )
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_popen.return_value = mock_proc

        pid = launch_browseros(url="https://example.com")
        assert pid == 12345
        mock_popen.assert_called_once()

    @patch("subprocess.Popen", side_effect=OSError("not found"))
    @patch("agents.core.native.browseros.discover_browseros")
    def test_launch_os_error(self, mock_discover, mock_popen):
        from agents.core.native.browseros import launch_browseros, BrowserOSInfo
        mock_discover.return_value = BrowserOSInfo(
            installed=True, executable_path="C:\\chrome.exe",
        )
        pid = launch_browseros()
        assert pid is None


# ── Tool Registration Tests ──────────────────────────────────────────────────


class TestToolRegistration:
    """Test register_browseros_tools()."""

    @patch("agents.core.native._load_native_config",
           return_value={"features": {"browseros": True}})
    def test_registers_four_tools(self, mock_config):
        from agents.core.native.browseros import register_browseros_tools
        registry = MagicMock()
        register_browseros_tools(registry)
        assert registry.register.call_count == 4
        # Verify tool names
        names = [call.kwargs.get("name", call.args[0] if call.args else "")
                 for call in registry.register.call_args_list]
        # Fall back to keyword args
        if not any(names):
            names = [c[1]["name"] for c in registry.register.call_args_list]
        assert "browseros.status" in names
        assert "browseros.launch" in names
        assert "browseros.set_default" in names
        assert "browseros.restore_default" in names

    @patch("agents.core.native._load_native_config",
           return_value={"features": {"browseros": False}})
    def test_disabled_registers_none(self, mock_config):
        from agents.core.native.browseros import register_browseros_tools
        registry = MagicMock()
        register_browseros_tools(registry)
        assert registry.register.call_count == 0


# ── BrowserOSInfo Dataclass Tests ────────────────────────────────────────────


class TestDataclasses:
    """Test dataclass defaults and properties."""

    def test_browseros_info_defaults(self):
        from agents.core.native.browseros import BrowserOSInfo
        info = BrowserOSInfo()
        assert not info.installed
        assert info.mcp_port == 12007
        assert info.executable_path == ""

    def test_health_overall_not_installed(self):
        from agents.core.native.browseros import BrowserOSHealth
        h = BrowserOSHealth(installed=False)
        assert h.overall == "not_installed"

    def test_health_overall_healthy(self):
        from agents.core.native.browseros import BrowserOSHealth
        h = BrowserOSHealth(installed=True, running=True, mcp_reachable=True)
        assert h.overall == "healthy"

    def test_health_overall_stopped(self):
        from agents.core.native.browseros import BrowserOSHealth
        h = BrowserOSHealth(installed=True, running=False, mcp_reachable=False)
        assert h.overall == "stopped"

    def test_health_overall_degraded(self):
        from agents.core.native.browseros import BrowserOSHealth
        h = BrowserOSHealth(installed=True, running=True, mcp_reachable=False)
        assert h.overall == "degraded"
