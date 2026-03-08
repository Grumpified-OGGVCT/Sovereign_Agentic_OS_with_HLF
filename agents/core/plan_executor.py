"""
Plan Executor — Translates SDD Plans into Executable DAG Pipelines.

The "brain" of the agent system.  Takes an SDD session with a
spec and decomposes it into a topologically-ordered SpindleDAG
where each node is executed by either a CodeAgent or BuildAgent.

Flow::

    SDDSession (EXECUTE phase)
        ↓
    PlanExecutor.plan_to_dag()  → SpindleDAG
        ↓
    PlanExecutor.execute_plan() → PlanExecutionResult
        ↓  (per node)
    CodeAgent / BuildAgent via AgentSandbox

Usage::

    from agents.core.plan_executor import PlanExecutor

    executor = PlanExecutor()
    result = executor.execute_plan(sdd_session)
    print(result.success, result.files_modified)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from agents.core.agent_sandbox import AgentSandbox
from agents.core.build_agent import BuildAgent, BuildResult
from agents.core.code_agent import CodeAgent, TaskResult
from agents.core.spindle import NodeStatus, SpindleDAG, SpindleNode

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Plan Task Types
# --------------------------------------------------------------------------- #


class PlanTaskType(StrEnum):
    """Types of tasks recognized by the PlanExecutor.

    CODE tasks are dispatched to CodeAgent.
    BUILD tasks are dispatched to BuildAgent.
    """
    CREATE_FILE = "create_file"
    MODIFY_FILE = "modify_file"
    REFACTOR = "refactor"
    DELETE_FILE = "delete_file"
    RUN_TESTS = "run_tests"
    RUN_LINT = "run_lint"
    CHECK_SYNTAX = "check_syntax"
    VALIDATE_IMPORTS = "validate_imports"

    @property
    def agent_type(self) -> str:
        """Which agent handles this task type."""
        code_types = {
            PlanTaskType.CREATE_FILE,
            PlanTaskType.MODIFY_FILE,
            PlanTaskType.REFACTOR,
            PlanTaskType.DELETE_FILE,
        }
        return "code-agent" if self in code_types else "build-agent"


# --------------------------------------------------------------------------- #
# Plan Execution Result
# --------------------------------------------------------------------------- #


@dataclass
class PlanStep:
    """A single step in a plan execution trace.

    Attributes:
        node_id: SpindleDAG node ID.
        task_type: The type of task executed.
        agent_type: Which agent handled it.
        success: Whether the step succeeded.
        result: TaskResult or BuildResult from the agent.
        duration: Execution time in seconds.
    """
    node_id: str
    task_type: str
    agent_type: str
    success: bool
    result: TaskResult | BuildResult | None = None
    duration: float = 0.0


@dataclass
class PlanExecutionResult:
    """Result of executing a full plan.

    Attributes:
        success: Whether all steps completed successfully.
        steps: Ordered list of executed steps.
        files_modified: Aggregate list of all files changed.
        test_results: Aggregate test pass/fail from BuildAgent.
        total_duration: Total execution time.
        error: Error message if the plan failed.
    """
    success: bool
    steps: list[PlanStep] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    test_results: dict[str, int] = field(default_factory=dict)
    total_duration: float = 0.0
    error: str | None = None


# --------------------------------------------------------------------------- #
# Plan Executor
# --------------------------------------------------------------------------- #


class PlanExecutor:
    """Translates SDD specs into DAGs and executes them.

    The executor:
    1. Parses an SDD session's spec into ordered tasks.
    2. Builds a SpindleDAG with proper dependency edges.
    3. Executes each node through CodeAgent or BuildAgent.
    4. Collects results into a PlanExecutionResult.

    Attributes:
        code_agent: The CodeAgent for file operations.
        build_agent: The BuildAgent for verification tasks.
    """

    def __init__(
        self,
        code_agent: CodeAgent | None = None,
        build_agent: BuildAgent | None = None,
    ) -> None:
        self.code_agent = code_agent or CodeAgent()
        self.build_agent = build_agent or BuildAgent()
        self._task_registry: dict[str, dict[str, Any]] = {}

    def plan_to_dag(self, tasks: list[dict[str, Any]]) -> SpindleDAG:
        # Clear registry between plans (Copilot #1)
        self._task_registry.clear()
        """Convert a list of task specs into a SpindleDAG.

        Each task dict must have a 'type' field matching a PlanTaskType.
        Tasks are added as nodes.  Code tasks are linked sequentially,
        then all build tasks depend on the last code task.

        Args:
            tasks: List of task specifications.

        Returns:
            A SpindleDAG with nodes and dependency edges.

        Raises:
            ValueError: If tasks list is empty.
        """
        if not tasks:
            raise ValueError("Cannot create DAG from empty task list")

        dag = SpindleDAG()
        code_node_ids: list[str] = []
        build_node_ids: list[str] = []

        # First pass: categorize tasks
        task_entries: list[tuple[str, str, dict[str, Any]]] = []
        for i, task in enumerate(tasks):
            task_type = task.get("type", "unknown")
            node_id = f"step-{i:03d}-{task_type}"
            try:
                pt = PlanTaskType(task_type)
                agent_id = pt.agent_type
            except ValueError:
                raise ValueError(
                    f"Unknown task type: '{task_type}'. "
                    f"Valid types: {[t.value for t in PlanTaskType]}"
                )

            task_entries.append((node_id, agent_id, task))
            if agent_id == "code-agent":
                code_node_ids.append(node_id)
            else:
                build_node_ids.append(node_id)

        # Precompute previous code-node mapping to avoid O(n²) lookups (Copilot #8)
        code_prev_map: dict[str, str] = {}
        prev_code_id: str | None = None
        for code_id in code_node_ids:
            if prev_code_id is not None:
                code_prev_map[code_id] = prev_code_id
            prev_code_id = code_id

        # Second pass: create nodes with depends_on
        for node_id, agent_id, task in task_entries:
            deps: list[str] = []
            if agent_id == "code-agent":
                prev_dep = code_prev_map.get(node_id)
                if prev_dep is not None:
                    deps.append(prev_dep)
            elif agent_id == "build-agent" and code_node_ids:
                deps.append(code_node_ids[-1])

            node = SpindleNode(
                node_id=node_id,
                agent_id=agent_id,
                depends_on=deps,
            )
            dag.add_node(node)
            self._task_registry[node_id] = task

        return dag

    def execute_plan(
        self,
        tasks: list[dict[str, Any]],
        sandbox: AgentSandbox,
    ) -> PlanExecutionResult:
        """Execute a complete plan.

        Builds a DAG, executes nodes in dependency order,
        stops on first failure (fail-fast).

        Args:
            tasks: List of task specifications.
            sandbox: AgentSandbox for all file/terminal operations.

        Returns:
            PlanExecutionResult with per-step traces.
        """
        start = time.time()

        if not tasks:
            return PlanExecutionResult(
                success=False,
                error="No tasks to execute",
                total_duration=time.time() - start,
            )

        try:
            dag = self.plan_to_dag(tasks)
        except ValueError as e:
            return PlanExecutionResult(
                success=False,
                error=str(e),
                total_duration=time.time() - start,
            )

        steps: list[PlanStep] = []
        all_files_modified: list[str] = []
        total_passed = 0
        total_failed = 0
        total_errors = 0
        overall_success = True

        # Execute in dependency order
        execution_order = dag.topological_order()

        for node_id in execution_order:
            node = dag.nodes[node_id]
            task = self._task_registry.get(node_id, {})
            task_type = task.get("type", "unknown")
            agent_type = node.agent_id or "unknown"

            step_start = time.time()
            node.status = NodeStatus.RUNNING

            try:
                if agent_type == "code-agent":
                    result = self.code_agent.execute_task(task, sandbox)
                    step = PlanStep(
                        node_id=node_id,
                        task_type=task_type,
                        agent_type=agent_type,
                        success=result.success,
                        result=result,
                        duration=time.time() - step_start,
                    )
                    if result.success:
                        all_files_modified.extend(result.files_modified)
                    else:
                        overall_success = False
                elif agent_type == "build-agent":
                    result = self.build_agent.execute_task(task, sandbox)
                    step = PlanStep(
                        node_id=node_id,
                        task_type=task_type,
                        agent_type=agent_type,
                        success=result.success,
                        result=result,
                        duration=time.time() - step_start,
                    )
                    total_passed += result.passed
                    total_failed += result.failed
                    total_errors += result.errors
                    if not result.success:
                        overall_success = False
                else:
                    step = PlanStep(
                        node_id=node_id,
                        task_type=task_type,
                        agent_type=agent_type,
                        success=False,
                        duration=time.time() - step_start,
                    )
                    overall_success = False

                node.status = (
                    NodeStatus.COMPLETED if step.success
                    else NodeStatus.FAILED
                )

            except Exception as e:
                step = PlanStep(
                    node_id=node_id,
                    task_type=task_type,
                    agent_type=agent_type,
                    success=False,
                    duration=time.time() - step_start,
                )
                node.status = NodeStatus.FAILED
                overall_success = False
                logger.exception(
                    "Plan step %s failed: %s", node_id, e,
                )

            steps.append(step)

            # Fail-fast: stop on first failure (Copilot #10: propagate error)
            if not step.success:
                failing_error = None
                if step.result and hasattr(step.result, 'error'):
                    failing_error = step.result.error
                break

        self._log_align("PLAN_EXECUTION", {
            "success": overall_success,
            "steps_completed": len(steps),
            "steps_total": len(execution_order),
            "files_modified": all_files_modified,
            "total_duration": time.time() - start,
        })

        # Determine error for result (Copilot #9 + #10)
        result_error = None
        if not overall_success:
            for s in reversed(steps):
                if not s.success and s.result and hasattr(s.result, 'error'):
                    result_error = s.result.error
                    break

        return PlanExecutionResult(
            success=overall_success,
            steps=steps,
            files_modified=list(dict.fromkeys(all_files_modified)),
            test_results={
                "passed": total_passed,
                "failed": total_failed,
                "errors": total_errors,
            },
            total_duration=time.time() - start,
            error=result_error,
        )

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _log_align(self, event: str, data: dict) -> None:
        """Log to ALIGN ledger."""
        try:
            from agents.core.logger import ALSLogger
            als = ALSLogger()
            als.log(event, data)
        except ImportError:
            # ALIGN ledger is optional; skip logging if not installed
            logger.debug(
                "ALSLogger not available; skipping ALIGN logging for %s",
                event,
            )
