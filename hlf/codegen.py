"""
HLF Code Generator — Programmatic HLF authoring for agents.

Enables agents to programmatically construct valid HLF programs instead of
string-concatenating raw source. Provides a builder-pattern API that
generates compilable HLF v2 source text.

Usage:
    from hlf.codegen import HLFCodeGenerator

    gen = HLFCodeGenerator()
    gen.set("target", "/deploy/prod")
    gen.intent("deploy", "${target}")
    gen.constraint("timeout", "30s")
    gen.memory("deploy_results", "Deployment succeeded at ${target}", confidence=0.95)
    gen.result(0, "deploy_complete")
    source = gen.build()
    # → '[HLF-v2]\\n[SET] target = "/deploy/prod"\\n...'
"""

from __future__ import annotations

from typing import Any


class HLFCodeGenerator:
    """Builder-pattern HLF source text generator.

    Produces syntactically valid HLF programs that can be compiled
    via hlfc.compile(). Ensures proper structure, terminator,
    and escape handling.
    """

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._version: str = "HLF-v2"

    def set(self, name: str, value: Any) -> HLFCodeGenerator:
        """Add a [SET] immutable binding."""
        self._lines.append(f'[SET] {name} = {_format_literal(value)}')
        return self

    def intent(self, action: str, target: str = "") -> HLFCodeGenerator:
        """Add an [INTENT] statement."""
        parts = [f'[INTENT] {_quote(action)}']
        if target:
            parts.append(_quote(target))
        self._lines.append(" ".join(parts))
        return self

    def constraint(self, key: str, value: Any) -> HLFCodeGenerator:
        """Add a [CONSTRAINT] statement."""
        self._lines.append(f'[CONSTRAINT] {_quote(key)} {_format_literal(value)}')
        return self

    def expect(self, outcome: str) -> HLFCodeGenerator:
        """Add an [EXPECT] statement."""
        self._lines.append(f'[EXPECT] {_quote(outcome)}')
        return self

    def action(self, verb: str, *args: Any) -> HLFCodeGenerator:
        """Add an [ACTION] statement."""
        formatted_args = " ".join(_format_literal(a) for a in args)
        self._lines.append(f'[ACTION] {_quote(verb)} {formatted_args}'.strip())
        return self

    def function(self, name: str, *args: Any) -> HLFCodeGenerator:
        """Add a [FUNCTION] statement."""
        formatted_args = " ".join(_format_literal(a) for a in args)
        self._lines.append(f'[FUNCTION] {name} {formatted_args}'.strip())
        return self

    def delegate(self, role: str, intent: str) -> HLFCodeGenerator:
        """Add a [DELEGATE] statement."""
        self._lines.append(f'[DELEGATE] {_quote(role)} {_quote(intent)}')
        return self

    def vote(self, decision: bool, rationale: str = "") -> HLFCodeGenerator:
        """Add a [VOTE] statement."""
        bool_str = "true" if decision else "false"
        self._lines.append(f'[VOTE] {bool_str} {_quote(rationale)}'.strip())
        return self

    def assert_(self, condition: str, error: str = "") -> HLFCodeGenerator:
        """Add an [ASSERT] statement."""
        self._lines.append(f'[ASSERT] {_quote(condition)} {_quote(error)}'.strip())
        return self

    def thought(self, reasoning: str) -> HLFCodeGenerator:
        """Add a [THOUGHT] statement."""
        self._lines.append(f'[THOUGHT] {_quote(reasoning)}')
        return self

    def observation(self, data: str) -> HLFCodeGenerator:
        """Add an [OBSERVATION] statement."""
        self._lines.append(f'[OBSERVATION] {_quote(data)}')
        return self

    def plan(self, *steps: str) -> HLFCodeGenerator:
        """Add a [PLAN] statement."""
        formatted = " ".join(_quote(s) for s in steps)
        self._lines.append(f'[PLAN] {formatted}')
        return self

    def memory(self, entity: str, content: str, confidence: float = 0.5) -> HLFCodeGenerator:
        """Add a [MEMORY] statement for Infinite RAG storage."""
        conf_part = f' confidence={confidence}' if confidence != 0.5 else ""
        # Grammar expects: [MEMORY] IDENT = literal ...
        self._lines.append(f'[MEMORY] {entity} = {_quote(content)}{conf_part}')
        return self

    def recall(self, entity: str, top_k: int = 5) -> HLFCodeGenerator:
        """Add a [RECALL] statement for Infinite RAG retrieval."""
        topk_part = f' top_k={top_k}' if top_k != 5 else ""
        # Grammar expects: [RECALL] IDENT = literal ...
        self._lines.append(f'[RECALL] {entity} = {_quote(entity)}{topk_part}')
        return self

    def assign(self, name: str, value: Any) -> HLFCodeGenerator:
        """Add an assignment (← operator)."""
        self._lines.append(f'{name} ← {_format_literal(value)}')
        return self

    def conditional(
        self,
        condition: str,
        then_stmt: str,
        else_stmt: str | None = None,
    ) -> HLFCodeGenerator:
        """Add a conditional (⊎ ⇒ ⇌)."""
        line = f'⊎ {condition} ⇒ {then_stmt}'
        if else_stmt:
            line += f' ⇌ {else_stmt}'
        self._lines.append(line)
        return self

    def tool(self, tool_name: str, *args: Any) -> HLFCodeGenerator:
        """Add a tool execution (↦ τ)."""
        formatted = " ".join(_format_literal(a) for a in args)
        self._lines.append(f'↦ τ({tool_name}) {formatted}'.strip())
        return self

    def parallel(self, *tasks: str) -> HLFCodeGenerator:
        """Add a parallel block (∥)."""
        task_list = ", ".join(tasks)
        self._lines.append(f'∥ [{task_list}]')
        return self

    def sync(self, refs: list[str], action: str) -> HLFCodeGenerator:
        """Add a sync barrier (⋈)."""
        ref_list = ", ".join(refs)
        self._lines.append(f'⋈ [{ref_list}] → {action}')
        return self

    def glyph(self, glyph: str, inner: str) -> HLFCodeGenerator:
        """Add a glyph-modified statement."""
        self._lines.append(f'{glyph} {inner}')
        return self

    def import_module(self, name: str) -> HLFCodeGenerator:
        """Add an [IMPORT] statement."""
        self._lines.append(f'[IMPORT] {name}')
        return self

    def module(self, name: str) -> HLFCodeGenerator:
        """Add a [MODULE] declaration."""
        self._lines.append(f'[MODULE] {name}')
        return self

    def result(self, code: int = 0, message: str = "ok") -> HLFCodeGenerator:
        """Add a [RESULT] terminator statement."""
        self._lines.append(f'[RESULT] {code} {_quote(message)}')
        return self

    def raw(self, line: str) -> HLFCodeGenerator:
        """Add a raw HLF line (no processing)."""
        self._lines.append(line)
        return self

    def build(self) -> str:
        """Build the final HLF source text with header and terminator."""
        lines = [f'[{self._version}]'] + self._lines + ['Ω']
        return "\n".join(lines) + "\n"

    def build_and_compile(self) -> dict:
        """Build source and compile it through hlfc.

        Returns:
            Compiled AST dict.

        Raises:
            HlfSyntaxError: If the generated source fails to compile.
        """
        from hlf.hlfc import compile as hlfc_compile
        return hlfc_compile(self.build())

    def __repr__(self) -> str:
        return f"HLFCodeGenerator({len(self._lines)} statements)"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _quote(value: str) -> str:
    """Quote a string value for HLF source, escaping internal quotes."""
    if not value:
        return '""'
    # Already quoted
    if value.startswith('"') and value.endswith('"'):
        return value
    escaped = value.replace('\\', '\\\\').replace('"', '\\"')
    return f'"{escaped}"'


def _format_literal(value: Any) -> str:
    """Format a value as an HLF literal."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        # Check if it's a variable reference
        if value.startswith("${") and value.endswith("}"):
            return value
        return _quote(value)
    return _quote(str(value))
