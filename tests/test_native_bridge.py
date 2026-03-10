"""
Comprehensive test suite for Enterprise Native OS Bridge.

Covers:
  - Singleton pattern + thread safety
  - Structured error hierarchy
  - Token bucket rate limiter
  - Health check system
  - Feature flags
  - Command allowlist security
  - ACFS confinement
  - Platform detection
  - Dataclass immutability
  - Host function governance assertions
  - Tray menu construction
  - User tool validation
  - Dependency checking
"""

from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import asdict
from unittest.mock import MagicMock, patch

import pytest

from agents.core.native import PLATFORM, detect_platform, check_dependencies, reset_bridge
from agents.core.native.bridge import (
    ACFSViolationError,
    ClipboardContent,
    CommandDeniedError,
    HealthReport,
    NativeBridge,
    NativeBridgeError,
    NotificationRequest,
    NotificationUrgency,
    ProcessInfo,
    RateLimitExceededError,
    ShellResult,
    SubsystemStatus,
    SubsystemUnavailableError,
    SystemInfo,
    TokenBucketRateLimiter,
    TrayMenuItem,
)


# Reset singleton before each test module to avoid cross-contamination
@pytest.fixture(autouse=True)
def _clean_bridge():
    reset_bridge()
    yield
    reset_bridge()


# ── Platform Detection ───────────────────────────────────────────────────────


class TestPlatformDetection:
    def test_returns_valid_platform(self) -> None:
        assert detect_platform() in ("windows", "darwin", "linux")

    def test_constant_matches_detect(self) -> None:
        assert PLATFORM == detect_platform()

    def test_platform_not_empty(self) -> None:
        assert len(PLATFORM) > 0


# ── Structured Error Hierarchy ───────────────────────────────────────────────


class TestErrorHierarchy:
    def test_base_error(self) -> None:
        err = NativeBridgeError("test", subsystem="shell", recoverable=True)
        assert err.subsystem == "shell"
        assert err.recoverable is True
        assert "test" in str(err)

    def test_command_denied(self) -> None:
        err = CommandDeniedError("rm")
        assert err.command == "rm"
        assert err.subsystem == "shell"
        assert "allowlist" in str(err).lower()
        assert isinstance(err, NativeBridgeError)

    def test_subsystem_unavailable(self) -> None:
        err = SubsystemUnavailableError("clipboard", "pyperclip not installed")
        assert err.subsystem == "clipboard"
        assert err.recoverable is False

    def test_rate_limit_exceeded(self) -> None:
        err = RateLimitExceededError(10, 60.0)
        assert err.limit == 10
        assert err.window_seconds == 60.0
        assert err.recoverable is True

    def test_acfs_violation(self) -> None:
        err = ACFSViolationError("/tmp/evil", "/home/user/project")
        assert err.path == "/tmp/evil"
        assert err.base_dir == "/home/user/project"
        assert "ACFS" in str(err)

    def test_all_errors_inherit_from_base(self) -> None:
        for err_cls in [CommandDeniedError, SubsystemUnavailableError,
                        RateLimitExceededError, ACFSViolationError]:
            assert issubclass(err_cls, NativeBridgeError)


# ── Token Bucket Rate Limiter ────────────────────────────────────────────────


class TestRateLimiter:
    def test_basic_consume(self) -> None:
        rl = TokenBucketRateLimiter(max_tokens=5, refill_seconds=60.0)
        for _ in range(5):
            assert rl.try_consume() is True
        assert rl.try_consume() is False

    def test_refill_over_time(self) -> None:
        rl = TokenBucketRateLimiter(max_tokens=2, refill_seconds=0.1)
        assert rl.try_consume() is True
        assert rl.try_consume() is True
        assert rl.try_consume() is False
        time.sleep(0.15)  # Wait for refill
        assert rl.try_consume() is True

    def test_remaining_property(self) -> None:
        rl = TokenBucketRateLimiter(max_tokens=10, refill_seconds=60.0)
        assert rl.remaining == 10
        rl.try_consume(3)
        assert rl.remaining == 7

    def test_thread_safety(self) -> None:
        rl = TokenBucketRateLimiter(max_tokens=100, refill_seconds=600.0)
        consumed = []

        def _consume():
            for _ in range(20):
                if rl.try_consume():
                    consumed.append(1)

        threads = [threading.Thread(target=_consume) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 200 attempts, 100 max → should be exactly 100
        assert len(consumed) == 100


# ── Dataclasses ──────────────────────────────────────────────────────────────


class TestDataclasses:
    def test_system_info_frozen(self) -> None:
        info = SystemInfo(
            platform="linux", platform_version="6.1.0", hostname="h",
            cpu_count=4, cpu_percent=0.0, memory_total_mb=0,
            memory_available_mb=0, disk_total_gb=0.0, disk_free_gb=0.0,
            python_version="3.12", uptime_seconds=0.0,
        )
        with pytest.raises(AttributeError):
            info.platform = "darwin"  # type: ignore[misc]

    def test_process_info(self) -> None:
        p = ProcessInfo(pid=1234, name="python", status="running", cpu_percent=5.0, memory_mb=100.0)
        assert p.pid == 1234

    def test_clipboard_content(self) -> None:
        c = ClipboardContent(text="test")
        assert c.format == "text/plain"

    def test_notification_defaults(self) -> None:
        n = NotificationRequest(title="T", body="B")
        assert n.urgency == NotificationUrgency.NORMAL
        assert n.timeout_ms == 5000

    def test_shell_result(self) -> None:
        r = ShellResult(command="echo", exit_code=0, stdout="hi", stderr="", duration_ms=1.0)
        assert r.timed_out is False

    def test_tray_nested(self) -> None:
        child = TrayMenuItem(label="Sub", action="sub")
        parent = TrayMenuItem(label="P", action="p", children=[child])
        assert len(parent.children) == 1

    def test_health_report_overall(self) -> None:
        healthy = HealthReport(
            platform="windows",
            clipboard=SubsystemStatus.HEALTHY,
            notifications=SubsystemStatus.HEALTHY,
            shell=SubsystemStatus.HEALTHY,
            tray=SubsystemStatus.HEALTHY,
            sysinfo=SubsystemStatus.HEALTHY,
        )
        assert healthy.overall == SubsystemStatus.HEALTHY

        degraded = HealthReport(
            platform="linux",
            clipboard=SubsystemStatus.UNAVAILABLE,
            notifications=SubsystemStatus.HEALTHY,
            shell=SubsystemStatus.HEALTHY,
            tray=SubsystemStatus.HEALTHY,
            sysinfo=SubsystemStatus.HEALTHY,
        )
        assert degraded.overall == SubsystemStatus.DEGRADED


# ── Command Allowlist Security ───────────────────────────────────────────────


class TestAllowlist:
    def test_safe_commands_allowed(self) -> None:
        for cmd in ["git", "python", "npm", "echo", "docker"]:
            assert cmd in NativeBridge._BASE_COMMAND_ALLOWLIST

    def test_dangerous_commands_blocked(self) -> None:
        for cmd in ["rm", "format", "shutdown", "del", "mkfs", "dd"]:
            assert cmd not in NativeBridge._BASE_COMMAND_ALLOWLIST

    def test_is_frozenset(self) -> None:
        assert isinstance(NativeBridge._BASE_COMMAND_ALLOWLIST, frozenset)

    def test_is_command_allowed_method(self) -> None:
        from agents.core.native import get_bridge
        bridge = get_bridge()
        assert bridge.is_command_allowed("git") is True
        assert bridge.is_command_allowed("rm") is False

    def test_strips_extension(self) -> None:
        from agents.core.native import get_bridge
        bridge = get_bridge()
        assert bridge.is_command_allowed("python.exe") is True
        assert bridge.is_command_allowed("git.cmd") is True

    def test_strips_path(self) -> None:
        from agents.core.native import get_bridge
        bridge = get_bridge()
        assert bridge.is_command_allowed("/usr/bin/python") is True
        assert bridge.is_command_allowed("C:\\Python312\\python.exe") is True


# ── Shell Security ───────────────────────────────────────────────────────────


class TestShellSecurity:
    def test_denied_command_raises_structured_error(self) -> None:
        from agents.core.native import get_bridge
        bridge = get_bridge()
        with pytest.raises(CommandDeniedError) as exc_info:
            bridge.shell_exec("rm", ["-rf", "/"])
        assert exc_info.value.command == "rm"
        assert exc_info.value.subsystem == "shell"

    def test_allowed_command_succeeds(self) -> None:
        from agents.core.native import get_bridge
        bridge = get_bridge()
        result = bridge.shell_exec("python", ["-c", "print('hello')"], timeout_seconds=10.0)
        assert result.exit_code == 0
        assert "hello" in result.stdout

    def test_timeout_returns_timed_out_result(self) -> None:
        from agents.core.native import get_bridge
        bridge = get_bridge()
        if sys.platform == "win32":
            result = bridge.shell_exec("ping", ["localhost", "-n", "100"], timeout_seconds=0.5)
        else:
            result = bridge.shell_exec("ping", ["localhost", "-c", "100"], timeout_seconds=0.5)
        assert result.timed_out is True

    def test_acfs_confinement(self) -> None:
        from agents.core.native import get_bridge
        bridge = get_bridge()
        with pytest.raises(ACFSViolationError):
            bridge.shell_exec("echo", ["test"], cwd="/tmp/evil_path")


# ── Singleton Pattern ────────────────────────────────────────────────────────


class TestSingleton:
    def test_same_instance_returned(self) -> None:
        from agents.core.native import get_bridge
        b1 = get_bridge()
        b2 = get_bridge()
        assert b1 is b2

    def test_reset_creates_new_instance(self) -> None:
        from agents.core.native import get_bridge, reset_bridge
        b1 = get_bridge()
        reset_bridge()
        b2 = get_bridge()
        assert b1 is not b2

    def test_thread_safe_creation(self) -> None:
        from agents.core.native import get_bridge, reset_bridge
        reset_bridge()
        instances = []

        def _get():
            instances.append(id(get_bridge()))

        threads = [threading.Thread(target=_get) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads should get the same instance
        assert len(set(instances)) == 1


# ── Bridge Integration ───────────────────────────────────────────────────────


class TestBridgeIntegration:
    def test_has_all_methods(self) -> None:
        from agents.core.native import get_bridge
        bridge = get_bridge()
        for method in ["system_info", "list_processes", "clipboard_read",
                       "clipboard_write", "notify", "shell_exec",
                       "open_file", "open_url", "launch_app",
                       "tray_available", "health", "is_command_allowed",
                       "check_rate_limit"]:
            assert hasattr(bridge, method), f"Missing method: {method}"

    def test_system_info_returns_typed(self) -> None:
        from agents.core.native import get_bridge
        bridge = get_bridge()
        info = bridge.system_info()
        assert isinstance(info, SystemInfo)
        assert info.cpu_count >= 1

    def test_system_info_serializable(self) -> None:
        from agents.core.native import get_bridge
        bridge = get_bridge()
        info = bridge.system_info()
        j = json.dumps(asdict(info))
        assert "platform" in j

    def test_health_returns_report(self) -> None:
        from agents.core.native import get_bridge
        bridge = get_bridge()
        report = bridge.health()
        assert isinstance(report, HealthReport)
        assert report.overall in (SubsystemStatus.HEALTHY, SubsystemStatus.DEGRADED)


# ── Governance Assertions ────────────────────────────────────────────────────


class TestGovernance:
    def _load_hf(self):
        from pathlib import Path
        path = Path(__file__).parent.parent / "governance" / "host_functions.json"
        return json.loads(path.read_text())

    def test_version_1_2_0(self) -> None:
        assert self._load_hf()["version"] == "1.2.0"

    def test_7_native_functions(self) -> None:
        fns = [f for f in self._load_hf()["functions"] if f["backend"] == "native_bridge"]
        assert len(fns) == 7

    def test_shell_exec_restricted_to_forge(self) -> None:
        fns = {f["name"]: f for f in self._load_hf()["functions"]}
        assert "hearth" not in fns["SHELL_EXEC"]["tier"]
        assert "forge" in fns["SHELL_EXEC"]["tier"]

    def test_clipboard_is_sensitive(self) -> None:
        fns = {f["name"]: f for f in self._load_hf()["functions"]}
        assert fns["CLIPBOARD_READ"]["sensitive"] is True

    def test_sys_info_available_to_hearth(self) -> None:
        fns = {f["name"]: f for f in self._load_hf()["functions"]}
        assert "hearth" in fns["SYS_INFO"]["tier"]

    def test_notify_low_gas(self) -> None:
        fns = {f["name"]: f for f in self._load_hf()["functions"]}
        assert fns["NOTIFY"]["gas"] == 1


# ── Tray Menu ────────────────────────────────────────────────────────────────


class TestTrayMenu:
    def test_default_menu_items(self) -> None:
        from agents.core.native.tray import SovereignTray
        tray = SovereignTray()
        menu = tray._create_default_menu()
        labels = [item.label for item in menu]
        assert any("Sovereign" in l for l in labels)
        assert "Quit" in labels
        assert "System Info" in labels

    def test_callback_registration(self) -> None:
        from agents.core.native.tray import SovereignTray
        tray = SovereignTray()
        called = []
        tray.register_callback("test", lambda: called.append(1))
        tray._callbacks["test"]()
        assert called == [1]

    def test_submenus(self) -> None:
        from agents.core.native.tray import SovereignTray
        tray = SovereignTray()
        menu = tray._create_default_menu()
        agents = next(i for i in menu if "Agents" in i.label)
        security = next(i for i in menu if "Security" in i.label)
        assert len(agents.children) > 0
        assert len(security.children) > 0


# ── User Tool Validation ────────────────────────────────────────────────────


class TestUserToolValidation:
    def test_validates_missing_name(self) -> None:
        from agents.core.native.user_tools import _validate_tool_entry
        errors = _validate_tool_entry({}, 0)
        assert any("name" in e.lower() for e in errors)

    def test_validates_reserved_prefix(self) -> None:
        from agents.core.native.user_tools import _validate_tool_entry
        errors = _validate_tool_entry({"name": "native.exploit", "description": "x", "transport": "http", "url": "http://x"}, 0)
        assert any("reserved" in e.lower() for e in errors)

    def test_validates_transport(self) -> None:
        from agents.core.native.user_tools import _validate_tool_entry
        errors = _validate_tool_entry({"name": "ok", "description": "x", "transport": "magic"}, 0)
        assert any("transport" in e.lower() for e in errors)

    def test_validates_missing_url_for_http(self) -> None:
        from agents.core.native.user_tools import _validate_tool_entry
        errors = _validate_tool_entry({"name": "ok", "description": "x", "transport": "http"}, 0)
        assert any("url" in e.lower() for e in errors)

    def test_valid_entry_passes(self) -> None:
        from agents.core.native.user_tools import _validate_tool_entry
        errors = _validate_tool_entry({
            "name": "my_tool", "description": "test tool",
            "transport": "http", "url": "http://localhost:8080",
        }, 0)
        assert len(errors) == 0


# ── Dependency Checking ─────────────────────────────────────────────────────


class TestDependencies:
    def test_check_dependencies_returns_dict(self) -> None:
        deps = check_dependencies()
        assert isinstance(deps, dict)
        assert "psutil" in deps
        assert "pyperclip" in deps
        assert all(isinstance(v, bool) for v in deps.values())

    def test_install_instructions_returns_string(self) -> None:
        from agents.core.native import install_instructions
        result = install_instructions()
        assert isinstance(result, str)


# ── Config Loading ───────────────────────────────────────────────────────────


class TestConfig:
    def test_load_config_returns_defaults(self) -> None:
        from agents.core.native import _load_native_config
        config = _load_native_config()
        assert config.get("enabled") is True
        assert "features" in config
        assert "shell" in config

    def test_config_has_feature_flags(self) -> None:
        from agents.core.native import _load_native_config
        config = _load_native_config()
        features = config["features"]
        assert features.get("clipboard") is True
        assert features.get("shell") is True

    def test_settings_json_has_native_section(self) -> None:
        from pathlib import Path
        settings = Path(__file__).parent.parent / "config" / "settings.json"
        data = json.loads(settings.read_text())
        assert "native" in data
        assert data["native"]["features"]["clipboard"] is True
