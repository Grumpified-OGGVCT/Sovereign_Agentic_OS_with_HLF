"""
Shell — Enterprise-grade governed subprocess execution.

Security layers (all O(1), zero runtime overhead):
  1. Command allowlist: frozenset membership check
  2. Rate limiter: token bucket in NativeBridge
  3. ACFS confinement: Path.is_relative_to() on cwd
  4. Output capping: stdout/stderr truncation
  5. Timeout: subprocess.run() timeout parameter
  6. Audit: ALSLogger for every invocation

All layers use structured error types from bridge.py.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from agents.core.logger import ALSLogger
from agents.core.native.bridge import (
    ACFSViolationError,
    CommandDeniedError,
    NativeBridge,
    RateLimitExceededError,
    ShellResult,
)

_logger = ALSLogger(agent_role="native-shell", goal_id="execution")


def shell_exec(
    command: str,
    args: list[str] | None = None,
    timeout_seconds: float = 30.0,
    cwd: str | None = None,
    bridge: NativeBridge | None = None,
    max_stdout: int = 10_000,
    max_stderr: int = 5_000,
) -> ShellResult:
    """Execute a governed shell command with full security enforcement.

    Security enforcement order (fail-fast):
      1. Allowlist check    → CommandDeniedError
      2. Rate limit check   → RateLimitExceededError
      3. ACFS confinement   → ACFSViolationError
      4. Timeout + cap      → ShellResult with timed_out=True

    Args:
        command: Base command (e.g., 'git', 'python').
        args: Command arguments.
        timeout_seconds: Max execution time.
        cwd: Working directory (ACFS-validated).
        bridge: NativeBridge for allowlist/rate-limit. If None, uses base allowlist.
        max_stdout: Max stdout bytes to return.
        max_stderr: Max stderr bytes to return.

    Returns:
        ShellResult with exit code, stdout, stderr, duration.

    Raises:
        CommandDeniedError: Command not in allowlist.
        RateLimitExceededError: Too many commands in time window.
        ACFSViolationError: Working directory escapes confinement.
    """
    full_args = [command] + (args or [])
    full_command = " ".join(full_args)

    # ── 1. Allowlist Check (O(1)) ────────────────────────────────────────
    if bridge and not bridge.is_command_allowed(command):
        _logger.log(
            "SHELL_DENIED",
            {"command": command, "reason": "not_in_allowlist"},
            anomaly_score=0.6,
        )
        raise CommandDeniedError(command, list(sorted(bridge._BASE_COMMAND_ALLOWLIST))[:10])
    elif bridge is None:
        base = command.split("/")[-1].split("\\")[-1].lower()
        for suffix in (".exe", ".cmd", ".bat", ".ps1"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        if base not in NativeBridge._BASE_COMMAND_ALLOWLIST:
            _logger.log(
                "SHELL_DENIED",
                {"command": command, "reason": "not_in_allowlist"},
                anomaly_score=0.6,
            )
            raise CommandDeniedError(command)

    # ── 2. Rate Limit Check (O(1)) ───────────────────────────────────────
    if bridge:
        bridge.check_rate_limit()

    # ── 3. ACFS Confinement ──────────────────────────────────────────────
    if cwd:
        cwd_path = Path(cwd).resolve()
        base_dir = Path(os.environ.get("BASE_DIR", os.getcwd())).resolve()
        if not cwd_path.is_relative_to(base_dir):
            _logger.log(
                "SHELL_ACFS_VIOLATION",
                {"cwd": cwd, "base_dir": str(base_dir)},
                anomaly_score=0.8,
            )
            raise ACFSViolationError(cwd, str(base_dir))

    # ── 4. Execute with Timeout + Output Cap ─────────────────────────────
    _logger.log("SHELL_EXEC", {"command": full_command[:200], "timeout": timeout_seconds})

    start = time.perf_counter()

    try:
        result = subprocess.run(
            full_args,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=cwd,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        duration_ms = (time.perf_counter() - start) * 1000

        shell_result = ShellResult(
            command=full_command,
            exit_code=result.returncode,
            stdout=result.stdout[:max_stdout],
            stderr=result.stderr[:max_stderr],
            duration_ms=round(duration_ms, 1),
            timed_out=False,
        )

        _logger.log(
            "SHELL_COMPLETE",
            {"command": full_command[:100], "exit_code": result.returncode, "duration_ms": round(duration_ms, 1)},
        )
        return shell_result

    except subprocess.TimeoutExpired:
        duration_ms = (time.perf_counter() - start) * 1000
        _logger.log(
            "SHELL_TIMEOUT",
            {"command": full_command[:100], "timeout": timeout_seconds},
            anomaly_score=0.3,
        )
        return ShellResult(
            command=full_command,
            exit_code=-1,
            stdout="",
            stderr=f"Command timed out after {timeout_seconds}s",
            duration_ms=round(duration_ms, 1),
            timed_out=True,
        )

    except FileNotFoundError:
        duration_ms = (time.perf_counter() - start) * 1000
        _logger.log(
            "SHELL_NOT_FOUND",
            {"command": command},
            anomaly_score=0.2,
        )
        return ShellResult(
            command=full_command,
            exit_code=-1,
            stdout="",
            stderr=f"Command not found: {command}",
            duration_ms=round(duration_ms, 1),
            timed_out=False,
        )

    except Exception as exc:
        duration_ms = (time.perf_counter() - start) * 1000
        _logger.log(
            "SHELL_ERROR",
            {"command": full_command[:100], "error": str(exc)[:120]},
            anomaly_score=0.5,
        )
        return ShellResult(
            command=full_command,
            exit_code=-1,
            stdout="",
            stderr=f"Execution error: {exc}",
            duration_ms=round(duration_ms, 1),
            timed_out=False,
        )
