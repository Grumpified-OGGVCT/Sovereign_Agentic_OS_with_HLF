#!/usr/bin/env python3
"""
HLF Token Limits Linter
Runs over .hlf files using tiktoken to ensure they don't exceed strict agentic token limits.
"""

import sys
from pathlib import Path

try:
    import tiktoken
except ImportError:
    print("Warning: tiktoken not found. Run `uv add tiktoken` to enable accurate linting.")
    sys.exit(1)

# Hard limit for any single intent script to ensure fast triage and keep context windows free
MAX_TOKENS = 1500


def lint_file(path: Path, enc: tiktoken.Encoding) -> list[str]:
    errors = []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        return [f"Failed to read file: {e}"]

    tokens = enc.encode(text)
    if len(tokens) > MAX_TOKENS:
        errors.append(f"File exceeds maximum token budget of {MAX_TOKENS} (Count: {len(tokens)})")

    if "[HLF-v2]" not in text and "[HLF-v3]" not in text:
        errors.append("Missing [HLF-v2] or [HLF-v3] header")
    if "\u03a9" not in text and "Omega" not in text:
        errors.append("Missing Ω terminator")

    return errors


def main() -> int:
    files = sys.argv[1:]
    if not files:
        # Auto-discover if none provided
        files = [str(p) for p in Path(".").glob("**/*.hlf")]

    has_errors = False
    try:
        enc = tiktoken.get_encoding("cl100k_base")
    except Exception as e:
        print(f"Failed to load tiktoken encoding: {e}")
        return 1

    for f in files:
        p = Path(f)
        if not p.is_file():
            # Might be passed by pre-commit as a deleted file, ignore
            continue

        errs = lint_file(p, enc)
        if errs:
            has_errors = True
            print(f"FAIL: {f}")
            for e in errs:
                print(f"  - {e}")

    if not has_errors:
        print(f"Linted {len(files)} files successfully.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
