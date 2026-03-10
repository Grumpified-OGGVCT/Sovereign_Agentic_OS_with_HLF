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

MCP Workflow Integrity (Azure Hat):
  - ToolLifecycleState: PENDING_APPROVAL → ACTIVE → DEPRECATED / REVOKED
  - HITL gates: tools with ``requires_hitl=True`` block execution until a
    human-issued approval token is presented via ``grant_hitl_approval()``.
  - WorkflowLedger: every ``execute()`` call is stamped with a monotonic
    step-ID and written to the session ledger for audit / replay.

Usage::

    registry = ToolRegistry()
    registry.register_builtins()

    # Look up a tool
    tool = registry.get("file.read")
    result = tool.execute(path="src/main.py")

    # Check permissions
    registry.can_use("sentinel", "terminal.exec")  # → True
    registry.can_use("scribe", "git.push")          # → False

    # HITL approval flow
    registry.grant_hitl_approval("terminal.exec", token="op-approved-abc123")
    result = registry.execute("terminal.exec", "sentinel",
                              hitl_token="op-approved-abc123", cmd="ls")
"""

from __future__ import annotations

import logging
import time
import uuid
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


# --------------------------------------------------------------------------- #
# Lifecycle States (Azure Hat — MCP Workflow Integrity)
# --------------------------------------------------------------------------- #


class ToolLifecycleState(StrEnum):
    """Lifecycle states for registered tools.

    State machine (Azure Hat):
      PENDING_APPROVAL → ACTIVE → DEPRECATED → REVOKED
                      ↘ REVOKED  (rejected without activation)
    """
    PENDING_APPROVAL = "pending_approval"  # Awaiting human HITL sign-off
    ACTIVE = "active"                       # Fully operational
    DEPRECATED = "deprecated"              # Soft-disabled; still usable with warning
    REVOKED = "revoked"                    # Hard-disabled; execution blocked


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


class HITLRequiredError(RuntimeError):
    """Raised when a tool requires a human-in-the-loop approval token.

    The caller must obtain a token via ``ToolRegistry.grant_hitl_approval()``
    and pass it as the ``hitl_token`` keyword argument to ``execute()``.
    """

    def __init__(self, tool_id: str) -> None:
        super().__init__(
            f"Tool '{tool_id}' requires human-in-the-loop approval. "
            "Call grant_hitl_approval(tool_id, token) and pass the token "
            "as hitl_token= to execute()."
        )
        self.tool_id = tool_id


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
        step_id: Monotonic workflow step identifier (Azure Hat ledger).
        metadata: Extra context (e.g., file path, exit code).
    """
    success: bool
    output: Any = None
    error: str | None = None
    duration: float = 0.0
    tool_id: str = ""
    step_id: str = ""
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
        lifecycle_state: MCP lifecycle state (Azure Hat).
        requires_hitl: If True, execution is blocked until a human-issued
            approval token is presented (Azure Hat HITL gate).
    """
    tool_id: str
    category: ToolCategory
    description: str
    execute_fn: Callable[..., Any]
    required_permission: ToolPermission = ToolPermission.READ
    timeout: float = 30.0
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    lifecycle_state: ToolLifecycleState = ToolLifecycleState.ACTIVE
    requires_hitl: bool = False

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
# Workflow Ledger (Azure Hat — step-ID tracking for auditability)
# --------------------------------------------------------------------------- #


@dataclass
class WorkflowLedgerEntry:
    """A single entry in the sequential workflow ledger.

    Every ``ToolRegistry.execute()`` call is recorded here so the full
    tool-invocation chain can be replayed or audited (Azure Hat requirement).

    Attributes:
        step_id: Unique monotonic identifier for this execution step.
        tool_id: The tool that was invoked.
        agent_role: The role of the agent that triggered the call.
        timestamp: Unix epoch time of the invocation.
        success: Whether the tool completed without error.
        hitl_approved: Whether a HITL approval token was validated.
        duration: Wall-clock execution time in seconds.
    """
    step_id: str
    tool_id: str
    agent_role: str
    timestamp: float
    success: bool
    hitl_approved: bool = False
    duration: float = 0.0


# --------------------------------------------------------------------------- #
# Tool Registry
# --------------------------------------------------------------------------- #


class ToolRegistry:
    """Central catalog of all available tools.

    Manages tool registration, lookup, and permission-based access
    control. Agents query the registry to discover what tools they
    can use, and the registry enforces permission checks.

    Azure Hat (MCP Workflow Integrity) additions:
      - ``set_lifecycle()`` — transition a tool through its lifecycle states.
      - ``grant_hitl_approval()`` / ``revoke_hitl_approval()`` — issue or
        withdraw single-use human-in-the-loop approval tokens.
      - ``workflow_ledger`` — read-only view of the sequential step-ID log.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._role_permissions: dict[str, set[ToolPermission]] = {
            k: set(v) for k, v in _DEFAULT_ROLE_PERMISSIONS.items()
        }
        self._invocation_log: list[dict[str, Any]] = []
        # Azure Hat: HITL approval tokens per tool (set of valid tokens)
        self._hitl_approvals: dict[str, set[str]] = {}
        # Azure Hat: sequential workflow ledger
        self._workflow_ledger: list[WorkflowLedgerEntry] = []
        self._step_counter: int = 0

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
        self, tool_id: str, agent_role: str, *, hitl_token: str | None = None, **kwargs: Any
    ) -> ToolResult:
        """Execute a tool with permission checks.

        Args:
            tool_id: The tool to execute.
            agent_role: The calling agent's role.
            hitl_token: Human-issued approval token, required when the
                tool's ``requires_hitl`` flag is ``True`` (Azure Hat gate).
            **kwargs: Arguments to pass to the tool.

        Returns:
            ToolResult — always returns (never raises).

        Raises:
            HITLRequiredError: If the tool requires human approval and no
                valid token was supplied. This is intentionally not caught
                here so callers know they must route the request to a human
                operator before retrying.
        """
        tool = self._tools.get(tool_id)
        if tool is None:
            return ToolResult(
                success=False,
                error=f"Tool '{tool_id}' not found",
                tool_id=tool_id,
            )

        # Azure Hat: lifecycle gate — only ACTIVE tools are executable
        if tool.lifecycle_state == ToolLifecycleState.REVOKED:
            return ToolResult(
                success=False,
                error=f"Tool '{tool_id}' has been revoked and cannot be executed",
                tool_id=tool_id,
            )
        if tool.lifecycle_state == ToolLifecycleState.PENDING_APPROVAL:
            return ToolResult(
                success=False,
                error=f"Tool '{tool_id}' is pending human approval and is not yet active",
                tool_id=tool_id,
            )

        if not self.can_use(agent_role, tool_id):
            return ToolResult(
                success=False,
                error=f"Agent role '{agent_role}' lacks permission for '{tool_id}' "
                      f"(requires {tool.required_permission})",
                tool_id=tool_id,
            )

        # Azure Hat: HITL gate — check human approval token before execution
        hitl_approved = False
        if tool.requires_hitl:
            if not self.has_hitl_approval(tool_id, hitl_token):
                raise HITLRequiredError(tool_id)
            # Consume the single-use token (discard from the stored set directly)
            if tool_id in self._hitl_approvals:
                self._hitl_approvals[tool_id].discard(hitl_token)
            hitl_approved = True

        result = tool.execute(**kwargs)

        # Azure Hat: generate step-ID and write to workflow ledger
        self._step_counter += 1
        step_id = f"step-{self._step_counter:06d}-{uuid.uuid4().hex[:8]}"
        result.step_id = step_id

        ledger_entry = WorkflowLedgerEntry(
            step_id=step_id,
            tool_id=tool_id,
            agent_role=agent_role,
            timestamp=time.time(),
            success=result.success,
            hitl_approved=hitl_approved,
            duration=result.duration,
        )
        self._workflow_ledger.append(ledger_entry)

        # Log invocation
        log_entry = {
            "tool_id": tool_id,
            "agent_role": agent_role,
            "success": result.success,
            "duration": result.duration,
            "timestamp": time.time(),
            "step_id": step_id,
            "hitl_approved": hitl_approved,
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

    @property
    def workflow_ledger(self) -> list[WorkflowLedgerEntry]:
        """Read-only sequential workflow ledger (Azure Hat step-ID audit trail)."""
        return list(self._workflow_ledger)

    # ----------------------------------------------------------------------- #
    # Azure Hat — Lifecycle Management
    # ----------------------------------------------------------------------- #

    def set_lifecycle(self, tool_id: str, state: ToolLifecycleState) -> bool:
        """Transition a tool to a new lifecycle state.

        Args:
            tool_id: The tool whose lifecycle state to change.
            state: The new lifecycle state.

        Returns:
            True if the tool was found and updated, False otherwise.
        """
        tool = self._tools.get(tool_id)
        if tool is None:
            return False
        old_state = tool.lifecycle_state
        tool.lifecycle_state = state
        logger.info(
            "ToolRegistry: lifecycle '%s' → '%s' for '%s'",
            old_state, state, tool_id,
        )
        self._log_align("TOOL_LIFECYCLE_CHANGE", {
            "tool_id": tool_id,
            "old_state": str(old_state),
            "new_state": str(state),
            "timestamp": time.time(),
        })
        return True

    # ----------------------------------------------------------------------- #
    # Azure Hat — HITL Approval Gates
    # ----------------------------------------------------------------------- #

    def grant_hitl_approval(self, tool_id: str, token: str) -> None:
        """Issue a human-in-the-loop approval token for a specific tool.

        The token is single-use: it is consumed (discarded) on the first
        successful ``execute()`` call that presents it.

        Args:
            tool_id: The tool to approve for one invocation.
            token: An opaque string token supplied by the human operator.
                   Use ``uuid.uuid4().hex`` or a similar unique value.
        """
        if not token:
            raise ValueError("HITL approval token must be a non-empty string")
        if tool_id not in self._hitl_approvals:
            self._hitl_approvals[tool_id] = set()
        self._hitl_approvals[tool_id].add(token)
        logger.info("ToolRegistry: HITL approval granted for '%s'", tool_id)
        self._log_align("HITL_APPROVAL_GRANTED", {
            "tool_id": tool_id,
            "timestamp": time.time(),
        })

    def revoke_hitl_approval(self, tool_id: str, token: str) -> bool:
        """Revoke a previously issued HITL approval token.

        Args:
            tool_id: The tool whose approval to revoke.
            token: The token to revoke.

        Returns:
            True if the token was found and removed, False otherwise.
        """
        tokens = self._hitl_approvals.get(tool_id, set())
        if token in tokens:
            tokens.discard(token)
            logger.info("ToolRegistry: HITL approval revoked for '%s'", tool_id)
            self._log_align("HITL_APPROVAL_REVOKED", {
                "tool_id": tool_id,
                "timestamp": time.time(),
            })
            return True
        return False

    def has_hitl_approval(self, tool_id: str, token: str | None) -> bool:
        """Check whether a valid HITL approval token exists for a tool.

        Args:
            tool_id: The tool to check.
            token: The token to validate.

        Returns:
            True if the token is present and valid.
        """
        if not token:
            return False
        return token in self._hitl_approvals.get(tool_id, set())

    def _log_align(self, event: str, data: dict) -> None:
        """Log to ALIGN ledger."""
        try:
            from agents.core.logger import ALSLogger
            als = ALSLogger()
            als.log(event, data)
        except ImportError:
            # ALIGN ledger is optional; skip logging if not installed
            logger.debug("ALSLogger not available; skipping ALIGN logging for %s", event)
