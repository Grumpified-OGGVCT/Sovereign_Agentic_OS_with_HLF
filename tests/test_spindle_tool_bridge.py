"""Tests for SpindleToolBridge — DAG-to-sandbox execution bridge."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.core.spindle_tool_bridge import (
    AgentExecutionTrace,
    AgentStep,
    SpindleToolBridge,
)
from agents.core.tool_registry import ToolRegistry

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_bridge(
    event_bus: Any = None,
) -> tuple[SpindleToolBridge, str]:
    """Create a bridge with a temp worktree."""
    registry = ToolRegistry()
    tmp = tempfile.mkdtemp()
    bridge = SpindleToolBridge(
        tool_registry=registry,
        event_bus=event_bus,
        max_steps_per_node=10,
    )
    return bridge, tmp


# --------------------------------------------------------------------------- #
# Sandbox Management Tests
# --------------------------------------------------------------------------- #


class TestSandboxManagement:
    def test_create_sandbox(self):
        bridge, tmp = _make_bridge()
        sandbox = bridge.get_or_create_sandbox(
            "agent-01", "developer", tmp,
        )
        assert sandbox is not None
        assert sandbox.agent_id == "agent-01"
        assert bridge.sandbox_count == 1

    def test_reuse_existing_sandbox(self):
        bridge, tmp = _make_bridge()
        s1 = bridge.get_or_create_sandbox("agent-01", "developer", tmp)
        s2 = bridge.get_or_create_sandbox("agent-01", "developer", tmp)
        assert s1 is s2
        assert bridge.sandbox_count == 1

    def test_multiple_sandboxes(self):
        bridge, tmp = _make_bridge()
        bridge.get_or_create_sandbox("agent-01", "sentinel", tmp)
        bridge.get_or_create_sandbox("agent-02", "scribe", tmp)
        assert bridge.sandbox_count == 2

    def test_get_sandbox(self):
        bridge, tmp = _make_bridge()
        bridge.get_or_create_sandbox("agent-01", "developer", tmp)
        assert bridge.get_sandbox("agent-01") is not None
        assert bridge.get_sandbox("nonexistent") is None


# --------------------------------------------------------------------------- #
# Node Execution Tests
# --------------------------------------------------------------------------- #


def _execute_fn_reads_file(context: dict) -> str:
    """Node function that reads a file via sandbox."""
    sandbox = context["_sandbox"]
    sandbox.write_file("output.txt", "generated content")
    result = sandbox.read_file("output.txt")
    return result.output


def _execute_fn_uses_tools(context: dict) -> dict:
    """Node function that uses multiple tools."""
    sandbox = context["_sandbox"]
    sandbox.write_file("src/main.py", "print('hello')")
    sandbox.write_file("src/util.py", "x = 42")
    files = sandbox.list_files("src/", "*.py")
    return {"files": files.output, "count": len(files.output)}


def _execute_fn_raises(context: dict) -> None:
    """Node function that crashes."""
    raise ValueError("node exploded")


class TestNodeExecution:
    def test_execute_node_success(self):
        bridge, tmp = _make_bridge()
        trace = bridge.execute_node(
            node_id="node-1",
            agent_id="agent-01",
            agent_role="developer",
            worktree_path=tmp,
            execute_fn=_execute_fn_reads_file,
            context={},
        )
        assert isinstance(trace, AgentExecutionTrace)
        assert trace.success is True
        assert trace.final_output == "generated content"
        assert trace.total_tool_calls >= 2  # write + read
        assert trace.total_duration > 0

    def test_execute_node_multi_tool(self):
        bridge, tmp = _make_bridge()
        trace = bridge.execute_node(
            node_id="node-2",
            agent_id="agent-02",
            agent_role="developer",
            worktree_path=tmp,
            execute_fn=_execute_fn_uses_tools,
            context={},
        )
        assert trace.success is True
        assert trace.total_tool_calls >= 3  # 2 writes + 1 list
        assert trace.final_output["count"] == 2

    def test_execute_node_failure(self):
        bridge, tmp = _make_bridge()
        trace = bridge.execute_node(
            node_id="node-fail",
            agent_id="agent-03",
            agent_role="developer",
            worktree_path=tmp,
            execute_fn=_execute_fn_raises,
            context={},
        )
        assert trace.success is False
        assert "node exploded" in trace.error

    def test_context_injection(self):
        """Verify sandbox and metadata are injected into context."""
        captured = {}

        def _capture_context(context: dict) -> str:
            captured.update(context)
            return "done"

        bridge, tmp = _make_bridge()
        bridge.execute_node(
            node_id="node-ctx",
            agent_id="agent-04",
            agent_role="sentinel",
            worktree_path=tmp,
            execute_fn=_capture_context,
            context={"existing": "data"},
        )
        assert "_sandbox" in captured
        assert "_agent_id" in captured
        assert captured["_agent_id"] == "agent-04"
        assert "_available_tools" in captured
        assert captured["existing"] == "data"


# --------------------------------------------------------------------------- #
# Trace Collection Tests
# --------------------------------------------------------------------------- #


class TestTraceCollection:
    def test_traces_accumulate(self):
        bridge, tmp = _make_bridge()
        bridge.execute_node(
            "n1", "a1", "developer", tmp, _execute_fn_reads_file, {},
        )
        bridge.execute_node(
            "n2", "a2", "developer", tmp, _execute_fn_uses_tools, {},
        )
        assert len(bridge.traces) == 2
        assert bridge.traces[0].node_id == "n1"
        assert bridge.traces[1].node_id == "n2"


# --------------------------------------------------------------------------- #
# Event Bus Integration Tests
# --------------------------------------------------------------------------- #


class TestEventBusIntegration:
    def test_events_published(self):
        mock_bus = MagicMock()
        bridge, tmp = _make_bridge(event_bus=mock_bus)

        bridge.execute_node(
            "n1", "a1", "developer", tmp, _execute_fn_reads_file, {},
        )

        # Should have published start + complete events
        assert mock_bus.publish.call_count >= 2


# --------------------------------------------------------------------------- #
# Data Structure Tests
# --------------------------------------------------------------------------- #


class TestDataStructures:
    def test_agent_step(self):
        step = AgentStep(
            step_number=1,
            action="file.write",
            tool_id="file.write",
            input_args={"path": "x.py"},
            reasoning="Need to create the file",
        )
        assert step.step_number == 1
        assert step.tool_id == "file.write"

    def test_agent_execution_trace(self):
        trace = AgentExecutionTrace(
            agent_id="test",
            node_id="node-1",
        )
        assert trace.success is True
        assert trace.total_tool_calls == 0
        assert trace.steps == []
