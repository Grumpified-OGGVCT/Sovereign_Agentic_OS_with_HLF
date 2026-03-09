"""
Tests for HLF Nuanced Translator — bidirectional English ↔ HLF.

Tests cover:
  - Nuance glyph creation and parsing
  - Intent detection from chaos English
  - English → HLF translation
  - HLF → English decompilation
  - Roundtrip fidelity
  - Tone detection accuracy
  - Edge cases (empty, very long, special chars)
"""

from __future__ import annotations

import pytest

from hlf.translator import (
    Tone,
    NuanceGlyph,
    DetectedIntent,
    detect_nuance,
    HLFTranslator,
    _safe_id,
    _humanize_verb,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def translator() -> HLFTranslator:
    return HLFTranslator()


# ─── Nuance Glyphs ──────────────────────────────────────────────────────────

class TestNuanceGlyph:
    def test_to_hlf(self) -> None:
        g = NuanceGlyph(tone=Tone.FRUSTRATED, context="stuck on deploy")
        assert g.to_hlf() == "~frust{stuck on deploy}"

    def test_to_hlf_no_context(self) -> None:
        g = NuanceGlyph(tone=Tone.NEUTRAL)
        assert g.to_hlf() == "~neut{}"

    def test_from_hlf(self) -> None:
        g = NuanceGlyph.from_hlf("~urgnt{fix now}")
        assert g is not None
        assert g.tone == Tone.URGENT
        assert g.context == "fix now"

    def test_from_hlf_invalid(self) -> None:
        assert NuanceGlyph.from_hlf("not a glyph") is None

    def test_roundtrip_glyph(self) -> None:
        original = NuanceGlyph(tone=Tone.ENTHUSIASTIC, context="love it")
        parsed = NuanceGlyph.from_hlf(original.to_hlf())
        assert parsed is not None
        assert parsed.tone == original.tone
        assert parsed.context == original.context


# ─── Intent Detection ───────────────────────────────────────────────────────

class TestDetectNuance:
    def test_frustrated(self) -> None:
        intent = detect_nuance("ugh this deploy is broken and I can't fix it")
        assert intent.tone == Tone.FRUSTRATED
        assert intent.action == "fix"

    def test_urgent(self) -> None:
        intent = detect_nuance("deploy this immediately, it's urgent!")
        assert intent.tone == Tone.URGENT
        assert intent.action == "deploy"

    def test_enthusiastic(self) -> None:
        intent = detect_nuance("awesome! build the new amazing feature!!")
        assert intent.tone == Tone.ENTHUSIASTIC

    def test_questioning(self) -> None:
        intent = detect_nuance("how do I configure the gateway?")
        assert intent.tone == Tone.QUESTIONING
        assert intent.action == "configure"

    def test_neutral(self) -> None:
        intent = detect_nuance("install the package")
        assert intent.tone == Tone.NEUTRAL
        assert intent.action == "install"

    def test_trailing_thought(self) -> None:
        intent = detect_nuance("maybe we should fix the tests...")
        assert "trailing_thought" in intent.modifiers

    def test_emphasis(self) -> None:
        intent = detect_nuance("fix this now!!! it's broken!!")
        assert "emphasis" in intent.modifiers

    def test_empty(self) -> None:
        intent = detect_nuance("")
        assert intent.tone == Tone.NEUTRAL
        assert intent.action == "process"


# ─── English → HLF ──────────────────────────────────────────────────────────

class TestEnglishToHLF:
    def test_basic(self, translator: HLFTranslator) -> None:
        result = translator.english_to_hlf("fix the broken gateway")
        assert "hlf" in result
        assert "ACTION: FIX" in result["hlf"]
        assert "TARGET:" in result["hlf"]
        assert result["intent"]["action"] == "fix"

    def test_preserves_tone(self, translator: HLFTranslator) -> None:
        result = translator.english_to_hlf("ugh this is broken and I can't fix it!")
        assert result["intent"]["tone"] == "frustrated"
        assert "~frust{" in result["glyph"]

    def test_generates_exec(self, translator: HLFTranslator) -> None:
        result = translator.english_to_hlf("install the new package")
        assert "EXEC" in result["hlf"]


# ─── HLF → English ──────────────────────────────────────────────────────────

class TestHLFToEnglish:
    def test_basic(self, translator: HLFTranslator) -> None:
        hlf = '''DEFINE TASK fix_gateway {
    ACTION: FIX;
    TARGET: "the gateway";
    TONE: frustrated;
}'''
        result = translator.hlf_to_english(hlf)
        assert "Fix" in result["english"]
        assert "gateway" in result["english"]

    def test_with_glyph(self, translator: HLFTranslator) -> None:
        hlf = '; ~urgnt{deploy now}\nACTION: DEPLOY;\nTARGET: "the server";'
        result = translator.hlf_to_english(hlf)
        assert result["tone"] == "urgent"
        assert "Deploy" in result["english"]

    def test_neutral(self, translator: HLFTranslator) -> None:
        hlf = 'ACTION: BUILD;\nTARGET: "the feature";'
        result = translator.hlf_to_english(hlf)
        assert result["tone"] == "neutral"


# ─── Roundtrip ───────────────────────────────────────────────────────────────

class TestRoundtrip:
    def test_roundtrip(self, translator: HLFTranslator) -> None:
        result = translator.roundtrip("install the discord bot")
        assert result["original"] == "install the discord bot"
        assert "INSTALL" in result["hlf"]
        assert result["tone_preserved"] is True

    def test_roundtrip_frustrated(self, translator: HLFTranslator) -> None:
        result = translator.roundtrip("ugh fix this broken test it won't pass")
        assert result["tone_preserved"] is True


# ─── Helpers ─────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_safe_id(self) -> None:
        assert _safe_id("hello world!") == "hello_world"

    def test_safe_id_empty(self) -> None:
        assert _safe_id("") == "task"

    def test_humanize_verb(self) -> None:
        assert _humanize_verb("fix") == "Fix"
        assert _humanize_verb("setup") == "Set up"
        assert _humanize_verb("search") == "Search for"
        assert _humanize_verb("unknown") == "Unknown"
