"""
Build Agent — Runs Tests, Lint, and Validates Code Quality.

The "verifier" of the agent system.  Executes pytest, ruff, import
checks, and syntax validation within the AgentSandbox.  Parses
structured output to provide actionable build results.

Task types:
  - run_tests: Execute pytest and parse pass/fail/error counts
  - run_lint: Execute ruff and parse violations
  - validate_imports: Analyze imports and flag disallowed or deep relative imports
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
from agents.core.ast_validator import validate_code as _validate_code

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
        skipped: Number of tests skipped (run_tests only).
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
    skipped: int = 0
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

    TASK_TYPES = {
        "run_tests",
        "run_lint",
        "validate_imports",
        "check_syntax",
        "check_forbidden_calls",
        "check_import_rules",
    }

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
            elif task_type == "check_forbidden_calls":
                result = self._check_forbidden_calls(task, sandbox)
            elif task_type == "check_import_rules":
                result = self._check_import_rules(task, sandbox)
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
        extra_args = task.get("extra_args", "-v")

        tool_result = sandbox.run_tests(test_path, extra_args)
        output = tool_result.output or ""

        # Parse pytest output for counts
        passed, failed, errors = self._parse_pytest_output(output)
        skipped = self._parse_pytest_skipped(output)

        return BuildResult(
            success=tool_result.success,
            passed=passed,
            failed=failed,
            skipped=skipped,
            errors=errors,
            output=output,
            error=tool_result.error if not tool_result.success else None,
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
            extra_args: Extra ruff arguments, e.g. "--select E,F --ignore E501"
        """
        paths = task.get("paths", ".")
        extra_args = task.get("extra_args", "")
        tool_result = sandbox.run_lint(paths, extra_args=extra_args)
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
            error=tool_result.error if not is_clean else None,
            metadata={
                "paths": paths,
                "violation_count": len(violations),
                "all_violations": violations,
            },
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

    def _check_forbidden_calls(
        self, task: dict[str, Any], sandbox: AgentSandbox,
    ) -> BuildResult:
        """Scan a file for forbidden AST patterns (os.system, subprocess.*, eval, exec).

        Uses ast_validator.validate_code() from the governance module.

        Task fields:
            path: Path to Python file to scan
        """
        path = task.get("path", "")
        if not path:
            return BuildResult(
                success=False,
                error="check_forbidden_calls requires 'path' field",
            )

        read_result = sandbox.read_file(path)
        if not read_result.success:
            return BuildResult(
                success=False,
                error=f"Cannot read {path}: {read_result.error}",
            )

        content = read_result.output
        is_safe, violations = _validate_code(content)

        return BuildResult(
            success=is_safe,
            passed=1 if is_safe else 0,
            failed=0 if is_safe else 1,
            errors=len(violations),
            warnings=violations,
            output=(
                f"No forbidden patterns found in {path}"
                if is_safe
                else f"{len(violations)} forbidden pattern(s) found in {path}"
            ),
            error=f"{len(violations)} forbidden call(s) detected" if not is_safe else None,
            metadata={"path": path, "violations": violations},
        )

    def _check_import_rules(
        self, task: dict[str, Any], sandbox: AgentSandbox,
    ) -> BuildResult:
        """Check imports in a file against configurable forbidden prefixes.

        Defaults to governance-defined forbidden prefixes when none are supplied.

        Task fields:
            path: Path to Python file to check
            forbidden_prefixes: Optional list of forbidden import prefix strings.
                Defaults to ["ctypes", "cffi"] from governance/module_import_rules.yaml.
        """
        # Governance-defined forbidden prefixes — mirrors governance/module_import_rules.yaml
        # (M-001 forbidden_prefixes). Update both locations when adding new rules.
        _GOVERNANCE_FORBIDDEN_PREFIXES = ["ctypes", "cffi"]

        path = task.get("path", "")
        if not path:
            return BuildResult(
                success=False,
                error="check_import_rules requires 'path' field",
            )

        forbidden: list[str] = task.get("forbidden_prefixes", _GOVERNANCE_FORBIDDEN_PREFIXES)

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
                error=f"Syntax error prevents import rule check: {e}",
                metadata={"path": path},
            )

        imported_modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module:
                    imported_modules.append(module)

        violations: list[str] = []
        for module in imported_modules:
            for prefix in forbidden:
                if module == prefix or module.startswith(f"{prefix}."):
                    violations.append(
                        f"Forbidden import '{module}' matches rule '{prefix}'"
                    )
                    break

        return BuildResult(
            success=len(violations) == 0,
            passed=len(imported_modules) - len(violations),
            failed=len(violations),
            warnings=violations,
            output=(
                f"Checked {len(imported_modules)} import(s); "
                f"{len(violations)} violation(s) found"
            ),
            error=f"{len(violations)} forbidden import(s) detected" if violations else None,
            metadata={"path": path, "imports_checked": len(imported_modules)},
        )

    # ------------------------------------------------------------------ #
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
    def _parse_pytest_skipped(output: str) -> int:
        """Parse pytest summary line for skipped test count.

        Handles formats like:
            "5 passed, 2 skipped in 3.45s"
            "10 passed, 1 skipped, 1 warning in 1.23s"

        Returns:
            Number of skipped tests.
        """
        m = re.search(r"(\d+)\s+skipped", output)
        return int(m.group(1)) if m else 0

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

    @property
    def summary(self) -> dict[str, Any]:
        """Aggregate summary of all executed tasks.

        Returns:
            Dict with total_tasks, total_passed, total_failed, total_errors,
            total_skipped, all_passing, and per-type breakdown.
        """
        per_type: dict[str, dict[str, int]] = {}
        for r in self._results:
            entry = per_type.setdefault(r.task_type, {
                "count": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0,
            })
            entry["count"] += 1
            entry["passed"] += r.passed
            entry["failed"] += r.failed
            entry["errors"] += r.errors
            entry["skipped"] += r.skipped

        return {
            "total_tasks": len(self._results),
            "total_passed": sum(r.passed for r in self._results),
            "total_failed": sum(r.failed for r in self._results),
            "total_errors": sum(r.errors for r in self._results),
            "total_skipped": sum(r.skipped for r in self._results),
            "all_passing": self.all_passing,
            "by_type": per_type,
        }

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
