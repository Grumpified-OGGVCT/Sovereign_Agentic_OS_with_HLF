"""
Tests for BuildAgent — test runner, linter, import/syntax validation.

Each test creates a temporary sandbox and validates BuildAgent
parses output correctly and returns structured results.
"""

from __future__ import annotations

import os
import tempfile

from agents.core.agent_sandbox import AgentSandbox
from agents.core.build_agent import BuildAgent
from agents.core.tool_registry import ToolRegistry

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_sandbox() -> tuple[AgentSandbox, str]:
    """Create a temporary sandbox for testing."""
    tmp = tempfile.mkdtemp(prefix="build_agent_test_")
    registry = ToolRegistry()
    sandbox = AgentSandbox(
        agent_id="test-build-agent",
        agent_role="developer",
        worktree_path=tmp,
        tool_registry=registry,
    )
    return sandbox, tmp


# --------------------------------------------------------------------------- #
# check_syntax
# --------------------------------------------------------------------------- #


class TestCheckSyntax:
    """BuildAgent.execute_task() with type=check_syntax."""

    def test_valid_python_passes(self) -> None:
        sandbox, tmp = _make_sandbox()
        filepath = os.path.join(tmp, "valid.py")
        with open(filepath, "w") as f:
            f.write("def foo():\n    return 42\n")

        agent = BuildAgent()
        result = agent.execute_task(
            {"type": "check_syntax", "path": "valid.py"},
            sandbox=sandbox,
        )
        assert result.success
        assert result.passed == 1
        assert result.errors == 0

    def test_invalid_python_fails(self) -> None:
        sandbox, tmp = _make_sandbox()
        filepath = os.path.join(tmp, "bad.py")
        with open(filepath, "w") as f:
            f.write("def foo(\n    return 42\n")

        agent = BuildAgent()
        result = agent.execute_task(
            {"type": "check_syntax", "path": "bad.py"},
            sandbox=sandbox,
        )
        assert not result.success
        assert result.errors == 1
        assert "Syntax error" in result.error

    def test_missing_path_fails(self) -> None:
        sandbox, _ = _make_sandbox()
        agent = BuildAgent()
        result = agent.execute_task(
            {"type": "check_syntax"},
            sandbox=sandbox,
        )
        assert not result.success
        assert "path" in result.error

    def test_nonexistent_file_fails(self) -> None:
        sandbox, _ = _make_sandbox()
        agent = BuildAgent()
        result = agent.execute_task(
            {"type": "check_syntax", "path": "ghost.py"},
            sandbox=sandbox,
        )
        assert not result.success


# --------------------------------------------------------------------------- #
# validate_imports
# --------------------------------------------------------------------------- #


class TestValidateImports:
    """BuildAgent.execute_task() with type=validate_imports."""

    def test_clean_imports(self) -> None:
        sandbox, tmp = _make_sandbox()
        filepath = os.path.join(tmp, "clean.py")
        with open(filepath, "w") as f:
            f.write("import os\nimport sys\nfrom pathlib import Path\n")

        agent = BuildAgent()
        result = agent.execute_task(
            {"type": "validate_imports", "path": "clean.py"},
            sandbox=sandbox,
        )
        assert result.success
        assert result.passed == 3  # 3 import statements

    def test_deep_relative_import_warns(self) -> None:
        sandbox, tmp = _make_sandbox()
        filepath = os.path.join(tmp, "deep.py")
        with open(filepath, "w") as f:
            f.write("from ....deeply.nested import thing\n")

        agent = BuildAgent()
        result = agent.execute_task(
            {"type": "validate_imports", "path": "deep.py"},
            sandbox=sandbox,
        )
        assert not result.success
        assert len(result.warnings) > 0
        assert "Deep relative import" in result.warnings[0]

    def test_syntax_error_in_file(self) -> None:
        sandbox, tmp = _make_sandbox()
        filepath = os.path.join(tmp, "broken.py")
        with open(filepath, "w") as f:
            f.write("import os\ndef (broken\n")

        agent = BuildAgent()
        result = agent.execute_task(
            {"type": "validate_imports", "path": "broken.py"},
            sandbox=sandbox,
        )
        assert not result.success
        assert "Syntax error" in result.error

    def test_missing_path_fails(self) -> None:
        sandbox, _ = _make_sandbox()
        agent = BuildAgent()
        result = agent.execute_task(
            {"type": "validate_imports"},
            sandbox=sandbox,
        )
        assert not result.success


# --------------------------------------------------------------------------- #
# Output Parsers (unit-tested via static methods)
# --------------------------------------------------------------------------- #


class TestPytestParser:
    """BuildAgent._parse_pytest_output() static method."""

    def test_all_passed(self) -> None:
        p, f, e = BuildAgent._parse_pytest_output(
            "==================== 42 passed in 5.67s ===================="
        )
        assert p == 42
        assert f == 0
        assert e == 0

    def test_mixed_results(self) -> None:
        p, f, e = BuildAgent._parse_pytest_output(
            "========= 10 passed, 3 failed, 1 error in 2.34s =========="
        )
        assert p == 10
        assert f == 3
        assert e == 1

    def test_all_failed(self) -> None:
        p, f, e = BuildAgent._parse_pytest_output(
            "==================== 5 failed in 1.23s ===================="
        )
        assert p == 0
        assert f == 5
        assert e == 0

    def test_errors_only(self) -> None:
        p, f, e = BuildAgent._parse_pytest_output(
            "==================== 2 errors in 0.5s ===================="
        )
        assert p == 0
        assert f == 0
        assert e == 2

    def test_empty_output(self) -> None:
        p, f, e = BuildAgent._parse_pytest_output("")
        assert p == 0
        assert f == 0
        assert e == 0


class TestPytestSkippedParser:
    """BuildAgent._parse_pytest_skipped() static method."""

    def test_skipped_parsed(self) -> None:
        output = "========== 5 passed, 3 skipped in 2.1s =========="
        assert BuildAgent._parse_pytest_skipped(output) == 3

    def test_no_skipped(self) -> None:
        output = "========== 10 passed in 1.23s =========="
        assert BuildAgent._parse_pytest_skipped(output) == 0

    def test_skipped_with_failures(self) -> None:
        output = "========== 4 passed, 2 failed, 1 skipped in 3.0s =========="
        assert BuildAgent._parse_pytest_skipped(output) == 1

    def test_empty_output(self) -> None:
        assert BuildAgent._parse_pytest_skipped("") == 0


class TestRuffParser:
    """BuildAgent._parse_ruff_output() static method."""

    def test_violations_parsed(self) -> None:
        output = (
            "src/foo.py:10:5: E501 Line too long\n"
            "src/bar.py:20:1: F401 Unused import\n"
        )
        violations = BuildAgent._parse_ruff_output(output)
        assert len(violations) == 2

    def test_clean_output(self) -> None:
        violations = BuildAgent._parse_ruff_output("All checks passed!")
        assert len(violations) == 0


# --------------------------------------------------------------------------- #
# check_forbidden_calls
# --------------------------------------------------------------------------- #


class TestCheckForbiddenCalls:
    """BuildAgent.execute_task() with type=check_forbidden_calls."""

    def test_safe_code_passes(self) -> None:
        sandbox, tmp = _make_sandbox()
        filepath = os.path.join(tmp, "safe.py")
        with open(filepath, "w") as f:
            f.write("def greet(name: str) -> str:\n    return f'Hello {name}'\n")

        agent = BuildAgent()
        result = agent.execute_task(
            {"type": "check_forbidden_calls", "path": "safe.py"},
            sandbox=sandbox,
        )
        assert result.success
        assert result.errors == 0
        assert result.passed == 1

    def test_os_system_flagged(self) -> None:
        sandbox, tmp = _make_sandbox()
        filepath = os.path.join(tmp, "dangerous.py")
        with open(filepath, "w") as f:
            f.write("import os\nos.system('echo hello')\n")

        agent = BuildAgent()
        result = agent.execute_task(
            {"type": "check_forbidden_calls", "path": "dangerous.py"},
            sandbox=sandbox,
        )
        assert not result.success
        assert result.errors >= 1
        assert any("os.system" in w for w in result.warnings)

    def test_eval_flagged(self) -> None:
        sandbox, tmp = _make_sandbox()
        filepath = os.path.join(tmp, "evil.py")
        with open(filepath, "w") as f:
            f.write("result = eval('1 + 1')\n")

        agent = BuildAgent()
        result = agent.execute_task(
            {"type": "check_forbidden_calls", "path": "evil.py"},
            sandbox=sandbox,
        )
        assert not result.success
        assert any("eval" in w for w in result.warnings)

    def test_exec_flagged(self) -> None:
        sandbox, tmp = _make_sandbox()
        filepath = os.path.join(tmp, "exec_file.py")
        with open(filepath, "w") as f:
            f.write("exec('print(\"pwned\")')\n")

        agent = BuildAgent()
        result = agent.execute_task(
            {"type": "check_forbidden_calls", "path": "exec_file.py"},
            sandbox=sandbox,
        )
        assert not result.success
        assert any("exec" in w for w in result.warnings)

    def test_missing_path_fails(self) -> None:
        sandbox, _ = _make_sandbox()
        agent = BuildAgent()
        result = agent.execute_task(
            {"type": "check_forbidden_calls"},
            sandbox=sandbox,
        )
        assert not result.success
        assert "path" in result.error

    def test_nonexistent_file_fails(self) -> None:
        sandbox, _ = _make_sandbox()
        agent = BuildAgent()
        result = agent.execute_task(
            {"type": "check_forbidden_calls", "path": "ghost.py"},
            sandbox=sandbox,
        )
        assert not result.success

    def test_metadata_contains_violations(self) -> None:
        sandbox, tmp = _make_sandbox()
        filepath = os.path.join(tmp, "meta.py")
        with open(filepath, "w") as f:
            f.write("import subprocess\nsubprocess.call(['ls'])\n")

        agent = BuildAgent()
        result = agent.execute_task(
            {"type": "check_forbidden_calls", "path": "meta.py"},
            sandbox=sandbox,
        )
        assert not result.success
        assert "violations" in result.metadata
        assert isinstance(result.metadata["violations"], list)


# --------------------------------------------------------------------------- #
# check_import_rules
# --------------------------------------------------------------------------- #


class TestCheckImportRules:
    """BuildAgent.execute_task() with type=check_import_rules."""

    def test_clean_imports_pass(self) -> None:
        sandbox, tmp = _make_sandbox()
        filepath = os.path.join(tmp, "clean.py")
        with open(filepath, "w") as f:
            f.write("import os\nimport sys\nfrom pathlib import Path\n")

        agent = BuildAgent()
        result = agent.execute_task(
            {"type": "check_import_rules", "path": "clean.py"},
            sandbox=sandbox,
        )
        assert result.success
        assert result.failed == 0

    def test_forbidden_prefix_flagged(self) -> None:
        sandbox, tmp = _make_sandbox()
        filepath = os.path.join(tmp, "ctypes_use.py")
        with open(filepath, "w") as f:
            f.write("import ctypes\nctypes.CDLL('libc.so.6')\n")

        agent = BuildAgent()
        result = agent.execute_task(
            {"type": "check_import_rules", "path": "ctypes_use.py"},
            sandbox=sandbox,
        )
        assert not result.success
        assert result.failed >= 1
        assert any("ctypes" in w for w in result.warnings)

    def test_cffi_flagged(self) -> None:
        sandbox, tmp = _make_sandbox()
        filepath = os.path.join(tmp, "cffi_use.py")
        with open(filepath, "w") as f:
            f.write("import cffi\nffi = cffi.FFI()\n")

        agent = BuildAgent()
        result = agent.execute_task(
            {"type": "check_import_rules", "path": "cffi_use.py"},
            sandbox=sandbox,
        )
        assert not result.success
        assert any("cffi" in w for w in result.warnings)

    def test_custom_forbidden_prefixes(self) -> None:
        sandbox, tmp = _make_sandbox()
        filepath = os.path.join(tmp, "custom.py")
        with open(filepath, "w") as f:
            f.write("import pickle\nx = pickle.loads(b'')\n")

        agent = BuildAgent()
        result = agent.execute_task(
            {
                "type": "check_import_rules",
                "path": "custom.py",
                "forbidden_prefixes": ["pickle"],
            },
            sandbox=sandbox,
        )
        assert not result.success
        assert any("pickle" in w for w in result.warnings)

    def test_submodule_forbidden(self) -> None:
        sandbox, tmp = _make_sandbox()
        filepath = os.path.join(tmp, "sub.py")
        with open(filepath, "w") as f:
            f.write("from ctypes import cdll\n")

        agent = BuildAgent()
        result = agent.execute_task(
            {"type": "check_import_rules", "path": "sub.py"},
            sandbox=sandbox,
        )
        assert not result.success
        assert result.failed >= 1

    def test_missing_path_fails(self) -> None:
        sandbox, _ = _make_sandbox()
        agent = BuildAgent()
        result = agent.execute_task(
            {"type": "check_import_rules"},
            sandbox=sandbox,
        )
        assert not result.success
        assert "path" in result.error

    def test_syntax_error_in_file(self) -> None:
        sandbox, tmp = _make_sandbox()
        filepath = os.path.join(tmp, "broken.py")
        with open(filepath, "w") as f:
            f.write("import os\ndef (broken\n")

        agent = BuildAgent()
        result = agent.execute_task(
            {"type": "check_import_rules", "path": "broken.py"},
            sandbox=sandbox,
        )
        assert not result.success
        assert "Syntax error" in result.error

    def test_metadata_imports_checked(self) -> None:
        sandbox, tmp = _make_sandbox()
        filepath = os.path.join(tmp, "meta.py")
        with open(filepath, "w") as f:
            f.write("import os\nimport sys\n")

        agent = BuildAgent()
        result = agent.execute_task(
            {"type": "check_import_rules", "path": "meta.py"},
            sandbox=sandbox,
        )
        assert result.success
        assert result.metadata["imports_checked"] == 2


# --------------------------------------------------------------------------- #
# Unknown task type
# --------------------------------------------------------------------------- #


class TestUnknownBuildTask:
    """BuildAgent error handling."""

    def test_unknown_type(self) -> None:
        sandbox, _ = _make_sandbox()
        agent = BuildAgent()
        result = agent.execute_task(
            {"type": "deploy_to_prod"},
            sandbox=sandbox,
        )
        assert not result.success
        assert "Unknown task type" in result.error


# --------------------------------------------------------------------------- #
# Introspection
# --------------------------------------------------------------------------- #


class TestBuildAgentIntrospection:
    """BuildAgent tracking and state."""

    def test_result_count(self) -> None:
        sandbox, tmp = _make_sandbox()
        with open(os.path.join(tmp, "ok.py"), "w") as f:
            f.write("x = 1\n")

        agent = BuildAgent()
        agent.execute_task(
            {"type": "check_syntax", "path": "ok.py"},
            sandbox=sandbox,
        )
        assert agent.result_count == 1

    def test_all_passing(self) -> None:
        sandbox, tmp = _make_sandbox()
        with open(os.path.join(tmp, "ok.py"), "w") as f:
            f.write("x = 1\n")

        agent = BuildAgent()
        agent.execute_task(
            {"type": "check_syntax", "path": "ok.py"},
            sandbox=sandbox,
        )
        assert agent.all_passing is True

    def test_not_all_passing(self) -> None:
        sandbox, tmp = _make_sandbox()
        with open(os.path.join(tmp, "bad.py"), "w") as f:
            f.write("def (\n")

        agent = BuildAgent()
        agent.execute_task(
            {"type": "check_syntax", "path": "bad.py"},
            sandbox=sandbox,
        )
        assert agent.all_passing is False

    def test_summary_empty(self) -> None:
        agent = BuildAgent()
        s = agent.summary
        assert s["total_tasks"] == 0
        assert s["all_passing"] is True
        assert s["by_type"] == {}

    def test_summary_after_tasks(self) -> None:
        sandbox, tmp = _make_sandbox()
        with open(os.path.join(tmp, "ok.py"), "w") as f:
            f.write("x = 1\n")
        with open(os.path.join(tmp, "bad.py"), "w") as f:
            f.write("def (\n")

        agent = BuildAgent()
        agent.execute_task({"type": "check_syntax", "path": "ok.py"}, sandbox=sandbox)
        agent.execute_task({"type": "check_syntax", "path": "bad.py"}, sandbox=sandbox)

        s = agent.summary
        assert s["total_tasks"] == 2
        assert s["total_passed"] == 1
        assert s["total_failed"] == 1
        assert s["all_passing"] is False
        assert "check_syntax" in s["by_type"]
        assert s["by_type"]["check_syntax"]["count"] == 2
