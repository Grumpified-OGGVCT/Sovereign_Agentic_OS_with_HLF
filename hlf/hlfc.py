"""
HLF Compiler — Lark LALR(1) parser.
Compiles .hlf source → validated JSON AST.
CLI: hlfc input.hlf [output.json]
"""
from __future__ import annotations

import json
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

    tag_stmt: "[" TAG "]" arglist
    set_stmt: "[" "SET" "]" IDENT "=" literal
    function_stmt: "[" "FUNCTION" "]" IDENT arglist
    result_stmt: "[" "RESULT" "]" arglist

    arglist: arg*
    arg: kv_pair | literal

    kv_pair: IDENT "=" literal
    literal: STRING
           | NUMBER
           | BOOL
           | PATH
           | IDENT

    TAG: /[A-Z_]+/
    IDENT: /[A-Za-z_][A-Za-z0-9_]*/
    PATH: /\/[^\s]*/
    STRING: /\"([^\"\\\\]|\\\\.)*\"/
    NUMBER: /-?\d+(\.\d+)?/
    BOOL: "true" | "false"
    TERMINATOR: /\u03a9|\bOmega\b/

    %ignore /\r?\n/
    %ignore " "+
    %ignore /\[HLF-v\d+\]/
    %ignore /\[HLF-v[^\]]*\]/
"""

_parser = Lark(_GRAMMAR, parser="lalr", start="start")


class HLFTransformer(Transformer):
    def start(self, items: list) -> dict[str, Any]:
        program = [i for i in items if i is not None]
        return {"version": "0.2.0", "program": program}

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

    def TERMINATOR(self, token: Token) -> None:
        return None


def compile(source: str) -> dict[str, Any]:  # noqa: A001
    """Parse HLF source and return JSON AST dict."""
    try:
        tree = _parser.parse(source)
    except LarkError as exc:
        raise HlfSyntaxError(str(exc)) from exc
    transformer = HLFTransformer()
    result = transformer.transform(tree)
    # Filter None entries
    result["program"] = [n for n in result["program"] if n is not None]
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
