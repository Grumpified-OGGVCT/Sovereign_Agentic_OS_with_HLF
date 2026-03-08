"""
Build Agent — Runs Tests, Lint, and Validates Code Quality.

The "verifier" of the agent system.  Executes pytest, ruff, import
checks, and syntax validation within the AgentSandbox.  Parses
structured output to provide actionable build results.

Task types:
  - run_tests: Execute pytest and parse pass/fail/error counts
  - run_lint: Execute ruff and parse violations
  - validate_imports: Check that all imports resolve
  - check_syntax: AST-parse a file to catch syntax errors

Usage::

    from agents.core.build_agent import BuildAgent

    agent = BuildAgent(agent_id="build-01")
    result = agent.execute_task(
        {"type": "run_tests", "test_path": "tests/"},
        sandbox=sandbox,
    )
"""

from __future__ import annotations

import ast
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from agents.core.agent_sandbox import AgentSandbox

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Build Result
# --------------------------------------------------------------------------- #


@dataclass
class BuildResult:
    """Result of a build agent task execution.

    Attributes:
        success: Whether the build/check passed.
        task_type: The type of check that was run.
        passed: Number of tests/checks that passed.
        failed: Number of tests/checks that failed.
        errors: Number of errors encountered.
        warnings: List of warning messages.
        output: Raw output from the tool.
        error: Error message if the task itself failed.
        duration: Execution time in seconds.
        metadata: Extra context from the task.
    """
    success: bool
    task_type: str = ""
    passed: int = 0
    failed: int = 0
    errors: int = 0
    warnings: list[str] = field(default_factory=list)
    output: str = ""
    error: str | None = None
    duration: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Build Agent
# --------------------------------------------------------------------------- #


class BuildAgent:
    """Agent that runs tests, linting, and code validation.

    All operations are performed through the AgentSandbox's
    terminal and file operations.

    Attributes:
        agent_id: Unique identifier for this agent instance.
        agent_role: Persona role (for permission lookups).
    """

    TASK_TYPES = {"run_tests", "run_lint", "validate_imports", "check_syntax"}

    def __init__(
        self,
        agent_id: str = "build-agent-01",
        agent_role: str = "developer",
    ) -> None:
        self.agent_id = agent_id
        self.agent_role = agent_role
        self._results: list[BuildResult] = []

    def execute_task(
        self, task: dict[str, Any], sandbox: AgentSandbox,
    ) -> BuildResult:
        """Execute a build/verification task within the sandbox.

        Args:
            task: Task specification with a 'type' field.
            sandbox: AgentSandbox for file and terminal operations.

        Returns:
            BuildResult with pass/fail counts and output.
        """
        start = time.time()
        task_type = task.get("type", "")

        if task_type not in self.TASK_TYPES:
            return BuildResult(
                success=False,
                task_type=task_type,
                error=(
                    f"Unknown task type: '{task_type}'. "
                    f"Supported: {sorted(self.TASK_TYPES)}"
                ),
                duration=time.time() - start,
            )

        try:
            if task_type == "run_tests":
                result = self._run_tests(task, sandbox)
            elif task_type == "run_lint":
                result = self._run_lint(task, sandbox)
            elif task_type == "validate_imports":
                result = self._validate_imports(task, sandbox)
            elif task_type == "check_syntax":
                result = self._check_syntax(task, sandbox)
            else:
                result = BuildResult(
                    success=False,
                    task_type=task_type,
                    error=f"Unhandled task type: {task_type}",
                )

            result.duration = time.time() - start
            result.task_type = task_type
            self._results.append(result)

            self._log_align("BUILD_AGENT_TASK", {
                "agent_id": self.agent_id,
                "task_type": task_type,
                "success": result.success,
                "passed": result.passed,
                "failed": result.failed,
                "errors": result.errors,
                "duration": result.duration,
            })

            return result

        except Exception as e:
            result = BuildResult(
                success=False,
                task_type=task_type,
                error=f"Task execution failed: {e}",
                duration=time.time() - start,
            )
            self._results.append(result)
            return result

    # ------------------------------------------------------------------ #
    # Task Implementations
    # ------------------------------------------------------------------ #

    def _run_tests(
        self, task: dict[str, Any], sandbox: AgentSandbox,
    ) -> BuildResult:
        """Run pytest and parse output.

        Task fields:
            test_path: Path to test file/directory (default: "tests/")
            extra_args: Additional pytest arguments (default: "")
        """
        test_path = task.get("test_path", "tests/")
        extra_args = task.get("extra_args", "-v --tb=short")

        tool_result = sandbox.run_tests(test_path, extra_args)
        output = tool_result.output or ""

        # Parse pytest output for counts
        passed, failed, errors = self._parse_pytest_output(output)

        return BuildResult(
            success=tool_result.success,
            passed=passed,
            failed=failed,
            errors=errors,
            output=output,
            metadata={
                "test_path": test_path,
                "exit_code": tool_result.metadata.get("exit_code", -1),
            },
        )

    def _run_lint(
        self, task: dict[str, Any], sandbox: AgentSandbox,
    ) -> BuildResult:
        """Run ruff linter and parse output.

        Task fields:
            paths: Paths to lint (default: ".")
        """
        paths = task.get("paths", ".")
        tool_result = sandbox.run_lint(paths)
        output = tool_result.output or ""

        # Parse ruff output
        violations = self._parse_ruff_output(output)
        is_clean = tool_result.success

        return BuildResult(
            success=is_clean,
            passed=1 if is_clean else 0,
            failed=0 if is_clean else 1,
            errors=len(violations),
            warnings=violations[:10],  # Cap at 10 warnings
            output=output,
            metadata={"paths": paths, "violation_count": len(violations)},
        )

    def _validate_imports(
        self, task: dict[str, Any], sandbox: AgentSandbox,
    ) -> BuildResult:
        """Check that all imports in a file are syntactically valid.

        Task fields:
            path: Path to Python file to validate
        """
        path = task.get("path", "")
        if not path:
            return BuildResult(
                success=False,
                error="validate_imports requires 'path' field",
            )

        read_result = sandbox.read_file(path)
        if not read_result.success:
            return BuildResult(
                success=False,
                error=f"Cannot read {path}: {read_result.error}",
            )

        content = read_result.output
        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            return BuildResult(
                success=False,
                errors=1,
                error=f"Syntax error prevents import validation: {e}",
                metadata={"path": path},
            )

        imports: list[str] = []
        warnings: list[str] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imports.append(module)

        # Check for relative imports that go too deep
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level and node.level > 3:
                warnings.append(
                    f"Deep relative import (level {node.level}): "
                    f"{node.module or ''}"
                )

        return BuildResult(
            success=len(warnings) == 0,
            passed=len(imports),
            warnings=warnings,
            output=f"Found {len(imports)} import statements",
            metadata={"path": path, "imports": imports},
        )

    def _check_syntax(
        self, task: dict[str, Any], sandbox: AgentSandbox,
    ) -> BuildResult:
        """AST-parse a file to check for syntax errors.

        Task fields:
            path: Path to Python file to check
        """
        path = task.get("path", "")
        if not path:
            return BuildResult(
                success=False,
                error="check_syntax requires 'path' field",
            )

        read_result = sandbox.read_file(path)
        if not read_result.success:
            return BuildResult(
                success=False,
                error=f"Cannot read {path}: {read_result.error}",
            )

        content = read_result.output
        try:
            ast.parse(content, filename=path)
            return BuildResult(
                success=True,
                passed=1,
                output=f"Syntax OK: {path}",
                metadata={"path": path, "lines": len(content.splitlines())},
            )
        except SyntaxError as e:
            return BuildResult(
                success=False,
                failed=1,
                errors=1,
                error=f"Syntax error in {path}: {e.msg} (line {e.lineno})",
                output=str(e),
                metadata={
                    "path": path,
                    "line": e.lineno,
                    "offset": e.offset,
                },
            )

    # ------------------------------------------------------------------ #
    # Output Parsers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_pytest_output(output: str) -> tuple[int, int, int]:
        """Parse pytest summary line for pass/fail/error counts.

        Handles formats like:
            "5 passed, 2 failed, 1 error in 3.45s"
            "10 passed in 1.23s"

        Returns:
            (passed, failed, errors) tuple
        """
        passed = failed = errors = 0

        # Match "N passed"
        m = re.search(r"(\d+)\s+passed", output)
        if m:
            passed = int(m.group(1))

        # Match "N failed"
        m = re.search(r"(\d+)\s+failed", output)
        if m:
            failed = int(m.group(1))

        # Match "N error" (singular or plural)
        m = re.search(r"(\d+)\s+errors?", output)
        if m:
            errors = int(m.group(1))

        return passed, failed, errors

    @staticmethod
    def _parse_ruff_output(output: str) -> list[str]:
        """Parse ruff output for violation messages.

        Returns list of violation strings.
        """
        violations = []
        for line in output.splitlines():
            # Ruff output format: "path:line:col: CODE message"
            if re.match(r".+:\d+:\d+:\s+\w+\d+", line):
                violations.append(line.strip())
        return violations

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #

    @property
    def results(self) -> list[BuildResult]:
        """All results from this agent."""
        return list(self._results)

    @property
    def result_count(self) -> int:
        """Number of tasks executed."""
        return len(self._results)

    @property
    def all_passing(self) -> bool:
        """True if all executed tasks succeeded."""
        return all(r.success for r in self._results) if self._results else True

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _log_align(self, event: str, data: dict) -> None:
        """Log to ALIGN ledger."""
        try:
            from agents.core.als_logger import ALSLogger
            als = ALSLogger()
            als.log(event, data)
        except ImportError:
            # ALIGN ledger is optional; skip logging if not installed
            logger.debug(
                "ALSLogger not available; skipping ALIGN logging for %s",
                event,
            )
