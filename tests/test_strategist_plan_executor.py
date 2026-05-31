"""
Tests for Strategist-level PlanExecutor additions.

Covers:
  - New PlanTaskType values (documentation, install_deps)
  - PlanDecompositionReport dataclass
  - PlanExecutor.analyze_decomposition() quality metrics
  - Explicit depends_on in task specs (arbitrary DAG patterns)
  - PlanExecutionResult includes decomposition_report
"""

from __future__ import annotations

import os
import tempfile

import pytest

from agents.core.agent_sandbox import AgentSandbox
from agents.core.plan_executor import (
    PlanDecompositionReport,
    PlanExecutor,
    PlanTaskType,
)
from agents.core.tool_registry import ToolRegistry


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_sandbox() -> tuple[AgentSandbox, str]:
    tmp = tempfile.mkdtemp(prefix="strategist_test_")
    registry = ToolRegistry()
    sandbox = AgentSandbox(
        agent_id="test-strategist",
        agent_role="developer",
        worktree_path=tmp,
        tool_registry=registry,
    )
    return sandbox, tmp


def _make_executor() -> PlanExecutor:
    return PlanExecutor()


# --------------------------------------------------------------------------- #
# New PlanTaskType values
# --------------------------------------------------------------------------- #


class TestNewTaskTypes:
    """DOCUMENTATION and INSTALL_DEPS route to code-agent."""

    def test_documentation_is_code_task(self) -> None:
        assert PlanTaskType("documentation").agent_type == "code-agent"

    def test_install_deps_is_code_task(self) -> None:
        assert PlanTaskType("install_deps").agent_type == "code-agent"

    def test_all_original_types_unchanged(self) -> None:
        for tt in ["create_file", "modify_file", "refactor", "delete_file"]:
            assert PlanTaskType(tt).agent_type == "code-agent"
        for tt in ["run_tests", "run_lint", "check_syntax", "validate_imports"]:
            assert PlanTaskType(tt).agent_type == "build-agent"


# --------------------------------------------------------------------------- #
# PlanDecompositionReport
# --------------------------------------------------------------------------- #


class TestPlanDecompositionReport:
    """PlanDecompositionReport dataclass defaults."""

    def test_defaults(self) -> None:
        r = PlanDecompositionReport(
            total_tasks=0,
            code_tasks=0,
            build_tasks=0,
            explicit_deps_count=0,
        )
        assert r.unknown_types == []
        assert r.tasks_without_description == []
        assert r.dag_summary == {}


# --------------------------------------------------------------------------- #
# analyze_decomposition
# --------------------------------------------------------------------------- #


class TestAnalyzeDecomposition:
    """PlanExecutor.analyze_decomposition() returns quality metrics."""

    def test_basic_counts(self) -> None:
        executor = _make_executor()
        report = executor.analyze_decomposition([
            {"type": "create_file", "path": "a.py", "content": ""},
            {"type": "modify_file", "path": "a.py", "content": ""},
            {"type": "run_tests", "paths": "."},
        ])
        assert report.total_tasks == 3
        assert report.code_tasks == 2
        assert report.build_tasks == 1

    def test_unknown_types_reported(self) -> None:
        executor = _make_executor()
        # analyze_decomposition itself doesn't raise on unknown types
        report = executor.analyze_decomposition([
            {"type": "create_file", "path": "a.py", "content": ""},
            {"type": "teleport"},  # unknown
        ])
        assert "teleport" in report.unknown_types
        assert report.total_tasks == 2  # both tasks counted despite one being invalid

    def test_tasks_without_description(self) -> None:
        executor = _make_executor()
        report = executor.analyze_decomposition([
            {"type": "create_file", "description": "Create main", "path": "a.py", "content": ""},
            {"type": "run_tests"},  # no description
        ])
        # First task has description, second does not
        assert any("run_tests" in nid for nid in report.tasks_without_description)
        assert all("create_file" not in nid for nid in report.tasks_without_description)

    def test_explicit_deps_counted(self) -> None:
        executor = _make_executor()
        report = executor.analyze_decomposition([
            {"type": "create_file", "path": "a.py", "content": ""},
            {"type": "create_file", "path": "b.py", "content": "", "depends_on": ["step-000-create_file"]},
        ])
        assert report.explicit_deps_count == 1

    def test_dag_summary_populated(self) -> None:
        executor = _make_executor()
        report = executor.analyze_decomposition([
            {"type": "create_file", "path": "a.py", "content": ""},
            {"type": "check_syntax", "path": "a.py"},
        ])
        assert "total_nodes" in report.dag_summary
        assert report.dag_summary["total_nodes"] == 2

    def test_empty_plan_returns_zeros(self) -> None:
        executor = _make_executor()
        report = executor.analyze_decomposition([])
        assert report.total_tasks == 0
        assert report.code_tasks == 0
        assert report.build_tasks == 0

    def test_all_code_no_build(self) -> None:
        executor = _make_executor()
        report = executor.analyze_decomposition([
            {"type": "create_file", "path": f"{i}.py", "content": ""}
            for i in range(5)
        ])
        assert report.build_tasks == 0
        assert report.code_tasks == 5


# --------------------------------------------------------------------------- #
# Explicit depends_on in task specs
# --------------------------------------------------------------------------- #


class TestExplicitDepsInTaskSpecs:
    """Tasks with explicit depends_on override implicit sequential wiring."""

    def test_explicit_dependency_creates_edge(self) -> None:
        executor = _make_executor()
        dag = executor.plan_to_dag([
            {"type": "create_file", "path": "a.py", "content": ""},  # step-000
            {"type": "create_file", "path": "b.py", "content": ""},  # step-001
            {
                "type": "create_file",
                "path": "c.py",
                "content": "",
                "depends_on": ["step-000-create_file"],  # skip step-001 dep
            },
        ])
        c_node = dag.get_node("step-002-create_file")
        # Explicit depends_on wins over implicit sequential chain
        assert c_node.depends_on == ["step-000-create_file"]

    def test_no_depends_on_falls_back_to_implicit(self) -> None:
        executor = _make_executor()
        dag = executor.plan_to_dag([
            {"type": "create_file", "path": "a.py", "content": ""},  # step-000
            {"type": "create_file", "path": "b.py", "content": ""},  # step-001
        ])
        b_node = dag.get_node("step-001-create_file")
        # Without explicit deps, implicit sequential chain applies
        assert b_node.depends_on == ["step-000-create_file"]

    def test_empty_depends_on_overrides_implicit(self) -> None:
        """A task with depends_on=[] declares independence even when implicit dep exists."""
        executor = _make_executor()
        dag = executor.plan_to_dag([
            {"type": "create_file", "path": "a.py", "content": ""},
            {"type": "create_file", "path": "b.py", "content": "", "depends_on": []},
        ])
        b_node = dag.get_node("step-001-create_file")
        # Explicit empty list → no dependencies
        assert b_node.depends_on == []

    def test_fan_out_diamond_pattern(self) -> None:
        """Two tasks fan out from one root, merge at a third."""
        executor = _make_executor()
        tasks = [
            {"type": "create_file", "path": "root.py", "content": ""},   # step-000
            {
                "type": "create_file", "path": "left.py", "content": "",
                "depends_on": ["step-000-create_file"],                   # step-001
            },
            {
                "type": "create_file", "path": "right.py", "content": "",
                "depends_on": ["step-000-create_file"],                   # step-002
            },
            {
                "type": "check_syntax", "path": "root.py",
                "depends_on": ["step-001-create_file", "step-002-create_file"],  # step-003
            },
        ]
        dag = executor.plan_to_dag(tasks)
        order = dag.topological_order()
        # root must be first, check_syntax must be last
        assert order[0] == "step-000-create_file"
        assert order[-1] == "step-003-check_syntax"
        # left and right are in the middle
        middle = set(order[1:3])
        assert middle == {"step-001-create_file", "step-002-create_file"}

    def test_node_metadata_includes_task_type(self) -> None:
        executor = _make_executor()
        dag = executor.plan_to_dag([
            {"type": "create_file", "path": "a.py", "content": "", "description": "Create root"},
        ])
        node = dag.get_node("step-000-create_file")
        assert node.metadata["task_type"] == "create_file"
        assert node.metadata["description"] == "Create root"

    def test_node_metadata_empty_description_when_missing(self) -> None:
        executor = _make_executor()
        dag = executor.plan_to_dag([
            {"type": "create_file", "path": "a.py", "content": ""},
        ])
        node = dag.get_node("step-000-create_file")
        assert node.metadata["description"] == ""


# --------------------------------------------------------------------------- #
# PlanExecutionResult.decomposition_report
# --------------------------------------------------------------------------- #


class TestExecutionResultDecompositionReport:
    """execute_plan attaches a PlanDecompositionReport to the result."""

    def test_report_present_on_success(self) -> None:
        sandbox, _ = _make_sandbox()
        executor = _make_executor()
        result = executor.execute_plan(
            [{"type": "create_file", "path": "app.py", "content": "x=1\n"}],
            sandbox=sandbox,
        )
        assert result.decomposition_report is not None
        assert result.decomposition_report.total_tasks == 1

    def test_report_present_on_failure(self) -> None:
        sandbox, _ = _make_sandbox()
        executor = _make_executor()
        result = executor.execute_plan(
            [
                {"type": "create_file", "path": "bad.py", "content": "def (\n"},
                {"type": "check_syntax", "path": "bad.py"},
            ],
            sandbox=sandbox,
        )
        # Execution may fail at syntax check
        assert result.decomposition_report is not None
        assert result.decomposition_report.total_tasks == 2

    def test_report_counts_are_correct(self) -> None:
        sandbox, _ = _make_sandbox()
        executor = _make_executor()
        result = executor.execute_plan(
            [
                {"type": "create_file", "path": "a.py", "content": "x=1\n"},
                {"type": "create_file", "path": "b.py", "content": "y=2\n"},
                {"type": "run_lint", "paths": "."},
            ],
            sandbox=sandbox,
        )
        report = result.decomposition_report
        assert report is not None
        assert report.code_tasks == 2
        assert report.build_tasks == 1
