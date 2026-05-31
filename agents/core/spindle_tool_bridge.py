"""
Spindle Tool Bridge — Connects DAG Nodes to Agent Sandbox Execution.

Bridges the gap between "Spindle says execute this node" and "agent
actually runs tools in a sandbox." Implements the agent execution loop:

    Think → Act → Observe → Think Again

Each SpindleNode with an agent_id gets wrapped in a sandbox. The bridge:
  1. Creates an AgentSandbox scoped to the agent's worktree
  2. Provides the sandbox as context to the node's execute_fn
  3. Publishes tool events to the Event Bus
  4. Collects action logs for the ALIGN ledger

Usage::

    bridge = SpindleToolBridge(
        tool_registry=registry,
        acfs_manager=acfs,
        event_bus=bus,
    )

    # Wrap a dag for sandboxed execution
    executor = bridge.create_executor(dag)
    result = executor.run(context={"topic": "build auth module"})
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from agents.core.agent_sandbox import AgentSandbox
from agents.core.tool_registry import ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

# Module-level event type mapping (built once, never mutated).
# Maps bridge event-key strings to EventType enum names, resolved lazily
# inside _publish_event to avoid importing event_bus at module import time.
_BRIDGE_EVENT_MAP: dict[str, str] = {
    "TOOL_BRIDGE_NODE_START": "NODE_STARTED",
    "TOOL_BRIDGE_NODE_COMPLETE": "NODE_COMPLETED",
    "TOOL_BRIDGE_NODE_FAILED": "NODE_FAILED",
}


# --------------------------------------------------------------------------- #
# Agent Execution Step
# --------------------------------------------------------------------------- #


@dataclass
class AgentStep:
    """A single step in an agent's execution loop.

    Attributes:
        step_number: Sequential step index.
        action: What tool was invoked.
        tool_id: The tool's identifier.
        input_args: Arguments passed to the tool.
        result: The tool's result.
        reasoning: Why the agent chose this action (from LLM).
        timestamp: When this step occurred.
    """
    step_number: int
    action: str
    tool_id: str
    input_args: dict[str, Any] = field(default_factory=dict)
    result: ToolResult | None = None
    reasoning: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class AgentExecutionTrace:
    """Complete trace of an agent's execution within a node.

    Attributes:
        agent_id: The executing agent.
        node_id: The DAG node being executed.
        steps: Ordered list of execution steps.
        total_tool_calls: Number of tools invoked.
        total_duration: Wall-clock time for the full execution.
        final_output: The node's return value.
        success: Whether execution completed without error.
    """
    agent_id: str
    node_id: str
    steps: list[AgentStep] = field(default_factory=list)
    total_tool_calls: int = 0
    total_duration: float = 0.0
    final_output: Any = None
    success: bool = True
    error: str | None = None


# --------------------------------------------------------------------------- #
# Spindle Tool Bridge
# --------------------------------------------------------------------------- #


class SpindleToolBridge:
    """Bridges SpindleDAG execution to AgentSandbox tool invocation.

    Creates sandboxes for agents, provides them during node execution,
    and collects execution traces for audit.

    Attributes:
        tool_registry: Central tool catalog.
        acfs_manager: ACFS worktree manager for sandbox scoping.
        event_bus: Event bus for publishing tool events.
        max_steps_per_node: Safety limit on agent steps.
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        acfs_manager: Any | None = None,
        event_bus: Any | None = None,
        max_steps_per_node: int = 50,
    ) -> None:
        self.tool_registry = tool_registry
        self.acfs_manager = acfs_manager
        self._event_bus = event_bus
        self.max_steps_per_node = max_steps_per_node
        self._sandboxes: dict[str, AgentSandbox] = {}
        self._traces: list[AgentExecutionTrace] = []

    def get_or_create_sandbox(
        self,
        agent_id: str,
        agent_role: str,
        worktree_path: str,
    ) -> AgentSandbox:
        """Get an existing sandbox or create a new one.

        Args:
            agent_id: Unique agent identifier.
            agent_role: Persona role for permissions.
            worktree_path: Path to the agent's worktree.

        Returns:
            AgentSandbox scoped to the agent's worktree.
        """
        if agent_id not in self._sandboxes:
            sandbox = AgentSandbox(
                agent_id=agent_id,
                agent_role=agent_role,
                worktree_path=worktree_path,
                tool_registry=self.tool_registry,
            )
            self._sandboxes[agent_id] = sandbox
            logger.info(
                f"ToolBridge: created sandbox for {agent_id} "
                f"at {worktree_path}"
            )
        return self._sandboxes[agent_id]

    def execute_node(
        self,
        node_id: str,
        agent_id: str,
        agent_role: str,
        worktree_path: str,
        execute_fn: Any,
        context: dict[str, Any],
    ) -> AgentExecutionTrace:
        """Execute a DAG node within a sandboxed environment.

        This wraps the node's execute_fn, injecting the sandbox into
        the context so the function can invoke tools.

        Args:
            node_id: The DAG node being executed.
            agent_id: The executing agent.
            agent_role: Agent's persona role.
            worktree_path: Path to agent's worktree.
            execute_fn: The node's execution function.
            context: Shared DAG context.

        Returns:
            AgentExecutionTrace with full step history.
        """
        trace = AgentExecutionTrace(
            agent_id=agent_id,
            node_id=node_id,
        )
        start = time.time()

        # Create sandbox and inject into context
        sandbox = self.get_or_create_sandbox(
            agent_id, agent_role, worktree_path,
        )
        context["_sandbox"] = sandbox
        context["_agent_id"] = agent_id
        context["_available_tools"] = self.tool_registry.list_tool_ids()

        # Publish start event
        self._publish_event("TOOL_BRIDGE_NODE_START", {
            "node_id": node_id,
            "agent_id": agent_id,
            "worktree": worktree_path,
        })

        try:
            # Execute the node function with sandbox in context
            result = execute_fn(context)
            trace.final_output = result
            trace.success = True
        except Exception as e:
            trace.success = False
            trace.error = str(e)
            logger.warning(
                f"ToolBridge: node '{node_id}' failed: {e}"
            )

        # Collect sandbox actions into trace steps
        for i, action in enumerate(sandbox.action_log):
            step = AgentStep(
                step_number=i + 1,
                action=action.get("tool_id", "unknown"),
                tool_id=action.get("tool_id", "unknown"),
                input_args={
                    k: v for k, v in action.items()
                    if k not in ("agent_id", "agent_role", "tool_id", "timestamp")
                },
                timestamp=action.get("timestamp", 0),
            )
            trace.steps.append(step)

        trace.total_tool_calls = len(trace.steps)
        trace.total_duration = time.time() - start

        # Publish completion event
        self._publish_event("TOOL_BRIDGE_NODE_COMPLETE", {
            "node_id": node_id,
            "agent_id": agent_id,
            "success": trace.success,
            "tool_calls": trace.total_tool_calls,
            "duration": trace.total_duration,
        })

        self._traces.append(trace)

        # Log to ALIGN
        self._log_align("TOOL_BRIDGE_EXECUTION", {
            "node_id": node_id,
            "agent_id": agent_id,
            "success": trace.success,
            "tool_calls": trace.total_tool_calls,
            "duration": trace.total_duration,
        })

        return trace

    def create_executor(self, dag: Any) -> Any:
        """Create a SpindleExecutor pre-wired with this tool bridge.

        The executor will automatically sandbox any node that has
        an agent_id assigned.

        Args:
            dag: A SpindleDAG to execute.

        Returns:
            SpindleExecutor with event_bus wired.
        """
        from agents.core.spindle import SpindleExecutor
        return SpindleExecutor(dag, event_bus=self._event_bus)

    @property
    def traces(self) -> list[AgentExecutionTrace]:
        """All execution traces collected by this bridge."""
        return list(self._traces)

    @property
    def sandbox_count(self) -> int:
        """Number of active sandboxes."""
        return len(self._sandboxes)

    def get_sandbox(self, agent_id: str) -> AgentSandbox | None:
        """Get a sandbox by agent_id."""
        return self._sandboxes.get(agent_id)

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def clear_traces(self) -> None:
        """Clear all accumulated execution traces.

        Useful between runs to prevent unbounded trace growth.
        """
        self._traces.clear()

    def clear_sandboxes(self) -> None:
        """Destroy all active sandboxes.

        Call between missions to release worktree resources.
        """
        self._sandboxes.clear()

    def summary(self) -> dict[str, Any]:
        """Return a lightweight observability snapshot of this bridge.

        Returns:
            Dict with sandbox_count, trace_count, total_tool_calls,
            total_duration, success_rate, and per-agent call counts.
            When no traces have been collected yet, success_rate is
            ``None`` (undefined — not 0.0 or 1.0 which would imply
            evidence of failures or successes).
        """
        if not self._traces:
            return {
                "sandbox_count": self.sandbox_count,
                "trace_count": 0,
                "total_tool_calls": 0,
                "total_duration": 0.0,
                "success_rate": None,
                "agents": {},
            }

        total_tool_calls = sum(t.total_tool_calls for t in self._traces)
        total_duration = sum(t.total_duration for t in self._traces)
        successes = sum(1 for t in self._traces if t.success)
        agents: dict[str, int] = {}
        for trace in self._traces:
            agents[trace.agent_id] = agents.get(trace.agent_id, 0) + trace.total_tool_calls

        return {
            "sandbox_count": self.sandbox_count,
            "trace_count": len(self._traces),
            "total_tool_calls": total_tool_calls,
            "total_duration": round(total_duration, 3),
            "success_rate": round(successes / len(self._traces), 3),
            "agents": agents,
        }

    def _publish_event(self, event_type: str, payload: dict) -> None:
        """Publish to event bus if available.

        Maps string event_type keys (via the module-level ``_BRIDGE_EVENT_MAP``)
        to the typed ``EventType`` enum so callers don't need to import the enum.
        """
        if self._event_bus is None:
            return
        try:
            from agents.core.event_bus import EventType, SpindleEvent

            # Resolve string key → EventType using the module-level map
            enum_name = _BRIDGE_EVENT_MAP.get(event_type, "NODE_COMPLETED")
            resolved: EventType = EventType[enum_name]

            self._event_bus.publish(SpindleEvent(
                event_type=resolved,
                source=f"tool_bridge:{payload.get('node_id', 'unknown')}",
                payload=payload,
            ))
        except ImportError:
            # Event bus integration is optional
            logger.debug("Event bus not available; skipping event publish")
        except Exception:
            logger.exception("Failed to publish event %s", event_type)

    def _log_align(self, event: str, data: dict) -> None:
        """Log to ALIGN ledger."""
        try:
            from agents.core.logger import ALSLogger
            als = ALSLogger()
            als.log(event, data)
        except ImportError:
            # ALIGN ledger is optional; skip logging if not installed
            logger.debug("ALSLogger not available; skipping ALIGN logging for %s", event)
