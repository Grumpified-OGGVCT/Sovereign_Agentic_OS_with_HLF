"""
NativeBridge — Enterprise-grade abstract base for platform-specific OS interactions.

Architecture:
  - Singleton per platform (thread-safe via `_lock`)
  - Health monitoring per subsystem (clipboard, notifications, shell, tray)
  - Token-bucket rate limiter for shell commands
  - Structured error hierarchy (NativeBridgeError → subtypes)
  - ALSLogger integration for full audit trail
  - Feature flags via config/settings.json

Security model (lightweight, zero runtime overhead):
  - O(1) frozenset command allowlist
  - Tier enforcement in dispatcher (reuses existing)
  - ACFS path confinement (reuses existing)
  - Gas metering in HLF runtime (reuses existing)
  - Rate limiting: token bucket (O(1) per call)
"""

from __future__ import annotations

import abc
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Structured Error Hierarchy ───────────────────────────────────────────────


class NativeBridgeError(Exception):
    """Base exception for all native bridge operations."""

    def __init__(self, message: str, subsystem: str = "unknown", recoverable: bool = True) -> None:
        self.subsystem = subsystem
        self.recoverable = recoverable
        super().__init__(message)


class CommandDeniedError(NativeBridgeError):
    """Raised when a shell command is not in the allowlist."""

    def __init__(self, command: str, allowlist_sample: list[str] | None = None) -> None:
        self.command = command
        self.allowlist_sample = allowlist_sample or []
        super().__init__(
            f"Command '{command}' denied — not in Sovereign OS allowlist",
            subsystem="shell",
            recoverable=True,
        )


class SubsystemUnavailableError(NativeBridgeError):
    """Raised when a required subsystem (clipboard, tray, etc.) is not available."""

    def __init__(self, subsystem: str, reason: str = "") -> None:
        msg = f"Subsystem '{subsystem}' unavailable"
        if reason:
            msg += f": {reason}"
        super().__init__(msg, subsystem=subsystem, recoverable=False)


class RateLimitExceededError(NativeBridgeError):
    """Raised when shell command rate limit is exceeded."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        super().__init__(
            f"Rate limit exceeded: max {limit} commands per {window_seconds}s",
            subsystem="shell",
            recoverable=True,
        )


class ACFSViolationError(NativeBridgeError):
    """Raised when a path escapes ACFS confinement."""

    def __init__(self, path: str, base_dir: str) -> None:
        self.path = path
        self.base_dir = base_dir
        super().__init__(
            f"ACFS violation: '{path}' escapes confinement (base: {base_dir})",
            subsystem="filesystem",
            recoverable=True,
        )


# ── Enums & Data Structures ─────────────────────────────────────────────────


class NotificationUrgency(Enum):
    LOW = "low"
    NORMAL = "normal"
    CRITICAL = "critical"


class SubsystemStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNCHECKED = "unchecked"


@dataclass(frozen=True)
class HealthReport:
    """Health status of all native subsystems."""

    platform: str
    clipboard: SubsystemStatus
    notifications: SubsystemStatus
    shell: SubsystemStatus
    tray: SubsystemStatus
    sysinfo: SubsystemStatus
    details: dict[str, str] = field(default_factory=dict)

    @property
    def overall(self) -> SubsystemStatus:
        statuses = [self.clipboard, self.notifications, self.shell, self.tray, self.sysinfo]
        if all(s == SubsystemStatus.HEALTHY for s in statuses):
            return SubsystemStatus.HEALTHY
        if any(s == SubsystemStatus.UNAVAILABLE for s in statuses):
            return SubsystemStatus.DEGRADED
        return SubsystemStatus.DEGRADED


@dataclass(frozen=True)
class SystemInfo:
    """Snapshot of host system information."""

    platform: str
    platform_version: str
    hostname: str
    cpu_count: int
    cpu_percent: float
    memory_total_mb: int
    memory_available_mb: int
    disk_total_gb: float
    disk_free_gb: float
    python_version: str
    uptime_seconds: float
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProcessInfo:
    """Snapshot of a running process."""

    pid: int
    name: str
    status: str
    cpu_percent: float
    memory_mb: float
    cmdline: str = ""


@dataclass(frozen=True)
class ClipboardContent:
    """Clipboard payload."""

    text: str
    format: str = "text/plain"


@dataclass(frozen=True)
class NotificationRequest:
    """Desktop notification parameters."""

    title: str
    body: str
    urgency: NotificationUrgency = NotificationUrgency.NORMAL
    icon_path: str | None = None
    timeout_ms: int = 5000
    actions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ShellResult:
    """Result of a governed shell command execution."""

    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool = False


@dataclass(frozen=True)
class TrayMenuItem:
    """A single item in the system tray context menu."""

    label: str
    action: str
    enabled: bool = True
    separator_before: bool = False
    children: list[TrayMenuItem] = field(default_factory=list)


# ── Rate Limiter (Token Bucket) ─────────────────────────────────────────────


class TokenBucketRateLimiter:
    """Thread-safe token bucket rate limiter.

    Zero overhead when not exceeded — single float comparison per call.
    """

    def __init__(self, max_tokens: int = 10, refill_seconds: float = 60.0) -> None:
        self.max_tokens = max_tokens
        self.refill_seconds = refill_seconds
        self._tokens = float(max_tokens)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def try_consume(self, tokens: int = 1) -> bool:
        """Attempt to consume tokens. Returns True if allowed."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            # Refill tokens proportionally
            self._tokens = min(
                self.max_tokens,
                self._tokens + (elapsed * self.max_tokens / self.refill_seconds),
            )
            self._last_refill = now

            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    @property
    def remaining(self) -> int:
        """Approximate remaining tokens (non-locking read)."""
        return max(0, int(self._tokens))


# ── Abstract Bridge ─────────────────────────────────────────────────────────


class NativeBridge(abc.ABC):
    """Abstract interface for platform-specific native OS operations.

    Singleton pattern: one instance per platform, created via get_bridge().
    Thread-safe via _lock.

    Implementations must override all abstract methods.
    """

    _instance: NativeBridge | None = None
    _lock: threading.Lock = threading.Lock()

    # Rate limiter for shell commands — shared across all bridges
    _shell_rate_limiter: TokenBucketRateLimiter = TokenBucketRateLimiter(
        max_tokens=10, refill_seconds=60.0
    )

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Reset singleton when subclassed (for testing isolation)."""
        super().__init_subclass__(**kwargs)
        cls._instance = None

    # ── Health Checks ────────────────────────────────────────────────────

    def health(self) -> HealthReport:
        """Run lightweight health checks on all subsystems.

        Each check is designed to be non-destructive and fast (<100ms total).
        """
        from agents.core.native import detect_platform

        details: dict[str, str] = {}

        # Clipboard
        try:
            self.clipboard_read()
            clipboard_status = SubsystemStatus.HEALTHY
        except Exception as e:
            clipboard_status = SubsystemStatus.DEGRADED
            details["clipboard"] = str(e)[:100]

        # Notifications
        try:
            from agents.core.native.notifications import _HAS_DESKTOP_NOTIFIER
            notif_status = SubsystemStatus.HEALTHY if _HAS_DESKTOP_NOTIFIER else SubsystemStatus.DEGRADED
            if not _HAS_DESKTOP_NOTIFIER:
                details["notifications"] = "desktop-notifier not installed, using fallback"
        except Exception as e:
            notif_status = SubsystemStatus.DEGRADED
            details["notifications"] = str(e)[:100]

        # Shell
        shell_status = SubsystemStatus.HEALTHY
        details["shell_rate_limit"] = f"{self._shell_rate_limiter.remaining}/{self._shell_rate_limiter.max_tokens} tokens"

        # Tray
        try:
            tray_ok = self.tray_available()
            tray_status = SubsystemStatus.HEALTHY if tray_ok else SubsystemStatus.UNAVAILABLE
            if not tray_ok:
                details["tray"] = "pystray/rumps not installed"
        except Exception as e:
            tray_status = SubsystemStatus.UNAVAILABLE
            details["tray"] = str(e)[:100]

        # Sysinfo
        try:
            from agents.core.native.sysinfo import _HAS_PSUTIL
            sysinfo_status = SubsystemStatus.HEALTHY if _HAS_PSUTIL else SubsystemStatus.DEGRADED
            if not _HAS_PSUTIL:
                details["sysinfo"] = "psutil not installed, limited info"
        except Exception as e:
            sysinfo_status = SubsystemStatus.DEGRADED
            details["sysinfo"] = str(e)[:100]

        return HealthReport(
            platform=detect_platform(),
            clipboard=clipboard_status,
            notifications=notif_status,
            shell=shell_status,
            tray=tray_status,
            sysinfo=sysinfo_status,
            details=details,
        )

    # ── System Info ──────────────────────────────────────────────────────

    @abc.abstractmethod
    def system_info(self) -> SystemInfo:
        """Gather current system information."""

    @abc.abstractmethod
    def list_processes(self, filter_name: str | None = None) -> list[ProcessInfo]:
        """List running processes, optionally filtered by name."""

    # ── Clipboard ────────────────────────────────────────────────────────

    @abc.abstractmethod
    def clipboard_read(self) -> ClipboardContent:
        """Read current clipboard contents."""

    @abc.abstractmethod
    def clipboard_write(self, text: str) -> bool:
        """Write text to the system clipboard."""

    # ── Notifications ────────────────────────────────────────────────────

    @abc.abstractmethod
    def notify(self, request: NotificationRequest) -> bool:
        """Send a desktop notification."""

    # ── Shell Execution ──────────────────────────────────────────────────

    @abc.abstractmethod
    def shell_exec(
        self,
        command: str,
        args: list[str] | None = None,
        timeout_seconds: float = 30.0,
        cwd: str | None = None,
    ) -> ShellResult:
        """Execute a governed shell command.

        Enforces:
          - Command allowlist (O(1) frozenset check)
          - Rate limit (token bucket)
          - ACFS confinement on cwd
          - Output capping (stdout 10KB, stderr 5KB)
          - Timeout

        Raises:
          CommandDeniedError: command not in allowlist
          RateLimitExceededError: too many commands in window
          ACFSViolationError: cwd escapes confinement
        """

    # ── Application Launching ────────────────────────────────────────────

    @abc.abstractmethod
    def open_file(self, path: str) -> bool:
        """Open a file with the system's default application."""

    @abc.abstractmethod
    def open_url(self, url: str) -> bool:
        """Open a URL in the default browser."""

    @abc.abstractmethod
    def launch_app(self, app_name: str, args: list[str] | None = None) -> int | None:
        """Launch a native application. Returns PID or None on failure."""

    # ── System Tray ──────────────────────────────────────────────────────

    @abc.abstractmethod
    def tray_available(self) -> bool:
        """Check if the system tray is available on this platform."""

    # ── Command Allowlist ────────────────────────────────────────────────

    _BASE_COMMAND_ALLOWLIST: frozenset[str] = frozenset({
        # Safe read-only / diagnostic commands
        "echo", "cat", "head", "tail", "wc", "grep", "find", "ls", "dir",
        "whoami", "hostname", "date", "uptime", "df", "du", "free",
        "env", "printenv", "which", "where", "type",
        # Version control
        "git", "gh",
        # Python toolchain
        "python", "python3", "pip", "pip3", "pytest", "mypy", "ruff",
        "black", "isort", "pylint", "flake8",
        # Node toolchain
        "node", "npm", "npx", "yarn", "pnpm",
        # Build tools
        "make", "cmake", "cargo", "go", "rustc", "gcc", "g++",
        # Package managers
        "apt", "brew", "winget", "choco", "pacman", "dnf", "yum",
        # Containers
        "docker", "podman", "kubectl",
        # Network diagnostics
        "ping", "curl", "wget", "nslookup", "dig", "traceroute",
        # Sovereign OS tools
        "hlfc", "hlffmt", "hlflint", "hlfrun",
    })

    def is_command_allowed(self, command: str) -> bool:
        """Check if a command is in the allowlist. O(1) frozenset lookup."""
        base = command.split()[0].split("/")[-1].split("\\")[-1] if command else ""
        for suffix in (".exe", ".cmd", ".bat", ".ps1"):
            if base.lower().endswith(suffix):
                base = base[: -len(suffix)]
                break
        return base.lower() in self._BASE_COMMAND_ALLOWLIST

    def check_rate_limit(self) -> None:
        """Check shell rate limit. Raises RateLimitExceededError if exceeded."""
        if not self._shell_rate_limiter.try_consume():
            raise RateLimitExceededError(
                limit=self._shell_rate_limiter.max_tokens,
                window_seconds=self._shell_rate_limiter.refill_seconds,
            )
