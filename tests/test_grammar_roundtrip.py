"""Tests for HLF grammar round-trip: parse → format → re-parse → assert structural equality."""

from __future__ import annotations

from pathlib import Path

from hlf.hlfc import compile as hlfc_compile
from hlf.hlffmt import format_hlf

REPO_ROOT = Path(__file__).parent.parent


def test_hello_world_roundtrip() -> None:
    source = (REPO_ROOT / "tests" / "fixtures" / "hello_world.hlf").read_text(encoding="utf-8")
    ast1 = hlfc_compile(source)
    formatted = format_hlf(source)
    ast2 = hlfc_compile(formatted)
    # Compare programs structurally (same tags in same order)
    tags1 = [n["tag"] for n in ast1["program"] if n]
    tags2 = [n["tag"] for n in ast2["program"] if n]
    assert tags1 == tags2, f"Tag sequences differ after round-trip:\n{tags1}\nvs\n{tags2}"


def test_roundtrip_preserves_intent_args() -> None:
    source = "[HLF-v2]\n[INTENT] analyze /etc/passwd\nΩ\n"
    ast1 = hlfc_compile(source)
    formatted = format_hlf(source)
    ast2 = hlfc_compile(formatted)
    intent1 = next(n for n in ast1["program"] if n and n["tag"] == "INTENT")
    intent2 = next(n for n in ast2["program"] if n and n["tag"] == "INTENT")
    assert intent1["args"] == intent2["args"]


def test_formatted_output_ends_with_terminator() -> None:
    source = '[HLF-v2]\n[INTENT] greet "world"\nΩ\n'
    formatted = format_hlf(source)
    assert formatted.strip().endswith("Ω"), f"Formatted output must end with Ω:\n{formatted}"


def test_formatted_output_starts_with_version_header() -> None:
    source = '[HLF-v2]\n[INTENT] greet "world"\nΩ\n'
    formatted = format_hlf(source)
    assert formatted.startswith("[HLF-v3]"), "Formatted output must start with [HLF-v3]"


def test_roundtrip_with_result_tag() -> None:
    source = '[HLF-v2]\n[INTENT] greet "world"\n[EXPECT] "Hello, world!"\n[RESULT] code=0 message="ok"\nΩ\n'
    ast1 = hlfc_compile(source)
    formatted = format_hlf(source)
    ast2 = hlfc_compile(formatted)
    assert ast1["version"] == ast2["version"]
    tags1 = {n["tag"] for n in ast1["program"] if n}
    tags2 = {n["tag"] for n in ast2["program"] if n}
    assert tags1 == tags2
