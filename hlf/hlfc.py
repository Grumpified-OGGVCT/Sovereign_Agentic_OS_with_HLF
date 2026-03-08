"""
HLF Compiler — Lark LALR(1) parser.
Compiles .hlf source → validated JSON AST.
CLI: hlfc input.hlf [output.json]
"""

from __future__ import annotations

import argparse
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
        | memory_stmt
        | recall_stmt
        | define_stmt
        | call_stmt
        | spec_stmt
        | spec_gate_stmt
        | spec_update_stmt
        | spec_seal_stmt

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

    // --- Infinite RAG: Memory Operations (⌂) ---
    memory_stmt: "[" "MEMORY" "]" IDENT "=" literal ("confidence" "=" NUMBER)? literal
    recall_stmt: "[" "RECALL" "]" IDENT "=" literal ("top_k" "=" NUMBER)?

    // --- HLF v4: Macro Definitions (Σ [DEFINE]) ---
    define_stmt: "Σ" "[" "DEFINE" "]" STRING "=" "{" line+ "}"
    call_stmt: "[" "CALL" "]" STRING arglist

    // --- Instinct: Living Spec Lifecycle ---
    spec_stmt: "[" "SPEC_DEFINE" "]" STRING arglist
    spec_gate_stmt: "[" "SPEC_GATE" "]" cond_expr
    spec_update_stmt: "[" "SPEC_UPDATE" "]" STRING arglist
    spec_seal_stmt: "[" "SPEC_SEAL" "]"

    // --- Glyph-prefixed statements (⌘ Ж ∇ ⩕ ⨝ Δ) ---
    // Previously %ignore'd — now properly parsed as statement modifiers
    glyph_stmt: GLYPH_PREFIX tag_stmt
              | GLYPH_PREFIX call_stmt
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
        return {"version": "0.4.0", "compiler": "HLFC-v0.4.0", "program": program}

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
        # Parse structured error-code: RESULT code message
        code = None
        message = None
        if args:
            # First arg is the error code (int)
            try:
                code = int(args[0]) if len(args) > 0 else 0
            except (ValueError, TypeError):
                code = None
            # Second arg is the message (string)
            message = str(args[1]) if len(args) > 1 else None

        # Classification: 0=success, 1-99=recoverable, 100+=fatal
        if code is not None:
            if code == 0:
                severity = "SUCCESS"
            elif code < 100:
                severity = "RECOVERABLE"
            else:
                severity = "FATAL"
            hr = f"Return {severity} (code={code})"
            if message:
                hr += f": {message}"
        else:
            hr = f"Return result with {len(args)} field(s)"

        return {
            "tag": "RESULT",
            "code": code,
            "message": message,
            "args": args,
            "terminator": True,
            "human_readable": hr,
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

    # --- Infinite RAG: Memory Operations (⌂) ---

    def memory_stmt(self, items: list) -> dict[str, Any]:
        # Grammar: [MEMORY] IDENT = literal [confidence=N] [content]
        # items[0] = IDENT (entity key/scope), items[1] = literal (stored value)
        entity = str(items[0])      # IDENT — the memory scope key
        stored_value = items[1]     # literal — the primary content
        # Optional confidence and additional content parameters
        confidence = None
        content = None
        for item in items[2:]:
            if isinstance(item, (int, float)):
                confidence = float(item)
            elif item is not None:
                content = item
        # If no separate content param, the stored_value IS the content
        if content is None:
            content = stored_value
        conf_str = f" (confidence: {confidence})" if confidence else ""
        content_str = str(content)
        return {
            "tag": "MEMORY",
            "operator": "⌂",
            "entity": entity,
            "confidence": confidence if confidence is not None else 0.5,
            "content": content,
            "human_readable": f"Store memory: '{content_str}' for entity '{entity}'{conf_str}",
        }

    def recall_stmt(self, items: list) -> dict[str, Any]:
        # Grammar: [RECALL] IDENT = literal [top_k=N]
        # items[0] = IDENT (entity key/scope), items[1] = literal (filter/query)
        entity = str(items[0])      # IDENT — the memory scope to recall from
        _filter = items[1]          # literal — recall filter (logged but entity is the key)
        top_k = None
        for item in items[2:]:
            if isinstance(item, (int, float)):
                top_k = int(item)
        return {
            "tag": "RECALL",
            "operator": "⌂?",
            "entity": entity,
            "top_k": top_k if top_k is not None else 5,
            "human_readable": f"Recall memories for entity '{entity}' (top_k={top_k or 5})",
        }

    # --- HLF v4: Macro Definitions (Σ [DEFINE]) ---

    def define_stmt(self, items: list) -> dict[str, Any]:
        name = str(items[0]).strip('"')
        body = [i for i in items[1:] if i is not None]
        return {
            "tag": "DEFINE",
            "operator": "Σ",
            "name": name,
            "body": body,
            "human_readable": f"Define macro '{name}' with {len(body)} statement(s)",
        }

    def call_stmt(self, items: list) -> dict[str, Any]:
        name = str(items[0]).strip('"')
        args = items[1] if len(items) > 1 else []
        return {
            "tag": "CALL",
            "operator": "⌘",
            "name": name,
            "args": args if isinstance(args, list) else [args],
            "human_readable": f"Call macro '{name}' with {len(args) if isinstance(args, list) else 1} argument(s)",
        }

    def struct_field(self, items: list) -> dict[str, str]:
        name = str(items[0])
        type_sym = str(items[1])
        return {
            "name": name,
            "type": type_sym,
            "type_name": self._TYPE_NAMES.get(type_sym, type_sym),
        }

    # --- Instinct: Living Spec Lifecycle ---

    def spec_stmt(self, items: list) -> dict[str, Any]:
        section = str(items[0]).strip('"')
        constraints = items[1] if len(items) > 1 else []
        constraint_count = len(constraints) if isinstance(constraints, list) else 1
        return {
            "tag": "SPEC_DEFINE",
            "section": section,
            "constraints": constraints if isinstance(constraints, list) else [constraints],
            "human_readable": f"Define spec section '{section}' with {constraint_count} constraint(s)",
        }

    def spec_gate_stmt(self, items: list) -> dict[str, Any]:
        condition = items[0]
        cond_desc = condition.get("human_readable", str(condition)) if isinstance(condition, dict) else str(condition)
        return {
            "tag": "SPEC_GATE",
            "condition": condition,
            "human_readable": f"Spec gate: assert {cond_desc}",
        }

    def spec_update_stmt(self, items: list) -> dict[str, Any]:
        section = str(items[0]).strip('"')
        updates = items[1] if len(items) > 1 else []
        return {
            "tag": "SPEC_UPDATE",
            "section": section,
            "updates": updates if isinstance(updates, list) else [updates],
            "human_readable": f"Update spec section '{section}'",
        }

    def spec_seal_stmt(self, items: list) -> dict[str, Any]:
        return {
            "tag": "SPEC_SEAL",
            "human_readable": "Seal spec — no further updates allowed",
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
            f"HLF compilation failed: {err_str}. Review the valid operator list and retry with corrected syntax."
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
                raise HlfSyntaxError(f"Immutable variable '{name}' cannot be reassigned")
            env[name] = node["value"]
    return env


def _pass2_expand_and_validate(program: list[dict[str, Any]], env: dict[str, Any]) -> list[dict[str, Any]]:
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


# ------------------------------------------------------------------ #
# Pass 3 — ALIGN Ledger Security Validation (V.2)
# ------------------------------------------------------------------ #

_ALIGN_RULES: list[dict[str, Any]] = []
_ALIGN_COMPILED: list[tuple[str, str, re.Pattern[str], str]] = []


def _load_align_ledger() -> None:
    """Load ALIGN_LEDGER.yaml at module init — compile all regex_block rules.

    Each rule becomes a tuple of (rule_id, rule_name, compiled_regex, action).
    Rules without regex_block (e.g. R-002 condition-based) are skipped
    since they require runtime context we don't have at compile-time.
    """
    global _ALIGN_RULES, _ALIGN_COMPILED
    ledger_path = Path(__file__).parent.parent / "governance" / "ALIGN_LEDGER.yaml"
    if not ledger_path.exists():
        return  # graceful degradation — no ledger, no enforcement

    try:
        # Use stdlib yaml-like parsing to avoid PyYAML dependency
        # The ALIGN_LEDGER.yaml is simple enough for a regex-based parse
        text = ledger_path.read_text(encoding="utf-8")

        # Try PyYAML first, fall back to manual parse
        try:
            import yaml

            data = yaml.safe_load(text)
        except ImportError:
            # Manual minimal YAML parse for the flat rule structure
            data = _parse_align_yaml_minimal(text)

        if not data or "rules" not in data:
            return

        _ALIGN_RULES = data["rules"]
        for rule in _ALIGN_RULES:
            rid = rule.get("id", "?")
            name = rule.get("name", "Unknown")
            regex_str = rule.get("regex_block")
            action = rule.get("action", "DROP")
            if regex_str:
                try:
                    compiled = re.compile(regex_str)
                    _ALIGN_COMPILED.append((rid, name, compiled, action))
                except re.error:
                    pass  # skip malformed regex
    except Exception:
        pass  # fail open at module load, enforced at runtime


def _parse_align_yaml_minimal(text: str) -> dict[str, Any]:
    """Minimal parser for ALIGN_LEDGER.yaml without PyYAML dependency.

    Handles the specific flat-list-of-dicts format used in the ledger.
    """
    rules: list[dict[str, str]] = []
    # Match lines like: - { id: "R-001", name: "ACFS Confinement", regex_block: '...', action: "DROP" }
    pattern = re.compile(
        r'-\s*\{\s*id:\s*"([^"]+)"\s*,\s*name:\s*"([^"]+)"\s*,'
        r'\s*(?:regex_block:\s*[\'"]([^\'"]+)[\'"]|condition:\s*"[^"]+")\s*,'
        r'\s*action:\s*"([^"]+)"\s*\}'
    )
    for m in pattern.finditer(text):
        rule: dict[str, str] = {
            "id": m.group(1),
            "name": m.group(2),
            "action": m.group(4),
        }
        if m.group(3):
            rule["regex_block"] = m.group(3)
        rules.append(rule)
    return {"version": "1.0-genesis", "rules": rules}


# Load at module init
_load_align_ledger()


class HlfAlignViolation(HlfSyntaxError):
    """Raised when compiled HLF violates an ALIGN Ledger rule.

    Attributes:
        rule_id: The ALIGN rule identifier (e.g. "R-001")
        rule_name: Human-readable rule name
        action: The enforcement action (DROP, DROP_AND_QUARANTINE, etc.)
        match: The string that triggered the violation
    """

    def __init__(self, rule_id: str, rule_name: str, action: str, match: str, node_tag: str = ""):
        self.rule_id = rule_id
        self.rule_name = rule_name
        self.action = action
        self.match = match
        self.node_tag = node_tag
        super().__init__(f"ALIGN violation [{rule_id}] {rule_name}: '{match}' in [{node_tag}] blocked by {action}")


def _extract_strings_from_node(node: dict[str, Any]) -> list[str]:
    """Recursively extract all string values from an AST node for scanning."""
    strings: list[str] = []

    def _walk(val: Any) -> None:
        if isinstance(val, str):
            strings.append(val)
        elif isinstance(val, list):
            for item in val:
                _walk(item)
        elif isinstance(val, dict):
            for v in val.values():
                _walk(v)

    _walk(node.get("args", []))
    _walk(node.get("value", ""))
    _walk(node.get("human_readable", ""))
    # Also check action/tool names for tool execution nodes
    if "name" in node and node.get("tag") != "SET":
        strings.append(str(node["name"]))

    return strings


def _pass3_align_validate(
    program: list[dict[str, Any]],
    *,
    strict: bool = True,
) -> list[dict[str, Any]]:
    """Pass 3 — Validate expanded AST against ALIGN Ledger rules.

    Scans every string in every AST node against all compiled ALIGN rules.
    If strict=True (default), raises HlfAlignViolation on first match.
    If strict=False, annotates nodes with violation metadata but continues.

    This is the compile-time security gate. Combined with the runtime
    sentinel_gate.py enforcement, it creates defense-in-depth.
    """
    if not _ALIGN_COMPILED:
        return program  # no rules loaded — pass through

    annotated: list[dict[str, Any]] = []
    for node in program:
        if node is None:
            continue

        node_tag = node.get("tag", "?")
        strings = _extract_strings_from_node(node)

        violations: list[dict[str, str]] = []
        for text in strings:
            for rule_id, rule_name, pattern, action in _ALIGN_COMPILED:
                match = pattern.search(text)
                if match:
                    if strict:
                        raise HlfAlignViolation(
                            rule_id=rule_id,
                            rule_name=rule_name,
                            action=action,
                            match=match.group(0),
                            node_tag=node_tag,
                        )
                    violations.append(
                        {
                            "rule_id": rule_id,
                            "rule_name": rule_name,
                            "action": action,
                            "matched": match.group(0),
                        }
                    )

        if violations and not strict:
            node = dict(node)
            node["align_violations"] = violations

        annotated.append(node)

    return annotated


def compile(source: str, *, align_strict: bool = True) -> dict[str, Any]:  # noqa: A001
    """
    Four-pass HLF compiler.

    Pass 1: collect immutable SET bindings.
    Pass 2: expand ``${VAR}`` references.
    Pass 3: validate against ALIGN Ledger security rules.
    Pass 4: enforce dictionary.json arity/type constraints.

    Args:
        source: HLF source code string.
        align_strict: If True (default), raise HlfAlignViolation on any
            ALIGN rule match. If False, annotate nodes with violation
            metadata but return the AST anyway (useful for analysis).

    Returns:
        Validated JSON AST dict with ``program``, ``compiler``, and
        ``human_readable`` fields.

    Raises:
        HlfSyntaxError: If the source fails to parse.
        HlfAlignViolation: If any AST content violates an ALIGN rule
            (only when align_strict=True).
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

    # Pass 3 — ALIGN Ledger security validation
    result["program"] = _pass3_align_validate(result["program"], strict=align_strict)

    # Pass 4 — dictionary.json arity/type enforcement
    _pass4_dictionary_validate(result["program"])

    # Record the ALIGN rules that were enforced
    if _ALIGN_COMPILED:
        result["align_rules_enforced"] = [
            {"id": rid, "name": name, "action": action} for rid, name, _, action in _ALIGN_COMPILED
        ]

    # Record dictionary enforcement metadata
    if _DICT_TAGS:
        result["dictionary_enforced"] = True
        result["dictionary_version"] = _DICT_VERSION

    return result


# ------------------------------------------------------------------ #
# Pass 4 — dictionary.json Arity / Type Enforcement
# ------------------------------------------------------------------ #

# Type checkers for dictionary.json "type" field
_TYPE_VALIDATORS: dict[str, Any] = {
    "string": lambda v: isinstance(v, str),
    "int": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "bool": lambda v: isinstance(v, bool) or str(v).lower() in ("true", "false"),
    "any": lambda _: True,
    "path": lambda v: isinstance(v, str),
    "identifier": lambda v: isinstance(v, str) and v.isidentifier(),
    "reference": lambda v: isinstance(v, dict) and "ref" in v and v.get("operator") == "&",
}

# Tag registry loaded from dictionary.json
_DICT_TAGS: dict[str, dict[str, Any]] = {}
_DICT_VERSION: str = "unknown"


def _load_dictionary() -> None:
    """Load governance/templates/dictionary.json at module init.

    Builds a tag registry mapping tag names to their argument specs,
    including expected arity, types, and properties (pure, immutable, etc.).
    """
    global _DICT_TAGS, _DICT_VERSION
    dict_path = Path(__file__).parent.parent / "governance" / "templates" / "dictionary.json"
    if not dict_path.exists():
        return

    try:
        data = json.loads(dict_path.read_text(encoding="utf-8"))
        _DICT_VERSION = data.get("version", "unknown")

        for tag_def in data.get("tags", []):
            name = tag_def.get("name")
            if not name:
                continue

            args_spec = tag_def.get("args", [])
            # Calculate arity bounds
            required = sum(1 for a in args_spec if not a.get("repeat"))
            has_repeat = any(a.get("repeat") for a in args_spec)

            _DICT_TAGS[name] = {
                "args": args_spec,
                "min_arity": required,
                "max_arity": None if has_repeat else len(args_spec),
                "pure": tag_def.get("pure", False),
                "immutable": tag_def.get("immutable", False),
                "terminator": tag_def.get("terminator", False),
            }
    except Exception:
        pass  # fail open — no dictionary, no enforcement


# Load at module init
_load_dictionary()


class HlfArityError(HlfSyntaxError):
    """Raised when an AST node violates dictionary.json arity constraints.

    Attributes:
        tag: The tag name that violated arity
        expected_min: Minimum expected arguments
        expected_max: Maximum expected arguments (None = unlimited)
        actual: Actual argument count provided
    """

    def __init__(self, tag: str, expected_min: int, expected_max: int | None, actual: int):
        self.tag = tag
        self.expected_min = expected_min
        self.expected_max = expected_max
        self.actual = actual
        if expected_max is None:
            constraint = f"at least {expected_min}"
        elif expected_min == expected_max:
            constraint = f"exactly {expected_min}"
        else:
            constraint = f"{expected_min}-{expected_max}"
        super().__init__(f"[{tag}] arity violation: expected {constraint} args, got {actual}")


class HlfTypeError(HlfSyntaxError):
    """Raised when an AST node argument violates dictionary.json type constraints.

    Attributes:
        tag: The tag name
        arg_name: The argument name
        expected_type: The expected type from dictionary.json
        actual_value: The actual value provided
    """

    def __init__(self, tag: str, arg_name: str, expected_type: str, actual_value: Any):
        self.tag = tag
        self.arg_name = arg_name
        self.expected_type = expected_type
        self.actual_value = actual_value
        super().__init__(
            f"[{tag}] type violation: arg '{arg_name}' expects {expected_type}, "
            f"got {type(actual_value).__name__} ({actual_value!r})"
        )


def _pass4_dictionary_validate(program: list[dict[str, Any]]) -> None:
    """Pass 4 — Validate AST nodes against dictionary.json tag specs.

    Checks:
    - Arity: min/max argument count per tag definition
    - Types: argument types match dictionary.json type specs
    - Properties: terminators, purity, immutability annotations

    Raises HlfArityError or HlfTypeError on violations.
    """
    if not _DICT_TAGS:
        return  # no dictionary loaded — pass through

    for node in program:
        if node is None:
            continue

        tag = node.get("tag")
        if not tag or tag not in _DICT_TAGS:
            continue  # unknown tags are allowed (forward compatibility)

        spec = _DICT_TAGS[tag]
        raw_args = node.get("args", [])

        # --- Count actual arguments ---
        # Some AST nodes (SET, FUNCTION, RESULT) store arguments as named
        # keys on the node dict rather than in an "args" list.  Count the
        # number of spec-defined arg names that are present on the node to
        # get the true arity.
        spec_arg_names = [a["name"] for a in spec["args"]]

        if isinstance(raw_args, list) and len(raw_args) > 0:
            actual_count = len(raw_args)
        else:
            # Count named fields present on the node itself
            actual_count = sum(1 for name in spec_arg_names if name in node)
            # If still zero, fall back to counting dict-style args
            if actual_count == 0 and isinstance(raw_args, dict):
                actual_count = len(raw_args)

        # --- Arity check ---
        min_arity = spec["min_arity"]
        max_arity = spec["max_arity"]

        if actual_count < min_arity:
            raise HlfArityError(tag, min_arity, max_arity, actual_count)
        if max_arity is not None and actual_count > max_arity:
            raise HlfArityError(tag, min_arity, max_arity, actual_count)

        # --- Type check (positional args) ---
        if isinstance(raw_args, list) and len(raw_args) > 0:
            for i, arg_spec in enumerate(spec["args"]):
                if i >= len(raw_args):
                    break  # remaining args are optional/repeat

                arg_name = arg_spec.get("name", f"arg{i}")
                expected_type = arg_spec.get("type", "any")
                validator = _TYPE_VALIDATORS.get(expected_type, lambda _: True)

                val = raw_args[i]
                if isinstance(val, dict) and arg_name in val and "ref" not in val:
                    val = val[arg_name]

                # Allow references (Pass-by-Ref) even if the type spec says something else,
                # as long as it's not explicitly forbidden or we want to allow it everywhere.
                is_ref = _TYPE_VALIDATORS["reference"](val)

                if not is_ref and not validator(val):
                    raise HlfTypeError(tag, arg_name, expected_type, val)

        # --- Annotate properties from dictionary ---
        if spec.get("pure") and "pure" not in node:
            node["pure"] = True
        if spec.get("terminator") and "terminator" not in node:
            node["terminator"] = True
        if spec.get("immutable") and "immutable" not in node:
            node["immutable"] = True


def main() -> None:
    """HLF Compiler CLI — compile .hlf to JSON AST, .hlb bytecode, or disassemble."""
    parser = argparse.ArgumentParser(
        prog="hlfc",
        description="HLF Compiler — compiles .hlf source to JSON AST or bytecode.",
    )
    parser.add_argument("input", help="Input file (.hlf source or .hlb for --disassemble)")
    parser.add_argument("output", nargs="?", default=None, help="Output file (default: stdout)")
    parser.add_argument(
        "--emit-bytecode",
        action="store_true",
        help="Compile HLF source to .hlb bytecode binary instead of JSON AST.",
    )
    parser.add_argument(
        "--disassemble",
        action="store_true",
        help="Disassemble a .hlb bytecode file to human-readable text.",
    )
    args = parser.parse_args()
    input_path = Path(args.input)

    if args.disassemble:
        # Disassemble mode: .hlb → text
        from hlf.bytecode import disassemble

        hlb_data = input_path.read_bytes()
        text = disassemble(hlb_data)
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
        else:
            print(text)
        return

    # Compile HLF source
    src = input_path.read_text(encoding="utf-8")
    ast = compile(src)

    if args.emit_bytecode:
        # Bytecode mode: .hlf → .hlb
        from hlf.bytecode import compile_to_bytecode

        hlb = compile_to_bytecode(ast)
        out_path = Path(args.output) if args.output else input_path.with_suffix(".hlb")
        out_path.write_bytes(hlb)
        size = len(hlb)
        print(f"Wrote {size} bytes to {out_path}", file=sys.stderr)
    else:
        # JSON AST mode (default)
        output = json.dumps(ast, indent=2)
        if args.output:
            Path(args.output).write_text(output, encoding="utf-8")
        else:
            print(output)


if __name__ == "__main__":
    main()
