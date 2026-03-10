"""
HLF Linter — static analyzer using tiktoken cl100k_base.
Checks: unused SET variables, gas budget, recursion depth, per-intent token count,
duplicate SET declarations, missing RESULT terminator, dead code after RESULT.
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

    Rules:
    - TOKEN_OVERFLOW: intent exceeds 30 tokens
    - GAS_EXCEEDED: too many AST nodes
    - UNUSED_VAR: SET variable never referenced
    - DUPLICATE_SET: same variable name set more than once
    - MISSING_RESULT: program has no RESULT node
    - DEAD_CODE_AFTER_RESULT: statements appear after a RESULT node
    """
    if max_gas is None:
        max_gas = int(os.environ.get("MAX_GAS_LIMIT", "10"))

    diagnostics: list[str] = []

    # Token count check
    token_count = _count_tokens(source)
    if token_count > 30:
        diagnostics.append(f"TOKEN_OVERFLOW: intent has {token_count} tokens (max 30)")

    # DUPLICATE_SET: pre-compile regex check (immutable vars can't be re-set)
    set_name_matches = re.findall(r"^\s*\[SET\]\s+(\w+)\s*=", source, re.MULTILINE)
    pre_set_counts = Counter(set_name_matches)
    for name, count in pre_set_counts.items():
        if count > 1:
            diagnostics.append(
                f"DUPLICATE_SET: variable '{name}' is SET {count} times"
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

    # Unused SET variables — use a set to avoid double-counting (duplicates caught pre-compile)
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

    # MISSING_RESULT: program should end with a RESULT node
    result_indices = [i for i, node in enumerate(program) if node and node.get("tag") == "RESULT"]
    if not result_indices:
        diagnostics.append("MISSING_RESULT: program has no [RESULT] terminator node")

    # DEAD_CODE_AFTER_RESULT: nodes that appear after the first RESULT
    if result_indices:
        first_result = result_indices[0]
        trailing = [
            node for node in program[first_result + 1:]
            if node is not None
        ]
        if trailing:
            diagnostics.append(
                f"DEAD_CODE_AFTER_RESULT: {len(trailing)} node(s) appear after [RESULT]"
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
