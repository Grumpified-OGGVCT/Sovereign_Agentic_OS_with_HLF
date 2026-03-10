"""
HLF Linter — static analyzer using tiktoken cl100k_base.
Checks: unused SET variables, gas budget, recursion depth, per-intent token count,
        missing RESULT terminator, duplicate SET names, unreachable code after RESULT.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

from hlf.hlfc import compile as hlfc_compile

# Maximum allowed FUNCTION call nesting depth before raising a lint warning.
_MAX_RECURSION_DEPTH = int(os.environ.get("HLF_MAX_RECURSION_DEPTH", "5"))


def _count_tokens(text: str) -> int:
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return len(text.split())


def _measure_nesting_depth(source: str) -> int:
    """Return the maximum observed bracket-nesting depth in *source*.

    Each ``[FUNCTION]`` or ``[ACTION]`` call that contains nested tag
    invocations increases the depth counter.  This is a heuristic based
    on bracket depth rather than a full call-graph analysis, which is
    sufficient for catching accidental unbounded recursion in HLF scripts.
    """
    depth = 0
    max_depth = 0
    for ch in source:
        if ch == "[":
            depth += 1
            if depth > max_depth:
                max_depth = depth
        elif ch == "]":
            depth = max(0, depth - 1)
    return max_depth


def lint(source: str, max_gas: int | None = None) -> list[str]:
    """Lint HLF source. Returns list of diagnostic strings (empty = clean)."""
    if max_gas is None:
        max_gas = int(os.environ.get("MAX_GAS_LIMIT", "10"))

    diagnostics: list[str] = []

    # ── Token count check ──────────────────────────────────────────────────
    token_count = _count_tokens(source)
    if token_count > 30:
        diagnostics.append(f"TOKEN_OVERFLOW: intent has {token_count} tokens (max 30)")

    # ── Duplicate SET names (source-level check, before compile) ──────────
    _set_decl_re = re.compile(r"^\s*\[SET\]\s+(\w+)\s*=", re.MULTILINE)
    seen_set_names: list[str] = []
    for m in _set_decl_re.finditer(source):
        name = m.group(1)
        if name in seen_set_names:
            diagnostics.append(f"DUPLICATE_SET: variable '{name}' is declared more than once")
        else:
            seen_set_names.append(name)

    try:
        ast = hlfc_compile(source)
    except Exception as exc:
        diagnostics.append(f"PARSE_ERROR: {exc}")
        return diagnostics

    program: list[dict[str, Any]] = ast.get("program", [])

    # ── Gas budget ─────────────────────────────────────────────────────────
    node_count = len(program)
    if node_count > max_gas:
        diagnostics.append(f"GAS_EXCEEDED: {node_count} AST nodes (limit {max_gas})")

    # ── Unused SET variables ───────────────────────────────────────────────
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

    # ── Missing RESULT terminator ──────────────────────────────────────────
    # Stdlib MODULE files are exempt because they use RESULT as a load-status line.
    is_module = any(n and n.get("tag") == "MODULE" for n in program)
    if not is_module:
        result_nodes = [n for n in program if n and n.get("tag") == "RESULT"]
        if not result_nodes:
            diagnostics.append("MISSING_RESULT: program has no [RESULT] terminator node")

    # ── Unreachable code after RESULT ──────────────────────────────────────
    result_index: int | None = None
    for idx, node in enumerate(program):
        if node and node.get("tag") == "RESULT":
            result_index = idx
            break
    if result_index is not None and result_index < len(program) - 1:
        # Count non-None, non-MODULE nodes after the first RESULT
        unreachable = [
            n
            for n in program[result_index + 1 :]
            if n and n.get("tag") not in ("MODULE",)
        ]
        if unreachable:
            tags = ", ".join(n.get("tag", "?") for n in unreachable[:3])
            diagnostics.append(
                f"UNREACHABLE_CODE: {len(unreachable)} node(s) after first [RESULT] "
                f"will never execute (first: {tags})"
            )

    # ── Recursion depth ────────────────────────────────────────────────────
    nesting = _measure_nesting_depth(source)
    if nesting > _MAX_RECURSION_DEPTH:
        diagnostics.append(
            f"RECURSION_DEPTH: nesting depth {nesting} exceeds limit {_MAX_RECURSION_DEPTH}"
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
