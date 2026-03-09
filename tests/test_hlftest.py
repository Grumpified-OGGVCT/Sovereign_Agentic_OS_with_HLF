"""
tests/test_hlftest.py — Unit tests for the HLF Test Harness.

Tests HLFTestRunner, assertion helpers, test report,
and CLI entry point.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from hlf.hlftest import (
    HLFTestResult,
    HLFTestReport,
    HLFTestRunner,
    assert_compiles,
    assert_lints_clean,
    assert_gas_under,
    _cli_main,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _prog(body: str = "[SET] x = 42") -> str:
    """Wrap HLF body in valid program structure."""
    return f"[HLF-v2]\n{body}\nΩ"


# ─── HLFTestResult Tests ────────────────────────────────────────────────────


class TestHLFTestResult:

    def test_passed_when_compiles_no_warnings(self):
        r = HLFTestResult(source="test.hlf", compiles=True, lint_warnings=[])
        assert r.passed is True

    def test_failed_when_compile_error(self):
        r = HLFTestResult(source="test.hlf", compiles=False)
        assert r.passed is False

    def test_failed_when_lint_warnings(self):
        r = HLFTestResult(source="test.hlf", compiles=True, lint_warnings=["W001"])
        assert r.passed is False


# ─── HLFTestReport Tests ────────────────────────────────────────────────────


class TestHLFTestReport:

    def test_empty_report(self):
        r = HLFTestReport()
        assert r.total == 0
        assert r.passed == 0
        assert r.failed == 0

    def test_report_counts(self):
        r = HLFTestReport(results=[
            HLFTestResult(source="a", compiles=True),
            HLFTestResult(source="b", compiles=False),
            HLFTestResult(source="c", compiles=True, lint_warnings=["W"]),
        ])
        assert r.total == 3
        assert r.passed == 1
        assert r.failed == 2
        assert r.compile_errors == 1

    def test_lint_warning_count(self):
        r = HLFTestReport(results=[
            HLFTestResult(source="a", compiles=True, lint_warnings=["W1", "W2"]),
            HLFTestResult(source="b", compiles=True, lint_warnings=["W3"]),
        ])
        assert r.lint_warning_count == 3


# ─── HLFTestRunner Tests ────────────────────────────────────────────────────


class TestHLFTestRunner:

    def test_valid_source_compiles(self):
        runner = HLFTestRunner()
        result = runner.test_source(_prog())
        assert result.compiles is True

    def test_invalid_source_fails(self):
        runner = HLFTestRunner()
        result = runner.test_source("this is not valid HLF")
        assert result.compiles is False
        assert result.compile_error != ""

    def test_gas_counted(self):
        runner = HLFTestRunner()
        result = runner.test_source(_prog())
        assert result.gas_used >= 0

    def test_elapsed_ms_recorded(self):
        runner = HLFTestRunner()
        result = runner.test_source(_prog())
        assert result.elapsed_ms >= 0

    def test_test_file(self, tmp_path):
        hlf_file = tmp_path / "test.hlf"
        hlf_file.write_text(_prog(), encoding="utf-8")

        runner = HLFTestRunner()
        result = runner.test_file(hlf_file)
        assert result.compiles is True
        assert str(hlf_file) in result.source

    def test_test_file_not_found(self, tmp_path):
        runner = HLFTestRunner()
        with pytest.raises(FileNotFoundError):
            runner.test_file(tmp_path / "nonexistent.hlf")

    def test_test_directory(self, tmp_path):
        # Create a few .hlf files
        for name in ["a", "b", "c"]:
            (tmp_path / f"{name}.hlf").write_text(_prog(), encoding="utf-8")

        runner = HLFTestRunner()
        report = runner.test_directory(tmp_path)
        assert report.total == 3
        assert report.total_gas >= 0

    def test_test_directory_empty(self, tmp_path):
        runner = HLFTestRunner()
        report = runner.test_directory(tmp_path)
        assert report.total == 0

    def test_count_nodes_dict(self):
        ast = {"type": "root", "children": [{"type": "leaf"}]}
        count = HLFTestRunner._count_nodes(ast)
        assert count >= 2  # at least root + leaf

    def test_count_nodes_flat(self):
        assert HLFTestRunner._count_nodes({"type": "leaf"}) >= 1


# ─── Assertion Helper Tests ─────────────────────────────────────────────────


class TestAssertionHelpers:

    def test_assert_compiles_passes(self):
        result = assert_compiles(_prog())
        assert result.compiles is True

    def test_assert_compiles_fails(self):
        with pytest.raises(AssertionError, match="compilation failed"):
            assert_compiles("invalid HLF code")

    def test_assert_compiles_custom_message(self):
        with pytest.raises(AssertionError, match="my context"):
            assert_compiles("bad", message="my context")

    def test_assert_gas_under_passes(self):
        result = assert_gas_under(_prog(), limit=10000)
        assert result.gas_used < 10000

    def test_assert_gas_under_fails(self):
        with pytest.raises(AssertionError, match="exceeds limit"):
            assert_gas_under(_prog(), limit=0)


# ─── CLI Tests ───────────────────────────────────────────────────────────────


class TestCLI:

    def test_cli_no_args_shows_help(self, capsys):
        code = _cli_main([])
        assert code == 0

    def test_cli_test_file(self, tmp_path, capsys):
        hlf_file = tmp_path / "test.hlf"
        hlf_file.write_text(_prog(), encoding="utf-8")

        code = _cli_main([str(hlf_file)])
        assert code == 0

    def test_cli_test_directory(self, tmp_path, capsys):
        for name in ["a", "b"]:
            (tmp_path / f"{name}.hlf").write_text(_prog(), encoding="utf-8")

        code = _cli_main([str(tmp_path)])
        assert code == 0

    def test_cli_invalid_file(self, tmp_path, capsys):
        hlf_file = tmp_path / "bad.hlf"
        hlf_file.write_text("this is not valid HLF", encoding="utf-8")

        code = _cli_main([str(hlf_file)])
        assert code == 1
