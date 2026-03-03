"""
HLF Formatter — canonical pretty-printer.
Uppercase tags, single space after ], mandatory trailing Ω, no trailing spaces.
CLI: hlffmt [--in-place] input.hlf
"""

from __future__ import annotations

import sys
from pathlib import Path

from hlf.hlfc import compile as hlfc_compile


def format_hlf(source: str) -> str:
    """Return canonical HLF representation of *source*."""
    ast = hlfc_compile(source)
    lines: list[str] = ["[HLF-v2]"]
    for node in ast.get("program", []):
        if node is None:
            continue
        tag = node.get("tag", "").upper()
        if tag == "SET":
            val = node.get("value", "")
            if isinstance(val, str):
                val = f'"{val}"'
            lines.append(f"[SET] {node['name']}={val}")
        elif tag == "FUNCTION":
            args_str = _format_args(node.get("args", []))
            lines.append(f"[FUNCTION] {node['name']} {args_str}".rstrip())
        elif tag == "RESULT":
            args_str = _format_args(node.get("args", []))
            lines.append(f"[RESULT] {args_str}".rstrip())
        elif tag == "MODULE":
            lines.append(f"[MODULE] {node['name']}")
        elif tag == "IMPORT":
            lines.append(f"[IMPORT] {node['name']}")
        else:
            args_str = _format_args(node.get("args", []))
            lines.append(f"[{tag}] {args_str}".rstrip())
    lines.append("Ω")
    return "\n".join(lines) + "\n"


def _format_args(args: list) -> str:
    parts = []
    for arg in args:
        if isinstance(arg, dict):
            for k, v in arg.items():
                if isinstance(v, str):
                    parts.append(f'{k}="{v}"')
                else:
                    parts.append(f"{k}={v}")
        elif isinstance(arg, str):
            parts.append(f'"{arg}"')
        else:
            parts.append(str(arg))
    return " ".join(parts)


def main() -> None:
    in_place = "--in-place" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--in-place"]
    if not args:
        print("Usage: hlffmt [--in-place] input.hlf", file=sys.stderr)
        sys.exit(1)
    p = Path(args[0])
    source = p.read_text(encoding="utf-8")
    formatted = format_hlf(source)
    if in_place:
        p.write_text(formatted, encoding="utf-8")
    else:
        print(formatted, end="")


if __name__ == "__main__":
    main()
