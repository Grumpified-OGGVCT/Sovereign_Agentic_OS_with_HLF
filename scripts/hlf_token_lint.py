#!/usr/bin/env python3
"""
HLF Token Linter — scans all .hlf and system prompt files.
Uses tiktoken cl100k_base. Exits 1 if any [INTENT] line exceeds 30 tokens
(.hlf files) or if a system prompt file exceeds 512 tokens.
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
_MAX_INTENT_TOKENS = 30
_MAX_PROMPT_TOKENS = 512


def _scan_hlf_file(path: Path) -> list[str]:
    """Check each [INTENT] line individually against the per-intent token limit."""
    violations = []
    for lineno, line in enumerate(path.read_text(errors="replace").splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith("[INTENT]"):
            continue
        token_count = count_tokens(stripped)
        if token_count > _MAX_INTENT_TOKENS:
            violations.append(
                f"{path}:{lineno}: {token_count} tokens (max {_MAX_INTENT_TOKENS}) — {stripped!r}"
            )
    return violations


def _scan_prompt_file(path: Path) -> list[str]:
    """Check the full prompt file against the prompt-level token limit."""
    violations = []
    text = path.read_text(errors="replace")
    token_count = count_tokens(text)
    if token_count > _MAX_PROMPT_TOKENS:
        violations.append(f"{path}: {token_count} tokens (max {_MAX_PROMPT_TOKENS})")
    return violations


def main() -> None:
    hlf_files = list(_REPO_ROOT.rglob("*.hlf"))
    prompt_files = list((_REPO_ROOT / "governance" / "templates").glob("system_prompt*.txt"))

    if not hlf_files and not prompt_files:
        print("No .hlf or system prompt files found.")
        sys.exit(0)

    all_violations: list[str] = []
    for f in hlf_files:
        all_violations.extend(_scan_hlf_file(f))
    for f in prompt_files:
        all_violations.extend(_scan_prompt_file(f))

    if all_violations:
        for v in all_violations:
            print(f"TOKEN_OVERFLOW: {v}", file=sys.stderr)
        sys.exit(1)
    else:
        total = len(hlf_files) + len(prompt_files)
        print(f"Scanned {total} file(s) — all within token limits.")
        sys.exit(0)


if __name__ == "__main__":
    main()
