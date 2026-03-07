"""
HLF REPL — Interactive Hieroglyphic Logic Framework Shell (hlfsh).

An interactive read-eval-print loop for the HLF language. Provides immediate
feedback for developing, debugging, and learning HLF programs.

Features:
  - Parse → Compile → Execute loop with gas tracking
  - Multi-line input (press Enter twice to submit)
  - REPL commands: .help, .env, .gas, .ast, .trace, .macros, .clear, .quit
  - Auto-wrapped in [HLF-v2] header and Ω terminator

Usage:
    python -m hlf.hlfsh
    python -c "from hlf.hlfsh import repl; repl()"
"""

from __future__ import annotations

import json
import sys  # noqa: F401 — needed for REPL stdin/stdout
import traceback
from typing import Any

from hlf.hlfc import HlfSyntaxError
from hlf.hlfc import compile as hlfc_compile
from hlf.hlfrun import HLFInterpreter, HlfRuntimeError
from hlf.insaits import decompile

# --------------------------------------------------------------------------- #
# REPL commands
# --------------------------------------------------------------------------- #

HELP_TEXT = """
HLF Shell (hlfsh) — Interactive REPL commands:

  .help              Show this help message
  .env               Show all variables in the current scope
  .gas               Show gas usage statistics
  .ast <hlf>         Show compiled AST for HLF source (no execution)
  .decompile <hlf>   Decompile HLF source to English
  .trace             Show execution trace from last run
  .macros            Show registered macros
  .clear             Clear the scope and reset state
  .quit / .exit      Exit the REPL

HLF programs are auto-wrapped:
  Your input → [HLF-v2] <your input> Ω

Multi-line: Enter a blank line to submit multi-line input.

Examples:
  hlfsh> [SET] x = 42
  hlfsh> [FUNCTION] HASH sha256 "hello"
  hlfsh> x ← 10 + 5
  hlfsh> ⊎ x > 10 ⇒ [ACTION] "log" "x is big"
"""


def repl(
    tier: str = "hearth",
    max_gas: int = 100,
    memory_engine: Any = None,
) -> None:
    """Start the interactive HLF REPL.

    Args:
        tier:           Deployment tier (hearth, forge, sovereign)
        max_gas:        Gas cap per execution
        memory_engine:  Optional InfiniteRAGEngine instance
    """
    print("╔══════════════════════════════════════════════════════╗")
    print("║  HLF Shell (hlfsh) v0.4.0                          ║")
    print("║  Hieroglyphic Logic Framework — Interactive REPL    ║")
    print("║  Type .help for commands, .quit to exit             ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()

    # Persistent state across REPL iterations
    scope: dict[str, Any] = {}
    total_gas = 0
    last_trace: list[dict] = []
    last_ast: dict = {}
    macros: dict[str, list] = {}

    while True:
        try:
            line = _read_input()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye! Ω")
            break

        if not line.strip():
            continue

        # ---- REPL commands ----
        stripped = line.strip()

        if stripped in (".quit", ".exit"):
            print("Goodbye! Ω")
            break

        if stripped == ".help":
            print(HELP_TEXT)
            continue

        if stripped == ".env":
            if scope:
                for k, v in sorted(scope.items()):
                    print(f"  {k} = {v!r}")
            else:
                print("  (scope is empty)")
            continue

        if stripped == ".gas":
            print(f"  Total gas used: {total_gas}")
            continue

        if stripped == ".trace":
            if last_trace:
                for entry in last_trace:
                    print(f"  {entry}")
            else:
                print("  (no trace from last execution)")
            continue

        if stripped == ".macros":
            if macros:
                for name, body in macros.items():
                    print(f"  Σ {name}: {len(body)} statement(s)")
            else:
                print("  (no macros defined)")
            continue

        if stripped == ".clear":
            scope.clear()
            macros.clear()
            total_gas = 0
            last_trace.clear()
            last_ast.clear()
            print("  Scope, macros, and gas cleared.")
            continue

        if stripped.startswith(".ast "):
            hlf_input = stripped[5:].strip()
            source = _wrap_source(hlf_input)
            try:
                ast = hlfc_compile(source)
                print(json.dumps(ast, indent=2, ensure_ascii=False))
            except HlfSyntaxError as e:
                print(f"  Compile error: {e}")
            continue

        if stripped.startswith(".decompile "):
            hlf_input = stripped[11:].strip()
            source = _wrap_source(hlf_input)
            try:
                ast = hlfc_compile(source)
                print(decompile(ast))
            except HlfSyntaxError as e:
                print(f"  Compile error: {e}")
            continue

        # ---- Execute HLF ----
        source = _wrap_source(line)
        try:
            ast = hlfc_compile(source)
            last_ast = ast

            interp = HLFInterpreter(scope=scope, tier=tier, max_gas=max_gas)
            interp._macros = dict(macros)  # Carry forward macros
            if memory_engine:
                interp._memory_engine = memory_engine

            result = interp.execute(ast)

            # Update persistent state
            scope.update(result.get("scope", {}))
            total_gas += result.get("gas_used", 0)
            last_trace = result.get("trace", [])
            macros.update({m: interp._macros[m] for m in interp._macros})

            # Display result
            code = result.get("code", 0)
            msg = result.get("message", "ok")
            gas_used = result.get("gas_used", 0)
            rv = result.get("result")

            status = "✓" if code == 0 else "✗"
            print(f"  {status} [{code}] {msg}  (gas: {gas_used})")
            if rv is not None:
                print(f"  → {rv}")

        except HlfSyntaxError as e:
            print(f"  ✗ Compile error: {e}")
        except HlfRuntimeError as e:
            print(f"  ✗ Runtime error: {e}")
        except Exception as e:
            print(f"  ✗ Error: {e}")
            traceback.print_exc()


# --------------------------------------------------------------------------- #
# Input helpers
# --------------------------------------------------------------------------- #


def _read_input() -> str:
    """Read one or more lines from stdin. Blank line finalizes multi-line."""
    first_line = input("hlfsh> ").rstrip()
    if not first_line:
        return ""

    lines = [first_line]

    # Check if this is a multi-line block (contains { but no })
    if "{" in first_line and "}" not in first_line:
        while True:
            try:
                cont = input("  ...> ").rstrip()
            except (EOFError, KeyboardInterrupt):
                break
            lines.append(cont)
            if "}" in cont:
                break

    return "\n".join(lines)


def _wrap_source(source: str) -> str:
    """Wrap bare HLF input in version header and terminator if needed."""
    s = source.strip()
    if not s.startswith("[HLF-"):
        s = f"[HLF-v2]\n{s}"
    if not s.rstrip().endswith("Ω"):
        s = f"{s}\nΩ"
    return s


# --------------------------------------------------------------------------- #
# CLI entry point
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    repl()
