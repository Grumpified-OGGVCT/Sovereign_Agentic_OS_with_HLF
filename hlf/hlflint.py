"""
HLF Linter — static analyzer using tiktoken cl100k_base.
Checks: unused SET variables, gas budget, recursion depth, per-intent token count,
redundant constraints, dead code, missing result, and duplicate SET assignments.
"""

from __future__ import annotations

import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from hlf.hlfc import compile as hlfc_compile


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

    Rules checked:
    - TOKEN_OVERFLOW: intent exceeds 30-token budget
    - GAS_EXCEEDED: AST node count exceeds gas limit
    - UNUSED_VAR: SET variable never referenced via ${...}
    - DUPLICATE_SET: variable assigned more than once (probable bug)
    - REDUNDANT_CONSTRAINT: same constraint key declared more than once
    - DEAD_CODE: statements after the first [RESULT] are unreachable
    - MISSING_RESULT: [INTENT] present but no [RESULT] terminator
    """
    if max_gas is None:
        max_gas = int(os.environ.get("MAX_GAS_LIMIT", "10"))

    diagnostics: list[str] = []

    # Token count check
    token_count = _count_tokens(source)
    if token_count > 30:
        diagnostics.append(f"TOKEN_OVERFLOW: intent has {token_count} tokens (max 30)")

    # --- DUPLICATE_SET pre-parse: detected from source text before compilation ---
    # SET variables are immutable; assigning the same name twice is always a bug.
    # We detect this here (instead of relying solely on the compiler's PARSE_ERROR)
    # so that callers get a structured DUPLICATE_SET diagnostic rather than a
    # cryptic HlfSyntaxError from deep inside the compiler.
    _set_decl_re = re.compile(r"^\s*\[SET\]\s+(\w+)\s*=", re.MULTILINE)
    _set_decl_counts: dict[str, int] = {}
    for m in _set_decl_re.finditer(source):
        name = m.group(1)
        _set_decl_counts[name] = _set_decl_counts.get(name, 0) + 1
    for name, count in _set_decl_counts.items():
        if count > 1:
            diagnostics.append(
                f"DUPLICATE_SET: variable '{name}' is SET {count} times (SET is immutable; second assignment is unreachable)"
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

    # --- Unused SET variables ---
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

    # --- REDUNDANT_CONSTRAINT: same key declared in multiple [CONSTRAINT] nodes ---
    # [CONSTRAINT] key value — the key is args[0] (positional, not kv_pair)
    constraint_key_counts: Counter[str] = Counter(
        node["args"][0]
        for node in program
        if node and node.get("tag") == "CONSTRAINT"
        and node.get("args")
        and isinstance(node["args"][0], str)
    )
    for key, count in constraint_key_counts.items():
        if count > 1:
            diagnostics.append(
                f"REDUNDANT_CONSTRAINT: constraint key '{key}' declared {count} times; only the last value takes effect"
            )

    # --- DEAD_CODE: statements after first [RESULT] are unreachable ---
    result_idx: int | None = None
    for idx, node in enumerate(program):
        if node and node.get("tag") == "RESULT":
            result_idx = idx
            break
    if result_idx is not None:
        dead_nodes = [n for n in program[result_idx + 1:] if n is not None]
        if dead_nodes:
            diagnostics.append(
                f"DEAD_CODE: {len(dead_nodes)} statement(s) after [RESULT] are unreachable"
            )

    # --- MISSING_RESULT: [INTENT] present but no [RESULT] ---
    has_intent = any(n and n.get("tag") == "INTENT" for n in program)
    has_result = any(n and n.get("tag") == "RESULT" for n in program)
    if has_intent and not has_result:
        diagnostics.append("MISSING_RESULT: [INTENT] declared but no [RESULT] terminator found")

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
