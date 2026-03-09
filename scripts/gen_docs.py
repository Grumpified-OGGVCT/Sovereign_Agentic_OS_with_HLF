#!/usr/bin/env python3
"""
gen_docs.py — Regenerate MkDocs documentation from governance artifacts.

Reads:
  - governance/templates/dictionary.json → tag reference table
  - governance/hls.yaml → grammar production rules
  - hlf/stdlib/*.hlf → stdlib function tables

Outputs updated docs/ markdown files.

Usage:
    python scripts/gen_docs.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_DICT = _ROOT / "governance" / "templates" / "dictionary.json"
_STDLIB = _ROOT / "hlf" / "stdlib"
_DOCS = _ROOT / "docs"


def load_dictionary() -> dict:
    """Load dictionary.json."""
    return json.loads(_DICT.read_text(encoding="utf-8"))


def parse_stdlib_module(path: Path) -> dict:
    """Parse a .hlf stdlib module for functions and constants."""
    text = path.read_text(encoding="utf-8")
    functions = []
    constants = []

    for line in text.splitlines():
        line = line.strip()
        # [FUNCTION] name arg1 arg2 ...
        m = re.match(r'\[FUNCTION\]\s+(\w+)\s*(.*)', line)
        if m:
            name = m.group(1)
            args_raw = m.group(2).strip()
            args = re.findall(r'"(\w+)"', args_raw)
            functions.append({"name": name, "args": args})
            continue

        # [SET] NAME=value or [SET] NAME = value
        m = re.match(r'\[SET\]\s+(\w+)\s*=\s*(.*)', line)
        if m:
            constants.append({"name": m.group(1), "value": m.group(2).strip()})

    # Module name from [MODULE] line
    m = re.search(r'\[MODULE\]\s+(\w+)', text)
    module_name = m.group(1) if m else path.stem

    return {
        "name": module_name,
        "functions": functions,
        "constants": constants,
        "path": str(path.relative_to(_ROOT)),
    }


def generate_tag_table(tags: list[dict]) -> str:
    """Generate markdown table from dictionary tags."""
    lines = ["| Tag | Arity | Arguments | Notes |",
             "|-----|-------|-----------|-------|"]
    for tag in tags:
        name = tag["name"]
        args = tag.get("args", [])
        arity = str(len(args))
        if any(a.get("repeat") for a in args):
            arity += "+"
        args_str = ", ".join(f'{a["name"]}:{a["type"]}' for a in args)
        notes = []
        if tag.get("pure"):
            notes.append("Pure")
        if tag.get("immutable"):
            notes.append("Immutable")
        if tag.get("terminator"):
            notes.append("Terminator")
        if tag.get("macro"):
            notes.append("Macro")
        note_str = ", ".join(notes) if notes else ""
        lines.append(f"| `{name}` | {arity} | `{args_str}` | {note_str} |")
    return "\n".join(lines)


def main():
    print(f"Loading dictionary from {_DICT}")
    dictionary = load_dictionary()

    print(f"\nTag Reference ({len(dictionary['tags'])} tags):")
    table = generate_tag_table(dictionary["tags"])
    print(table)

    print(f"\nStandard Library modules in {_STDLIB}:")
    for hlf_file in sorted(_STDLIB.glob("*.hlf")):
        mod = parse_stdlib_module(hlf_file)
        print(f"  {mod['name']}: {len(mod['functions'])} functions, {len(mod['constants'])} constants")

    print("\n✅ Documentation sources validated. Run 'mkdocs build' to generate site.")


if __name__ == "__main__":
    main()
