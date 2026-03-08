"""
Agent Sandbox — Sandboxed Execution Environment for Agents.

Provides a scoped, audited runtime for agents to interact with the
filesystem, terminal, and external services during Spindle DAG execution.

Security model:
  - Agents are scoped to their ACFS worktree (path traversal blocked)
  - All invocations logged to ALIGN ledger
  - Timeouts enforced on all commands
  - Permission checks via ToolRegistry

Usage::

    from agents.core.tool_registry import ToolRegistry
    from agents.core.agent_sandbox import AgentSandbox

    registry = ToolRegistry()
    registry.register_builtins()

    sandbox = AgentSandbox(
        agent_id="sentinel-01",
        agent_role="sentinel",
        worktree_path="/path/to/worktree",
        tool_registry=registry,
    )

    result = sandbox.read_file("src/main.py")
    result = sandbox.run_command("pytest tests/ -v")
    result = sandbox.write_file("src/fix.py", "print('fixed')")
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents.core.tool_registry import (
    ToolCategory,
    ToolDefinition,
    ToolPermission,
    ToolRegistry,
    ToolResult,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Command Result (terminal execution)
# --------------------------------------------------------------------------- #


@dataclass
class CommandResult:
    """Result of a terminal command execution."""
    exit_code: int
    stdout: str
    stderr: str
    duration: float = 0.0
    timed_out: bool = False
    command: str = ""


# --------------------------------------------------------------------------- #
# Agent Sandbox
# --------------------------------------------------------------------------- #


class AgentSandbox:
    """Sandboxed execution environment for a single agent.

    Scopes all file operations to the agent's worktree directory
    and enforces permission checks via the ToolRegistry.

    Attributes:
        agent_id: Unique identifier for this agent instance.
        agent_role: Persona role (for permission lookups).
        worktree_path: Root directory the agent can access.
        tool_registry: Registry for permission checks and tool lookup.
    """

    def __init__(
        self,
        agent_id: str,
        agent_role: str,
        worktree_path: str | Path,
        tool_registry: ToolRegistry,
        default_timeout: float = 30.0,
    ) -> None:
        self.agent_id = agent_id
        self.agent_role = agent_role
        self.worktree_path = Path(worktree_path).resolve()
        self.tool_registry = tool_registry
        self.default_timeout = default_timeout
        self._action_log: list[dict[str, Any]] = []

        # Register built-in tools scoped to this sandbox
        self._register_sandbox_tools()

    # ------------------------------------------------------------------ #
    # Path Security
    # ------------------------------------------------------------------ #

    def _resolve_safe_path(self, relative_path: str) -> Path:
        """Resolve a path ensuring it stays within the worktree.

        Raises:
            PermissionError: If the resolved path escapes the worktree.
        """
        # Normalize and resolve
        target = (self.worktree_path / relative_path).resolve()

        # Check containment
        try:
            target.relative_to(self.worktree_path)
        except ValueError as exc:
            raise PermissionError(
                f"Path traversal blocked: '{relative_path}' resolves to "
                f"'{target}' outside worktree '{self.worktree_path}'"
            ) from exc

        return target

    # ------------------------------------------------------------------ #
    # File Operations
    # ------------------------------------------------------------------ #

    def read_file(self, path: str) -> ToolResult:
        """Read a file within the worktree.

        Args:
            path: Relative path within the worktree.
        """
        start = time.time()
        try:
            safe_path = self._resolve_safe_path(path)
            if not safe_path.exists():
                return ToolResult(
                    success=False,
                    error=f"File not found: {path}",
                    tool_id="file.read",
                    duration=time.time() - start,
                )
            content = safe_path.read_text(encoding="utf-8")
            self._log_action("file.read", {"path": path, "size": len(content)})
            return ToolResult(
                success=True,
                output=content,
                tool_id="file.read",
                duration=time.time() - start,
                metadata={"path": str(safe_path), "size": len(content)},
            )
        except PermissionError as e:
            return ToolResult(
                success=False, error=str(e),
                tool_id="file.read", duration=time.time() - start,
            )
        except Exception as e:
            return ToolResult(
                success=False, error=f"Read failed: {e}",
                tool_id="file.read", duration=time.time() - start,
            )

    def write_file(self, path: str, content: str) -> ToolResult:
        """Write content to a file within the worktree.

        Creates parent directories if needed.

        Args:
            path: Relative path within the worktree.
            content: Content to write.
        """
        start = time.time()
        try:
            safe_path = self._resolve_safe_path(path)
            safe_path.parent.mkdir(parents=True, exist_ok=True)
            safe_path.write_text(content, encoding="utf-8")
            self._log_action("file.write", {"path": path, "size": len(content)})
            return ToolResult(
                success=True,
                output=f"Written {len(content)} bytes to {path}",
                tool_id="file.write",
                duration=time.time() - start,
                metadata={"path": str(safe_path), "size": len(content)},
            )
        except PermissionError as e:
            return ToolResult(
                success=False, error=str(e),
                tool_id="file.write", duration=time.time() - start,
            )
        except Exception as e:
            return ToolResult(
                success=False, error=f"Write failed: {e}",
                tool_id="file.write", duration=time.time() - start,
            )

    def delete_file(self, path: str) -> ToolResult:
        """Delete a file within the worktree."""
        start = time.time()
        try:
            safe_path = self._resolve_safe_path(path)
            if not safe_path.exists():
                return ToolResult(
                    success=False, error=f"File not found: {path}",
                    tool_id="file.delete", duration=time.time() - start,
                )
            safe_path.unlink()
            self._log_action("file.delete", {"path": path})
            return ToolResult(
                success=True, output=f"Deleted {path}",
                tool_id="file.delete", duration=time.time() - start,
            )
        except PermissionError as e:
            return ToolResult(
                success=False, error=str(e),
                tool_id="file.delete", duration=time.time() - start,
            )

    def list_files(self, path: str = ".", pattern: str = "*") -> ToolResult:
        """List files in a directory within the worktree.

        Args:
            path: Relative directory path.
            pattern: Glob pattern to filter files.
        """
        start = time.time()
        try:
            safe_path = self._resolve_safe_path(path)
            if not safe_path.is_dir():
                return ToolResult(
                    success=False, error=f"Not a directory: {path}",
                    tool_id="file.list", duration=time.time() - start,
                )
            files = [
                str(f.relative_to(self.worktree_path))
                for f in safe_path.rglob(pattern)
                if f.is_file()
            ]
            self._log_action("file.list", {"path": path, "count": len(files)})
            return ToolResult(
                success=True, output=files,
                tool_id="file.list", duration=time.time() - start,
                metadata={"count": len(files)},
            )
        except PermissionError as e:
            return ToolResult(
                success=False, error=str(e),
                tool_id="file.list", duration=time.time() - start,
            )

    def search_files(self, query: str, file_pattern: str = "*.py") -> ToolResult:
        """Search for text within files in the worktree.

        Args:
            query: Text to search for.
            file_pattern: Glob pattern for files to search.
        """
        start = time.time()
        matches = []
        try:
            for filepath in self.worktree_path.rglob(file_pattern):
                if not filepath.is_file():
                    continue
                try:
                    content = filepath.read_text(encoding="utf-8")
                    for i, line in enumerate(content.splitlines(), 1):
                        if query in line:
                            matches.append({
                                "file": str(filepath.relative_to(self.worktree_path)),
                                "line": i,
                                "content": line.strip(),
                            })
                except (UnicodeDecodeError, PermissionError):
                    continue

            self._log_action("file.search", {"query": query, "matches": len(matches)})
            return ToolResult(
                success=True, output=matches,
                tool_id="file.search", duration=time.time() - start,
                metadata={"match_count": len(matches)},
            )
        except Exception as e:
            return ToolResult(
                success=False, error=f"Search failed: {e}",
                tool_id="file.search", duration=time.time() - start,
            )

    # ------------------------------------------------------------------ #
    # Terminal Operations
    # ------------------------------------------------------------------ #

    def run_command(
        self,
        command: str,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        """Execute a shell command within the worktree directory.

        Args:
            command: Shell command to execute.
            timeout: Max seconds (defaults to self.default_timeout).
            env: Additional environment variables.

        Returns:
            CommandResult with exit code, stdout, stderr.
        """
        if timeout is None:
            timeout = self.default_timeout

        start = time.time()
        cmd_env = dict(os.environ)
        if env:
            cmd_env.update(env)

        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(self.worktree_path),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=cmd_env,
            )
            duration = time.time() - start

            self._log_action("terminal.exec", {
                "command": command,
                "exit_code": proc.returncode,
                "duration": duration,
            })

            return CommandResult(
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration=duration,
                command=command,
            )
        except subprocess.TimeoutExpired:
            duration = time.time() - start
            self._log_action("terminal.exec", {
                "command": command,
                "timed_out": True,
                "duration": duration,
            })
            return CommandResult(
                exit_code=-1,
                stdout="",
                stderr=f"Command timed out after {timeout}s",
                duration=duration,
                timed_out=True,
                command=command,
            )
        except Exception as e:
            return CommandResult(
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration=time.time() - start,
                command=command,
            )

    # ------------------------------------------------------------------ #
    # Git Operations (scoped to worktree)
    # ------------------------------------------------------------------ #

    def git_status(self) -> ToolResult:
        """Get git status of the worktree."""
        result = self.run_command("git status --porcelain")
        return ToolResult(
            success=result.exit_code == 0,
            output=result.stdout,
            error=result.stderr if result.exit_code != 0 else None,
            tool_id="git.status",
            duration=result.duration,
        )

    def git_diff(self, staged: bool = False) -> ToolResult:
        """Get git diff of the worktree."""
        cmd = "git diff --cached" if staged else "git diff"
        result = self.run_command(cmd)
        return ToolResult(
            success=result.exit_code == 0,
            output=result.stdout,
            error=result.stderr if result.exit_code != 0 else None,
            tool_id="git.diff",
            duration=result.duration,
        )

    # ------------------------------------------------------------------ #
    # Build Operations
    # ------------------------------------------------------------------ #

    def run_tests(
        self, test_path: str = "tests/", extra_args: str = ""
    ) -> ToolResult:
        """Run pytest within the worktree."""
        cmd = f"python -m pytest {test_path} {extra_args} --tb=short"
        result = self.run_command(cmd, timeout=120)
        return ToolResult(
            success=result.exit_code == 0,
            output=result.stdout,
            error=result.stderr if result.exit_code != 0 else None,
            tool_id="build.test",
            duration=result.duration,
            metadata={"exit_code": result.exit_code},
        )

    def run_lint(self, paths: str = ".") -> ToolResult:
        """Run ruff linter within the worktree."""
        cmd = f"ruff check {paths}"
        result = self.run_command(cmd)
        return ToolResult(
            success=result.exit_code == 0,
            output=result.stdout,
            error=result.stderr if result.exit_code != 0 else None,
            tool_id="build.lint",
            duration=result.duration,
        )

    # ------------------------------------------------------------------ #
    # Action Log & Introspection
    # ------------------------------------------------------------------ #

    @property
    def action_log(self) -> list[dict[str, Any]]:
        """Read-only access to this sandbox's action history."""
        return list(self._action_log)

    @property
    def action_count(self) -> int:
        """Number of actions taken in this sandbox."""
        return len(self._action_log)

    def get_action_summary(self) -> dict[str, int]:
        """Summarize actions by tool_id."""
        summary: dict[str, int] = {}
        for action in self._action_log:
            tool_id = action.get("tool_id", "unknown")
            summary[tool_id] = summary.get(tool_id, 0) + 1
        return summary

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _log_action(self, tool_id: str, data: dict[str, Any]) -> None:
        """Log an action to the sandbox log and ALIGN ledger."""
        entry = {
            "agent_id": self.agent_id,
            "agent_role": self.agent_role,
            "tool_id": tool_id,
            "timestamp": time.time(),
            **data,
        }
        self._action_log.append(entry)

        try:
            from agents.core.als_logger import ALSLogger
            als = ALSLogger()
            als.log("SANDBOX_ACTION", entry)
        except ImportError:
            # ALIGN ledger is optional; skip logging if not installed
            logger.debug("ALSLogger not available; skipping ALIGN logging")

    def _register_sandbox_tools(self) -> None:
        """Register scoped tool implementations into the registry."""
        tools = [
            ToolDefinition(
                tool_id="file.read",
                category=ToolCategory.FILE,
                description="Read a file within the agent's worktree",
                execute_fn=lambda path="": self.read_file(path),
                required_permission=ToolPermission.READ,
            ),
            ToolDefinition(
                tool_id="file.write",
                category=ToolCategory.FILE,
                description="Write content to a file within the agent's worktree",
                execute_fn=lambda path="", content="": self.write_file(path, content),
                required_permission=ToolPermission.WRITE,
            ),
            ToolDefinition(
                tool_id="file.delete",
                category=ToolCategory.FILE,
                description="Delete a file within the agent's worktree",
                execute_fn=lambda path="": self.delete_file(path),
                required_permission=ToolPermission.WRITE,
            ),
            ToolDefinition(
                tool_id="file.list",
                category=ToolCategory.FILE,
                description="List files in a directory within the worktree",
                execute_fn=lambda path=".", pattern="*": self.list_files(path, pattern),
                required_permission=ToolPermission.READ,
            ),
            ToolDefinition(
                tool_id="file.search",
                category=ToolCategory.FILE,
                description="Search for text within files in the worktree",
                execute_fn=lambda query="", file_pattern="*.py": self.search_files(query, file_pattern),
                required_permission=ToolPermission.READ,
            ),
            ToolDefinition(
                tool_id="terminal.exec",
                category=ToolCategory.TERMINAL,
                description="Execute a shell command in the worktree",
                execute_fn=lambda command="", timeout=30: self.run_command(command, timeout),
                required_permission=ToolPermission.EXECUTE,
            ),
            ToolDefinition(
                tool_id="git.status",
                category=ToolCategory.GIT,
                description="Get git status of the worktree",
                execute_fn=self.git_status,
                required_permission=ToolPermission.READ,
            ),
            ToolDefinition(
                tool_id="git.diff",
                category=ToolCategory.GIT,
                description="Get git diff of the worktree",
                execute_fn=lambda staged=False: self.git_diff(staged),
                required_permission=ToolPermission.READ,
            ),
            ToolDefinition(
                tool_id="build.test",
                category=ToolCategory.BUILD,
                description="Run pytest within the worktree",
                execute_fn=lambda test_path="tests/", extra_args="": self.run_tests(test_path, extra_args),
                required_permission=ToolPermission.EXECUTE,
            ),
            ToolDefinition(
                tool_id="build.lint",
                category=ToolCategory.BUILD,
                description="Run ruff linter within the worktree",
                execute_fn=lambda paths=".": self.run_lint(paths),
                required_permission=ToolPermission.EXECUTE,
            ),
        ]
        for tool in tools:
            self.tool_registry.register(tool)
