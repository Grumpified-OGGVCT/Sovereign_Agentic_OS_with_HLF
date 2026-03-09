"""
HLF Test Harness — Compile, lint, and validate HLF source files.

Provides:
  - HLFTestRunner for programmatic test execution
  - assert_compiles(), assert_lints_clean(), assert_gas_under() helpers
  - pytest plugin (--hlf-dir) for auto-discovering .hlf test files
  - CLI entry: python -m hlf.hlftest [path ...]

Part of Phase 5.3 — DX Tooling (Sovereign OS Master Build Plan).
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hlf.hlfc import compile as hlf_compile
from hlf.hlflint import lint as hlf_lint

_logger = logging.getLogger("hlf.test")


# ─── Test Result ─────────────────────────────────────────────────────────────


@dataclass
class HLFTestResult:
    """Result of testing a single HLF source file or snippet."""

    source: str                 # File path or "<snippet>"
    compiles: bool = False
    compile_error: str = ""
    lint_warnings: list[str] = field(default_factory=list)
    gas_used: int = 0
    ast_node_count: int = 0
    elapsed_ms: float = 0.0

    @property
    def passed(self) -> bool:
        """True if compilation succeeded and no lint warnings."""
        return self.compiles and len(self.lint_warnings) == 0


# ─── Test Report ─────────────────────────────────────────────────────────────


@dataclass
class HLFTestReport:
    """Aggregated test report."""

    results: list[HLFTestResult] = field(default_factory=list)
    total_gas: int = 0
    total_elapsed_ms: float = 0.0

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def compile_errors(self) -> int:
        return sum(1 for r in self.results if not r.compiles)

    @property
    def lint_warning_count(self) -> int:
        return sum(len(r.lint_warnings) for r in self.results)


# ─── Test Runner ─────────────────────────────────────────────────────────────


class HLFTestRunner:
    """
    HLF Test Runner — compiles, lints, and validates HLF sources.

    Args:
        gas_limit: Maximum allowed gas per file (0 = unlimited).
        strict_lint: Treat lint warnings as failures.
    """

    def __init__(
        self,
        gas_limit: int = 0,
        strict_lint: bool = True,
    ):
        self.gas_limit = gas_limit
        self.strict_lint = strict_lint

    def test_source(self, source: str, name: str = "<snippet>") -> HLFTestResult:
        """
        Test a single HLF source string.

        Args:
            source: HLF source code.
            name: Name for the test result (file path or label).

        Returns:
            HLFTestResult with compilation and lint results.
        """
        start = time.monotonic()
        result = HLFTestResult(source=name)

        # Step 1: Compile
        try:
            ast = hlf_compile(source)
            result.compiles = True

            # Count AST nodes for gas estimation
            if isinstance(ast, dict):
                result.ast_node_count = self._count_nodes(ast)
                result.gas_used = result.ast_node_count
            elif isinstance(ast, list):
                result.ast_node_count = sum(
                    self._count_nodes(n) if isinstance(n, dict) else 1
                    for n in ast
                )
                result.gas_used = result.ast_node_count

        except Exception as e:
            result.compiles = False
            result.compile_error = str(e)

        # Step 2: Lint
        try:
            warnings = hlf_lint(source)
            if isinstance(warnings, list):
                result.lint_warnings = [str(w) for w in warnings]
        except Exception:
            pass  # Lint failures are non-fatal

        result.elapsed_ms = (time.monotonic() - start) * 1000
        return result

    def test_file(self, path: Path) -> HLFTestResult:
        """
        Test a single .hlf file.

        Args:
            path: Path to the .hlf file.

        Returns:
            HLFTestResult.

        Raises:
            FileNotFoundError: If path doesn't exist.
        """
        if not path.exists():
            raise FileNotFoundError(f"HLF file not found: {path}")

        source = path.read_text(encoding="utf-8")
        return self.test_source(source, name=str(path))

    def test_directory(self, directory: Path, pattern: str = "*.hlf") -> HLFTestReport:
        """
        Test all .hlf files in a directory (recursive).

        Args:
            directory: Root directory to scan.
            pattern: Glob pattern for matching files.

        Returns:
            HLFTestReport with aggregated results.
        """
        report = HLFTestReport()
        start = time.monotonic()

        for path in sorted(directory.rglob(pattern)):
            result = self.test_file(path)
            report.results.append(result)
            report.total_gas += result.gas_used

        report.total_elapsed_ms = (time.monotonic() - start) * 1000
        return report

    @staticmethod
    def _count_nodes(node: dict) -> int:
        """Recursively count AST nodes."""
        count = 1
        for value in node.values():
            if isinstance(value, dict):
                count += HLFTestRunner._count_nodes(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        count += HLFTestRunner._count_nodes(item)
                    else:
                        count += 1
        return count


# ─── Assertion Helpers ───────────────────────────────────────────────────────


def assert_compiles(source: str, message: str = "") -> HLFTestResult:
    """
    Assert that HLF source compiles successfully.

    Args:
        source: HLF source code.
        message: Optional assertion message.

    Raises:
        AssertionError: If compilation fails.
    """
    runner = HLFTestRunner()
    result = runner.test_source(source, name="<assert_compiles>")
    if not result.compiles:
        msg = f"HLF compilation failed: {result.compile_error}"
        if message:
            msg = f"{message}: {msg}"
        raise AssertionError(msg)
    return result


def assert_lints_clean(source: str, message: str = "") -> HLFTestResult:
    """
    Assert that HLF source has no lint warnings.

    Args:
        source: HLF source code.
        message: Optional assertion message.

    Raises:
        AssertionError: If lint warnings found.
    """
    runner = HLFTestRunner()
    result = runner.test_source(source, name="<assert_lints_clean>")
    if result.lint_warnings:
        msg = f"HLF lint warnings ({len(result.lint_warnings)}): {result.lint_warnings[:3]}"
        if message:
            msg = f"{message}: {msg}"
        raise AssertionError(msg)
    return result


def assert_gas_under(source: str, limit: int, message: str = "") -> HLFTestResult:
    """
    Assert that HLF source uses gas under a specified limit.

    Args:
        source: HLF source code.
        limit: Maximum gas allowed.
        message: Optional assertion message.

    Raises:
        AssertionError: If gas exceeds limit.
    """
    runner = HLFTestRunner()
    result = runner.test_source(source, name="<assert_gas_under>")
    if result.gas_used > limit:
        msg = f"Gas {result.gas_used} exceeds limit {limit}"
        if message:
            msg = f"{message}: {msg}"
        raise AssertionError(msg)
    return result


# ─── pytest Plugin ───────────────────────────────────────────────────────────


def pytest_addoption(parser):
    """Register --hlf-dir option with pytest."""
    parser.addoption(
        "--hlf-dir",
        action="store",
        default=None,
        help="Directory containing .hlf test files",
    )


def pytest_collect_file(parent, file_path):
    """Collect .hlf files as test items when --hlf-dir is used."""
    if file_path.suffix == ".hlf" and file_path.stat().st_size > 0:
        return HLFTestFile.from_parent(parent, path=file_path)
    return None


class HLFTestFile:
    """pytest collector for .hlf files."""

    @classmethod
    def from_parent(cls, parent, path):
        """Create an HLF test file collector."""
        instance = cls()
        instance._path = path
        instance._parent = parent
        return instance

    def collect(self):
        """Yield test items from the .hlf file."""
        yield HLFTestItem.from_parent(
            self._parent,
            name=f"hlf_compile[{self._path.name}]",
            path=self._path,
        )


class HLFTestItem:
    """pytest test item for a single .hlf file."""

    @classmethod
    def from_parent(cls, parent, name, path):
        instance = cls()
        instance._name = name
        instance._path = path
        return instance

    def runtest(self):
        """Run compilation + lint on the .hlf file."""
        source = self._path.read_text(encoding="utf-8")
        result = HLFTestRunner().test_source(source, name=str(self._path))
        if not result.compiles:
            raise Exception(f"Compile error: {result.compile_error}")

    def repr_failure(self, excinfo):
        return str(excinfo.value)

    def reportinfo(self):
        return self._path, 0, f"hlf: {self._path.name}"


# ─── CLI Entry Point ────────────────────────────────────────────────────────


def _cli_main(argv: list[str] | None = None) -> int:
    """CLI entry point for hlf-test."""
    args = argv if argv is not None else sys.argv[1:]

    if not args:
        print("Usage: python -m hlf.hlftest <path> [paths...]")
        print("\nTest .hlf files or directories containing .hlf files.")
        print("Options:")
        print("  --strict    Treat lint warnings as failures")
        print("  --gas-limit N  Maximum gas per file")
        return 0

    strict = "--strict" in args
    gas_limit = 0

    if "--gas-limit" in args:
        idx = args.index("--gas-limit")
        if idx + 1 < len(args):
            gas_limit = int(args[idx + 1])
            args = [a for i, a in enumerate(args) if i != idx and i != idx + 1]

    paths = [Path(a) for a in args if not a.startswith("--")]
    runner = HLFTestRunner(gas_limit=gas_limit, strict_lint=strict)

    total_report = HLFTestReport()

    for p in paths:
        if p.is_dir():
            report = runner.test_directory(p)
            total_report.results.extend(report.results)
            total_report.total_gas += report.total_gas
        elif p.is_file():
            result = runner.test_file(p)
            total_report.results.append(result)
            total_report.total_gas += result.gas_used
        else:
            print(f"⚠ Path not found: {p}", file=sys.stderr)

    # Print results
    for r in total_report.results:
        status = "✓" if r.passed else "✗"
        warns = f" ({len(r.lint_warnings)} warnings)" if r.lint_warnings else ""
        err = f" — {r.compile_error}" if r.compile_error else ""
        print(f"  {status} {r.source}{warns}{err}  [{r.gas_used}g, {r.elapsed_ms:.0f}ms]")

    print(f"\n{'─' * 60}")
    print(f"  Total: {total_report.total}  Passed: {total_report.passed}  Failed: {total_report.failed}")
    print(f"  Gas: {total_report.total_gas}  Time: {total_report.total_elapsed_ms:.0f}ms")

    if strict:
        return 0 if total_report.failed == 0 else 1
    else:
        return 0 if total_report.compile_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(_cli_main())
