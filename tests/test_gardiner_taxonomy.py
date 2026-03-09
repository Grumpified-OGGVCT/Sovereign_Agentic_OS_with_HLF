"""Tests for Gardiner Sign-List Taxonomy."""

from __future__ import annotations

import pytest

from hlf.gardiner_taxonomy import (
    GARDINER_CATEGORIES,
    GardinerRegistry,
    GardinerSign,
)


class TestGardinerSign:
    def test_to_dict(self):
        sign = GardinerSign(
            unicode_char="Ω",
            hlf_name="Terminal Conclusion",
            gardiner_code="Z6",
            gardiner_category="Z",
            gardiner_description="Terminal stroke",
            hlf_semantics="Final state",
        )
        d = sign.to_dict()
        assert d["unicode"] == "Ω"
        assert d["gardiner_code"] == "Z6"


class TestGardinerRegistry:
    def setup_method(self):
        self.reg = GardinerRegistry()

    def test_core_mappings_loaded(self):
        assert self.reg.sign_count >= 10

    def test_lookup_omega(self):
        sign = self.reg.lookup("Ω")
        assert sign is not None
        assert sign.hlf_name == "Terminal Conclusion"
        assert sign.gardiner_code == "Z6"

    def test_lookup_delta(self):
        sign = self.reg.lookup("Δ")
        assert sign is not None
        assert sign.gardiner_category == "N"

    def test_lookup_unknown(self):
        assert self.reg.lookup("🍕") is None

    def test_lookup_by_code(self):
        sign = self.reg.lookup_by_code("Z6")
        assert sign is not None
        assert sign.unicode_char == "Ω"

    def test_get_category(self):
        desc = self.reg.get_category("Z")
        assert desc == "Strokes and geometric figures"

    def test_get_category_unknown(self):
        assert self.reg.get_category("XX") is None

    def test_list_categories(self):
        cats = self.reg.list_categories()
        assert len(cats) >= 20
        assert "A" in cats
        assert "Z" in cats

    def test_list_by_category(self):
        signs = self.reg.list_by_category("Z")
        assert len(signs) >= 1  # At least Ω and ≡

    def test_list_all(self):
        all_signs = self.reg.list_all()
        assert len(all_signs) >= 10

    def test_register_new(self):
        sign = self.reg.register(
            unicode_char="★",
            hlf_name="Star Marker",
            gardiner_code="N14",
            gardiner_category="N",
            gardiner_description="Star",
            hlf_semantics="Priority marker",
        )
        assert self.reg.lookup("★") is sign
        assert self.reg.sign_count >= 11

    def test_cross_reference(self):
        xref = self.reg.cross_reference("Ω")
        assert xref is not None
        assert xref["hlf"]["name"] == "Terminal Conclusion"
        assert xref["gardiner"]["category_name"] == "Strokes and geometric figures"

    def test_cross_reference_unknown(self):
        assert self.reg.cross_reference("🍕") is None

    def test_taxonomy_report(self):
        report = self.reg.get_taxonomy_report()
        assert report["total_mappings"] >= 10
        assert report["coverage_pct"] > 0
        assert "by_category" in report

    def test_all_core_glyphs_mapped(self):
        """Ensure every specified HLF glyph has a Gardiner mapping."""
        for glyph in ["Ω", "Δ", "Ж", "⩕", "⌘", "∇", "⨝", "§", "≡", "↦"]:
            sign = self.reg.lookup(glyph)
            assert sign is not None, f"Missing Gardiner mapping for {glyph}"
