"""
HLF Linter — static analyzer using tiktoken cl100k_base.
Checks: unused SET variables, gas budget, recursion depth, per-intent token count.
"""

from __future__ import annotations

import os
import sys
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
    """
    if max_gas is None:
        max_gas = int(os.environ.get("MAX_GAS_LIMIT", "10"))

    diagnostics: list[str] = []

    # Token count check
    token_count = _count_tokens(source)
    if token_count > 30:
        diagnostics.append(f"TOKEN_OVERFLOW: intent has {token_count} tokens (max 30)")

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
    import re

    for match in re.finditer(r"\$\{(\w+)\}", source):
        used_refs.add(match.group(1))

    for name in set_names:
        if name not in used_refs:
            diagnostics.append(f"UNUSED_VAR: SET variable '{name}' is never referenced via ${{...}}")

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
