"""
HLF Linter — static analyzer using tiktoken cl100k_base.
Checks: unused SET variables, gas budget, recursion depth, per-intent token count,
duplicate SET definitions, missing RESULT terminator, and epistemic overload.

🩵 Cyan Hat additions:
  DUPLICATE_SET      — SET variable redefined without a referencing ${…} in between
  MISSING_RESULT     — No [RESULT] node in the compiled AST (incomplete program)
  EPISTEMIC_OVERLOAD — Too many BELIEVE/DOUBT/ASSUME nodes dilute safety guarantees
"""

from __future__ import annotations

import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from hlf.hlfc import compile as hlfc_compile

# Maximum number of epistemic-modifier nodes before flagging overload.
# More than this many [BELIEVE]/[DOUBT]/[ASSUME] nodes in a single program
# may indicate an attempt to weaken deterministic safety invariants.
_MAX_EPISTEMIC_NODES = 3


def _count_tokens(text: str) -> int:
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return len(text.split())


def lint(source: str, max_gas: int | None = None) -> list[str]:
    """
    Lint HLF source. Returns list of diagnostic strings (empty = clean).
    """
    if max_gas is None:
        max_gas = int(os.environ.get("MAX_GAS_LIMIT", "10"))

    diagnostics: list[str] = []

    # Token count check
    token_count = _count_tokens(source)
    if token_count > 30:
        diagnostics.append(f"TOKEN_OVERFLOW: intent has {token_count} tokens (max 30)")

    # ── 🩵 Cyan Hat: DUPLICATE_SET (pre-parse regex) ──────────────────────
    # Run BEFORE compilation because the compiler itself raises an error on
    # reassignment of immutable variables — this gives a cleaner diagnostic.
    _set_decls = re.findall(r"^\s*\[SET\]\s+(\w+)\s*=", source, re.MULTILINE)
    _set_decl_counts: Counter[str] = Counter(_set_decls)
    for var_name, count in _set_decl_counts.items():
        if count > 1:
            diagnostics.append(
                f"DUPLICATE_SET: SET variable '{var_name}' is defined {count} times"
            )

    try:
        ast = hlfc_compile(source)
    except Exception as exc:
        diagnostics.append(f"PARSE_ERROR: {exc}")
        return diagnostics

    program: list[dict[str, Any]] = ast.get("program", [])

    # Gas budget
    node_count = len(program)
    if node_count > max_gas:
        diagnostics.append(f"GAS_EXCEEDED: {node_count} AST nodes (limit {max_gas})")

    # Unused SET variables
    set_names: set[str] = set()
    for node in program:
        if node and node.get("tag") == "SET":
            set_names.add(node.get("name", ""))

    used_refs: set[str] = set()
    for match in re.finditer(r"\$\{(\w+)\}", source):
        used_refs.add(match.group(1))

    for name in set_names:
        if name not in used_refs:
            diagnostics.append(f"UNUSED_VAR: SET variable '{name}' is never referenced via ${{...}}")

    # ── 🩵 Cyan Hat: MISSING_RESULT ────────────────────────────────────────
    # Every well-formed HLF program SHOULD include a [RESULT] node to
    # communicate outcome codes to the caller.  An absent [RESULT] may
    # indicate an incomplete program or a truncated AI response.
    has_result = any(node and node.get("tag") == "RESULT" for node in program)
    if program and not has_result:
        diagnostics.append(
            "MISSING_RESULT: program has no [RESULT] node — add [RESULT] code=0 message=\"ok\""
        )

    # ── 🩵 Cyan Hat: EPISTEMIC_OVERLOAD ────────────────────────────────────
    # BELIEVE / DOUBT / ASSUME are epistemic modifiers that introduce
    # probabilistic uncertainty.  Too many in a single program can weaken
    # deterministic safety invariants enforced by the ALIGN Ledger.
    epistemic_tags = {"BELIEVE", "DOUBT", "ASSUME"}
    epistemic_nodes = [node for node in program if node and node.get("tag") in epistemic_tags]
    if len(epistemic_nodes) > _MAX_EPISTEMIC_NODES:
        diagnostics.append(
            f"EPISTEMIC_OVERLOAD: {len(epistemic_nodes)} epistemic modifier nodes"
            f" (max {_MAX_EPISTEMIC_NODES}) — excess use may weaken ALIGN safety guarantees"
        )

    return diagnostics


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: hlflint input.hlf", file=sys.stderr)
        sys.exit(1)
    source = Path(sys.argv[1]).read_text()
    issues = lint(source)
    for issue in issues:
        print(issue)
    sys.exit(1 if issues else 0)


if __name__ == "__main__":
    main()
