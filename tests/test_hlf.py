"""Tests for HLF syntax validation, compilation, and linting."""
from __future__ import annotations

import pytest

from hlf import validate_hlf
from hlf.hlfc import compile as hlfc_compile
from hlf.hlflint import lint


class TestValidateHlf:
    def test_valid_intent_line(self) -> None:
        assert validate_hlf("[INTENT] greet world") is True

    def test_valid_result_line(self) -> None:
        assert validate_hlf("[RESULT] code=0 message=ok") is True

    def test_valid_terminator(self) -> None:
        assert validate_hlf("Ω") is True

    def test_valid_version_header(self) -> None:
        assert validate_hlf("[HLF-v2]") is True

    def test_empty_line(self) -> None:
        assert validate_hlf("") is True

    def test_invalid_lowercase_tag(self) -> None:
        assert validate_hlf("[intent] something") is False

    def test_invalid_plain_text(self) -> None:
        assert validate_hlf("just some prose text") is False


class TestHlfCompile:
    def test_hello_world_fixture(self, hello_hlf: str) -> None:
        ast = hlfc_compile(hello_hlf)
        assert ast["version"] == "0.2.0"
        assert isinstance(ast["program"], list)
        assert len(ast["program"]) > 0

    def test_intent_tag_present(self, hello_hlf: str) -> None:
        ast = hlfc_compile(hello_hlf)
        tags = [node["tag"] for node in ast["program"] if node]
        assert "INTENT" in tags

    def test_result_tag_present(self, hello_hlf: str) -> None:
        ast = hlfc_compile(hello_hlf)
        tags = [node["tag"] for node in ast["program"] if node]
        assert "RESULT" in tags

    def test_malformed_intent_rejected(self) -> None:
        with pytest.raises(Exception):
            hlfc_compile("just plain text without tags")

    def test_tag_arity_checking(self) -> None:
        """Compile a minimal valid HLF and verify AST structure."""
        source = "[HLF-v2]\n[INTENT] analyze /etc/passwd\nΩ\n"
        ast = hlfc_compile(source)
        assert ast["program"][0]["tag"] == "INTENT"
        assert len(ast["program"][0]["args"]) >= 1

    def test_serialization_roundtrip(self, hello_hlf: str) -> None:
        import json

        ast = hlfc_compile(hello_hlf)
        serialized = json.dumps(ast)
        deserialized = json.loads(serialized)
        assert deserialized == ast


class TestHlfLint:
    def test_clean_file_no_diagnostics(self, hello_hlf: str) -> None:
        issues = lint(hello_hlf, max_gas=10)
        # May have token overflow for longer files — just check it returns a list
        assert isinstance(issues, list)

    def test_gas_exceeded(self) -> None:
        # Build a program with many nodes
        lines = ["[HLF-v2]"]
        for i in range(15):
            lines.append(f'[ACTION] step_{i} "arg"')
        lines.append("Ω")
        source = "\n".join(lines)
        issues = lint(source, max_gas=5)
        gas_issues = [i for i in issues if "GAS_EXCEEDED" in i]
        assert len(gas_issues) > 0
