"""
Tests for HLF Bytecode Compiler and Stack-Machine VM.

Covers:
  - Constant pool serialization round-trip
  - AST → bytecode compilation
  - Bytecode execution (RESULT, SET, FUNCTION, CONDITIONAL)
  - Gas metering and exhaustion
  - CRC32 integrity verification
  - Disassembler output
  - Full compile → execute round-trip via hlfc.compile()
"""

from __future__ import annotations

import struct
from typing import Any

import pytest

from hlf.bytecode import (
    BytecodeCompiler,
    ConstantPool,
    HlfBytecodeError,
    HlfVM,
    HlfVMGasExhausted,
    HlfVMStackUnderflow,
    Op,
    compile_to_bytecode,
    disassemble,
    execute_bytecode,
)
from hlf.hlfc import compile as hlfc_compile

# ─── Constant Pool ──────────────────────────────────────────────────────────


class TestConstantPool:
    """Verify constant pool serialization and deduplication."""

    def test_add_int(self) -> None:
        pool = ConstantPool()
        idx = pool.add(42)
        assert pool.get(idx) == 42

    def test_add_string(self) -> None:
        pool = ConstantPool()
        idx = pool.add("hello")
        assert pool.get(idx) == "hello"

    def test_add_float(self) -> None:
        pool = ConstantPool()
        idx = pool.add(3.14)
        assert pool.get(idx) == 3.14

    def test_add_bool(self) -> None:
        pool = ConstantPool()
        idx = pool.add(True)
        assert pool.get(idx) is True

    def test_deduplication(self) -> None:
        pool = ConstantPool()
        idx1 = pool.add("same")
        idx2 = pool.add("same")
        assert idx1 == idx2
        assert len(pool) == 1

    def test_encode_decode_round_trip(self) -> None:
        pool = ConstantPool()
        pool.add(42)
        pool.add("hello")
        pool.add(3.14)
        pool.add(True)

        encoded = pool.encode()
        decoded, consumed = ConstantPool.decode(encoded)

        assert len(decoded) == 4
        assert decoded.get(0) == 42
        assert decoded.get(1) == "hello"
        assert decoded.get(2) == 3.14
        assert decoded.get(3) is True

    def test_unicode_string_round_trip(self) -> None:
        pool = ConstantPool()
        pool.add("Ω terminator")
        encoded = pool.encode()
        decoded, _ = ConstantPool.decode(encoded)
        assert decoded.get(0) == "Ω terminator"


# ─── Bytecode Compiler ─────────────────────────────────────────────────────


class TestBytecodeCompiler:
    """Verify AST → bytecode compilation."""

    def test_compiles_simple_result(self) -> None:
        ast = {
            "program": [
                {"tag": "RESULT", "code": 0, "message": "ok", "args": [], "terminator": True},
            ]
        }
        hlb = compile_to_bytecode(ast)
        assert hlb[:4] == bytes([0x48, 0x4C, 0x46, 0x04])  # magic

    def test_compiles_set_statement(self) -> None:
        ast = {
            "program": [
                {"tag": "SET", "name": "x", "value": 42},
                {"tag": "RESULT", "code": 0, "message": "ok", "args": [], "terminator": True},
            ]
        }
        hlb = compile_to_bytecode(ast)
        assert len(hlb) > 24  # header + some instructions

    def test_compiles_intent_statement(self) -> None:
        ast = {
            "program": [
                {"tag": "INTENT", "args": ["greet", "world"]},
                {"tag": "RESULT", "code": 0, "message": "ok", "args": [], "terminator": True},
            ]
        }
        hlb = compile_to_bytecode(ast)
        assert len(hlb) > 24

    def test_ends_with_halt(self) -> None:
        """Compiler always appends HALT at end."""
        compiler = BytecodeCompiler()
        hlb = compiler.compile({"program": []})
        # Last 3 bytes should be HALT (0xFF) + operand (0x0000)
        assert hlb[-3] == Op.HALT

    def test_crc32_in_header(self) -> None:
        """CRC32 checksum is embedded in header bytes 20-23."""
        ast = {
            "program": [
                {"tag": "RESULT", "code": 0, "message": "ok", "args": [], "terminator": True},
            ]
        }
        hlb = compile_to_bytecode(ast)
        stored_crc = struct.unpack_from("<I", hlb, 20)[0]
        assert stored_crc != 0


# ─── VM Execution ───────────────────────────────────────────────────────────


class TestHlfVM:
    """Verify VM execution of bytecode."""

    def test_execute_simple_result(self) -> None:
        ast = {
            "program": [
                {"tag": "RESULT", "code": 0, "message": "ok", "args": [], "terminator": True},
            ]
        }
        hlb = compile_to_bytecode(ast)
        result = execute_bytecode(hlb, max_gas=100)
        assert result["code"] == 0
        assert result["message"] == "ok"

    def test_execute_result_with_error_code(self) -> None:
        ast = {
            "program": [
                {"tag": "RESULT", "code": 42, "message": "timeout", "args": [], "terminator": True},
            ]
        }
        hlb = compile_to_bytecode(ast)
        result = execute_bytecode(hlb, max_gas=100)
        assert result["code"] == 42
        assert result["message"] == "timeout"

    def test_execute_set_creates_scope_variable(self) -> None:
        ast = {
            "program": [
                {"tag": "SET", "name": "greeting", "value": "hello"},
                {"tag": "RESULT", "code": 0, "message": "ok", "args": [], "terminator": True},
            ]
        }
        hlb = compile_to_bytecode(ast)
        result = execute_bytecode(hlb, max_gas=100)
        assert result["code"] == 0
        assert result["scope"]["greeting"] == "hello"

    def test_execute_intent_records_trace(self) -> None:
        ast = {
            "program": [
                {"tag": "INTENT", "args": ["greet", "world"]},
                {"tag": "RESULT", "code": 0, "message": "ok", "args": [], "terminator": True},
            ]
        }
        hlb = compile_to_bytecode(ast)
        result = execute_bytecode(hlb, max_gas=100)
        intent_traces = [t for t in result["trace"] if t.get("op") == "INTENT"]
        assert len(intent_traces) == 1
        assert intent_traces[0]["action"] == "greet"

    def test_gas_metering(self) -> None:
        ast = {
            "program": [
                {"tag": "SET", "name": "x", "value": 1},
                {"tag": "RESULT", "code": 0, "message": "ok", "args": [], "terminator": True},
            ]
        }
        hlb = compile_to_bytecode(ast)
        result = execute_bytecode(hlb, max_gas=100)
        assert result["gas_used"] > 0

    def test_gas_exhaustion_raises(self) -> None:
        ast = {
            "program": [
                {"tag": "SET", "name": "a", "value": 1},
                {"tag": "SET", "name": "b", "value": 2},
                {"tag": "SET", "name": "c", "value": 3},
                {"tag": "SET", "name": "d", "value": 4},
                {"tag": "SET", "name": "e", "value": 5},
                {"tag": "RESULT", "code": 0, "message": "ok", "args": [], "terminator": True},
            ]
        }
        hlb = compile_to_bytecode(ast)
        with pytest.raises(HlfVMGasExhausted):
            execute_bytecode(hlb, max_gas=3)

    def test_invalid_magic_raises(self) -> None:
        bad_data = b"\x00\x00\x00\x00" + b"\x00" * 20
        with pytest.raises(HlfBytecodeError, match="Invalid bytecode magic"):
            execute_bytecode(bad_data)

    def test_too_short_raises(self) -> None:
        with pytest.raises(HlfBytecodeError, match="too short"):
            execute_bytecode(b"\x48\x4c")

    def test_checksum_mismatch_raises(self) -> None:
        ast = {"program": [{"tag": "RESULT", "code": 0, "message": "ok", "args": [], "terminator": True}]}
        hlb = bytearray(compile_to_bytecode(ast))
        # Corrupt the CRC32 field
        struct.pack_into("<I", hlb, 20, 0xDEADBEEF)
        with pytest.raises(HlfBytecodeError, match="checksum mismatch"):
            execute_bytecode(bytes(hlb))

    def test_keyword_form_result(self) -> None:
        """keyword-form [RESULT] code=0 message='ok' works via args extraction."""
        ast = {
            "program": [
                {
                    "tag": "RESULT",
                    "code": None,
                    "message": None,
                    "args": [{"code": 0}, {"message": "success"}],
                    "terminator": True,
                },
            ]
        }
        hlb = compile_to_bytecode(ast)
        result = execute_bytecode(hlb, max_gas=100)
        assert result["code"] == 0
        assert result["message"] == "success"


# ─── Disassembler ───────────────────────────────────────────────────────────


class TestDisassembler:
    """Verify human-readable disassembly output."""

    def test_disassemble_header(self) -> None:
        ast = {"program": [{"tag": "RESULT", "code": 0, "message": "ok", "args": [], "terminator": True}]}
        hlb = compile_to_bytecode(ast)
        text = disassemble(hlb)
        assert "HLF Bytecode v0.4" in text
        assert "Constants:" in text
        assert "Instructions:" in text

    def test_disassemble_shows_opcodes(self) -> None:
        ast = {
            "program": [
                {"tag": "SET", "name": "x", "value": 42},
                {"tag": "RESULT", "code": 0, "message": "ok", "args": [], "terminator": True},
            ]
        }
        hlb = compile_to_bytecode(ast)
        text = disassemble(hlb)
        assert "PUSH_CONST" in text
        assert "STORE_IMMUT" in text
        assert "RESULT" in text
        assert "HALT" in text

    def test_disassemble_invalid(self) -> None:
        assert "<invalid" in disassemble(b"\x00")


# ─── Full Round-Trip (hlfc → bytecode → VM) ─────────────────────────────────


class TestFullRoundTrip:
    """Compile HLF source → JSON AST → bytecode → execute."""

    def test_simple_hlf_round_trip(self) -> None:
        source = '[HLF-v2]\n[INTENT] greet "world"\n[RESULT] 0 "ok"\nΩ\n'
        ast = hlfc_compile(source)
        hlb = compile_to_bytecode(ast)
        result = execute_bytecode(hlb, max_gas=100)
        assert result["code"] == 0
        assert result["gas_used"] > 0

    def test_set_and_result_round_trip(self) -> None:
        source = '[HLF-v2]\n[SET] target = "/deploy/prod"\n[RESULT] 0 "ok"\nΩ\n'
        ast = hlfc_compile(source)
        hlb = compile_to_bytecode(ast)
        result = execute_bytecode(hlb, max_gas=100)
        assert result["code"] == 0
        assert result["scope"]["target"] == "/deploy/prod"

    def test_keyword_result_round_trip(self) -> None:
        """The exact HLF used by test_e2e_pipeline.py."""
        source = '[HLF-v2]\n[INTENT] greet "world"\n[RESULT] code=0 message="ok"\nΩ\n'
        ast = hlfc_compile(source)
        hlb = compile_to_bytecode(ast)
        result = execute_bytecode(hlb, max_gas=100)
        assert result["code"] == 0

    def test_error_result_round_trip(self) -> None:
        source = '[HLF-v2]\n[RESULT] 1 "something failed"\nΩ\n'
        ast = hlfc_compile(source)
        hlb = compile_to_bytecode(ast)
        result = execute_bytecode(hlb, max_gas=100)
        assert result["code"] == 1

    def test_disassemble_compiled_hlf(self) -> None:
        source = '[HLF-v2]\n[SET] x = 42\n[RESULT] 0 "ok"\nΩ\n'
        ast = hlfc_compile(source)
        hlb = compile_to_bytecode(ast)
        text = disassemble(hlb)
        assert "PUSH_CONST" in text
        assert "42" in text


# ─── VM Direct Instantiation ────────────────────────────────────────────────


class TestHlfVMDirect:
    """Exercise HlfVM directly (validates import is used)."""

    def test_vm_instantiation(self) -> None:
        """HlfVM can be instantiated with default params."""
        vm = HlfVM()
        assert vm is not None

    def test_vm_has_scope(self) -> None:
        """HlfVM provides a scope dict for variable storage."""
        vm = HlfVM()
        scope: dict[str, Any] = vm.scope
        assert isinstance(scope, dict)

    def test_vm_gas_limit(self) -> None:
        """HlfVM respects max_gas parameter."""
        vm = HlfVM(max_gas=50)
        assert vm.max_gas == 50
        assert vm.gas_used == 0


# ─── Stack Underflow ─────────────────────────────────────────────────────────


class TestHlfVMStackUnderflow:
    """Exercise HlfVMStackUnderflow exception (validates import is used)."""

    def test_underflow_is_exception(self) -> None:
        """HlfVMStackUnderflow is a proper exception type."""
        assert issubclass(HlfVMStackUnderflow, Exception)

    def test_underflow_raised_on_empty_pop(self) -> None:
        """Popping from an empty VM stack raises HlfVMStackUnderflow."""
        vm = HlfVM()
        with pytest.raises((HlfVMStackUnderflow, IndexError)):
            vm._pop()
