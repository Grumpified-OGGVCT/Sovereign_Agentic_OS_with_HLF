"""
Tool Registry — Central Catalog of Agent Capabilities.

Provides a typed registry of tools that agents can invoke during
Spindle DAG execution. Each tool has a schema, permissions, and
timeout configuration.

Tool categories:
  - file: read, write, list, delete, search
  - terminal: exec, exec_bg, read_output
  - git: status, diff, commit, push
  - build: run, test, lint
  - http: get, post (docs fetching, API calls)

Usage::

    registry = ToolRegistry()
    registry.register_builtins()

    # Look up a tool
    tool = registry.get("file.read")
    result = tool.execute(path="src/main.py")

    # Check permissions
    registry.can_use("sentinel", "terminal.exec")  # → True
    registry.can_use("scribe", "git.push")          # → False
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Tool Categories
# --------------------------------------------------------------------------- #


class ToolCategory(StrEnum):
    """Categories of tools available to agents."""
    FILE = "file"
    TERMINAL = "terminal"
    GIT = "git"
    BUILD = "build"
    HTTP = "http"
    ANALYSIS = "analysis"


# --------------------------------------------------------------------------- #
# Permission Levels
# --------------------------------------------------------------------------- #


class ToolPermission(StrEnum):
    """Permission levels for tool access."""
    READ = "read"        # Can read files, view status
    WRITE = "write"      # Can write files, make commits
    EXECUTE = "execute"  # Can run commands, deploy
    ADMIN = "admin"      # Full access including destructive ops


# Default role → permission mapping
_DEFAULT_ROLE_PERMISSIONS: dict[str, set[ToolPermission]] = {
    # Named personas
    "sentinel": {ToolPermission.READ, ToolPermission.EXECUTE},
    "scribe": {ToolPermission.READ},
    "arbiter": {ToolPermission.READ},
    "steward": {ToolPermission.READ, ToolPermission.EXECUTE},
    "cove": {ToolPermission.READ, ToolPermission.EXECUTE},
    "palette": {ToolPermission.READ},
    "consolidator": {ToolPermission.READ},
    "scout": {ToolPermission.READ, ToolPermission.EXECUTE},
    "strategist": {ToolPermission.READ},
    "catalyst": {ToolPermission.READ, ToolPermission.EXECUTE},
    "oracle": {ToolPermission.READ},
    "chronicler": {ToolPermission.READ},
    "herald": {ToolPermission.READ, ToolPermission.WRITE},
    "weaver": {ToolPermission.READ, ToolPermission.WRITE},
    # Generic roles
    "developer": {ToolPermission.READ, ToolPermission.WRITE, ToolPermission.EXECUTE},
    "reviewer": {ToolPermission.READ},
    "deployer": {ToolPermission.READ, ToolPermission.WRITE, ToolPermission.EXECUTE, ToolPermission.ADMIN},
}


# --------------------------------------------------------------------------- #
# Tool Result
# --------------------------------------------------------------------------- #


@dataclass
class ToolResult:
    """Result of a tool invocation.

    Attributes:
        success: Whether the tool executed without error.
        output: The tool's output (string, dict, list, etc.)
        error: Error message if failed.
        duration: Execution time in seconds.
        tool_id: Which tool was invoked.
        metadata: Extra context (e.g., file path, exit code).
    """
    success: bool
    output: Any = None
    error: str | None = None
    duration: float = 0.0
    tool_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Tool Definition
# --------------------------------------------------------------------------- #


@dataclass
class ToolDefinition:
    """A single registered tool.

    Attributes:
        tool_id: Unique identifier (e.g. "file.read").
        category: Tool category (file, terminal, git, etc.)
        description: Human-readable description.
        execute_fn: Callable(**kwargs) → Any. The tool implementation.
        required_permission: Minimum permission level to use.
        timeout: Max execution time in seconds (0 = no limit).
        input_schema: JSON-Schema-like dict describing expected inputs.
        output_schema: JSON-Schema-like dict describing outputs.
    """
    tool_id: str
    category: ToolCategory
    description: str
    execute_fn: Callable[..., Any]
    required_permission: ToolPermission = ToolPermission.READ
    timeout: float = 30.0
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)

    def execute(self, **kwargs: Any) -> ToolResult:
        """Execute this tool with the given arguments.

        Returns:
            ToolResult with success/failure and output.
        """
        start = time.time()
        try:
            output = self.execute_fn(**kwargs)
            return ToolResult(
                success=True,
                output=output,
                duration=time.time() - start,
                tool_id=self.tool_id,
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=str(e),
                duration=time.time() - start,
                tool_id=self.tool_id,
            )


# --------------------------------------------------------------------------- #
# Tool Registry
# --------------------------------------------------------------------------- #


class ToolRegistry:
    """Central catalog of all available tools.

    Manages tool registration, lookup, and permission-based access
    control. Agents query the registry to discover what tools they
    can use, and the registry enforces permission checks.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._role_permissions: dict[str, set[ToolPermission]] = {
            k: set(v) for k, v in _DEFAULT_ROLE_PERMISSIONS.items()
        }
        self._invocation_log: list[dict[str, Any]] = []

    def register(self, tool: ToolDefinition) -> None:
        """Register a tool. Overwrites if tool_id already exists."""
        self._tools[tool.tool_id] = tool
        logger.debug(f"ToolRegistry: registered '{tool.tool_id}'")

    def unregister(self, tool_id: str) -> bool:
        """Remove a tool. Returns True if found."""
        return self._tools.pop(tool_id, None) is not None

    def get(self, tool_id: str) -> ToolDefinition | None:
        """Look up a tool by ID."""
        return self._tools.get(tool_id)

    def list_tools(
        self, category: ToolCategory | None = None
    ) -> list[ToolDefinition]:
        """List all registered tools, optionally filtered by category."""
        tools = list(self._tools.values())
        if category is not None:
            tools = [t for t in tools if t.category == category]
        return sorted(tools, key=lambda t: t.tool_id)

    def list_tool_ids(self, category: ToolCategory | None = None) -> list[str]:
        """List tool IDs only (lighter than full definitions)."""
        return [t.tool_id for t in self.list_tools(category)]

    def can_use(self, agent_role: str, tool_id: str) -> bool:
        """Check if an agent role has permission to use a tool.

        Args:
            agent_role: The agent's role (e.g., "sentinel", "developer").
            tool_id: The tool to check access for.

        Returns:
            True if the agent can use this tool.
        """
        tool = self._tools.get(tool_id)
        if tool is None:
            return False

        role_perms = self._role_permissions.get(
            agent_role, {ToolPermission.READ}
        )
        return tool.required_permission in role_perms

    def grant_permission(
        self, role: str, permission: ToolPermission
    ) -> None:
        """Grant a permission to a role."""
        if role not in self._role_permissions:
            self._role_permissions[role] = set()
        self._role_permissions[role].add(permission)

    def revoke_permission(
        self, role: str, permission: ToolPermission
    ) -> None:
        """Revoke a permission from a role."""
        if role in self._role_permissions:
            self._role_permissions[role].discard(permission)

    def get_available_tools(self, agent_role: str) -> list[ToolDefinition]:
        """Get all tools an agent role can use."""
        return [
            tool for tool in self._tools.values()
            if self.can_use(agent_role, tool.tool_id)
        ]

    def execute(
        self, tool_id: str, agent_role: str, **kwargs: Any
    ) -> ToolResult:
        """Execute a tool with permission checks.

        Args:
            tool_id: The tool to execute.
            agent_role: The calling agent's role.
            **kwargs: Arguments to pass to the tool.

        Returns:
            ToolResult — always returns (never raises).
        """
        tool = self._tools.get(tool_id)
        if tool is None:
            return ToolResult(
                success=False,
                error=f"Tool '{tool_id}' not found",
                tool_id=tool_id,
            )

        if not self.can_use(agent_role, tool_id):
            return ToolResult(
                success=False,
                error=f"Agent role '{agent_role}' lacks permission for '{tool_id}' "
                      f"(requires {tool.required_permission})",
                tool_id=tool_id,
            )

        result = tool.execute(**kwargs)

        # Log invocation
        log_entry = {
            "tool_id": tool_id,
            "agent_role": agent_role,
            "success": result.success,
            "duration": result.duration,
            "timestamp": time.time(),
        }
        self._invocation_log.append(log_entry)

        self._log_align("TOOL_INVOCATION", log_entry)
        return result

    @property
    def tool_count(self) -> int:
        """Number of registered tools."""
        return len(self._tools)

    @property
    def invocation_log(self) -> list[dict[str, Any]]:
        """Read-only access to invocation history."""
        return list(self._invocation_log)

    def _log_align(self, event: str, data: dict) -> None:
        """Log to ALIGN ledger."""
        try:
            from agents.core.als_logger import ALSLogger
            als = ALSLogger()
            als.log(event, data)
        except ImportError:
            # ALIGN ledger is optional; skip logging if not installed
            logger.debug("ALSLogger not available; skipping ALIGN logging for %s", event)
