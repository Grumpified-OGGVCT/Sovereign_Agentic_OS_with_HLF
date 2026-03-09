"""
Tests for Instinct Living Spec Opcodes: SPEC_DEFINE, SPEC_GATE, SPEC_UPDATE, SPEC_SEAL.

Covers:
  - Compile + execute SPEC_DEFINE → verify registry population
  - SPEC_GATE passes when constraint met
  - SPEC_GATE halts with error when constraint violated
  - SPEC_UPDATE records mutation, check trace entry
  - SPEC_SEAL locks spec, verify subsequent SPEC_UPDATE raises error
  - SPEC_SEAL checksum is deterministic
  - InsAIts decompilation of all 4 spec tags
  - SPEC_DEFINE after SPEC_SEAL raises error
"""

from __future__ import annotations

import pytest

from hlf.hlfc import HlfRuntimeError
from hlf.hlfc import compile as hlfc_compile
from hlf.hlfrun import HLFInterpreter
from hlf.insaits import decompile

# --------------------------------------------------------------------------- #
# SPEC_DEFINE
# --------------------------------------------------------------------------- #


class TestSpecDefine:
    """SPEC_DEFINE registers spec sections with constraints."""

    def test_compile_spec_define(self) -> None:
        """SPEC_DEFINE compiles to a valid AST node."""
        source = '[SPEC_DEFINE] "auth_module" "must use mTLS" "no plaintext"\nΩ'
        ast = hlfc_compile(source)
        prog = ast["program"]
        spec_node = prog[0]
        assert spec_node["tag"] == "SPEC_DEFINE"
        assert spec_node["section"] == "auth_module"
        assert "must use mTLS" in spec_node["constraints"]
        assert "no plaintext" in spec_node["constraints"]

    def test_execute_spec_define(self) -> None:
        """SPEC_DEFINE populates the spec registry."""
        ast = {
            "program": [
                {
                    "tag": "SPEC_DEFINE",
                    "section": "auth_module",
                    "constraints": ["must use mTLS", "no plaintext"],
                }
            ]
        }
        interp = HLFInterpreter(max_gas=50)
        result = interp.execute(ast)
        assert "auth_module" in result["spec_registry"]
        entry = result["spec_registry"]["auth_module"]
        assert entry["status"] == "active"
        assert len(entry["constraints"]) == 2
        assert result["spec_sealed"] is False


# --------------------------------------------------------------------------- #
# SPEC_GATE
# --------------------------------------------------------------------------- #


class TestSpecGate:
    """SPEC_GATE asserts constraints — passes or halts."""

    def test_spec_gate_passes(self) -> None:
        """SPEC_GATE passes when condition is truthy."""
        ast = {
            "program": [
                {
                    "tag": "SPEC_GATE",
                    "condition": {"op": "COMPARE", "operator": "==", "left": 1, "right": 1},
                }
            ]
        }
        interp = HLFInterpreter(max_gas=50)
        result = interp.execute(ast)
        assert result["code"] == 0
        gate_trace = [t for t in result["trace"] if t["tag"] == "SPEC_GATE"]
        assert len(gate_trace) == 1
        assert gate_trace[0]["condition_result"] is True

    def test_spec_gate_halts_on_violation(self) -> None:
        """SPEC_GATE raises HlfRuntimeError when condition is falsy."""
        ast = {
            "program": [
                {
                    "tag": "SPEC_GATE",
                    "condition": {"op": "COMPARE", "operator": "==", "left": 1, "right": 2},
                }
            ]
        }
        interp = HLFInterpreter(max_gas=50)
        with pytest.raises(HlfRuntimeError, match="SPEC_GATE violation"):
            interp.execute(ast)

    def test_spec_gate_with_scope_variable(self) -> None:
        """SPEC_GATE can evaluate variables from scope."""
        ast = {
            "program": [
                {"tag": "SET", "name": "mTLS_enabled", "value": True},
                {
                    "tag": "SPEC_GATE",
                    "condition": "mTLS_enabled",
                },
            ]
        }
        interp = HLFInterpreter(max_gas=50)
        result = interp.execute(ast)
        assert result["code"] == 0


# --------------------------------------------------------------------------- #
# SPEC_UPDATE
# --------------------------------------------------------------------------- #


class TestSpecUpdate:
    """SPEC_UPDATE records mutations to spec sections."""

    def test_spec_update_records_mutation(self) -> None:
        """SPEC_UPDATE appends to the section's update history."""
        ast = {
            "program": [
                {
                    "tag": "SPEC_DEFINE",
                    "section": "auth_module",
                    "constraints": ["must use mTLS"],
                },
                {
                    "tag": "SPEC_UPDATE",
                    "section": "auth_module",
                    "updates": ["added OAuth2 fallback"],
                },
            ]
        }
        interp = HLFInterpreter(max_gas=50)
        result = interp.execute(ast)
        entry = result["spec_registry"]["auth_module"]
        assert len(entry["updates"]) == 1
        assert "added OAuth2 fallback" in entry["updates"][0]

    def test_spec_update_auto_registers_section(self) -> None:
        """SPEC_UPDATE auto-registers a section if it doesn't exist."""
        ast = {
            "program": [
                {
                    "tag": "SPEC_UPDATE",
                    "section": "new_section",
                    "updates": ["initial scaffolding"],
                }
            ]
        }
        interp = HLFInterpreter(max_gas=50)
        result = interp.execute(ast)
        assert "new_section" in result["spec_registry"]
        assert len(result["spec_registry"]["new_section"]["updates"]) == 1


# --------------------------------------------------------------------------- #
# SPEC_SEAL
# --------------------------------------------------------------------------- #


class TestSpecSeal:
    """SPEC_SEAL locks the spec and emits a SHA-256 checksum."""

    def test_spec_seal_locks_spec(self) -> None:
        """SPEC_SEAL prevents subsequent SPEC_UPDATE."""
        ast = {
            "program": [
                {"tag": "SPEC_DEFINE", "section": "auth", "constraints": ["mTLS"]},
                {"tag": "SPEC_SEAL"},
                {"tag": "SPEC_UPDATE", "section": "auth", "updates": ["bad update"]},
            ]
        }
        interp = HLFInterpreter(max_gas=50)
        with pytest.raises(HlfRuntimeError, match="sealed"):
            interp.execute(ast)

    def test_spec_seal_prevents_define(self) -> None:
        """SPEC_SEAL prevents subsequent SPEC_DEFINE."""
        ast = {
            "program": [
                {"tag": "SPEC_DEFINE", "section": "auth", "constraints": ["mTLS"]},
                {"tag": "SPEC_SEAL"},
                {"tag": "SPEC_DEFINE", "section": "new_section", "constraints": ["bad"]},
            ]
        }
        interp = HLFInterpreter(max_gas=50)
        with pytest.raises(HlfRuntimeError, match="sealed"):
            interp.execute(ast)

    def test_spec_seal_checksum_deterministic(self) -> None:
        """Same spec content produces same checksum."""
        ast = {
            "program": [
                {"tag": "SPEC_DEFINE", "section": "auth", "constraints": ["mTLS"]},
                {"tag": "SPEC_SEAL"},
            ]
        }
        r1 = HLFInterpreter(max_gas=50).execute(ast)
        r2 = HLFInterpreter(max_gas=50).execute(ast)
        assert r1["scope"]["SPEC_CHECKSUM"] == r2["scope"]["SPEC_CHECKSUM"]
        assert len(r1["scope"]["SPEC_CHECKSUM"]) == 64  # SHA-256 hex

    def test_spec_seal_double_seal_raises(self) -> None:
        """Attempting to seal an already-sealed spec raises error."""
        ast = {
            "program": [
                {"tag": "SPEC_DEFINE", "section": "auth", "constraints": ["mTLS"]},
                {"tag": "SPEC_SEAL"},
                {"tag": "SPEC_SEAL"},
            ]
        }
        interp = HLFInterpreter(max_gas=50)
        with pytest.raises(HlfRuntimeError, match="already sealed"):
            interp.execute(ast)


# --------------------------------------------------------------------------- #
# InsAIts Decompilation
# --------------------------------------------------------------------------- #


class TestSpecInsAIts:
    """InsAIts decompiles SPEC_* tags to human-readable prose."""

    def test_decompile_spec_define(self) -> None:
        """SPEC_DEFINE decompiles to readable prose."""
        ast = {
            "version": "0.4.0",
            "compiler": "HLFC-v0.4.0",
            "program": [
                {
                    "tag": "SPEC_DEFINE",
                    "section": "auth_module",
                    "constraints": ["must use mTLS", "no plaintext"],
                    "human_readable": "Define spec section 'auth_module'",
                }
            ],
        }
        prose = decompile(ast)
        assert "spec section" in prose.lower()
        assert "auth_module" in prose

    def test_decompile_spec_gate(self) -> None:
        """SPEC_GATE decompiles with condition description."""
        ast = {
            "version": "0.4.0",
            "compiler": "HLFC-v0.4.0",
            "program": [
                {
                    "tag": "SPEC_GATE",
                    "condition": {"op": "COMPARE", "operator": "==", "left": "x", "right": 1},
                    "human_readable": "Spec gate: assert x == 1",
                }
            ],
        }
        prose = decompile(ast)
        assert "gate" in prose.lower() or "ASSERT" in prose

    def test_decompile_spec_seal(self) -> None:
        """SPEC_SEAL decompiles to seal/lock prose."""
        ast = {
            "version": "0.4.0",
            "compiler": "HLFC-v0.4.0",
            "program": [
                {
                    "tag": "SPEC_SEAL",
                    "human_readable": "Seal spec",
                }
            ],
        }
        prose = decompile(ast)
        assert "seal" in prose.lower() or "SEAL" in prose


# --------------------------------------------------------------------------- #
# End-to-End: Compile → Execute → Verify
# --------------------------------------------------------------------------- #


class TestSpecEndToEnd:
    """Full lifecycle: SPEC_DEFINE → SPEC_GATE → SPEC_UPDATE → SPEC_SEAL."""

    def test_full_spec_lifecycle(self) -> None:
        """Complete spec lifecycle runs successfully."""
        ast = {
            "program": [
                {"tag": "SET", "name": "mTLS_enabled", "value": True},
                {
                    "tag": "SPEC_DEFINE",
                    "section": "auth_module",
                    "constraints": ["must use mTLS", "no plaintext passwords"],
                },
                {
                    "tag": "SPEC_GATE",
                    "condition": "mTLS_enabled",
                },
                {
                    "tag": "SPEC_UPDATE",
                    "section": "auth_module",
                    "updates": ["added OAuth2 fallback"],
                },
                {"tag": "SPEC_SEAL"},
                {"tag": "RESULT", "code": 0, "message": "spec lifecycle complete"},
            ]
        }
        interp = HLFInterpreter(max_gas=50)
        result = interp.execute(ast)
        assert result["code"] == 0
        assert result["spec_sealed"] is True
        assert "SPEC_CHECKSUM" in result["scope"]
        assert "auth_module" in result["spec_registry"]
        entry = result["spec_registry"]["auth_module"]
        assert len(entry["updates"]) == 1
        assert len(entry["constraints"]) == 2
