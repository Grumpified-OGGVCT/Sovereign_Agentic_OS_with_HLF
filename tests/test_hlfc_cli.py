"""Tests for hlfc CLI — JSON, bytecode, and disassembly modes."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Minimal valid HLF source
_HLF_SRC = '[HLF-v2]\n[SET] greeting = "hello"\n[RESULT] 0 "ok"\n\u03a9'


@pytest.fixture()
def hlf_file(tmp_path: Path) -> Path:
    p = tmp_path / "test_prog.hlf"
    p.write_text(_HLF_SRC, encoding="utf-8")
    return p


def _run_hlfc(*args: str) -> subprocess.CompletedProcess[str]:
    """Run hlfc as a subprocess."""
    return subprocess.run(
        [sys.executable, "-m", "hlf.hlfc", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


# ── JSON mode ────────────────────────────────────────────────────────────────


class TestJsonMode:
    def test_json_to_stdout(self, hlf_file: Path) -> None:
        result = _run_hlfc(str(hlf_file))
        assert result.returncode == 0
        ast = json.loads(result.stdout)
        assert "program" in ast
        assert ast["compiler"] == "HLFC-v0.4.0"

    def test_json_to_file(self, hlf_file: Path, tmp_path: Path) -> None:
        out = tmp_path / "out.json"
        result = _run_hlfc(str(hlf_file), str(out))
        assert result.returncode == 0
        ast = json.loads(out.read_text(encoding="utf-8"))
        assert "program" in ast


# ── Bytecode mode ────────────────────────────────────────────────────────────


class TestBytecodeMode:
    def test_emit_bytecode_default_output(self, hlf_file: Path) -> None:
        result = _run_hlfc("--emit-bytecode", str(hlf_file))
        assert result.returncode == 0
        hlb_path = hlf_file.with_suffix(".hlb")
        assert hlb_path.exists()
        data = hlb_path.read_bytes()
        # Check HLF\x04 magic
        assert data[:4] == bytes([0x48, 0x4C, 0x46, 0x04])

    def test_emit_bytecode_explicit_output(self, hlf_file: Path, tmp_path: Path) -> None:
        out = tmp_path / "custom.hlb"
        result = _run_hlfc("--emit-bytecode", str(hlf_file), str(out))
        assert result.returncode == 0
        assert out.exists()
        data = out.read_bytes()
        assert data[:4] == bytes([0x48, 0x4C, 0x46, 0x04])

    def test_emit_bytecode_executable(self, hlf_file: Path) -> None:
        """Round-trip: compile to bytecode, then execute it."""
        from hlf.bytecode import execute_bytecode

        _run_hlfc("--emit-bytecode", str(hlf_file))
        hlb_data = hlf_file.with_suffix(".hlb").read_bytes()
        result = execute_bytecode(hlb_data)
        assert result["code"] == 0
        assert result["message"] == "ok"


# ── Disassemble mode ─────────────────────────────────────────────────────────


class TestDisassembleMode:
    def test_disassemble_to_stdout(self, hlf_file: Path) -> None:
        # First compile to .hlb
        _run_hlfc("--emit-bytecode", str(hlf_file))
        hlb_path = hlf_file.with_suffix(".hlb")
        # Then disassemble
        result = _run_hlfc("--disassemble", str(hlb_path))
        assert result.returncode == 0
        assert "HLF Bytecode v0.4" in result.stdout
        assert "Constants:" in result.stdout
        assert "Instructions:" in result.stdout

    def test_disassemble_to_file(self, hlf_file: Path, tmp_path: Path) -> None:
        _run_hlfc("--emit-bytecode", str(hlf_file))
        hlb_path = hlf_file.with_suffix(".hlb")
        out = tmp_path / "disasm.txt"
        result = _run_hlfc("--disassemble", str(hlb_path), str(out))
        assert result.returncode == 0
        text = out.read_text(encoding="utf-8")
        assert "HLF Bytecode v0.4" in text


# ── Error handling ───────────────────────────────────────────────────────────


class TestErrors:
    def test_bad_hlf_source(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.hlf"
        bad.write_text("this is not valid HLF at all", encoding="utf-8")
        result = _run_hlfc(str(bad))
        assert result.returncode != 0

    def test_no_input_shows_help(self) -> None:
        result = _run_hlfc()
        # argparse prints to stderr and returns 2 on missing required args
        assert result.returncode == 2
