"""
Tests for CodeAgent — file creation, modification, refactoring, deletion.

Each test creates a temporary sandbox and validates that CodeAgent
operations go through the sandbox's path-safe file ops.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from agents.core.agent_sandbox import AgentSandbox
from agents.core.code_agent import CodeAgent
from agents.core.tool_registry import ToolRegistry

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_sandbox() -> tuple[AgentSandbox, str]:
    """Create a temporary sandbox for testing."""
    tmp = tempfile.mkdtemp(prefix="code_agent_test_")
    registry = ToolRegistry()
    sandbox = AgentSandbox(
        agent_id="test-code-agent",
        agent_role="developer",
        worktree_path=tmp,
        tool_registry=registry,
    )
    return sandbox, tmp


# --------------------------------------------------------------------------- #
# create_file
# --------------------------------------------------------------------------- #


class TestCreateFile:
    """CodeAgent.execute_task() with type=create_file."""

    def test_creates_new_file(self) -> None:
        sandbox, tmp = _make_sandbox()
        agent = CodeAgent()
        result = agent.execute_task(
            {"type": "create_file", "path": "hello.py", "content": "print('hi')"},
            sandbox=sandbox,
        )
        assert result.success
        assert "hello.py" in result.files_modified
        assert os.path.exists(os.path.join(tmp, "hello.py"))
        with open(os.path.join(tmp, "hello.py")) as f:
            assert f.read() == "print('hi')"

    def test_refuses_overwrite(self) -> None:
        sandbox, tmp = _make_sandbox()
        # Create file first
        with open(os.path.join(tmp, "exists.py"), "w") as f:
            f.write("original")

        agent = CodeAgent()
        result = agent.execute_task(
            {"type": "create_file", "path": "exists.py", "content": "new"},
            sandbox=sandbox,
        )
        assert not result.success
        assert "already exists" in result.error

    def test_missing_path_field(self) -> None:
        sandbox, _ = _make_sandbox()
        agent = CodeAgent()
        result = agent.execute_task(
            {"type": "create_file", "content": "data"},
            sandbox=sandbox,
        )
        assert not result.success
        assert "path" in result.error

    def test_empty_content_creates_empty_file(self) -> None:
        sandbox, tmp = _make_sandbox()
        agent = CodeAgent()
        result = agent.execute_task(
            {"type": "create_file", "path": "empty.py", "content": ""},
            sandbox=sandbox,
        )
        assert result.success
        assert os.path.exists(os.path.join(tmp, "empty.py"))

    def test_nested_path_creates_parents(self) -> None:
        sandbox, tmp = _make_sandbox()
        agent = CodeAgent()
        result = agent.execute_task(
            {
                "type": "create_file",
                "path": "src/utils/helper.py",
                "content": "# helper",
            },
            sandbox=sandbox,
        )
        assert result.success
        assert os.path.exists(os.path.join(tmp, "src", "utils", "helper.py"))


# --------------------------------------------------------------------------- #
# modify_file
# --------------------------------------------------------------------------- #


class TestModifyFile:
    """CodeAgent.execute_task() with type=modify_file."""

    def test_full_content_replacement(self) -> None:
        sandbox, tmp = _make_sandbox()
        with open(os.path.join(tmp, "target.py"), "w") as f:
            f.write("old content")

        agent = CodeAgent()
        result = agent.execute_task(
            {"type": "modify_file", "path": "target.py", "content": "new content"},
            sandbox=sandbox,
        )
        assert result.success
        with open(os.path.join(tmp, "target.py")) as f:
            assert f.read() == "new content"

    def test_find_replace_changes(self) -> None:
        sandbox, tmp = _make_sandbox()
        with open(os.path.join(tmp, "config.py"), "w") as f:
            f.write("DEBUG = True\nVERSION = '1.0'\n")

        agent = CodeAgent()
        result = agent.execute_task(
            {
                "type": "modify_file",
                "path": "config.py",
                "changes": [
                    {"find": "DEBUG = True", "replace": "DEBUG = False"},
                    {"find": "VERSION = '1.0'", "replace": "VERSION = '2.0'"},
                ],
            },
            sandbox=sandbox,
        )
        assert result.success
        with open(os.path.join(tmp, "config.py")) as f:
            content = f.read()
        assert "DEBUG = False" in content
        assert "VERSION = '2.0'" in content

    def test_no_matching_changes_fails(self) -> None:
        sandbox, tmp = _make_sandbox()
        with open(os.path.join(tmp, "file.py"), "w") as f:
            f.write("unchanged")

        agent = CodeAgent()
        result = agent.execute_task(
            {
                "type": "modify_file",
                "path": "file.py",
                "changes": [{"find": "nonexistent", "replace": "foo"}],
            },
            sandbox=sandbox,
        )
        assert not result.success
        assert "not found" in result.error

    def test_missing_file_fails(self) -> None:
        sandbox, _ = _make_sandbox()
        agent = CodeAgent()
        result = agent.execute_task(
            {"type": "modify_file", "path": "ghost.py", "content": "x"},
            sandbox=sandbox,
        )
        assert not result.success

    def test_missing_content_and_changes_fails(self) -> None:
        sandbox, tmp = _make_sandbox()
        with open(os.path.join(tmp, "file.py"), "w") as f:
            f.write("data")

        agent = CodeAgent()
        result = agent.execute_task(
            {"type": "modify_file", "path": "file.py"},
            sandbox=sandbox,
        )
        assert not result.success
        assert "content" in result.error or "changes" in result.error


# --------------------------------------------------------------------------- #
# refactor
# --------------------------------------------------------------------------- #


class TestRefactor:
    """CodeAgent.execute_task() with type=refactor."""

    def test_bulk_rename(self) -> None:
        sandbox, tmp = _make_sandbox()
        for name in ["a.py", "b.py"]:
            with open(os.path.join(tmp, name), "w") as f:
                f.write("import old_module\n")

        agent = CodeAgent()
        result = agent.execute_task(
            {"type": "refactor", "find": "old_module", "replace": "new_module"},
            sandbox=sandbox,
        )
        assert result.success
        assert len(result.files_modified) == 2
        for name in ["a.py", "b.py"]:
            with open(os.path.join(tmp, name)) as f:
                assert "new_module" in f.read()

    def test_no_matches_succeeds_with_zero_files(self) -> None:
        sandbox, tmp = _make_sandbox()
        with open(os.path.join(tmp, "clean.py"), "w") as f:
            f.write("nothing to change\n")

        agent = CodeAgent()
        result = agent.execute_task(
            {"type": "refactor", "find": "nonexistent_symbol", "replace": "x"},
            sandbox=sandbox,
        )
        assert result.success
        assert len(result.files_modified) == 0

    def test_missing_find_fails(self) -> None:
        sandbox, _ = _make_sandbox()
        agent = CodeAgent()
        result = agent.execute_task(
            {"type": "refactor", "replace": "x"},
            sandbox=sandbox,
        )
        assert not result.success
        assert "find" in result.error


# --------------------------------------------------------------------------- #
# delete_file
# --------------------------------------------------------------------------- #


class TestDeleteFile:
    """CodeAgent.execute_task() with type=delete_file."""

    def test_deletes_existing_file(self) -> None:
        sandbox, tmp = _make_sandbox()
        filepath = os.path.join(tmp, "doomed.py")
        with open(filepath, "w") as f:
            f.write("bye")

        agent = CodeAgent()
        result = agent.execute_task(
            {"type": "delete_file", "path": "doomed.py"},
            sandbox=sandbox,
        )
        assert result.success
        assert not os.path.exists(filepath)

    def test_delete_nonexistent_fails(self) -> None:
        sandbox, _ = _make_sandbox()
        agent = CodeAgent()
        result = agent.execute_task(
            {"type": "delete_file", "path": "ghost.py"},
            sandbox=sandbox,
        )
        assert not result.success

    def test_missing_path_fails(self) -> None:
        sandbox, _ = _make_sandbox()
        agent = CodeAgent()
        result = agent.execute_task(
            {"type": "delete_file"},
            sandbox=sandbox,
        )
        assert not result.success


# --------------------------------------------------------------------------- #
# Unknown task type
# --------------------------------------------------------------------------- #


class TestUnknownTask:
    """CodeAgent error handling for invalid inputs."""

    def test_unknown_type_returns_error(self) -> None:
        sandbox, _ = _make_sandbox()
        agent = CodeAgent()
        result = agent.execute_task(
            {"type": "launch_missiles"},
            sandbox=sandbox,
        )
        assert not result.success
        assert "Unknown task type" in result.error

    def test_missing_type_returns_error(self) -> None:
        sandbox, _ = _make_sandbox()
        agent = CodeAgent()
        result = agent.execute_task({}, sandbox=sandbox)
        assert not result.success


# --------------------------------------------------------------------------- #
# Introspection
# --------------------------------------------------------------------------- #


class TestCodeAgentIntrospection:
    """CodeAgent task tracking and success rate."""

    def test_task_count_increments(self) -> None:
        sandbox, tmp = _make_sandbox()
        agent = CodeAgent()
        agent.execute_task(
            {"type": "create_file", "path": "a.py", "content": "1"},
            sandbox=sandbox,
        )
        agent.execute_task(
            {"type": "create_file", "path": "b.py", "content": "2"},
            sandbox=sandbox,
        )
        assert agent.task_count == 2

    def test_success_rate(self) -> None:
        sandbox, _ = _make_sandbox()
        agent = CodeAgent()
        # One success, one failure
        agent.execute_task(
            {"type": "create_file", "path": "ok.py", "content": "x"},
            sandbox=sandbox,
        )
        agent.execute_task(
            {"type": "create_file"},  # missing path = fail
            sandbox=sandbox,
        )
        assert agent.success_rate == pytest.approx(0.5)

    def test_empty_success_rate(self) -> None:
        agent = CodeAgent()
        assert agent.success_rate == 0.0
