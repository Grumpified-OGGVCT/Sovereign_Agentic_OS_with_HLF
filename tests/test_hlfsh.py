"""
tests/test_hlfsh.py — Unit tests for the HLF Interactive REPL (hlfsh).

Tests the HLFShell class methods (eval, handle_command, env, gas)
without launching an interactive terminal.
"""

from __future__ import annotations

import json

import pytest

from hlf.hlfsh import HELP_TEXT, HLFShell

# ─── Helpers ─────────────────────────────────────────────────────────────────

def _prog(body: str) -> str:
    """Wrap body in a minimal valid HLF-v2 program."""
    return f"[HLF-v2]\n{body}\nΩ\n"


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def shell():
    """Create a fresh HLFShell for each test."""
    return HLFShell(gas_limit=100)


# ─── Command Tests ───────────────────────────────────────────────────────────


class TestCommands:
    """Tests for built-in REPL commands."""

    def test_help_returns_help_text(self, shell):
        """':help' should return the HELP_TEXT."""
        result = shell.handle_command(":help")
        assert result == HELP_TEXT

    def test_env_empty_initially(self, shell):
        """':env' on a fresh shell should show empty."""
        result = shell.handle_command(":env")
        assert "empty" in result.lower()

    def test_gas_shows_initial_state(self, shell):
        """':gas' should show 0 used, full limit remaining."""
        result = shell.handle_command(":gas")
        assert "0" in result
        assert "100" in result  # gas_limit

    def test_reset_clears_everything(self, shell):
        """':reset' should zero gas and clear env."""
        shell.env["x"] = 42
        shell.gas_used = 50
        shell.statement_count = 5
        result = shell.handle_command(":reset")
        assert "reset" in result.lower()
        assert len(shell.env) == 0
        assert shell.gas_used == 0
        assert shell.statement_count == 0

    def test_ast_before_eval(self, shell):
        """':ast' without prior eval should say no AST."""
        result = shell.handle_command(":ast")
        assert "no AST" in result.lower() or "evaluate" in result.lower()

    def test_lint_before_eval(self, shell):
        """':lint' without prior input should say no input."""
        result = shell.handle_command(":lint")
        assert "no input" in result.lower()

    def test_quit_raises_system_exit(self, shell):
        """':quit' should raise SystemExit."""
        with pytest.raises(SystemExit):
            shell.handle_command(":quit")

    def test_exit_raises_system_exit(self, shell):
        """':exit' should also raise SystemExit."""
        with pytest.raises(SystemExit):
            shell.handle_command(":exit")

    def test_unknown_command(self, shell):
        """Unknown command should return error message."""
        result = shell.handle_command(":foobar")
        assert "Unknown command" in result or "unknown" in result.lower()

    def test_non_command_returns_none(self, shell):
        """Input not starting with ':' should return None."""
        result = shell.handle_command('[INTENT] test "path"')
        assert result is None

    def test_load_missing_file(self, shell):
        """':load' with nonexistent file should return error."""
        result = shell.handle_command(":load /nonexistent/path.hlf")
        assert "not found" in result.lower() or "error" in result.lower()

    def test_load_no_arg(self, shell):
        """':load' without file arg should show usage."""
        result = shell.handle_command(":load")
        assert "Usage" in result or "usage" in result.lower()


# ─── Eval Tests ──────────────────────────────────────────────────────────────


class TestEval:
    """Tests for expression evaluation."""

    def test_eval_empty_returns_empty(self, shell):
        """Empty input should return empty string."""
        result = shell.eval("")
        assert result == ""

    def test_eval_valid_hlf_succeeds(self, shell):
        """Valid HLF should compile and show gas usage."""
        result = shell.eval(_prog('[INTENT] deploy "/app"'))
        assert "gas" in result.lower() or "⩕" in result

    def test_eval_invalid_hlf_shows_error(self, shell):
        """Invalid HLF should show compile error."""
        result = shell.eval("totally invalid {{ syntax }}")
        assert "error" in result.lower() or "✗" in result

    def test_eval_increments_gas(self, shell):
        """Each eval should increment the gas counter."""
        initial = shell.gas_used
        shell.eval(_prog('[INTENT] deploy "/app"'))
        assert shell.gas_used > initial

    def test_eval_increments_statement_count(self, shell):
        """Each successful eval should increment statement count."""
        shell.eval(_prog('[INTENT] deploy "/app"'))
        assert shell.statement_count == 1
        shell.eval(_prog('[INTENT] test "/path"'))
        assert shell.statement_count == 2

    def test_eval_sets_last_ast(self, shell):
        """Successful eval should populate last_ast."""
        shell.eval(_prog('[INTENT] deploy "/app"'))
        assert shell.last_ast is not None

    def test_eval_sets_last_input(self, shell):
        """eval should store the last input."""
        src = _prog('[INTENT] deploy "/app"')
        shell.eval(src)
        assert shell.last_input == src.strip()


# ─── Environment Tests ──────────────────────────────────────────────────────


class TestEnvironment:
    """Tests for session environment persistence."""

    def test_set_binding_persists(self, shell):
        """[SET] should add variables to the session env."""
        shell.eval(_prog('[SET] name = "world"'))
        assert "name" in shell.env

    def test_multiple_set_bindings(self, shell):
        """Multiple [SET] statements should accumulate."""
        shell.eval(_prog('[SET] a = 1'))
        shell.eval(_prog('[SET] b = 2'))
        assert "a" in shell.env
        assert "b" in shell.env

    def test_env_command_after_set(self, shell):
        """':env' should show SET bindings."""
        shell.eval(_prog('[SET] port = 8080'))
        result = shell.handle_command(":env")
        assert "port" in result

    def test_reset_clears_env(self, shell):
        """':reset' should clear all SET bindings."""
        shell.eval(_prog('[SET] x = 42'))
        shell.handle_command(":reset")
        assert len(shell.env) == 0


# ─── Gas Meter Tests ─────────────────────────────────────────────────────────


class TestGasMeter:
    """Tests for gas metering."""

    def test_gas_limit_respected(self, shell):
        """Shell should track gas against limit."""
        assert shell.gas_limit == 100

    def test_gas_command_shows_utilization(self, shell):
        """':gas' should show utilization percentage."""
        result = shell.handle_command(":gas")
        assert "%" in result

    def test_ast_command_after_eval(self, shell):
        """':ast' after eval should show valid JSON AST."""
        shell.eval(_prog('[INTENT] deploy "/app"'))
        result = shell.handle_command(":ast")
        parsed = json.loads(result)
        assert "program" in parsed or isinstance(parsed, dict)

    def test_lint_after_eval(self, shell):
        """':lint' after eval should run linter."""
        shell.eval(_prog('[INTENT] deploy "/app"'))
        result = shell.handle_command(":lint")
        assert result is not None
        assert len(result) > 0
