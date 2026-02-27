#!/usr/bin/env python3
"""
HLF Token Linter — scans all .hlf and system prompt files.
Uses tiktoken cl100k_base. Exits 1 if any intent exceeds 30 tokens.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import tiktoken

    _enc = tiktoken.get_encoding("cl100k_base")

    def count_tokens(text: str) -> int:
        return len(_enc.encode(text))

except ImportError:
    def count_tokens(text: str) -> int:  # type: ignore[misc]
        return len(text.split())


_REPO_ROOT = Path(__file__).parent.parent
_MAX_TOKENS = 30


def _scan_file(path: Path) -> list[str]:
    violations = []
    text = path.read_text(errors="replace")
    token_count = count_tokens(text)
    if token_count > _MAX_TOKENS:
        violations.append(f"{path}: {token_count} tokens (max {_MAX_TOKENS})")
    return violations


def main() -> None:
    hlf_files = list(_REPO_ROOT.rglob("*.hlf"))
    prompt_files = list((_REPO_ROOT / "governance" / "templates").glob("system_prompt*.txt"))
    all_files = hlf_files + prompt_files

    if not all_files:
        print("No .hlf or system prompt files found.")
        sys.exit(0)

    all_violations: list[str] = []
    for f in all_files:
        all_violations.extend(_scan_file(f))

    if all_violations:
        for v in all_violations:
            print(f"TOKEN_OVERFLOW: {v}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"Scanned {len(all_files)} file(s) — all within {_MAX_TOKENS} token limit.")
        sys.exit(0)


if __name__ == "__main__":
    main()
