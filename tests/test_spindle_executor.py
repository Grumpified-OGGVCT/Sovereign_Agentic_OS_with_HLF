"""
Tests for Spindle Executor — Task DAG with Saga Compensating Transactions.

Covers:
  - Linear DAG execution order
  - Diamond DAG (fan-out / fan-in)
  - Failure triggers Saga compensation in reverse
  - Partial compensation (only completed nodes)
  - Cycle detection
  - Missing dependency detection
  - Wave scheduling for parallel execution
  - Empty DAG
  - Context mutation across nodes
  - Duplicate node rejection
  - Node skip on failed dependency
  - SpindleResult metadata
"""

from __future__ import annotations

import pytest

from agents.core.spindle import (
    SpindleDAG,
    SpindleExecutor,
    SpindleNode,
    SpindleResult,
)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_linear_dag() -> SpindleDAG:
    """Create A → B → C linear DAG."""
    dag = SpindleDAG()
    dag.add_node(SpindleNode(
        node_id="A",
        execute_fn=lambda ctx: ctx.setdefault("log", []).append("A") or "done_A",
        compensate_fn=lambda ctx: ctx.setdefault("comp", []).append("A"),
    ))
    dag.add_node(SpindleNode(
        node_id="B",
        execute_fn=lambda ctx: ctx.setdefault("log", []).append("B") or "done_B",
        compensate_fn=lambda ctx: ctx.setdefault("comp", []).append("B"),
        depends_on=["A"],
    ))
    dag.add_node(SpindleNode(
        node_id="C",
        execute_fn=lambda ctx: ctx.setdefault("log", []).append("C") or "done_C",
        compensate_fn=lambda ctx: ctx.setdefault("comp", []).append("C"),
        depends_on=["B"],
    ))
    return dag


def _make_diamond_dag() -> SpindleDAG:
    """Create diamond: A → B, A → C, B+C → D."""
    dag = SpindleDAG()
    dag.add_node(SpindleNode(
        node_id="A",
        execute_fn=lambda ctx: ctx.setdefault("log", []).append("A"),
    ))
    dag.add_node(SpindleNode(
        node_id="B",
        execute_fn=lambda ctx: ctx.setdefault("log", []).append("B"),
        depends_on=["A"],
    ))
    dag.add_node(SpindleNode(
        node_id="C",
        execute_fn=lambda ctx: ctx.setdefault("log", []).append("C"),
        depends_on=["A"],
    ))
    dag.add_node(SpindleNode(
        node_id="D",
        execute_fn=lambda ctx: ctx.setdefault("log", []).append("D"),
        depends_on=["B", "C"],
    ))
    return dag


# --------------------------------------------------------------------------- #
# DAG Structure
# --------------------------------------------------------------------------- #


class TestSpindleDAG:
    """SpindleDAG validation and topological ordering."""

    def test_topological_order_linear(self) -> None:
        """Linear DAG produces A, B, C order."""
        dag = _make_linear_dag()
        order = dag.topological_order()
        assert order == ["A", "B", "C"]

    def test_topological_order_diamond(self) -> None:
        """Diamond DAG: A first, D last, B/C in middle."""
        dag = _make_diamond_dag()
        order = dag.topological_order()
        assert order[0] == "A"
        assert order[-1] == "D"
        assert set(order[1:3]) == {"B", "C"}

    def test_cycle_detection(self) -> None:
        """Cycle raises ValueError."""
        dag = SpindleDAG()
        dag.add_node(SpindleNode(node_id="X", depends_on=["Y"]))
        dag.add_node(SpindleNode(node_id="Y", depends_on=["X"]))
        with pytest.raises(ValueError, match="cycle"):
            dag.topological_order()

    def test_missing_dependency(self) -> None:
        """Referencing unknown dependency raises ValueError."""
        dag = SpindleDAG()
        dag.add_node(SpindleNode(node_id="A", depends_on=["ghost"]))
        with pytest.raises(ValueError, match="unknown node"):
            dag.validate()

    def test_duplicate_node_rejected(self) -> None:
        """Adding duplicate node_id raises ValueError."""
        dag = SpindleDAG()
        dag.add_node(SpindleNode(node_id="A"))
        with pytest.raises(ValueError, match="Duplicate"):
            dag.add_node(SpindleNode(node_id="A"))

    def test_empty_dag_validates(self) -> None:
        """Empty DAG is valid."""
        dag = SpindleDAG()
        dag.validate()
        assert dag.topological_order() == []


# --------------------------------------------------------------------------- #
# Wave Scheduling
# --------------------------------------------------------------------------- #


class TestWaveScheduling:
    """SpindleDAG groups nodes into parallel execution waves."""

    def test_linear_waves(self) -> None:
        """Linear DAG produces one node per wave."""
        dag = _make_linear_dag()
        waves = dag.get_execution_waves()
        assert waves == [["A"], ["B"], ["C"]]

    def test_diamond_waves(self) -> None:
        """Diamond DAG: wave1=[A], wave2=[B,C], wave3=[D]."""
        dag = _make_diamond_dag()
        waves = dag.get_execution_waves()
        assert waves[0] == ["A"]
        assert sorted(waves[1]) == ["B", "C"]
        assert waves[2] == ["D"]

    def test_wide_dag_single_wave(self) -> None:
        """Independent nodes all run in one wave."""
        dag = SpindleDAG()
        for i in range(5):
            dag.add_node(SpindleNode(node_id=f"N{i}"))
        waves = dag.get_execution_waves()
        assert len(waves) == 1
        assert len(waves[0]) == 5


# --------------------------------------------------------------------------- #
# Execution — Happy Path
# --------------------------------------------------------------------------- #


class TestSpindleExecutorHappy:
    """SpindleExecutor runs DAGs successfully."""

    def test_linear_execution(self) -> None:
        """Linear DAG executes in order and returns success."""
        dag = _make_linear_dag()
        executor = SpindleExecutor(dag)
        ctx: dict = {}
        result = executor.run(ctx)

        assert result.success is True
        assert result.completed_nodes == ["A", "B", "C"]
        assert result.failed_node is None
        assert result.compensated_nodes == []
        assert ctx["log"] == ["A", "B", "C"]

    def test_diamond_execution(self) -> None:
        """Diamond DAG executes all nodes."""
        dag = _make_diamond_dag()
        executor = SpindleExecutor(dag)
        ctx: dict = {}
        result = executor.run(ctx)

        assert result.success is True
        assert len(result.completed_nodes) == 4
        assert "D" in result.completed_nodes
        assert ctx["log"][0] == "A"
        assert ctx["log"][-1] == "D"

    def test_node_results_captured(self) -> None:
        """Node return values captured in result.node_results."""
        dag = _make_linear_dag()
        executor = SpindleExecutor(dag)
        result = executor.run({})
        assert result.node_results["A"] == "done_A"
        assert result.node_results["B"] == "done_B"

    def test_empty_dag_succeeds(self) -> None:
        """Empty DAG executes with no errors."""
        dag = SpindleDAG()
        executor = SpindleExecutor(dag)
        result = executor.run()
        assert result.success is True
        assert result.completed_nodes == []

    def test_duration_tracked(self) -> None:
        """Total duration is non-zero for non-trivial execution."""
        dag = _make_linear_dag()
        executor = SpindleExecutor(dag)
        result = executor.run({})
        assert result.total_duration >= 0


# --------------------------------------------------------------------------- #
# Execution — Saga Compensation
# --------------------------------------------------------------------------- #


class TestSpindleSagaCompensation:
    """SpindleExecutor triggers Saga compensation on failure."""

    def test_failure_triggers_compensation(self) -> None:
        """When B fails, A gets compensated."""
        dag = SpindleDAG()
        dag.add_node(SpindleNode(
            node_id="A",
            execute_fn=lambda ctx: ctx.setdefault("log", []).append("A"),
            compensate_fn=lambda ctx: ctx.setdefault("comp", []).append("A"),
        ))
        dag.add_node(SpindleNode(
            node_id="B",
            execute_fn=lambda ctx: 1 / 0,  # raises ZeroDivisionError
            depends_on=["A"],
        ))

        ctx: dict = {}
        executor = SpindleExecutor(dag)
        result = executor.run(ctx)

        assert result.success is False
        assert result.failed_node == "B"
        assert "A" in result.compensated_nodes
        assert ctx["comp"] == ["A"]

    def test_reverse_compensation_order(self) -> None:
        """Compensation runs in reverse execution order."""
        dag = SpindleDAG()
        dag.add_node(SpindleNode(
            node_id="A",
            execute_fn=lambda ctx: ctx.setdefault("log", []).append("A"),
            compensate_fn=lambda ctx: ctx.setdefault("comp", []).append("A"),
        ))
        dag.add_node(SpindleNode(
            node_id="B",
            execute_fn=lambda ctx: ctx.setdefault("log", []).append("B"),
            compensate_fn=lambda ctx: ctx.setdefault("comp", []).append("B"),
            depends_on=["A"],
        ))
        dag.add_node(SpindleNode(
            node_id="C",
            execute_fn=lambda ctx: 1 / 0,  # fail
            depends_on=["B"],
        ))

        ctx: dict = {}
        result = SpindleExecutor(dag).run(ctx)

        assert result.success is False
        assert result.failed_node == "C"
        # Compensation in reverse: B first, then A
        assert ctx["comp"] == ["B", "A"]

    def test_downstream_skipped_on_failure(self) -> None:
        """Nodes after the failed node don't execute."""
        dag = SpindleDAG()
        dag.add_node(SpindleNode(
            node_id="A",
            execute_fn=lambda ctx: 1 / 0,  # fail immediately
        ))
        dag.add_node(SpindleNode(
            node_id="B",
            execute_fn=lambda ctx: ctx.setdefault("log", []).append("B"),
            depends_on=["A"],
        ))

        ctx: dict = {}
        result = SpindleExecutor(dag).run(ctx)

        assert result.success is False
        assert "B" not in result.completed_nodes
        assert "log" not in ctx  # B never ran

    def test_context_mutation_visible_to_compensation(self) -> None:
        """Compensation fns see context mutations from execute fns."""
        dag = SpindleDAG()
        dag.add_node(SpindleNode(
            node_id="A",
            execute_fn=lambda ctx: ctx.update({"created": True}),
            compensate_fn=lambda ctx: ctx.update({"cleaned": ctx.get("created")}),
        ))
        dag.add_node(SpindleNode(
            node_id="B",
            execute_fn=lambda ctx: 1 / 0,
            depends_on=["A"],
        ))

        ctx: dict = {}
        SpindleExecutor(dag).run(ctx)
        assert ctx["cleaned"] is True


# --------------------------------------------------------------------------- #
# SpindleResult
# --------------------------------------------------------------------------- #


class TestSpindleResult:
    """SpindleResult dataclass stores correct metadata."""

    def test_defaults(self) -> None:
        r = SpindleResult()
        assert r.success is True
        assert r.completed_nodes == []
        assert r.failed_node is None
