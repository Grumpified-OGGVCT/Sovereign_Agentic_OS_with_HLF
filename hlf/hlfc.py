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

from lark import Lark, Token, Transformer
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
        | tool_stmt
        | cond_stmt
        | assign_stmt
        | parallel_stmt
        | sync_stmt
        | struct_stmt
        | glyph_stmt

    // --- Existing statements (backward-compatible) ---
    tag_stmt: "[" TAG "]" arglist
    set_stmt: "[" "SET" "]" IDENT "=" literal
    function_stmt: "[" "FUNCTION" "]" IDENT arglist
    result_stmt: "[" "RESULT" "]" arglist
    module_stmt: "[" "MODULE" "]" IDENT
    import_stmt: "[" "IMPORT" "]" IDENT

    // --- RFC 9005: Tool Execution (↦ τ) ---
    tool_stmt: "↦" "τ" "(" DOTTED_IDENT ")" type_ann? arglist
    DOTTED_IDENT: /[A-Za-z_][A-Za-z0-9_.]+/

    // --- RFC 9005: Conditional Logic (⊎ ⇒ ⇌) ---
    cond_stmt: "⊎" cond_expr "⇒" line ("⇌" line)?
    cond_expr: negation
             | intersection
             | union
             | comparison
             | math_expr
    negation: "¬" cond_primary
    intersection: cond_primary "∩" cond_primary
    union: cond_primary "∪" cond_primary
    cond_primary: "(" cond_expr ")"
                | comparison
                | math_expr

    // --- RFC 9005: Math Expressions ---
    comparison: math_expr COMP_OP math_expr
    COMP_OP: "==" | "!=" | ">=" | "<=" | ">" | "<"
    math_expr: math_term ((PLUS | MINUS) math_term)*
    math_term: math_factor ((STAR | SLASH) math_factor)*
    math_factor: literal
               | "(" math_expr ")"
    PLUS: "+"
    MINUS: "-"
    STAR: "*"
    SLASH: "/"

    // --- RFC 9005: Assignment (←) ---
    assign_stmt: IDENT type_ann? "←" assign_rhs
    assign_rhs: tool_stmt | math_expr

    // --- RFC 9005: Type Annotations (::) ---
    type_ann: "::" TYPE_SYM
    TYPE_SYM: "𝕊" | "ℕ" | "𝔹" | "𝕁" | "𝔸"

    // --- RFC 9005: Concurrency (∥ ⋈) ---
    parallel_stmt: "∥" "[" line ("," line)* "]"
    sync_stmt: "⋈" "[" IDENT ("," IDENT)* "]" "→" line

    // --- RFC 9005: Pass-by-Reference (&) ---
    ref_arg: "&" IDENT

    // --- RFC 9005: Epistemic Modifier (_{ρ:val}) ---
    epistemic: "_{" "ρ" ":" NUMBER "}"

    // --- RFC 9007: Struct Operator (≡) ---
    struct_stmt: IDENT "≡" "{" struct_field ("," struct_field)* "}"
    struct_field: IDENT ":" TYPE_SYM

    // --- Glyph-prefixed statements (⌘ Ж ∇ ⩕ ⨝ Δ) ---
    // Previously %ignore'd — now properly parsed as statement modifiers
    glyph_stmt: GLYPH_PREFIX tag_stmt
              | GLYPH_PREFIX glyph_stmt
    GLYPH_PREFIX: /[⌘Ж∇⩕⨝Δ~§]/

    arglist: arg*
    arg: kv_pair | ref_arg | literal
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
"""

_parser = Lark(_GRAMMAR, parser="lalr", start="start")


class HLFTransformer(Transformer):
    """
    Transform parse tree → JSON AST with human-readable translations.

    Every AST node includes a ``human_readable`` field to satisfy the
    InsAIts V2 Transparent Compression mandate: humans must always be
    able to audit what the mathematical HLF expression means.
    """

    # --- Glyph-prefix symbol map for human-readable translation ---
    _GLYPH_NAMES: dict[str, str] = {
        "⌘": "EXECUTE",
        "Ж": "CONSTRAINT",
        "∇": "PARAMETER",
        "⩕": "PRIORITY",
        "⨝": "JOIN",
        "Δ": "DELTA",
        "~": "AESTHETIC",
        "§": "EXPRESSION",
    }

    _TYPE_NAMES: dict[str, str] = {
        "𝕊": "String",
        "ℕ": "Number",
        "𝔹": "Boolean",
        "𝕁": "JSON",
        "𝔸": "Array",
    }

    def start(self, items: list) -> dict[str, Any]:
        program = [i for i in items if i is not None]
        return {"version": "0.4.0", "program": program}

    def line(self, items: list) -> Any:
        return items[0] if items else None

    # --- Existing statements (backward-compatible) ---

    def tag_stmt(self, items: list) -> dict[str, Any]:
        tag = str(items[0])
        args = items[1] if len(items) > 1 else []
        return {
            "tag": tag,
            "args": args,
            "human_readable": f"Execute {tag} action with {len(args)} argument(s)",
        }

    def set_stmt(self, items: list) -> dict[str, Any]:
        name = str(items[0])
        value = items[1]
        return {
            "tag": "SET",
            "name": name,
            "value": value,
            "immutable": True,
            "human_readable": f"Bind immutable variable '{name}' = {value!r}",
        }

    def function_stmt(self, items: list) -> dict[str, Any]:
        name = str(items[0])
        args = items[1] if len(items) > 1 else []
        return {
            "tag": "FUNCTION",
            "name": name,
            "args": args,
            "pure": True,
            "human_readable": f"Call pure function '{name}' with {len(args)} argument(s)",
        }

    def result_stmt(self, items: list) -> dict[str, Any]:
        args = items[0] if items else []
        return {
            "tag": "RESULT",
            "args": args,
            "human_readable": f"Return result with {len(args)} field(s)",
        }

    def module_stmt(self, items: list) -> dict[str, Any]:
        name = str(items[0])
        return {
            "tag": "MODULE",
            "name": name,
            "human_readable": f"Declare module '{name}'",
        }

    def import_stmt(self, items: list) -> dict[str, Any]:
        name = str(items[0])
        return {
            "tag": "IMPORT",
            "name": name,
            "human_readable": f"Import module '{name}'",
        }

    # --- RFC 9005: Tool Execution (↦ τ) ---

    def tool_stmt(self, items: list) -> dict[str, Any]:
        tool_name = str(items[0])
        type_ann = None
        args = []
        for item in items[1:]:
            if isinstance(item, dict) and "type" in item:
                type_ann = item
            elif isinstance(item, list):
                args = item
        return {
            "tag": "TOOL",
            "operator": "↦ τ",
            "tool": tool_name,
            "type_annotation": type_ann,
            "args": args,
            "human_readable": (
                f"Execute tool '{tool_name}'"
                + (f" returning {type_ann['type']}" if type_ann else "")
                + (f" with {len(args)} argument(s)" if args else "")
            ),
        }

    def DOTTED_IDENT(self, token: Token) -> str:
        return str(token)

    # --- RFC 9005: Conditional Logic (⊎ ⇒ ⇌) ---

    def cond_stmt(self, items: list) -> dict[str, Any]:
        condition = items[0]
        then_branch = items[1]
        else_branch = items[2] if len(items) > 2 else None
        cond_desc = condition.get("human_readable", str(condition)) if isinstance(condition, dict) else str(condition)
        result: dict[str, Any] = {
            "tag": "CONDITIONAL",
            "operator": "⊎ ⇒ ⇌",
            "condition": condition,
            "then": then_branch,
            "human_readable": f"IF {cond_desc} THEN {_hr(then_branch)}",
        }
        if else_branch:
            result["else"] = else_branch
            result["human_readable"] += f" ELSE {_hr(else_branch)}"
        return result

    def cond_expr(self, items: list) -> Any:
        return items[0] if items else None

    def cond_primary(self, items: list) -> Any:
        return items[0] if items else None

    def assign_rhs(self, items: list) -> Any:
        return items[0] if items else None

    def negation(self, items: list) -> dict[str, Any]:
        operand = items[0]
        return {
            "op": "NOT",
            "operator": "¬",
            "operand": operand,
            "human_readable": f"NOT {_hr(operand)}",
        }

    def intersection(self, items: list) -> dict[str, Any]:
        return {
            "op": "AND",
            "operator": "∩",
            "left": items[0],
            "right": items[1],
            "human_readable": f"{_hr(items[0])} AND {_hr(items[1])}",
        }

    def union(self, items: list) -> dict[str, Any]:
        return {
            "op": "OR",
            "operator": "∪",
            "left": items[0],
            "right": items[1],
            "human_readable": f"{_hr(items[0])} OR {_hr(items[1])}",
        }

    def atom(self, items: list) -> Any:
        return items[0] if items else None

    # --- RFC 9005: Math Expressions ---

    def comparison(self, items: list) -> dict[str, Any]:
        left, op, right = items[0], str(items[1]), items[2]
        return {
            "op": "COMPARE",
            "operator": op,
            "left": left,
            "right": right,
            "human_readable": f"{_hr(left)} {op} {_hr(right)}",
        }

    def math_expr(self, items: list) -> Any:
        if len(items) == 1:
            return items[0]
        # Build left-associative binary tree
        result = items[0]
        i = 1
        while i < len(items):
            op = str(items[i])
            right = items[i + 1]
            result = {
                "op": "MATH",
                "operator": op,
                "left": result,
                "right": right,
                "human_readable": f"{_hr(result)} {op} {_hr(right)}",
            }
            i += 2
        return result

    def math_term(self, items: list) -> Any:
        if len(items) == 1:
            return items[0]
        result = items[0]
        i = 1
        while i < len(items):
            op = str(items[i])
            right = items[i + 1]
            result = {
                "op": "MATH",
                "operator": op,
                "left": result,
                "right": right,
                "human_readable": f"{_hr(result)} {op} {_hr(right)}",
            }
            i += 2
        return result

    def math_factor(self, items: list) -> Any:
        return items[0] if items else None

    # --- RFC 9005: Assignment (←) ---

    def assign_stmt(self, items: list) -> dict[str, Any]:
        name = str(items[0])
        type_ann = None
        value = None
        for item in items[1:]:
            if isinstance(item, dict) and "type" in item:
                type_ann = item
            elif item is not None:
                value = item
        type_str = f" (type: {type_ann['type']})" if type_ann else ""
        return {
            "tag": "ASSIGN",
            "operator": "←",
            "name": name,
            "type_annotation": type_ann,
            "value": value,
            "human_readable": f"Assign '{name}'{type_str} ← {_hr(value)}",
        }

    # --- RFC 9005: Type Annotations ---

    def type_ann(self, items: list) -> dict[str, str]:
        sym = str(items[0])
        return {
            "type": sym,
            "type_name": self._TYPE_NAMES.get(sym, sym),
            "human_readable": f"Type: {self._TYPE_NAMES.get(sym, sym)}",
        }

    def TYPE_SYM(self, token: Token) -> str:
        return str(token)

    # --- RFC 9005: Concurrency (∥ ⋈) ---

    def parallel_stmt(self, items: list) -> dict[str, Any]:
        tasks = [i for i in items if i is not None]
        return {
            "tag": "PARALLEL",
            "operator": "∥",
            "tasks": tasks,
            "human_readable": f"Execute {len(tasks)} task(s) in parallel",
        }

    def sync_stmt(self, items: list) -> dict[str, Any]:
        # Last item is the line to execute; preceding are IDENT refs
        refs = [str(i) for i in items[:-1]]
        action = items[-1]
        return {
            "tag": "SYNC",
            "operator": "⋈",
            "refs": refs,
            "action": action,
            "human_readable": f"Wait for [{', '.join(refs)}] then {_hr(action)}",
        }

    # --- RFC 9005: Pass-by-Reference ---

    def ref_arg(self, items: list) -> dict[str, Any]:
        name = str(items[0])
        return {
            "ref": name,
            "operator": "&",
            "human_readable": f"Pass '{name}' by reference",
        }

    # --- RFC 9005: Epistemic Modifier ---

    def epistemic(self, items: list) -> dict[str, Any]:
        confidence = items[0]
        return {
            "tag": "EPISTEMIC",
            "operator": "ρ",
            "confidence": confidence,
            "human_readable": f"Confidence: {confidence}",
        }

    # --- RFC 9007: Struct Operator (≡) ---

    def struct_stmt(self, items: list) -> dict[str, Any]:
        name = str(items[0])
        fields = [i for i in items[1:] if isinstance(i, dict)]
        field_desc = ", ".join(f"{f['name']}: {f['type_name']}" for f in fields)
        return {
            "tag": "STRUCT",
            "operator": "≡",
            "name": name,
            "fields": fields,
            "human_readable": f"Define struct '{name}' with fields: {field_desc}",
        }

    def struct_field(self, items: list) -> dict[str, str]:
        name = str(items[0])
        type_sym = str(items[1])
        return {
            "name": name,
            "type": type_sym,
            "type_name": self._TYPE_NAMES.get(type_sym, type_sym),
        }

    # --- Glyph-prefixed statements ---

    def glyph_stmt(self, items: list) -> dict[str, Any]:
        glyph = str(items[0])
        inner = items[1]
        glyph_name = self._GLYPH_NAMES.get(glyph, glyph)
        inner_hr = inner.get("human_readable", str(inner)) if isinstance(inner, dict) else str(inner)
        # Wrap the inner statement with the glyph modifier
        return {
            "tag": "GLYPH_MODIFIED",
            "glyph": glyph,
            "glyph_name": glyph_name,
            "inner": inner,
            "human_readable": f"[{glyph_name}] {inner_hr}",
        }

    def GLYPH_PREFIX(self, token: Token) -> str:
        return str(token)

    # --- Terminal handlers (unchanged) ---

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


def _hr(value: Any) -> str:
    """Extract human-readable string from an AST node or value."""
    if isinstance(value, dict):
        return value.get("human_readable", str(value))
    return str(value)


def format_correction(source: str, error: HlfSyntaxError) -> dict[str, Any]:
    """
    Iterative Intervention Engine — format a structured correction
    response for an agent that sent malformed HLF.

    Returns a dict with:
    - ``error``: the original error message
    - ``source``: the source that failed
    - ``correction_hlf``: a suggested HLF correction (if determinable)
    - ``human_readable``: plain-language explanation of the error
    - ``valid_operators``: list of valid RFC 9005/9007 operators
    - ``suggestion``: what the agent should do differently

    The calling system should send this back to the offending agent
    so it can self-adapt and retry.
    """
    err_str = str(error)

    # Catalog of valid operators for reference
    valid_ops = {
        "↦ τ(tool.name)": "Execute a tool (RFC 9005 §4.1)",
        "⊎ condition ⇒ then ⇌ else": "Conditional logic (RFC 9005 §3.2)",
        "¬ expr": "Logical negation (RFC 9005 §3.1)",
        "expr ∩ expr": "Logical AND / intersection (RFC 9005 §3.1)",
        "expr ∪ expr": "Logical OR / union (RFC 9005 §3.1)",
        "name ← value": "Assignment / computation (RFC 9005 §5.1)",
        ":: 𝕊|ℕ|𝔹|𝕁|𝔸": "Type annotation (RFC 9005 §2.3)",
        "∥ [ tasks ]": "Parallel execution (RFC 9005 §6.1)",
        "⋈ [ refs ] → stmt": "Synchronization barrier (RFC 9005 §6.2)",
        "& name": "Pass-by-reference (RFC 9005 §5.3)",
        "≡ { fields }": "Struct definition (RFC 9007 §2.1)",
        "⌘ Ж ∇ ⩕": "Statement modifiers (execute/constraint/param/priority)",
    }

    return {
        "error": err_str,
        "source": source,
        "correction_hlf": None,  # Future: AI-powered suggestion
        "human_readable": (
            f"HLF compilation failed: {err_str}. "
            "Review the valid operator list and retry with corrected syntax."
        ),
        "valid_operators": valid_ops,
        "suggestion": (
            "Consult docs/HLF_GRAMMAR_REFERENCE.md before composing HLF. "
            "Use the operator catalog above to find the correct glyph. "
            "Every HLF program must end with Ω (terminator)."
        ),
    }


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
    src = Path(sys.argv[1]).read_text(encoding="utf-8")
    ast = compile(src)
    output = json.dumps(ast, indent=2)
    if len(sys.argv) >= 3:
        Path(sys.argv[2]).write_text(output, encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
