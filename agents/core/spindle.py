"""
Spindle Executor — Task DAG with Saga Compensating Transactions.

Executes directed acyclic graphs of tasks with automatic rollback.
If any node fails, all previously completed nodes run their compensation
functions in reverse order (Saga pattern).

Usage::

    from agents.core.spindle import SpindleNode, SpindleDAG, SpindleExecutor

    dag = SpindleDAG()
    dag.add_node(SpindleNode(
        node_id="parse",
        execute_fn=lambda ctx: parse_spec(ctx),
        compensate_fn=lambda ctx: cleanup_parse(ctx),
    ))
    dag.add_node(SpindleNode(
        node_id="validate",
        execute_fn=lambda ctx: validate_spec(ctx),
        compensate_fn=lambda ctx: revert_validation(ctx),
        depends_on=["parse"],
    ))

    executor = SpindleExecutor(dag)
    result = executor.run(context={"topic": "auth upgrade"})

All operations log to the ALIGN Ledger for forensic traceability.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class NodeStatus(Enum):
    """Execution status of a Spindle DAG node."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    COMPENSATED = "compensated"
    SKIPPED = "skipped"


@dataclass
class SpindleNode:
    """A single task node in the Spindle DAG.

    Attributes:
        node_id: Unique identifier for this node
        execute_fn: Callable(context) -> result. The main task logic.
        compensate_fn: Callable(context) -> None. Undo logic if downstream fails.
        depends_on: List of node_ids that must complete before this node runs.
        agent_id: Optional agent assignment for worktree isolation.
        status: Current execution status.
        result: Return value from execute_fn.
        error: Exception if execution failed.
        duration: Execution time in seconds.
    """
    node_id: str
    execute_fn: Callable[[dict[str, Any]], Any] | None = None
    compensate_fn: Callable[[dict[str, Any]], None] | None = None
    depends_on: list[str] = field(default_factory=list)
    agent_id: str | None = None
    status: NodeStatus = NodeStatus.PENDING
    result: Any = None
    error: Exception | None = None
    duration: float = 0.0


@dataclass
class SpindleResult:
    """Result of a full DAG execution.

    Attributes:
        success: Whether all nodes completed without failure.
        completed_nodes: List of node_ids that completed successfully.
        failed_node: The node_id that failed (if any).
        compensated_nodes: List of node_ids whose compensation ran.
        execution_order: Order in which nodes were executed.
        total_duration: Total wall-clock time for the execution.
        node_results: Map of node_id -> result for completed nodes.
    """
    success: bool = True
    completed_nodes: list[str] = field(default_factory=list)
    failed_node: str | None = None
    compensated_nodes: list[str] = field(default_factory=list)
    execution_order: list[str] = field(default_factory=list)
    total_duration: float = 0.0
    node_results: dict[str, Any] = field(default_factory=dict)


class SpindleDAG:
    """Directed Acyclic Graph of task nodes.

    Nodes are connected by dependency edges. The DAG validates
    that no cycles exist and that all dependencies are satisfiable.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, SpindleNode] = {}

    def add_node(self, node: SpindleNode) -> None:
        """Add a node to the DAG.

        Raises:
            ValueError: If node_id already exists or would create a cycle.
        """
        if node.node_id in self._nodes:
            raise ValueError(f"Duplicate node_id: {node.node_id}")
        self._nodes[node.node_id] = node

    def get_node(self, node_id: str) -> SpindleNode:
        """Get a node by ID.

        Raises:
            KeyError: If node doesn't exist.
        """
        return self._nodes[node_id]

    @property
    def nodes(self) -> dict[str, SpindleNode]:
        """All nodes in the DAG."""
        return dict(self._nodes)

    def validate(self) -> None:
        """Validate the DAG structure.

        Raises:
            ValueError: If dependencies reference missing nodes or cycles exist.
        """
        # Check all dependencies exist
        for node in self._nodes.values():
            for dep_id in node.depends_on:
                if dep_id not in self._nodes:
                    raise ValueError(
                        f"Node '{node.node_id}' depends on unknown node '{dep_id}'"
                    )

        # Cycle detection via topological sort
        self.topological_order()

    def topological_order(self) -> list[str]:
        """Return nodes in topological execution order.

        Raises:
            ValueError: If the graph contains a cycle.
        """
        in_degree: dict[str, int] = {nid: 0 for nid in self._nodes}
        for node in self._nodes.values():
            for _ in node.depends_on:
                # dep_id must run before node — so node has in-degree from dep
                in_degree[node.node_id] += 1

        # Start with nodes that have no dependencies
        queue: deque[str] = deque(
            nid for nid, deg in in_degree.items() if deg == 0
        )
        order: list[str] = []

        while queue:
            nid = queue.popleft()
            order.append(nid)

            # Reduce in-degree for dependents
            for node in self._nodes.values():
                if nid in node.depends_on:
                    in_degree[node.node_id] -= 1
                    if in_degree[node.node_id] == 0:
                        queue.append(node.node_id)

        if len(order) != len(self._nodes):
            raise ValueError("DAG contains a cycle — cannot determine execution order")

        return order

    def get_execution_waves(self) -> list[list[str]]:
        """Group nodes into parallel execution waves.

        Nodes in the same wave have no dependencies on each other and
        can run simultaneously. Waves execute sequentially.

        Returns:
            List of waves, where each wave is a list of node_ids.
        """
        completed: set[str] = set()
        waves: list[list[str]] = []

        remaining = set(self._nodes.keys())
        while remaining:
            # Find all nodes whose dependencies are fully satisfied
            wave = [
                nid for nid in remaining
                if all(dep in completed for dep in self._nodes[nid].depends_on)
            ]
            if not wave:
                raise ValueError("DAG contains a cycle — cannot form waves")

            waves.append(sorted(wave))  # Sort for deterministic ordering
            completed.update(wave)
            remaining -= set(wave)

        return waves


class SpindleExecutor:
    """Executes a SpindleDAG with Saga compensation on failure.

    Nodes run in topological order. If any node's execute_fn raises
    an exception, all previously completed nodes run their compensate_fn
    in reverse order.
    """

    def __init__(self, dag: SpindleDAG) -> None:
        self.dag = dag

    def run(self, context: dict[str, Any] | None = None) -> SpindleResult:
        """Execute the full DAG with Saga compensation.

        Args:
            context: Shared mutable context dict passed to all execute/compensate fns.

        Returns:
            SpindleResult with execution details.
        """
        if context is None:
            context = {}

        self.dag.validate()

        result = SpindleResult()
        start_time = time.time()

        # Get execution order
        order = self.dag.topological_order()

        for node_id in order:
            node = self.dag.get_node(node_id)

            # Check if all dependencies completed
            deps_satisfied = all(
                self.dag.get_node(dep).status == NodeStatus.COMPLETED
                for dep in node.depends_on
            )
            if not deps_satisfied:
                node.status = NodeStatus.SKIPPED
                continue

            # Execute the node
            node.status = NodeStatus.RUNNING
            node_start = time.time()

            self._log_align("SPINDLE_NODE_START", {
                "node_id": node_id,
                "agent_id": node.agent_id or "unassigned",
            })

            try:
                if node.execute_fn:
                    node.result = node.execute_fn(context)
                node.status = NodeStatus.COMPLETED
                node.duration = time.time() - node_start

                result.completed_nodes.append(node_id)
                result.execution_order.append(node_id)
                result.node_results[node_id] = node.result

                self._log_align("SPINDLE_NODE_COMPLETE", {
                    "node_id": node_id,
                    "duration": node.duration,
                })

            except Exception as e:
                node.status = NodeStatus.FAILED
                node.error = e
                node.duration = time.time() - node_start

                result.success = False
                result.failed_node = node_id
                result.execution_order.append(node_id)

                self._log_align("SPINDLE_NODE_FAILED", {
                    "node_id": node_id,
                    "error": str(e),
                })

                logger.warning(f"Spindle: node '{node_id}' failed: {e}")

                # Saga compensation — unwind in reverse order
                self._compensate(context, result)
                break

        result.total_duration = time.time() - start_time
        return result

    def _compensate(
        self,
        context: dict[str, Any],
        result: SpindleResult,
    ) -> None:
        """Run compensation for all completed nodes in reverse order."""
        for node_id in reversed(result.completed_nodes):
            node = self.dag.get_node(node_id)
            if node.compensate_fn:
                try:
                    node.compensate_fn(context)
                    node.status = NodeStatus.COMPENSATED
                    result.compensated_nodes.append(node_id)

                    self._log_align("SPINDLE_NODE_COMPENSATED", {
                        "node_id": node_id,
                    })

                    logger.info(f"Spindle: compensated node '{node_id}'")
                except Exception as comp_err:
                    # Compensation failure is critical — log but continue
                    logger.error(
                        f"Spindle: compensation FAILED for '{node_id}': {comp_err}"
                    )
                    self._log_align("SPINDLE_COMPENSATION_FAILED", {
                        "node_id": node_id,
                        "error": str(comp_err),
                    })

    def _log_align(self, event: str, data: dict) -> None:
        """Log an event to the ALIGN ledger."""
        try:
            from agents.core.als_logger import ALSLogger
            als = ALSLogger()
            als.log(event, data)
        except ImportError:
            pass  # Standalone mode
