"""
InsAIts V2 Decompressor — HLF AST → Human-Readable English.

Implements the InsAIts V2 Transparent Compression mandate: every HLF AST
can be decompiled back into readable prose for human audit.

Two modes:
  1. decompile(ast)       → Complete English prose string
  2. decompile_live(ast)  → Generator yielding lines (for streaming)

Usage:
    from hlf.insaits import decompile
    ast = hlfc.compile(source)
    print(decompile(ast))

CLI:
    python -m hlf.insaits input.hlf
    python -m hlf.insaits --json compiled.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Generator


def decompile(ast: dict) -> str:
    """Decompile a full HLF AST into human-readable English prose.

    Walks the program array and expands each node's human_readable field
    into a structured, indented document.
    """
    lines = list(decompile_live(ast))
    return "\n".join(lines)


def decompile_live(ast: dict) -> Generator[str, None, None]:
    """Streaming decompiler — yields one line at a time.

    Useful for real-time display and audit logging.
    """
    version = ast.get("version", "unknown")
    compiler = ast.get("compiler", "unknown")
    program = ast.get("program", [])

    yield f"Program (v{version}, {len(program)} statements, compiler: {compiler}):"
    yield ""

    for i, node in enumerate(program, 1):
        if node is None:
            continue
        yield from _decompile_node(node, depth=1, index=i)

    yield ""
    yield "  [Program terminates]"


def _decompile_node(
    node: Any,
    depth: int = 1,
    index: int | None = None,
) -> Generator[str, None, None]:
    """Recursively decompile a single AST node into English."""
    if node is None:
        return

    if not isinstance(node, dict):
        yield f"{'  ' * depth}{node}"
        return

    indent = "  " * depth
    tag = node.get("tag", "")
    hr = node.get("human_readable", "")
    prefix = f"{index}. " if index else "→ "

    # Use human_readable as the primary translation
    if tag == "SET":
        name = node.get("name", "?")
        value = node.get("value", "?")
        yield f"{indent}{prefix}Set variable '{name}' = {_format_value(value)}"

    elif tag == "ASSIGN":
        name = node.get("name", "?")
        value = node.get("value", "?")
        yield f"{indent}{prefix}Assign '{name}' ← {_format_value(value)}"

    elif tag == "CONDITIONAL":
        cond = node.get("condition", {})
        yield f"{indent}{prefix}IF {_describe_expr(cond)} THEN:"
        then_branch = node.get("then")
        if then_branch:
            yield from _decompile_node(then_branch, depth + 1)
        else_branch = node.get("else")
        if else_branch:
            yield f"{indent}  ELSE:"
            yield from _decompile_node(else_branch, depth + 1)

    elif tag == "PARALLEL":
        tasks = node.get("tasks", [])
        yield f"{indent}{prefix}Execute {len(tasks)} task(s) in parallel:"
        for j, task in enumerate(tasks, 1):
            yield f"{indent}  Task {j}:"
            yield from _decompile_node(task, depth + 2)

    elif tag == "SYNC":
        refs = node.get("refs", [])
        yield f"{indent}{prefix}Wait for [{', '.join(refs)}] then:"
        action = node.get("action")
        if action:
            yield from _decompile_node(action, depth + 1)

    elif tag == "STRUCT":
        name = node.get("name", "?")
        fields = node.get("fields", [])
        field_str = ", ".join(f"{f.get('name', '?')}: {f.get('type_name', '?')}" for f in fields)
        yield f"{indent}{prefix}Define struct '{name}' with fields: {field_str}"

    elif tag == "GLYPH_MODIFIED":
        glyph = node.get("glyph", "?")
        glyph_name = node.get("glyph_name", "?")
        yield f"{indent}{prefix}[{glyph_name} ({glyph})] modifier applied:"
        inner = node.get("inner")
        if inner:
            yield from _decompile_node(inner, depth + 1)

    elif tag == "TOOL":
        tool = node.get("tool", "?")
        args = node.get("args", [])
        yield f"{indent}{prefix}Execute tool '{tool}' with {len(args)} argument(s)"

    elif tag == "FUNCTION":
        name = node.get("name", "?")
        yield f"{indent}{prefix}Call function '{name}'"

    elif tag == "RESULT":
        code = node.get("code", 0)
        message = node.get("message", "ok")
        # Also check args for code/message
        for arg in node.get("args", []):
            if isinstance(arg, dict):
                if "code" in arg:
                    code = arg["code"]
                if "message" in arg:
                    message = arg["message"]
        yield f"{indent}{prefix}Return code {code}: \"{message}\""

    elif tag == "MEMORY":
        entity = node.get("entity", "?")
        content = node.get("content", "?")
        confidence = node.get("confidence", 0.5)
        yield f"{indent}{prefix}Store memory: '{content}' for entity '{entity}' (confidence: {confidence})"

    elif tag == "RECALL":
        entity = node.get("entity", "?")
        top_k = node.get("top_k", 5)
        yield f"{indent}{prefix}Recall memories for entity '{entity}' (top {top_k})"

    elif tag == "DEFINE":
        name = node.get("name", "?")
        body = node.get("body", [])
        yield f"{indent}{prefix}Define macro '{name}' with {len(body)} statement(s):"
        for j, stmt in enumerate(body, 1):
            yield from _decompile_node(stmt, depth + 1, index=j)

    elif tag == "CALL":
        name = node.get("name", "?")
        args = node.get("args", [])
        yield f"{indent}{prefix}Call macro '{name}' with arguments: {', '.join(str(a) for a in args)}"

    elif tag == "IMPORT":
        name = node.get("name", "") or ""
        args = node.get("args", [])
        mod_name = name or (args[0] if args else "?")
        yield f"{indent}{prefix}Import module '{mod_name}'"

    elif tag == "MODULE":
        name = node.get("name", "") or ""
        args = node.get("args", [])
        mod_name = name or (args[0] if args else "?")
        yield f"{indent}{prefix}Declare module '{mod_name}'"

    elif hr:
        # Fallback: use the human_readable field directly
        yield f"{indent}{prefix}{hr}"

    else:
        # Last resort: show the tag and args
        args = node.get("args", [])
        yield f"{indent}{prefix}[{tag}] {', '.join(str(a) for a in args)}"


def _describe_expr(expr: Any) -> str:
    """Describe an expression node in English."""
    if expr is None:
        return "?"

    if isinstance(expr, (str, int, float, bool)):
        return str(expr)

    if not isinstance(expr, dict):
        return str(expr)

    op = expr.get("op", "")
    hr = expr.get("human_readable", "")

    if hr:
        return hr

    if op == "COMPARE":
        left = _describe_expr(expr.get("left"))
        right = _describe_expr(expr.get("right"))
        operator = expr.get("operator", "==")
        return f"{left} {operator} {right}"

    if op == "MATH":
        left = _describe_expr(expr.get("left"))
        right = _describe_expr(expr.get("right"))
        operator = expr.get("operator", "+")
        return f"{left} {operator} {right}"

    if op == "NOT":
        operand = _describe_expr(expr.get("operand"))
        return f"NOT {operand}"

    if op in ("AND", "OR"):
        left = _describe_expr(expr.get("left"))
        right = _describe_expr(expr.get("right"))
        return f"{left} {op} {right}"

    return hr or str(expr)


def _format_value(value: Any) -> str:
    """Format a value for display."""
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, dict):
        hr = value.get("human_readable")
        if hr:
            return hr
        return str(value)
    return str(value)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m hlf.insaits <input.hlf|--json input.json>")
        sys.exit(1)

    from hlf.hlfc import compile as hlfc_compile

    if sys.argv[1] == "--json":
        with open(sys.argv[2], encoding="utf-8") as f:
            ast = json.load(f)
    else:
        source = Path(sys.argv[1]).read_text(encoding="utf-8")
        ast = hlfc_compile(source)

    print(decompile(ast))
