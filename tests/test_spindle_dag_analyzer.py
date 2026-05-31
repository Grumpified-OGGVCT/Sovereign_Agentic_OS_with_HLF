"""
Tests for SpindleDAGAnalyzer — Strategist architecture-review lens.

Covers:
  - root_nodes / leaf_nodes identification
  - node_depths for linear, diamond, and wide DAGs
  - critical_path detection
  - parallelism_factor score
  - decomposition_summary aggregation
  - SpindleNode metadata field
"""

from __future__ import annotations

import pytest

from agents.core.spindle import (
    SpindleDAG,
    SpindleDAGAnalyzer,
    SpindleNode,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _linear_dag() -> SpindleDAG:
    """A → B → C."""
    dag = SpindleDAG()
    dag.add_node(SpindleNode(node_id="A"))
    dag.add_node(SpindleNode(node_id="B", depends_on=["A"]))
    dag.add_node(SpindleNode(node_id="C", depends_on=["B"]))
    return dag


def _diamond_dag() -> SpindleDAG:
    """A → B, A → C, B + C → D."""
    dag = SpindleDAG()
    dag.add_node(SpindleNode(node_id="A"))
    dag.add_node(SpindleNode(node_id="B", depends_on=["A"]))
    dag.add_node(SpindleNode(node_id="C", depends_on=["A"]))
    dag.add_node(SpindleNode(node_id="D", depends_on=["B", "C"]))
    return dag


def _wide_dag(n: int = 5) -> SpindleDAG:
    """N independent nodes (no edges)."""
    dag = SpindleDAG()
    for i in range(n):
        dag.add_node(SpindleNode(node_id=f"N{i}"))
    return dag


# --------------------------------------------------------------------------- #
# root_nodes / leaf_nodes
# --------------------------------------------------------------------------- #


class TestRootLeafNodes:
    """SpindleDAGAnalyzer identifies entry and exit nodes correctly."""

    def test_linear_root(self) -> None:
        a = SpindleDAGAnalyzer(_linear_dag())
        assert a.root_nodes() == ["A"]

    def test_linear_leaf(self) -> None:
        a = SpindleDAGAnalyzer(_linear_dag())
        assert a.leaf_nodes() == ["C"]

    def test_diamond_root(self) -> None:
        a = SpindleDAGAnalyzer(_diamond_dag())
        assert a.root_nodes() == ["A"]

    def test_diamond_leaf(self) -> None:
        a = SpindleDAGAnalyzer(_diamond_dag())
        assert a.leaf_nodes() == ["D"]

    def test_wide_dag_all_roots(self) -> None:
        a = SpindleDAGAnalyzer(_wide_dag(4))
        assert len(a.root_nodes()) == 4

    def test_wide_dag_all_leaves(self) -> None:
        a = SpindleDAGAnalyzer(_wide_dag(4))
        assert len(a.leaf_nodes()) == 4

    def test_empty_dag_no_roots(self) -> None:
        a = SpindleDAGAnalyzer(SpindleDAG())
        assert a.root_nodes() == []

    def test_empty_dag_no_leaves(self) -> None:
        a = SpindleDAGAnalyzer(SpindleDAG())
        assert a.leaf_nodes() == []


# --------------------------------------------------------------------------- #
# node_depths
# --------------------------------------------------------------------------- #


class TestNodeDepths:
    """SpindleDAGAnalyzer.node_depths() measures depth from root."""

    def test_linear_depths(self) -> None:
        depths = SpindleDAGAnalyzer(_linear_dag()).node_depths()
        assert depths == {"A": 0, "B": 1, "C": 2}

    def test_diamond_depths(self) -> None:
        depths = SpindleDAGAnalyzer(_diamond_dag()).node_depths()
        assert depths["A"] == 0
        assert depths["B"] == 1
        assert depths["C"] == 1
        assert depths["D"] == 2

    def test_wide_depths_all_zero(self) -> None:
        depths = SpindleDAGAnalyzer(_wide_dag(3)).node_depths()
        assert all(d == 0 for d in depths.values())

    def test_empty_dag_depths(self) -> None:
        depths = SpindleDAGAnalyzer(SpindleDAG()).node_depths()
        assert depths == {}


# --------------------------------------------------------------------------- #
# critical_path
# --------------------------------------------------------------------------- #


class TestCriticalPath:
    """SpindleDAGAnalyzer.critical_path() returns the longest chain."""

    def test_linear_critical_path(self) -> None:
        cp = SpindleDAGAnalyzer(_linear_dag()).critical_path()
        assert cp == ["A", "B", "C"]

    def test_diamond_critical_path_length(self) -> None:
        cp = SpindleDAGAnalyzer(_diamond_dag()).critical_path()
        # Must be length 3: A → (B or C) → D
        assert len(cp) == 3
        assert cp[0] == "A"
        assert cp[-1] == "D"
        assert cp[1] in {"B", "C"}

    def test_wide_dag_single_node_path(self) -> None:
        cp = SpindleDAGAnalyzer(_wide_dag(5)).critical_path()
        # Any single independent node
        assert len(cp) == 1

    def test_empty_dag_empty_path(self) -> None:
        cp = SpindleDAGAnalyzer(SpindleDAG()).critical_path()
        assert cp == []

    def test_single_node_path(self) -> None:
        dag = SpindleDAG()
        dag.add_node(SpindleNode(node_id="solo"))
        cp = SpindleDAGAnalyzer(dag).critical_path()
        assert cp == ["solo"]


# --------------------------------------------------------------------------- #
# parallelism_factor
# --------------------------------------------------------------------------- #


class TestParallelismFactor:
    """SpindleDAGAnalyzer.parallelism_factor() scores 0–1."""

    def test_serial_is_zero(self) -> None:
        # 3 nodes, critical path length 3 → factor = 1 - 3/3 = 0
        pf = SpindleDAGAnalyzer(_linear_dag()).parallelism_factor()
        assert pf == pytest.approx(0.0)

    def test_diamond_partial(self) -> None:
        # 4 nodes, critical path 3 → factor = 1 - 3/4 = 0.25
        pf = SpindleDAGAnalyzer(_diamond_dag()).parallelism_factor()
        assert pf == pytest.approx(0.25)

    def test_wide_dag_full_parallel(self) -> None:
        # 5 nodes, critical path 1 → factor = 1 - 1/5 = 0.8
        pf = SpindleDAGAnalyzer(_wide_dag(5)).parallelism_factor()
        assert pf == pytest.approx(0.8)

    def test_empty_dag_returns_one(self) -> None:
        pf = SpindleDAGAnalyzer(SpindleDAG()).parallelism_factor()
        assert pf == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# decomposition_summary
# --------------------------------------------------------------------------- #


class TestDecompositionSummary:
    """SpindleDAGAnalyzer.decomposition_summary() returns structured report."""

    def test_linear_summary_keys(self) -> None:
        summary = SpindleDAGAnalyzer(_linear_dag()).decomposition_summary()
        expected_keys = {
            "total_nodes",
            "root_count",
            "leaf_count",
            "critical_path_length",
            "critical_path",
            "parallelism_factor",
            "max_depth",
            "wave_count",
        }
        assert expected_keys.issubset(set(summary.keys()))

    def test_linear_summary_values(self) -> None:
        summary = SpindleDAGAnalyzer(_linear_dag()).decomposition_summary()
        assert summary["total_nodes"] == 3
        assert summary["root_count"] == 1
        assert summary["leaf_count"] == 1
        assert summary["critical_path_length"] == 3
        assert summary["max_depth"] == 2
        assert summary["wave_count"] == 3

    def test_diamond_summary(self) -> None:
        summary = SpindleDAGAnalyzer(_diamond_dag()).decomposition_summary()
        assert summary["total_nodes"] == 4
        assert summary["wave_count"] == 3  # [A], [B,C], [D]

    def test_empty_dag_summary(self) -> None:
        summary = SpindleDAGAnalyzer(SpindleDAG()).decomposition_summary()
        assert summary["total_nodes"] == 0
        assert summary["wave_count"] == 0  # empty DAG: 0 waves, not None

    def test_cycle_wave_count_is_none(self) -> None:
        """A DAG with a cycle reports wave_count=None (cannot form waves)."""
        dag = SpindleDAG()
        # Add two nodes with a cycle by bypassing add_node validation
        # (validate() is called lazily by get_execution_waves)
        dag._nodes["X"] = SpindleNode(node_id="X", depends_on=["Y"])
        dag._nodes["Y"] = SpindleNode(node_id="Y", depends_on=["X"])
        summary = SpindleDAGAnalyzer(dag).decomposition_summary()
        assert summary["wave_count"] is None


# --------------------------------------------------------------------------- #
# SpindleNode metadata field
# --------------------------------------------------------------------------- #


class TestSpindleNodeMetadata:
    """SpindleNode.metadata is an optional free-form annotations dict."""

    def test_default_metadata_is_empty_dict(self) -> None:
        node = SpindleNode(node_id="x")
        assert node.metadata == {}

    def test_metadata_round_trip(self) -> None:
        node = SpindleNode(
            node_id="x",
            metadata={"description": "parse spec", "owner": "codegen"},
        )
        assert node.metadata["description"] == "parse spec"
        assert node.metadata["owner"] == "codegen"

    def test_metadata_stored_in_dag(self) -> None:
        dag = SpindleDAG()
        dag.add_node(SpindleNode(
            node_id="annotated",
            metadata={"task_type": "create_file", "estimated_duration_s": 0.5},
        ))
        node = dag.get_node("annotated")
        assert node.metadata["task_type"] == "create_file"
