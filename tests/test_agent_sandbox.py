"""Tests for AgentSandbox — sandboxed execution environment."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.core.agent_sandbox import AgentSandbox
from agents.core.tool_registry import ToolRegistry

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _make_sandbox(tmp_path: Path | None = None) -> tuple[AgentSandbox, Path]:
    """Create a sandbox in a temp directory."""
    if tmp_path is None:
        tmp_path = Path(tempfile.mkdtemp())
    registry = ToolRegistry()
    sandbox = AgentSandbox(
        agent_id="test-agent-01",
        agent_role="developer",
        worktree_path=str(tmp_path),
        tool_registry=registry,
    )
    return sandbox, tmp_path


# --------------------------------------------------------------------------- #
# File Operation Tests
# --------------------------------------------------------------------------- #


class TestFileOperations:
    def test_write_and_read(self):
        sandbox, tmp = _make_sandbox()
        write_result = sandbox.write_file("hello.txt", "Hello World")
        assert write_result.success is True

        read_result = sandbox.read_file("hello.txt")
        assert read_result.success is True
        assert read_result.output == "Hello World"

    def test_read_nonexistent(self):
        sandbox, tmp = _make_sandbox()
        result = sandbox.read_file("no_such_file.txt")
        assert result.success is False
        assert "not found" in result.error.lower()

    def test_write_creates_directories(self):
        sandbox, tmp = _make_sandbox()
        result = sandbox.write_file("deep/nested/dir/file.py", "code")
        assert result.success is True
        assert (tmp / "deep" / "nested" / "dir" / "file.py").exists()

    def test_delete_file(self):
        sandbox, tmp = _make_sandbox()
        sandbox.write_file("deleteme.txt", "bye")
        assert (tmp / "deleteme.txt").exists()

        result = sandbox.delete_file("deleteme.txt")
        assert result.success is True
        assert not (tmp / "deleteme.txt").exists()

    def test_delete_nonexistent(self):
        sandbox, tmp = _make_sandbox()
        result = sandbox.delete_file("ghost.txt")
        assert result.success is False

    def test_list_files(self):
        sandbox, tmp = _make_sandbox()
        sandbox.write_file("a.py", "# a")
        sandbox.write_file("b.py", "# b")
        sandbox.write_file("sub/c.py", "# c")

        result = sandbox.list_files(".", "*.py")
        assert result.success is True
        assert len(result.output) == 3

    def test_search_files(self):
        sandbox, tmp = _make_sandbox()
        sandbox.write_file("main.py", "def hello():\n    return 'world'")
        sandbox.write_file("util.py", "x = 42")

        result = sandbox.search_files("hello", "*.py")
        assert result.success is True
        assert len(result.output) == 1
        assert result.output[0]["file"] == "main.py"
        assert result.output[0]["line"] == 1


# --------------------------------------------------------------------------- #
# Path Security Tests
# --------------------------------------------------------------------------- #


class TestPathSecurity:
    def test_path_traversal_blocked(self):
        sandbox, tmp = _make_sandbox()
        result = sandbox.read_file("../../etc/passwd")
        assert result.success is False
        err = result.error.lower()
        assert "traversal" in err or "blocked" in err

    def test_absolute_path_blocked(self):
        sandbox, tmp = _make_sandbox()
        # On Windows, an absolute path like C:\... would resolve outside worktree
        # On Unix, /etc/passwd would
        if os.name == "nt":
            result = sandbox.read_file("C:\\Windows\\System32\\config")
        else:
            result = sandbox.read_file("/etc/passwd")
        assert result.success is False

    def test_dotdot_in_nested_path(self):
        sandbox, tmp = _make_sandbox()
        sandbox.write_file("sub/file.txt", "inside")
        # This resolves within worktree, should work
        result = sandbox.read_file("sub/../sub/file.txt")
        assert result.success is True
        assert result.output == "inside"

    def test_write_traversal_blocked(self):
        sandbox, tmp = _make_sandbox()
        evil_code = "import os; os.system('rm -rf /')"
        result = sandbox.write_file("../../evil.py", evil_code)
        assert result.success is False


# --------------------------------------------------------------------------- #
# Terminal Execution Tests
# --------------------------------------------------------------------------- #


class TestTerminalExecution:
    def test_simple_command(self):
        sandbox, tmp = _make_sandbox()
        result = sandbox.run_command("echo hello")
        assert result.exit_code == 0
        assert "hello" in result.stdout

    def test_command_failure(self):
        sandbox, tmp = _make_sandbox()
        result = sandbox.run_command('python -c "import sys; sys.exit(1)"')
        assert result.exit_code == 1

    def test_command_timeout(self):
        sandbox, tmp = _make_sandbox()
        if os.name == "nt":
            result = sandbox.run_command("ping -n 10 127.0.0.1", timeout=1)
        else:
            result = sandbox.run_command("sleep 10", timeout=1)
        assert result.timed_out is True
        assert result.exit_code == -1

    def test_command_cwd_is_worktree(self):
        sandbox, tmp = _make_sandbox()
        sandbox.write_file("marker.txt", "I am here")
        cmd = "dir marker.txt" if os.name == "nt" else "ls marker.txt"
        result = sandbox.run_command(cmd)
        assert result.exit_code == 0
        assert "marker" in result.stdout


# --------------------------------------------------------------------------- #
# Action Log Tests
# --------------------------------------------------------------------------- #


class TestActionLog:
    def test_actions_logged(self):
        sandbox, tmp = _make_sandbox()
        sandbox.write_file("a.txt", "content")
        sandbox.read_file("a.txt")
        sandbox.delete_file("a.txt")

        assert sandbox.action_count == 3
        log = sandbox.action_log
        assert log[0]["tool_id"] == "file.write"
        assert log[1]["tool_id"] == "file.read"
        assert log[2]["tool_id"] == "file.delete"

    def test_action_summary(self):
        sandbox, tmp = _make_sandbox()
        sandbox.write_file("a.txt", "x")
        sandbox.write_file("b.txt", "y")
        sandbox.read_file("a.txt")

        summary = sandbox.get_action_summary()
        assert summary["file.write"] == 2
        assert summary["file.read"] == 1

    def test_terminal_actions_logged(self):
        sandbox, tmp = _make_sandbox()
        sandbox.run_command("echo test")
        assert sandbox.action_count == 1
        assert sandbox.action_log[0]["tool_id"] == "terminal.exec"


# --------------------------------------------------------------------------- #
# Git/Build Operation Tests
# --------------------------------------------------------------------------- #


class TestBuildOps:
    def test_git_status(self):
        sandbox, tmp = _make_sandbox()
        # May fail if not a git repo, but should return a ToolResult
        result = sandbox.git_status()
        assert isinstance(result.tool_id, str)
        assert result.tool_id == "git.status"

    def test_run_lint(self):
        sandbox, tmp = _make_sandbox()
        sandbox.write_file("clean.py", "x = 1\n")
        result = sandbox.run_lint("clean.py")
        assert result.tool_id == "build.lint"
