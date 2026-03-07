"""Tests for the InsAIts V2 Decompiler — HLF AST → English translation.

Covers:
  - decompile(): full AST → English prose
  - decompile_live(): streaming generator
  - decompile_bytecode(): .hlb binary → English prose
  - Tag-specific output for SET, INTENT, RESULT, CONDITIONAL,
    MEMORY, RECALL, TOOL, etc.
"""

from __future__ import annotations

import pytest

from hlf.hlfc import compile as hlfc_compile
from hlf.insaits import decompile, decompile_bytecode, decompile_live

# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _prog(body: str) -> str:
    """Wrap body lines in the HLF v3 program envelope."""
    return f'[HLF-v3]\n{body}\nΩ\n'


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #


@pytest.fixture()
def simple_ast() -> dict:
    """A simple 3-statement AST: SET, INTENT, RESULT."""
    return hlfc_compile(_prog(
        '[SET] target_host = "example.com"\n'
        '[INTENT] SCAN "target_host"\n'
        '[RESULT] code=0 message="ok"'
    ))


@pytest.fixture()
def memory_ast() -> dict:
    """AST with MEMORY and RECALL statements."""
    return hlfc_compile(_prog(
        '[SET] host = "10.0.0.1"\n'
        '[MEMORY] host = "discovered" confidence=0.9 "scan result"\n'
        '[RECALL] host = "discovered" top_k=3\n'
        '[RESULT] code=0 message="done"'
    ))


# ------------------------------------------------------------------ #
# decompile() Tests
# ------------------------------------------------------------------ #


class TestDecompile:
    """Tests for the full decompile function."""

    def test_returns_string(self, simple_ast: dict) -> None:
        """decompile returns a non-empty string."""
        result = decompile(simple_ast)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_header(self, simple_ast: dict) -> None:
        """Output starts with program header."""
        result = decompile(simple_ast)
        assert "Program" in result
        assert "statements" in result

    def test_set_statement(self, simple_ast: dict) -> None:
        """SET statements produce prose about variable setting."""
        result = decompile(simple_ast)
        assert "target_host" in result

    def test_result_statement(self, simple_ast: dict) -> None:
        """RESULT produces prose about return code."""
        result = decompile(simple_ast)
        assert "0" in result  # code=0

    def test_terminates(self, simple_ast: dict) -> None:
        """Output ends with termination marker."""
        result = decompile(simple_ast)
        assert "terminates" in result.lower()

    def test_memory_statement(self, memory_ast: dict) -> None:
        """MEMORY produces memory-related prose."""
        result = decompile(memory_ast)
        assert "host" in result.lower()

    def test_recall_statement(self, memory_ast: dict) -> None:
        """RECALL produces recall-related prose."""
        result = decompile(memory_ast)
        assert "host" in result.lower()


# ------------------------------------------------------------------ #
# decompile_live() Tests
# ------------------------------------------------------------------ #


class TestDecompileLive:
    """Tests for the streaming decompiler."""

    def test_yields_strings(self, simple_ast: dict) -> None:
        """decompile_live yields string lines."""
        lines = list(decompile_live(simple_ast))
        assert all(isinstance(line, str) for line in lines)
        assert len(lines) > 0

    def test_matches_decompile(self, simple_ast: dict) -> None:
        """Streaming output should match full decompile output."""
        streamed = "\n".join(decompile_live(simple_ast))
        full = decompile(simple_ast)
        assert streamed == full


# ------------------------------------------------------------------ #
# decompile_bytecode() Tests
# ------------------------------------------------------------------ #


class TestDecompileBytecode:
    """Tests for the .hlb → English decompiler."""

    def test_basic_bytecode_decompile(self) -> None:
        """Compile HLF to bytecode, then decompile to English."""
        from hlf.bytecode import BytecodeCompiler

        source = _prog('[SET] count = 42\n[RESULT] 0 "ok"')
        ast = hlfc_compile(source)
        compiler = BytecodeCompiler()
        hlb = compiler.compile(ast)

        result = decompile_bytecode(hlb)
        assert isinstance(result, str)
        assert "Bytecode Program" in result
        assert "terminates" in result.lower()

    def test_bytecode_contains_ops(self) -> None:
        """Decompiled bytecode contains recognizable operations."""
        from hlf.bytecode import BytecodeCompiler

        source = _prog('[SET] x = "hello"\n[RESULT] 0 "done"')
        ast = hlfc_compile(source)
        compiler = BytecodeCompiler()
        hlb = compiler.compile(ast)

        result = decompile_bytecode(hlb)
        # Should contain prose translations of opcodes
        assert any(
            word in result
            for word in [
                "Load value", "Set immutable", "Assign",
                "variable", "Program terminates",
            ]
        )

    def test_memory_bytecode_round_trip(self) -> None:
        """MEMORY/RECALL opcodes survive bytecode → English round-trip."""
        from hlf.bytecode import BytecodeCompiler

        source = _prog(
            '[MEMORY] host = "found" confidence=0.9 "scan result"\n'
            '[RECALL] host = "found" top_k=3\n'
            '[RESULT] 0 "done"'
        )
        ast = hlfc_compile(source)
        compiler = BytecodeCompiler()
        hlb = compiler.compile(ast)

        result = decompile_bytecode(hlb)
        assert "Store memory" in result or "MEMORY_STORE" in result
        assert "Recall" in result or "MEMORY_RECALL" in result


# ------------------------------------------------------------------ #
# Tag Coverage
# ------------------------------------------------------------------ #


class TestTagCoverage:
    """Ensure all major tags produce decompile output."""

    @pytest.mark.parametrize("source,expected_text", [
        (_prog('[SET] x = 1'), "x"),
        (_prog('[INTENT] SCAN "target"'), "INTENT"),
        (_prog('[RESULT] code=0 message="ok"'), "0"),
    ])
    def test_tag_produces_output(self, source: str, expected_text: str) -> None:
        """Each tag produces expected prose."""
        ast = hlfc_compile(source)
        result = decompile(ast)
        assert expected_text in result, (
            f"Expected '{expected_text}' in decompile output for '{source}'"
        )
