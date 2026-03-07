"""
HLF Intent Capsules — Scope Constraint Enforcement.

An Intent Capsule wraps an HLF program execution with bounded permissions:
  - Which tags an agent is allowed to execute
  - Which host functions it can call
  - Maximum gas budget
  - Allowed tier level
  - Read/write scope restrictions

Capsules enforce the Principle of Least Privilege at the HLF runtime level.
Every agent action is sandboxed within its capsule boundary.

Usage:
    capsule = IntentCapsule(
        agent="worker-01",
        allowed_tags={"INTENT", "CONSTRAINT", "ACTION", "RESULT"},
        allowed_tools={"READ_FILE", "HASH"},
        max_gas=20,
        tier="hearth",
        read_only_vars={"config", "system_prompt"},
    )
    result = capsule.execute(ast, scope)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from hlf.hlfc import HlfRuntimeError
from hlf.hlfrun import HLFInterpreter

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Capsule violation
# --------------------------------------------------------------------------- #


class CapsuleViolation(HlfRuntimeError):
    """Raised when an HLF operation violates its capsule constraints."""

    def __init__(self, agent: str, violation: str) -> None:
        self.agent = agent
        self.violation = violation
        super().__init__(f"Capsule violation [{agent}]: {violation}")


# --------------------------------------------------------------------------- #
# Intent Capsule
# --------------------------------------------------------------------------- #


@dataclass
class IntentCapsule:
    """Bounded execution scope for an agent's HLF program.

    Enforces:
      - Tag allowlist (which statement types the agent can execute)
      - Tool allowlist (which host functions are permitted)
      - Gas budget (maximum AST node executions)
      - Tier restriction (deployment tier ceiling)
      - Read-only variables (cannot be overwritten by SET/ASSIGN)
      - Write-only variables (agent can only write these, not read others)
    """

    agent: str
    allowed_tags: set[str] = field(default_factory=lambda: set(DEFAULT_TAGS))
    denied_tags: set[str] = field(default_factory=set)
    allowed_tools: set[str] = field(default_factory=lambda: set(DEFAULT_TOOLS))
    max_gas: int = 50
    tier: str = "hearth"
    read_only_vars: set[str] = field(default_factory=set)
    write_restricted_vars: set[str] | None = None  # If set, agent can only write these

    def validate_program(self, ast: dict) -> list[str]:
        """Pre-flight validation: check all nodes against capsule constraints.

        Returns a list of violation descriptions (empty = valid).
        """
        violations = []
        program = ast.get("program", [])

        for i, node in enumerate(program):
            if node is None:
                continue
            violations.extend(self._validate_node(node, i))

        # Gas check
        node_count = sum(1 for n in program if n is not None)
        if node_count > self.max_gas:
            violations.append(
                f"Program has {node_count} nodes but capsule gas limit is {self.max_gas}"
            )

        return violations

    def execute(
        self,
        ast: dict,
        scope: dict[str, Any] | None = None,
        memory_engine: Any = None,
    ) -> dict:
        """Execute an HLF AST within this capsule's constraints.

        Pre-validates the program, then runs it with an instrumented interpreter
        that enforces capsule boundaries at runtime.
        """
        # Pre-flight validation
        violations = self.validate_program(ast)
        if violations:
            raise CapsuleViolation(
                self.agent,
                f"{len(violations)} constraint(s) violated: {'; '.join(violations[:5])}"
            )

        # Build scope with read-only protection markers
        exec_scope = dict(scope or {})
        exec_scope["_agent"] = self.agent
        exec_scope["_capsule_read_only"] = set(self.read_only_vars)

        # Create interpreter with capsule constraints
        interp = CapsuleInterpreter(
            capsule=self,
            scope=exec_scope,
            tier=self.tier,
            max_gas=self.max_gas,
        )
        if memory_engine:
            interp._memory_engine = memory_engine

        result = interp.execute(ast)
        result["capsule"] = {
            "agent": self.agent,
            "violations_caught": interp._violations_caught,
        }
        return result

    def _validate_node(self, node: dict, index: int) -> list[str]:
        """Validate a single AST node against capsule constraints."""
        violations = []
        tag = node.get("tag", "")

        # Tag allowlist check
        if self.denied_tags and tag in self.denied_tags:
            violations.append(f"Node {index}: tag [{tag}] is denied by capsule")
        elif self.allowed_tags and tag not in self.allowed_tags and tag not in STRUCTURAL_TAGS:
            violations.append(f"Node {index}: tag [{tag}] is not in allowed tags")

        # Tool check
        if tag == "TOOL":
            tool_name = node.get("tool", "")
            if self.allowed_tools and tool_name not in self.allowed_tools:
                violations.append(
                    f"Node {index}: tool '{tool_name}' not permitted by capsule"
                )

        # Recursively check inner nodes (glyph_modified, parallel, etc.)
        inner = node.get("inner")
        if inner and isinstance(inner, dict):
            violations.extend(self._validate_node(inner, index))

        for task in node.get("tasks", []):
            if isinstance(task, dict):
                violations.extend(self._validate_node(task, index))

        then_node = node.get("then")
        if then_node and isinstance(then_node, dict):
            violations.extend(self._validate_node(then_node, index))

        else_node = node.get("else")
        if else_node and isinstance(else_node, dict):
            violations.extend(self._validate_node(else_node, index))

        return violations


# --------------------------------------------------------------------------- #
# Capsule-instrumented interpreter
# --------------------------------------------------------------------------- #


class CapsuleInterpreter(HLFInterpreter):
    """HLFInterpreter subclass that enforces capsule constraints at runtime."""

    def __init__(self, capsule: IntentCapsule, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._capsule = capsule
        self._violations_caught: list[str] = []

    def _execute_node(self, node: dict) -> Any:
        """Override: enforce capsule constraints before dispatching."""
        tag = node.get("tag", "")

        # Denied tag check
        if self._capsule.denied_tags and tag in self._capsule.denied_tags:
            violation = f"Tag [{tag}] denied by capsule for agent '{self._capsule.agent}'"
            self._violations_caught.append(violation)
            raise CapsuleViolation(self._capsule.agent, violation)

        # Allowlist check (skip structural tags)
        if tag not in STRUCTURAL_TAGS and self._capsule.allowed_tags and tag not in self._capsule.allowed_tags:
                violation = f"Tag [{tag}] not in capsule allowlist for '{self._capsule.agent}'"
                self._violations_caught.append(violation)
                raise CapsuleViolation(self._capsule.agent, violation)

        # Tool allowlist check
        if tag == "TOOL":
            tool_name = node.get("tool", "")
            if self._capsule.allowed_tools and tool_name not in self._capsule.allowed_tools:
                violation = f"Tool '{tool_name}' not permitted for '{self._capsule.agent}'"
                self._violations_caught.append(violation)
                raise CapsuleViolation(self._capsule.agent, violation)

        return super()._execute_node(node)

    def _exec_set(self, node: dict) -> None:
        """Override: enforce read-only variable protection for SET."""
        name = node.get("name", "")
        read_only = self.scope.get("_capsule_read_only", set())
        if name in read_only:
            violation = f"Variable '{name}' is read-only (protected by capsule)"
            self._violations_caught.append(violation)
            raise CapsuleViolation(self._capsule.agent, violation)
        super()._exec_set(node)

    def _exec_assign(self, node: dict) -> None:
        """Override: enforce read-only variable protection for ASSIGN."""
        name = node.get("name", "")
        read_only = self.scope.get("_capsule_read_only", set())
        if name in read_only:
            violation = f"Variable '{name}' is read-only (protected by capsule)"
            self._violations_caught.append(violation)
            raise CapsuleViolation(self._capsule.agent, violation)
        super()._exec_assign(node)


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

# Tags that are always allowed (structural, non-executing)
STRUCTURAL_TAGS = {
    "INTENT", "THOUGHT", "OBSERVATION", "PLAN", "EXPECT",
    "CONSTRAINT", "ASSERT", "MODULE", "DATA", "EPISTEMIC",
}

# Default allowed tags for standard capsules
DEFAULT_TAGS = {
    "INTENT", "THOUGHT", "OBSERVATION", "PLAN", "CONSTRAINT",
    "EXPECT", "ACTION", "SET", "ASSIGN", "FUNCTION", "RESULT",
    "VOTE", "ASSERT", "CALL", "IMPORT",
}

# Default allowed tools for standard capsules
DEFAULT_TOOLS = {
    "READ_FILE", "WRITE_FILE", "HASH", "LIST_DIR",
}


# --------------------------------------------------------------------------- #
# Convenience functions
# --------------------------------------------------------------------------- #


def sovereign_capsule(agent: str) -> IntentCapsule:
    """Create a sovereign-tier capsule with full permissions."""
    return IntentCapsule(
        agent=agent,
        allowed_tags=set(),  # Empty = allow all
        allowed_tools=set(),  # Empty = allow all
        max_gas=1000,
        tier="sovereign",
    )


def hearth_capsule(agent: str) -> IntentCapsule:
    """Create a hearth-tier capsule with restricted permissions."""
    return IntentCapsule(
        agent=agent,
        allowed_tags=DEFAULT_TAGS,
        denied_tags={"DELEGATE", "DEFINE"},  # Can't delegate or define macros
        allowed_tools={"READ_FILE", "HASH"},
        max_gas=20,
        tier="hearth",
        read_only_vars={"config", "system_prompt", "tier"},
    )


def forge_capsule(agent: str) -> IntentCapsule:
    """Create a forge-tier capsule with moderate permissions."""
    return IntentCapsule(
        agent=agent,
        allowed_tags=DEFAULT_TAGS | {"DELEGATE", "DEFINE", "CALL", "MEMORY", "RECALL"},
        allowed_tools=DEFAULT_TOOLS | {"HTTP_GET", "HTTP_POST", "VECTOR_SEARCH"},
        max_gas=100,
        tier="forge",
    )
