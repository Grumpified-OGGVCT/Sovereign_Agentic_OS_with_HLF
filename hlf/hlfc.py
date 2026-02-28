"""
HLF Compiler — Lark LALR(1) parser.
Compiles .hlf source → validated JSON AST.
CLI: hlfc input.hlf [output.json]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from lark import Lark, Transformer, Token, Tree
from lark.exceptions import LarkError


class HlfSyntaxError(ValueError):
    """Raised when HLF source fails to parse."""


class HlfRuntimeError(RuntimeError):
    """Raised when HLF execution encounters a runtime fault."""

_GRAMMAR = r"""
    start: line+ TERMINATOR
    line: tag_stmt
        | set_stmt
        | function_stmt
        | result_stmt
        | module_stmt
        | import_stmt

    tag_stmt: "[" TAG "]" arglist
    set_stmt: "[" "SET" "]" IDENT "=" literal
    function_stmt: "[" "FUNCTION" "]" IDENT arglist
    result_stmt: "[" "RESULT" "]" arglist
    module_stmt: "[" "MODULE" "]" IDENT
    import_stmt: "[" "IMPORT" "]" IDENT

    arglist: arg*
    arg: kv_pair | literal

    kv_pair: IDENT "=" literal
    literal: STRING
           | NUMBER
           | BOOL
           | PATH
           | VAR_REF
           | IDENT

    TAG: /[A-Z_]+/
    IDENT: /[A-Za-z_][A-Za-z0-9_]*/
    PATH: /\/[^\s]*/
    STRING: /\"([^\"\\\\]|\\\\.)*\"/
    NUMBER: /-?\d+(\.\d+)?/
    BOOL: "true" | "false"
    VAR_REF: /\$\{[A-Za-z_][A-Za-z0-9_]*\}/
    TERMINATOR: /\u03a9|\bOmega\b/

    %ignore /\r?\n/
    %ignore " "+
    %ignore /\[HLF-v\d+\]/
    %ignore /\[HLF-v[^\]]*\]/
    %ignore /Δ|Ж|⩕|⌘|∇|⨝/
"""

_parser = Lark(_GRAMMAR, parser="lalr", start="start")


class HLFTransformer(Transformer):
    def start(self, items: list) -> dict[str, Any]:
        program = [i for i in items if i is not None]
        return {"version": "0.3.0", "program": program}

    def line(self, items: list) -> Any:
        return items[0] if items else None

    def tag_stmt(self, items: list) -> dict[str, Any]:
        tag = str(items[0])
        args = items[1] if len(items) > 1 else []
        return {"tag": tag, "args": args}

    def set_stmt(self, items: list) -> dict[str, Any]:
        name = str(items[0])
        value = items[1]
        return {"tag": "SET", "name": name, "value": value, "immutable": True}

    def function_stmt(self, items: list) -> dict[str, Any]:
        name = str(items[0])
        args = items[1] if len(items) > 1 else []
        return {"tag": "FUNCTION", "name": name, "args": args, "pure": True}

    def result_stmt(self, items: list) -> dict[str, Any]:
        args = items[0] if items else []
        return {"tag": "RESULT", "args": args}

    def module_stmt(self, items: list) -> dict[str, Any]:
        name = str(items[0])
        return {"tag": "MODULE", "name": name}

    def import_stmt(self, items: list) -> dict[str, Any]:
        name = str(items[0])
        return {"tag": "IMPORT", "name": name}

    def arglist(self, items: list) -> list:
        return [i for i in items if i is not None]

    def arg(self, items: list) -> Any:
        return items[0] if items else None

    def literal(self, items: list) -> Any:
        return items[0] if items else None

    def kv_pair(self, items: list) -> dict[str, Any]:
        return {str(items[0]): items[1]}

    def TAG(self, token: Token) -> str:
        return str(token)

    def IDENT(self, token: Token) -> str:
        return str(token)

    def PATH(self, token: Token) -> str:
        return str(token)

    def STRING(self, token: Token) -> str:
        return str(token)[1:-1]

    def NUMBER(self, token: Token) -> float | int:
        s = str(token)
        return float(s) if "." in s else int(s)

    def BOOL(self, token: Token) -> bool:
        return str(token) == "true"

    def VAR_REF(self, token: Token) -> str:
        # Preserve the ${VAR} reference string; Pass 2 expands it.
        return str(token)

    def TERMINATOR(self, token: Token) -> None:
        return None


# ------------------------------------------------------------------ #
# Two-Pass compiler (Phase 5 v0.3)
# ------------------------------------------------------------------ #

_VAR_RE = re.compile(r"\$\{(\w+)\}")


def _expand_vars(value: Any, env: dict[str, Any]) -> Any:
    """Recursively expand ``${VAR}`` references in string values."""
    if isinstance(value, str):
        def _replace(m: re.Match) -> str:
            key = m.group(1)
            return str(env.get(key, m.group(0)))

        return _VAR_RE.sub(_replace, value)
    if isinstance(value, list):
        return [_expand_vars(v, env) for v in value]
    if isinstance(value, dict):
        return {k: _expand_vars(v, env) for k, v in value.items()}
    return value


def _pass1_collect_env(program: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Pass 1 — collect immutable SET bindings into a variable environment.
    Raises HlfSyntaxError on duplicate SET (immutable binding).
    """
    env: dict[str, Any] = {}
    for node in program:
        if node and node.get("tag") == "SET":
            name = node["name"]
            if name in env:
                raise HlfSyntaxError(
                    f"Immutable variable '{name}' cannot be reassigned"
                )
            env[name] = node["value"]
    return env


def _pass2_expand_and_validate(
    program: list[dict[str, Any]], env: dict[str, Any]
) -> list[dict[str, Any]]:
    """
    Pass 2 — expand ``${VAR}`` references in all arg values using *env*,
    then return the expanded program.
    """
    expanded: list[dict[str, Any]] = []
    for node in program:
        if node is None:
            continue
        node = dict(node)
        # Expand variable references in args
        if "args" in node:
            node["args"] = _expand_vars(node["args"], env)
        if "value" in node:
            node["value"] = _expand_vars(node["value"], env)
        expanded.append(node)
    return expanded


def compile(source: str) -> dict[str, Any]:  # noqa: A001
    """
    Two-pass HLF compiler.

    Pass 1: collect immutable SET bindings.
    Pass 2: expand ``${VAR}`` references and return the validated JSON AST.
    """
    try:
        tree = _parser.parse(source)
    except LarkError as exc:
        raise HlfSyntaxError(str(exc)) from exc
    transformer = HLFTransformer()
    result = transformer.transform(tree)
    # Filter None entries
    result["program"] = [n for n in result["program"] if n is not None]

    # Pass 1 — build environment from SET bindings
    env = _pass1_collect_env(result["program"])

    # Pass 2 — expand variable references
    result["program"] = _pass2_expand_and_validate(result["program"], env)

    return result


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: hlfc input.hlf [output.json]", file=sys.stderr)
        sys.exit(1)
    src = Path(sys.argv[1]).read_text()
    ast = compile(src)
    output = json.dumps(ast, indent=2)
    if len(sys.argv) >= 3:
        Path(sys.argv[2]).write_text(output)
    else:
        print(output)


if __name__ == "__main__":
    main()
