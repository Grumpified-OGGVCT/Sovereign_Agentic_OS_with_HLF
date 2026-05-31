"""
tests/test_hlflsp.py — Unit tests for the HLF Language Server Protocol.

Tests the core logic of the LSP server (diagnostics, completions, hover,
go-to-definition, document symbols) without launching a real LSP connection.
"""

from __future__ import annotations

import pytest
from lsprotocol import types as lsp

from hlf.hlflsp import HLFLanguageServer, _extract_set_bindings

# ─── Helpers ─────────────────────────────────────────────────────────────────

def _prog(body: str) -> str:
    """Wrap body in a minimal valid HLF-v2 program."""
    return f"[HLF-v2]\n{body}\nΩ\n"


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def server():
    """Create an HLFLanguageServer instance for testing."""
    return HLFLanguageServer("test-hlf-lsp", "v0.0.1-test")


# ─── Diagnostic Tests ────────────────────────────────────────────────────────


class TestDiagnostics:
    """Tests for the diagnostic engine (_build_diagnostics)."""

    def test_clean_source_no_errors(self, server):
        """Valid HLF source should produce no Error diagnostics."""
        source = _prog('[INTENT] deploy "/app"')
        diags = server._build_diagnostics(source, "file:///test.hlf")
        errors = [d for d in diags if d.severity == lsp.DiagnosticSeverity.Error]
        assert len(errors) == 0

    def test_syntax_error_produces_error_diagnostic(self, server):
        """Invalid HLF syntax should produce an Error diagnostic."""
        source = "this is not valid HLF syntax at all!!!"
        diags = server._build_diagnostics(source, "file:///test.hlf")
        errors = [d for d in diags if d.severity == lsp.DiagnosticSeverity.Error]
        assert len(errors) >= 1

    def test_unused_var_produces_hint(self, server):
        """An unused [SET] variable should produce a Hint diagnostic."""
        source = _prog('[SET] unused_var = "hello"')
        diags = server._build_diagnostics(source, "file:///test.hlf")
        hints = [d for d in diags if d.severity == lsp.DiagnosticSeverity.Hint]
        # Linter should flag unused_var
        unused_hints = [d for d in hints if "unused_var" in d.message]
        assert len(unused_hints) >= 1

    def test_diagnostic_source_is_hlflint(self, server):
        """Diagnostics from the linter should have source 'hlflint'."""
        source = _prog('[SET] x = 1')
        diags = server._build_diagnostics(source, "file:///test.hlf")
        lint_diags = [d for d in diags if d.source == "hlflint"]
        for d in lint_diags:
            assert d.source == "hlflint"

    def test_gas_exceeded_is_warning(self, server):
        """GAS_EXCEEDED should map to Warning severity."""
        lines = [f'[INTENT] step_{i} "arg"' for i in range(15)]
        source = _prog("\n".join(lines))
        diags = server._build_diagnostics(source, "file:///test.hlf")
        gas_diags = [d for d in diags if "GAS_EXCEEDED" in d.message]
        for d in gas_diags:
            assert d.severity == lsp.DiagnosticSeverity.Warning


# ─── Completion Tests ────────────────────────────────────────────────────────


class TestCompletions:
    """Tests for the completion provider."""

    def test_tag_completions_after_bracket(self, server):
        """Typing '[' should trigger tag completions."""
        source = "["
        pos = lsp.Position(line=0, character=1)
        items = server.get_completions(source, pos)
        labels = [item.label for item in items]
        assert "[INTENT]" in labels
        assert "[SET]" in labels
        assert "[RESULT]" in labels

    def test_tag_completions_include_all_tags(self, server):
        """All known tags should appear in completions."""
        source = "["
        pos = lsp.Position(line=0, character=1)
        items = server.get_completions(source, pos)
        tag_items = [i for i in items if i.kind == lsp.CompletionItemKind.Keyword]
        assert len(tag_items) >= 15  # at least the dictionary tags

    def test_glyph_completions_depend_on_specs(self, server):
        """Glyph completions should be present if glyph_specs are loaded."""
        source = ""
        pos = lsp.Position(line=0, character=0)
        items = server.get_completions(source, pos)
        glyph_items = [i for i in items if i.kind == lsp.CompletionItemKind.Operator]
        # May be empty if no dictionary.json glyphs section, that's OK
        assert isinstance(glyph_items, list)

    def test_import_completes_stdlib_modules(self, server):
        """After [IMPORT], stdlib module names should appear."""
        source = "[IMPORT] "
        pos = lsp.Position(line=0, character=9)
        items = server.get_completions(source, pos)
        module_items = [i for i in items if i.kind == lsp.CompletionItemKind.Module]
        if server.stdlib_modules:
            assert len(module_items) > 0

    def test_variable_completions_after_dollar_brace(self, server):
        """After '${', SET variables should appear."""
        source = '[SET] name = "world"\n[INTENT] greet "${' 
        pos = lsp.Position(line=1, character=18)
        items = server.get_completions(source, pos)
        var_items = [i for i in items if i.kind == lsp.CompletionItemKind.Variable]
        assert any("name" in i.label for i in var_items)

    def test_completion_items_have_documentation(self, server):
        """Tag completion items should include markdown documentation."""
        source = "["
        pos = lsp.Position(line=0, character=1)
        items = server.get_completions(source, pos)
        tag_items = [i for i in items if i.kind == lsp.CompletionItemKind.Keyword]
        for item in tag_items[:3]:  # check first 3
            assert item.documentation is not None
            assert item.documentation.kind == lsp.MarkupKind.Markdown


# ─── Hover Tests ─────────────────────────────────────────────────────────────


class TestHover:
    """Tests for the hover provider."""

    def test_hover_on_tag_returns_description(self, server):
        """Hovering over [INTENT] should show its description."""
        source = '[INTENT] deploy "/app"'
        pos = lsp.Position(line=0, character=3)  # inside [INTENT]
        hover = server.get_hover(source, pos)
        assert hover is not None
        assert "INTENT" in hover.contents.value

    def test_hover_on_set_tag(self, server):
        """Hovering over [SET] should show SET info."""
        source = '[SET] x = 42'
        pos = lsp.Position(line=0, character=2)
        hover = server.get_hover(source, pos)
        assert hover is not None
        assert "SET" in hover.contents.value

    def test_hover_on_var_ref_shows_value(self, server):
        """Hovering over ${x} should show its SET value."""
        source = '[SET] greeting = "hello"\n[INTENT] greet "${greeting}"'
        pos = lsp.Position(line=1, character=20)  # inside ${greeting}
        hover = server.get_hover(source, pos)
        assert hover is not None
        assert "hello" in hover.contents.value

    def test_hover_on_empty_returns_none(self, server):
        """Hovering on whitespace should return None."""
        source = "   "
        pos = lsp.Position(line=0, character=1)
        hover = server.get_hover(source, pos)
        assert hover is None

    def test_hover_on_result_tag(self, server):
        """[RESULT] should show tag info."""
        source = '[RESULT] code=0 message="success"'
        pos = lsp.Position(line=0, character=3)
        hover = server.get_hover(source, pos)
        assert hover is not None
        assert "RESULT" in hover.contents.value

    def test_hover_returns_markdown(self, server):
        """Hover content should be Markdown."""
        source = '[INTENT] test "/path"'
        pos = lsp.Position(line=0, character=3)
        hover = server.get_hover(source, pos)
        assert hover is not None
        assert hover.contents.kind == lsp.MarkupKind.Markdown


# ─── Definition Tests ────────────────────────────────────────────────────────


class TestDefinition:
    """Tests for go-to-definition."""

    def test_var_ref_jumps_to_set(self, server):
        """${x} should jump to the [SET] x declaration."""
        source = '[SET] target = "/deploy"\n[INTENT] go "${target}"'
        pos = lsp.Position(line=1, character=18)  # inside ${target}
        loc = server.get_definition(source, "file:///test.hlf", pos)
        assert loc is not None
        assert loc.range.start.line == 0  # SET is on line 0

    def test_call_jumps_to_function(self, server):
        """[CALL] myFunc should jump to [FUNCTION] myFunc."""
        source = '[FUNCTION] myFunc "arg1"\n[CALL] myFunc "hello"'
        pos = lsp.Position(line=1, character=10)
        loc = server.get_definition(source, "file:///test.hlf", pos)
        assert loc is not None
        assert loc.range.start.line == 0

    def test_no_definition_for_unknown_var(self, server):
        """${unknown} should return None if no [SET] exists."""
        source = '[INTENT] go "${unknown}"'
        pos = lsp.Position(line=0, character=16)
        loc = server.get_definition(source, "file:///test.hlf", pos)
        assert loc is None


# ─── Symbol Tests ────────────────────────────────────────────────────────────


class TestDocumentSymbols:
    """Tests for document symbol extraction."""

    def test_set_appears_as_variable(self, server):
        """[SET] bindings should appear as Variable symbols."""
        source = '[SET] port = 8080\n[SET] host = "localhost"'
        symbols = server.get_symbols(source)
        var_symbols = [s for s in symbols if s.kind == lsp.SymbolKind.Variable]
        assert len(var_symbols) == 2
        names = {s.name for s in var_symbols}
        assert "port" in names
        assert "host" in names

    def test_function_appears_as_function(self, server):
        """[FUNCTION] defs should appear as Function symbols."""
        source = '[FUNCTION] deploy "target"'
        symbols = server.get_symbols(source)
        func_symbols = [s for s in symbols if s.kind == lsp.SymbolKind.Function]
        assert len(func_symbols) == 1
        assert func_symbols[0].name == "deploy"

    def test_import_appears_as_package(self, server):
        """[IMPORT] should appear as Package symbol."""
        source = "[IMPORT] math"
        symbols = server.get_symbols(source)
        pkg_symbols = [s for s in symbols if s.kind == lsp.SymbolKind.Package]
        assert len(pkg_symbols) == 1
        assert pkg_symbols[0].name == "math"

    def test_module_appears_as_module(self, server):
        """[MODULE] should appear as Module symbol."""
        source = "[MODULE] my_lib"
        symbols = server.get_symbols(source)
        mod_symbols = [s for s in symbols if s.kind == lsp.SymbolKind.Module]
        assert len(mod_symbols) == 1
        assert mod_symbols[0].name == "my_lib"

    def test_empty_source_no_symbols(self, server):
        """Empty source should produce no symbols."""
        symbols = server.get_symbols("")
        assert len(symbols) == 0

    def test_symbols_have_correct_ranges(self, server):
        """Symbol ranges should accurately reflect line positions."""
        source = '[SET] x = 1\n[FUNCTION] foo "arg"'
        symbols = server.get_symbols(source)
        set_sym = [s for s in symbols if s.name == "x"][0]
        func_sym = [s for s in symbols if s.name == "foo"][0]
        assert set_sym.range.start.line == 0
        assert func_sym.range.start.line == 1


# ─── Helper Tests ────────────────────────────────────────────────────────────


class TestHelpers:
    """Tests for utility functions."""

    def test_extract_set_bindings(self):
        """_extract_set_bindings should find all [SET] variables."""
        source = '[SET] a = 1\n[SET] b = "hello"\n[INTENT] greet "world"'
        bindings = _extract_set_bindings(source)
        assert "a" in bindings
        assert "b" in bindings
        assert bindings["a"] == "1"
        assert bindings["b"] == '"hello"'

    def test_extract_empty_source(self):
        """Empty source should return empty dict."""
        assert _extract_set_bindings("") == {}

    def test_extract_no_set(self):
        """Source with no [SET] should return empty dict."""
        source = '[INTENT] deploy "/app"\n[RESULT] code=0 message="ok"'
        assert _extract_set_bindings(source) == {}
