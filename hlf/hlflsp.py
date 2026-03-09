"""
HLF Language Server Protocol — pygls-based LSP for .hlf files.

Provides real-time IDE support:
  - textDocument/publishDiagnostics: lint + syntax errors
  - textDocument/completion: tags, glyphs, stdlib modules, host functions
  - textDocument/hover: tag descriptions, host function signatures, SET values
  - textDocument/definition: IMPORT targets, SET/FUNCTION declaration sites
  - textDocument/documentSymbol: outline of SET, FUNCTION, IMPORT nodes

Launch:
  python -m hlf.hlflsp              # stdio (default, for VS Code)
  python -m hlf.hlflsp --tcp 2087   # TCP mode (for debugging)

References:
  - LSP 3.17: https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/
  - pygls 2.x: https://github.com/openlawlibrary/pygls
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

from lsprotocol import types as lsp
from pygls.lsp.server import LanguageServer

# ─── Internal Imports ────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

logger = logging.getLogger("hlf.lsp")

# ─── Data Loaders ────────────────────────────────────────────────────────────


def _load_dictionary() -> dict[str, Any]:
    """Load HLF tag dictionary from governance/templates/dictionary.json."""
    path = _PROJECT_ROOT / "governance" / "templates" / "dictionary.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"tags": [], "glyphs": {}}


def _load_host_functions() -> list[dict[str, Any]]:
    """Load host function registry from governance/host_functions.json."""
    path = _PROJECT_ROOT / "governance" / "host_functions.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("functions", [])
    return []


def _load_stdlib_modules() -> list[str]:
    """Discover stdlib module names from hlf/stdlib/."""
    stdlib_dir = _PROJECT_ROOT / "hlf" / "stdlib"
    if stdlib_dir.is_dir():
        return sorted(p.stem for p in stdlib_dir.glob("*.hlf"))
    return []


# ─── Tag / Glyph Metadata ───────────────────────────────────────────────────

_TAG_DESCRIPTIONS: dict[str, str] = {
    "INTENT": "Declare a goal — the 'what' of an action. Args: action (string), target (path)",
    "THOUGHT": "Pure reasoning step — no side effects. Args: reasoning (string)",
    "OBSERVATION": "Record observed data — pure, no mutation. Args: data (any)",
    "PLAN": "Ordered step list. Args: steps (any, repeatable)",
    "CONSTRAINT": "Set a key-value boundary. Args: key (string), value (any)",
    "EXPECT": "Declare expected outcome. Args: outcome (string)",
    "ACTION": "Execute a verb. Args: verb (string), args (any, repeatable)",
    "SET": "Immutable variable binding — duplicate names raise error. Args: name (id), value (any)",
    "FUNCTION": "Define a pure function. Args: name (id), args (any, repeatable)",
    "DELEGATE": "Hand off to another agent role. Args: role (id), intent (string)",
    "VOTE": "Cast a governance vote. Args: decision (bool), rationale (string)",
    "ASSERT": "Gold Hat verification gate — halts on failure. Args: condition (bool), error (string)",
    "RESULT": "Terminator — final output with exit code. Args: code (int), message (string)",
    "MODULE": "Declare module name. Args: name (id)",
    "IMPORT": "Load another HLF module. Args: name (id)",
    "DATA": "Embed structured data. Args: id (string)",
    "MEMORY": "Store to Infinite RAG. Args: entity (string), content (any), confidence (any)",
    "RECALL": "Retrieve from Infinite RAG. Args: entity (string), top_k (int)",
    "DEFINE": "Create a reusable macro. Args: name (string), body (any)",
    "CALL": "Invoke a defined macro/function. Args: name (string), args (any, repeatable)",
    "WHILE": "Blue Hat process loop. Args: condition (string), body (any, repeatable)",
    "TRY": "Begin error-handling block. Args: body (any, repeatable)",
    "CATCH": "Error handler for TRY. Args: handler (any, repeatable)",
    "RETURN": "Return a value from a function. Args: value (any)",
    "TOOL": "Invoke a host function via tool bridge. Args: tool_name, args",
}


# ─── HLF Language Server ────────────────────────────────────────────────────


class HLFLanguageServer(LanguageServer):
    """Language Server for the Hieroglyphic Logic Framework (HLF)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.dictionary = _load_dictionary()
        self.host_functions = _load_host_functions()
        self.stdlib_modules = _load_stdlib_modules()
        self.tag_specs: dict[str, dict[str, Any]] = {}
        self.glyph_specs: dict[str, dict[str, Any]] = {}

        # Index tag specs by name
        for tag in self.dictionary.get("tags", []):
            self.tag_specs[tag["name"]] = tag

        # Index glyph specs by symbol
        for glyph, info in self.dictionary.get("glyphs", {}).items():
            self.glyph_specs[glyph] = info

    # ── Diagnostic Engine ────────────────────────────────────────────────

    def _build_diagnostics(self, source: str, uri: str) -> list[lsp.Diagnostic]:
        """Run hlflint + compiler to produce LSP diagnostics."""
        diagnostics: list[lsp.Diagnostic] = []

        # Phase 1: Linter diagnostics
        try:
            from hlf.hlflint import lint
            issues = lint(source)
            for issue in issues:
                severity = lsp.DiagnosticSeverity.Warning
                line = 0

                if issue.startswith("PARSE_ERROR"):
                    severity = lsp.DiagnosticSeverity.Error
                    # Try to extract line number from parse errors
                    line_match = re.search(r"line (\d+)", issue)
                    if line_match:
                        line = max(0, int(line_match.group(1)) - 1)
                elif issue.startswith("GAS_EXCEEDED"):
                    severity = lsp.DiagnosticSeverity.Warning
                elif issue.startswith("TOKEN_OVERFLOW"):
                    severity = lsp.DiagnosticSeverity.Information
                elif issue.startswith("UNUSED_VAR"):
                    severity = lsp.DiagnosticSeverity.Hint

                diagnostics.append(lsp.Diagnostic(
                    range=lsp.Range(
                        start=lsp.Position(line=line, character=0),
                        end=lsp.Position(line=line, character=1000),
                    ),
                    message=issue,
                    severity=severity,
                    source="hlflint",
                ))
        except Exception as exc:
            logger.debug("Linter failed: %s", exc)

        # Phase 2: Compiler syntax validation (only if linter didn't report PARSE_ERROR)
        has_parse_error = any("PARSE_ERROR" in d.message for d in diagnostics)
        if not has_parse_error:
            try:
                from hlf.hlfc import compile as hlfc_compile
                hlfc_compile(source)
            except Exception as exc:
                err_msg = str(exc)
                line = 0
                line_match = re.search(r"line (\d+)", err_msg)
                if line_match:
                    line = max(0, int(line_match.group(1)) - 1)

                diagnostics.append(lsp.Diagnostic(
                    range=lsp.Range(
                        start=lsp.Position(line=line, character=0),
                        end=lsp.Position(line=line, character=1000),
                    ),
                    message=f"Syntax error: {err_msg}",
                    severity=lsp.DiagnosticSeverity.Error,
                    source="hlfc",
                ))

        return diagnostics

    # ── Completion Provider ──────────────────────────────────────────────

    def get_completions(self, source: str, position: lsp.Position) -> list[lsp.CompletionItem]:
        """Generate completion items based on cursor context."""
        items: list[lsp.CompletionItem] = []
        lines = source.splitlines()
        if position.line >= len(lines):
            return items

        line_text = lines[position.line]
        prefix = line_text[:position.character].strip()

        # If typing after '[', complete tag names
        if prefix.endswith("[") or re.match(r"^\[?[A-Z]*$", prefix):
            for tag_name, description in _TAG_DESCRIPTIONS.items():
                items.append(lsp.CompletionItem(
                    label=f"[{tag_name}]",
                    kind=lsp.CompletionItemKind.Keyword,
                    detail=description.split(".")[0],
                    documentation=lsp.MarkupContent(
                        kind=lsp.MarkupKind.Markdown,
                        value=f"**{tag_name}**\n\n{description}",
                    ),
                    insert_text=f"[{tag_name}] ",
                    sort_text=f"0_{tag_name}",
                ))

        # Glyph completions (always available)
        for glyph, info in self.glyph_specs.items():
            items.append(lsp.CompletionItem(
                label=glyph,
                kind=lsp.CompletionItemKind.Operator,
                detail=info.get("name", ""),
                documentation=lsp.MarkupContent(
                    kind=lsp.MarkupKind.Markdown,
                    value=f"**{info.get('name', glyph)}**\n\n{info.get('enforces', '')}",
                ),
                sort_text=f"2_{glyph}",
            ))

        # If after [IMPORT], complete stdlib module names
        if "[IMPORT]" in prefix or prefix.startswith("IMPORT"):
            for mod_name in self.stdlib_modules:
                items.append(lsp.CompletionItem(
                    label=mod_name,
                    kind=lsp.CompletionItemKind.Module,
                    detail=f"stdlib module: {mod_name}",
                    insert_text=mod_name,
                    sort_text=f"1_{mod_name}",
                ))

        # If after [TOOL] or ↦, complete host function names
        if "[TOOL]" in prefix or "↦" in prefix or "τ" in prefix:
            for hf in self.host_functions:
                args_str = ", ".join(
                    f"{a.get('name', '?')}: {a.get('type', 'any')}"
                    for a in hf.get("args", [])
                )
                items.append(lsp.CompletionItem(
                    label=hf["name"],
                    kind=lsp.CompletionItemKind.Function,
                    detail=f"({args_str}) → {hf.get('returns', 'any')} [gas: {hf.get('gas', 1)}]",
                    documentation=lsp.MarkupContent(
                        kind=lsp.MarkupKind.Markdown,
                        value=(
                            f"**{hf['name']}**\n\n"
                            f"- Backend: `{hf.get('backend', 'builtin')}`\n"
                            f"- Tiers: {', '.join(hf.get('tier', []))}\n"
                            f"- Gas cost: {hf.get('gas', 1)}\n"
                            f"- Sensitive: {'⚠️ Yes' if hf.get('sensitive') else 'No'}"
                        ),
                    ),
                    insert_text=hf["name"],
                    sort_text=f"1_{hf['name']}",
                ))

        # Variable reference completions (${...})
        if "${" in prefix:
            set_vars = _extract_set_bindings(source)
            for var_name, value in set_vars.items():
                items.append(lsp.CompletionItem(
                    label=f"${{{var_name}}}",
                    kind=lsp.CompletionItemKind.Variable,
                    detail=f"= {value}",
                    insert_text=f"{var_name}}}",
                    sort_text=f"0_{var_name}",
                ))

        return items

    # ── Hover Provider ───────────────────────────────────────────────────

    def get_hover(self, source: str, position: lsp.Position) -> lsp.Hover | None:
        """Provide hover information for tags, glyphs, SET bindings."""
        lines = source.splitlines()
        if position.line >= len(lines):
            return None

        line_text = lines[position.line]

        # Check if hovering over a [TAG]
        for match in re.finditer(r"\[([A-Z_]+)\]", line_text):
            start, end = match.start(), match.end()
            if start <= position.character <= end:
                tag_name = match.group(1)
                if tag_name in _TAG_DESCRIPTIONS:
                    spec = self.tag_specs.get(tag_name, {})
                    args_doc = ""
                    if spec.get("args"):
                        args_doc = "\n\n**Arguments:**\n" + "\n".join(
                            f"- `{a['name']}`: {a.get('type', 'any')}"
                            + (" *(repeatable)*" if a.get("repeat") else "")
                            for a in spec["args"]
                        )
                    flags = []
                    if spec.get("pure"):
                        flags.append("🟢 Pure (no side effects)")
                    if spec.get("immutable"):
                        flags.append("🔒 Immutable binding")
                    if spec.get("terminator"):
                        flags.append("⏹️ Terminator (halts execution)")
                    if spec.get("macro"):
                        flags.append("🔄 Macro definition")
                    flags_doc = "\n".join(flags)
                    if flags_doc:
                        flags_doc = "\n\n" + flags_doc

                    return lsp.Hover(
                        contents=lsp.MarkupContent(
                            kind=lsp.MarkupKind.Markdown,
                            value=f"### [{tag_name}]\n\n{_TAG_DESCRIPTIONS[tag_name]}{args_doc}{flags_doc}",
                        ),
                        range=lsp.Range(
                            start=lsp.Position(line=position.line, character=start),
                            end=lsp.Position(line=position.line, character=end),
                        ),
                    )

        # Check if hovering over a glyph
        for glyph, info in self.glyph_specs.items():
            idx = line_text.find(glyph)
            if idx >= 0 and idx <= position.character <= idx + len(glyph):
                return lsp.Hover(
                    contents=lsp.MarkupContent(
                        kind=lsp.MarkupKind.Markdown,
                        value=f"### {glyph} — {info.get('name', 'Glyph')}\n\n{info.get('enforces', '')}",
                    ),
                    range=lsp.Range(
                        start=lsp.Position(line=position.line, character=idx),
                        end=lsp.Position(line=position.line, character=idx + len(glyph)),
                    ),
                )

        # Check if hovering over a ${VAR} reference
        for match in re.finditer(r"\$\{(\w+)\}", line_text):
            start, end = match.start(), match.end()
            if start <= position.character <= end:
                var_name = match.group(1)
                set_vars = _extract_set_bindings(source)
                if var_name in set_vars:
                    return lsp.Hover(
                        contents=lsp.MarkupContent(
                            kind=lsp.MarkupKind.Markdown,
                            value=f"### ${{{var_name}}}\n\n**Value:** `{set_vars[var_name]}`\n\n*Bound via `[SET]` — immutable*",
                        ),
                        range=lsp.Range(
                            start=lsp.Position(line=position.line, character=start),
                            end=lsp.Position(line=position.line, character=end),
                        ),
                    )

        return None

    # ── Definition Provider ──────────────────────────────────────────────

    def get_definition(
        self, source: str, uri: str, position: lsp.Position
    ) -> lsp.Location | None:
        """Go-to-definition for IMPORT targets, SET variables, FUNCTION names."""
        lines = source.splitlines()
        if position.line >= len(lines):
            return None
        line_text = lines[position.line]

        # If on a ${VAR}, jump to its [SET] declaration
        for match in re.finditer(r"\$\{(\w+)\}", line_text):
            start, end = match.start(), match.end()
            if start <= position.character <= end:
                var_name = match.group(1)
                for i, src_line in enumerate(lines):
                    set_match = re.search(rf"\[SET\]\s+{re.escape(var_name)}\b", src_line)
                    if set_match:
                        return lsp.Location(
                            uri=uri,
                            range=lsp.Range(
                                start=lsp.Position(line=i, character=set_match.start()),
                                end=lsp.Position(line=i, character=set_match.end()),
                            ),
                        )

        # If on an [IMPORT] line, resolve the module path
        import_match = re.match(r"\[IMPORT\]\s+(\w+)", line_text)
        if import_match:
            mod_name = import_match.group(1)
            try:
                from hlf.runtime import ModuleLoader
                loader = ModuleLoader()
                mod_path = loader.resolve_path(mod_name)
                if mod_path and mod_path.exists():
                    return lsp.Location(
                        uri=mod_path.as_uri(),
                        range=lsp.Range(
                            start=lsp.Position(line=0, character=0),
                            end=lsp.Position(line=0, character=0),
                        ),
                    )
            except Exception:
                pass

        # If on a [CALL] target, jump to [FUNCTION] or [DEFINE]
        call_match = re.match(r"\[CALL\]\s+(\w+)", line_text)
        if call_match:
            func_name = call_match.group(1)
            for i, src_line in enumerate(lines):
                func_match = re.search(
                    rf"\[(FUNCTION|DEFINE)\]\s+{re.escape(func_name)}\b", src_line
                )
                if func_match:
                    return lsp.Location(
                        uri=uri,
                        range=lsp.Range(
                            start=lsp.Position(line=i, character=func_match.start()),
                            end=lsp.Position(line=i, character=func_match.end()),
                        ),
                    )

        return None

    # ── Document Symbol Provider ─────────────────────────────────────────

    def get_symbols(self, source: str) -> list[lsp.DocumentSymbol]:
        """Extract document symbols for the outline view."""
        symbols: list[lsp.DocumentSymbol] = []
        lines = source.splitlines()

        for i, line in enumerate(lines):
            # [SET] variable
            set_match = re.match(r"\[SET\]\s+(\w+)\s*=?\s*(.*)", line)
            if set_match:
                name = set_match.group(1)
                value = set_match.group(2).strip() or "?"
                symbols.append(lsp.DocumentSymbol(
                    name=name,
                    kind=lsp.SymbolKind.Variable,
                    detail=f"= {value}",
                    range=lsp.Range(
                        start=lsp.Position(line=i, character=0),
                        end=lsp.Position(line=i, character=len(line)),
                    ),
                    selection_range=lsp.Range(
                        start=lsp.Position(line=i, character=set_match.start(1)),
                        end=lsp.Position(line=i, character=set_match.end(1)),
                    ),
                ))

            # [FUNCTION] definition
            func_match = re.match(r"\[FUNCTION\]\s+(\w+)", line)
            if func_match:
                symbols.append(lsp.DocumentSymbol(
                    name=func_match.group(1),
                    kind=lsp.SymbolKind.Function,
                    range=lsp.Range(
                        start=lsp.Position(line=i, character=0),
                        end=lsp.Position(line=i, character=len(line)),
                    ),
                    selection_range=lsp.Range(
                        start=lsp.Position(line=i, character=func_match.start(1)),
                        end=lsp.Position(line=i, character=func_match.end(1)),
                    ),
                ))

            # [IMPORT] module
            import_match = re.match(r"\[IMPORT\]\s+(\w+)", line)
            if import_match:
                symbols.append(lsp.DocumentSymbol(
                    name=import_match.group(1),
                    kind=lsp.SymbolKind.Package,
                    detail="module import",
                    range=lsp.Range(
                        start=lsp.Position(line=i, character=0),
                        end=lsp.Position(line=i, character=len(line)),
                    ),
                    selection_range=lsp.Range(
                        start=lsp.Position(line=i, character=import_match.start(1)),
                        end=lsp.Position(line=i, character=import_match.end(1)),
                    ),
                ))

            # [MODULE] declaration
            mod_match = re.match(r"\[MODULE\]\s+(\w+)", line)
            if mod_match:
                symbols.append(lsp.DocumentSymbol(
                    name=mod_match.group(1),
                    kind=lsp.SymbolKind.Module,
                    range=lsp.Range(
                        start=lsp.Position(line=i, character=0),
                        end=lsp.Position(line=i, character=len(line)),
                    ),
                    selection_range=lsp.Range(
                        start=lsp.Position(line=i, character=mod_match.start(1)),
                        end=lsp.Position(line=i, character=mod_match.end(1)),
                    ),
                ))

        return symbols


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _extract_set_bindings(source: str) -> dict[str, str]:
    """Extract all [SET] bindings from raw source text."""
    bindings: dict[str, str] = {}
    for match in re.finditer(r"\[SET\]\s+(\w+)\s*=?\s*(.*)", source):
        bindings[match.group(1)] = match.group(2).strip() or "<unset>"
    return bindings


# ─── Server Wiring ──────────────────────────────────────────────────────────

_server = HLFLanguageServer("hlf-lsp", "v0.1.0")


@_server.feature(lsp.TEXT_DOCUMENT_DID_OPEN)
def did_open(params: lsp.DidOpenTextDocumentParams) -> None:
    """Publish diagnostics when a document is opened."""
    doc = params.text_document
    diagnostics = _server._build_diagnostics(doc.text, doc.uri)
    _server.publish_diagnostics(doc.uri, diagnostics)


@_server.feature(lsp.TEXT_DOCUMENT_DID_CHANGE)
def did_change(params: lsp.DidChangeTextDocumentParams) -> None:
    """Re-publish diagnostics when a document changes."""
    doc = _server.workspace.get_text_document(params.text_document.uri)
    diagnostics = _server._build_diagnostics(doc.source, doc.uri)
    _server.publish_diagnostics(doc.uri, diagnostics)


@_server.feature(lsp.TEXT_DOCUMENT_DID_SAVE)
def did_save(params: lsp.DidSaveTextDocumentParams) -> None:
    """Re-publish diagnostics on save."""
    doc = _server.workspace.get_text_document(params.text_document.uri)
    diagnostics = _server._build_diagnostics(doc.source, doc.uri)
    _server.publish_diagnostics(doc.uri, diagnostics)


@_server.feature(lsp.TEXT_DOCUMENT_COMPLETION)
def completions(params: lsp.CompletionParams) -> lsp.CompletionList:
    """Provide completion items."""
    doc = _server.workspace.get_text_document(params.text_document.uri)
    items = _server.get_completions(doc.source, params.position)
    return lsp.CompletionList(is_incomplete=False, items=items)


@_server.feature(lsp.TEXT_DOCUMENT_HOVER)
def hover(params: lsp.HoverParams) -> lsp.Hover | None:
    """Provide hover information."""
    doc = _server.workspace.get_text_document(params.text_document.uri)
    return _server.get_hover(doc.source, params.position)


@_server.feature(lsp.TEXT_DOCUMENT_DEFINITION)
def definition(params: lsp.DefinitionParams) -> lsp.Location | None:
    """Provide go-to-definition."""
    doc = _server.workspace.get_text_document(params.text_document.uri)
    return _server.get_definition(doc.source, doc.uri, params.position)


@_server.feature(lsp.TEXT_DOCUMENT_DOCUMENT_SYMBOL)
def document_symbol(params: lsp.DocumentSymbolParams) -> list[lsp.DocumentSymbol]:
    """Provide document symbols for outline."""
    doc = _server.workspace.get_text_document(params.text_document.uri)
    return _server.get_symbols(doc.source)


# ─── Entry Point ─────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="HLF Language Server")
    parser.add_argument("--tcp", type=int, help="Run in TCP mode on given port")
    parser.add_argument("--host", default="127.0.0.1", help="TCP host (default: 127.0.0.1)")
    args = parser.parse_args()

    if args.tcp:
        _server.start_tcp(args.host, args.tcp)
    else:
        _server.start_io()


if __name__ == "__main__":
    main()
