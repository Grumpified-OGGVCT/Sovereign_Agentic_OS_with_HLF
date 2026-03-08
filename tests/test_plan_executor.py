"""
Tests for PlanExecutor — DAG construction, execution, and integration.

Validates that PlanExecutor correctly:
  - Converts task lists into SpindleDAGs
  - Wires dependency edges correctly
  - Routes tasks to CodeAgent / BuildAgent
  - Provides structured PlanExecutionResult
"""

from __future__ import annotations

import os
import tempfile

import pytest

from agents.core.agent_sandbox import AgentSandbox
from agents.core.plan_executor import (
    PlanExecutor,
    PlanTaskType,
)
from agents.core.tool_registry import ToolRegistry

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_sandbox() -> tuple[AgentSandbox, str]:
    """Create a temporary sandbox for testing."""
    tmp = tempfile.mkdtemp(prefix="plan_exec_test_")
    registry = ToolRegistry()
    sandbox = AgentSandbox(
        agent_id="test-plan-executor",
        agent_role="developer",
        worktree_path=tmp,
        tool_registry=registry,
    )
    return sandbox, tmp


def _make_executor() -> PlanExecutor:
    """Create a default PlanExecutor."""
    return PlanExecutor()


# --------------------------------------------------------------------------- #
# PlanTaskType
# --------------------------------------------------------------------------- #


class TestPlanTaskType:
    """PlanTaskType enum and agent_type property."""

    def test_code_task_types(self) -> None:
        for tt in ["create_file", "modify_file", "refactor", "delete_file"]:
            assert PlanTaskType(tt).agent_type == "code-agent"

    def test_build_task_types(self) -> None:
        for tt in ["run_tests", "run_lint", "check_syntax", "validate_imports"]:
            assert PlanTaskType(tt).agent_type == "build-agent"


# --------------------------------------------------------------------------- #
# plan_to_dag
# --------------------------------------------------------------------------- #


class TestPlanToDAG:
    """PlanExecutor.plan_to_dag() DAG construction."""

    def test_single_task(self) -> None:
        executor = _make_executor()
        dag = executor.plan_to_dag([
            {"type": "create_file", "path": "foo.py", "content": "# foo"},
        ])
        assert len(dag.nodes) == 1
        node = list(dag.nodes.values())[0]
        assert node.agent_id == "code-agent"

    def test_multiple_code_tasks_sequential(self) -> None:
        executor = _make_executor()
        dag = executor.plan_to_dag([
            {"type": "create_file", "path": "a.py", "content": "1"},
            {"type": "create_file", "path": "b.py", "content": "2"},
            {"type": "modify_file", "path": "a.py", "content": "updated"},
        ])
        assert len(dag.nodes) == 3
        # Should have sequential edges
        order = dag.topological_order()
        assert len(order) == 3
        assert "step-000" in order[0]
        assert "step-001" in order[1]
        assert "step-002" in order[2]

    def test_build_task_depends_on_last_code(self) -> None:
        executor = _make_executor()
        dag = executor.plan_to_dag([
            {"type": "create_file", "path": "a.py", "content": "x"},
            {"type": "create_file", "path": "b.py", "content": "y"},
            {"type": "check_syntax", "path": "a.py"},
        ])
        assert len(dag.nodes) == 3
        # Build task should come after code tasks
        order = dag.topological_order()
        code_positions = [
            i for i, nid in enumerate(order) if "create_file" in nid
        ]
        build_positions = [
            i for i, nid in enumerate(order) if "check_syntax" in nid
        ]
        assert max(code_positions) < min(build_positions)

    def test_empty_tasks_raises(self) -> None:
        executor = _make_executor()
        with pytest.raises(ValueError, match="empty"):
            executor.plan_to_dag([])

    def test_mixed_code_and_build(self) -> None:
        executor = _make_executor()
        dag = executor.plan_to_dag([
            {"type": "create_file", "path": "main.py", "content": "# main"},
            {"type": "run_lint", "paths": "."},
            {"type": "check_syntax", "path": "main.py"},
        ])
        assert len(dag.nodes) == 3
        order = dag.topological_order()
        # Code should come before builds
        assert "create_file" in order[0]


# --------------------------------------------------------------------------- #
# execute_plan
# --------------------------------------------------------------------------- #


class TestExecutePlan:
    """PlanExecutor.execute_plan() end-to-end."""

    def test_single_create_file(self) -> None:
        sandbox, tmp = _make_sandbox()
        executor = _make_executor()
        result = executor.execute_plan(
            [{"type": "create_file", "path": "app.py", "content": "print(1)"}],
            sandbox=sandbox,
        )
        assert result.success
        assert len(result.steps) == 1
        assert "app.py" in result.files_modified
        assert os.path.exists(os.path.join(tmp, "app.py"))

    def test_create_then_check_syntax(self) -> None:
        sandbox, tmp = _make_sandbox()
        executor = _make_executor()
        result = executor.execute_plan(
            [
                {"type": "create_file", "path": "valid.py", "content": "x = 1\n"},
                {"type": "check_syntax", "path": "valid.py"},
            ],
            sandbox=sandbox,
        )
        assert result.success
        assert len(result.steps) == 2
        # Both steps should succeed
        assert all(s.success for s in result.steps)

    def test_create_then_syntax_check_fails_on_bad_code(self) -> None:
        sandbox, tmp = _make_sandbox()
        executor = _make_executor()
        result = executor.execute_plan(
            [
                {"type": "create_file", "path": "bad.py", "content": "def (\n"},
                {"type": "check_syntax", "path": "bad.py"},
            ],
            sandbox=sandbox,
        )
        assert not result.success
        # File created OK but syntax check should fail
        assert result.steps[0].success is True
        assert result.steps[1].success is False

    def test_fail_fast_stops_execution(self) -> None:
        sandbox, _ = _make_sandbox()
        executor = _make_executor()
        # First task will fail (missing path), should not execute second
        result = executor.execute_plan(
            [
                {"type": "create_file"},  # missing path = fail
                {"type": "create_file", "path": "b.py", "content": "x"},
            ],
            sandbox=sandbox,
        )
        assert not result.success
        assert len(result.steps) == 1  # Stopped after first failure

    def test_empty_plan_fails(self) -> None:
        sandbox, _ = _make_sandbox()
        executor = _make_executor()
        result = executor.execute_plan([], sandbox=sandbox)
        assert not result.success
        assert "No tasks" in result.error

    def test_multiple_file_creation_pipeline(self) -> None:
        sandbox, tmp = _make_sandbox()
        executor = _make_executor()
        result = executor.execute_plan(
            [
                {"type": "create_file", "path": "src/a.py", "content": "# a\n"},
                {"type": "create_file", "path": "src/b.py", "content": "# b\n"},
                {"type": "create_file", "path": "src/c.py", "content": "# c\n"},
            ],
            sandbox=sandbox,
        )
        assert result.success
        assert len(result.files_modified) == 3
        for name in ["a.py", "b.py", "c.py"]:
            assert os.path.exists(os.path.join(tmp, "src", name))


# --------------------------------------------------------------------------- #
# Full Pipeline Integration
# --------------------------------------------------------------------------- #


class TestFullPipeline:
    """End-to-end: create files → modify → verify."""

    def test_create_modify_verify_pipeline(self) -> None:
        sandbox, tmp = _make_sandbox()
        executor = _make_executor()

        result = executor.execute_plan(
            [
                {
                    "type": "create_file",
                    "path": "calc.py",
                    "content": "def add(a, b):\n    return a + b\n",
                },
                {
                    "type": "modify_file",
                    "path": "calc.py",
                    "changes": [
                        {
                            "find": "def add(a, b):",
                            "replace": "def add(a: int, b: int) -> int:",
                        },
                    ],
                },
                {"type": "check_syntax", "path": "calc.py"},
            ],
            sandbox=sandbox,
        )

        assert result.success
        assert len(result.steps) == 3

        # Verify final file content
        with open(os.path.join(tmp, "calc.py")) as f:
            content = f.read()
        assert "def add(a: int, b: int) -> int:" in content

    def test_plan_execution_result_has_duration(self) -> None:
        sandbox, _ = _make_sandbox()
        executor = _make_executor()
        result = executor.execute_plan(
            [{"type": "create_file", "path": "t.py", "content": "#"}],
            sandbox=sandbox,
        )
        assert result.total_duration > 0
        assert result.steps[0].duration > 0
