"""
Tests — Shared Mandates Compliance Audit.

Validates that:
  1. The _shared_mandates.md file exists and contains all nine mandates.
  2. Every persona file in config/personas/ (except _shared_mandates.md itself)
     has a "Collaboration Protocol" section.
  3. No persona file contains banned model names (MODEL-AGNOSTIC POLICY).
  4. The crew_orchestrator correctly loads and injects the shared mandates.
  5. The ALIGN_LEDGER.yaml is well-formed and contains the minimum required rules.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PERSONAS_DIR = _REPO_ROOT / "config" / "personas"
_SHARED_MANDATES_FILE = _PERSONAS_DIR / "_shared_mandates.md"
_ALIGN_LEDGER = _REPO_ROOT / "governance" / "ALIGN_LEDGER.yaml"

# Persona files that are not personas themselves
_NON_PERSONA_FILES = {"_shared_mandates.md"}

# Nine mandate headings that must appear in _shared_mandates.md
_REQUIRED_MANDATE_HEADINGS = [
    "Mandate 1",
    "Mandate 2",
    "Mandate 3",
    "Mandate 4",
    "Mandate 5",
    "Mandate 6",
    "Mandate 7",
    "Mandate 8",
    "Mandate 9",
]

# Pattern used in the anti-reductionism mandate
_KEY_MANDATE_PHRASES = [
    "Anti-Reductionism",
    "Backup Before Modify",
    "Persona and Prompt Fidelity",
    "Evidence-Based Reasoning",
    "HLF Recursive Self-Improvement",
    "Transparency and Auditability",
    "Escalation Over Assumption",
    "Concurrency and Isolation Safety",
    "Dependency and Version Discipline",
]

# Banned model names per the MODEL-AGNOSTIC POLICY.
# These exact strings (case-insensitive) must not appear in persona files.
_BANNED_MODEL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bGPT-4o\b", re.IGNORECASE),
    re.compile(r"\bGPT-4\b", re.IGNORECASE),
    re.compile(r"\bGPT-3\.5\b", re.IGNORECASE),
    # "Claude" as an AI provider/model; allow the word in non-model contexts
    re.compile(r"\bClaude\s+[0-9]", re.IGNORECASE),
    re.compile(r"\bDeepSeek\b", re.IGNORECASE),
    # "Llama" as a model name (not as part of a general sentence)
    re.compile(r"\bLlama[-\s]?[0-9]", re.IGNORECASE),
    re.compile(r"\bMistral\b", re.IGNORECASE),
    re.compile(r"\bGemini\b", re.IGNORECASE),
    re.compile(r"\bGrok\b", re.IGNORECASE),
]

# Minimum ALIGN rules that must be present
_REQUIRED_ALIGN_RULES = ["R-001", "R-002", "R-003", "R-004", "R-005", "R-006", "R-007", "R-008"]


def _all_persona_files() -> list[Path]:
    """Return all persona .md files, excluding _shared_mandates.md."""
    return [
        p
        for p in _PERSONAS_DIR.glob("*.md")
        if p.name not in _NON_PERSONA_FILES
    ]


# ===========================================================================
# 1. Shared Mandates File Integrity
# ===========================================================================


class TestSharedMandatesFileIntegrity:
    """Verify the _shared_mandates.md file is complete and well-formed."""

    def test_shared_mandates_file_exists(self) -> None:
        assert _SHARED_MANDATES_FILE.exists(), (
            f"_shared_mandates.md not found at {_SHARED_MANDATES_FILE}. "
            "This file is the source-of-truth for all universal operating mandates."
        )

    def test_shared_mandates_not_empty(self) -> None:
        content = _SHARED_MANDATES_FILE.read_text(encoding="utf-8")
        assert len(content.strip()) > 500, (
            "_shared_mandates.md appears truncated or empty."
        )

    @pytest.mark.parametrize("heading", _REQUIRED_MANDATE_HEADINGS)
    def test_mandate_heading_present(self, heading: str) -> None:
        content = _SHARED_MANDATES_FILE.read_text(encoding="utf-8")
        assert heading in content, (
            f"Required mandate heading '{heading}' not found in _shared_mandates.md."
        )

    @pytest.mark.parametrize("phrase", _KEY_MANDATE_PHRASES)
    def test_key_mandate_phrase_present(self, phrase: str) -> None:
        content = _SHARED_MANDATES_FILE.read_text(encoding="utf-8")
        assert phrase in content, (
            f"Expected mandate phrase '{phrase}' not found in _shared_mandates.md."
        )

    def test_enforcement_section_present(self) -> None:
        content = _SHARED_MANDATES_FILE.read_text(encoding="utf-8")
        assert "## Enforcement" in content, (
            "The '## Enforcement' section is missing from _shared_mandates.md."
        )

    def test_authority_level_supreme(self) -> None:
        content = _SHARED_MANDATES_FILE.read_text(encoding="utf-8")
        assert "SUPREME" in content, (
            "The 'SUPREME' authority marker is missing from _shared_mandates.md."
        )


# ===========================================================================
# 2. Persona File Inventory
# ===========================================================================


class TestPersonaInventory:
    """Verify all expected persona files are present."""

    _EXPECTED_PERSONAS = {
        "arbiter.md",
        "catalyst.md",
        "cdda.md",
        "chronicler.md",
        "consolidator.md",
        "cove.md",
        "herald.md",
        "oracle.md",
        "palette.md",
        "scout.md",
        "scribe.md",
        "sentinel.md",
        "steward.md",
        "strategist.md",
        "weaver.md",
    }

    def test_expected_persona_files_exist(self) -> None:
        existing = {p.name for p in _PERSONAS_DIR.glob("*.md")}
        missing = self._EXPECTED_PERSONAS - existing
        assert not missing, (
            f"Missing persona file(s): {sorted(missing)}. "
            "Do not delete persona files (Mandate 3: Persona and Prompt Fidelity)."
        )

    def test_no_empty_persona_files(self) -> None:
        for persona_file in _all_persona_files():
            content = persona_file.read_text(encoding="utf-8").strip()
            assert len(content) > 0, f"Persona file is empty: {persona_file.name}"


# ===========================================================================
# 3. Collaboration Protocol Compliance
# ===========================================================================


class TestCollaborationProtocolCompliance:
    """Every persona must have a Collaboration Protocol section."""

    @pytest.mark.parametrize("persona_file", _all_persona_files(), ids=lambda p: p.name)
    def test_has_collaboration_protocol(self, persona_file: Path) -> None:
        content = persona_file.read_text(encoding="utf-8")
        assert "Collaboration Protocol" in content, (
            f"Persona '{persona_file.name}' is missing a 'Collaboration Protocol' section. "
            "All personas must document how they collaborate with other agents."
        )


# ===========================================================================
# 4. Model-Agnostic Policy Compliance
# ===========================================================================


class TestModelAgnosticPolicy:
    """No persona file may contain banned model names."""

    @pytest.mark.parametrize("persona_file", _all_persona_files(), ids=lambda p: p.name)
    def test_no_banned_model_names(self, persona_file: Path) -> None:
        content = persona_file.read_text(encoding="utf-8")
        violations: list[str] = []
        for pattern in _BANNED_MODEL_PATTERNS:
            matches = pattern.findall(content)
            if matches:
                violations.append(f"Pattern '{pattern.pattern}' matched: {matches}")
        assert not violations, (
            f"Persona '{persona_file.name}' contains banned model name(s) "
            f"(MODEL-AGNOSTIC POLICY violation):\n" + "\n".join(violations)
        )


# ===========================================================================
# 5. Crew Orchestrator — Shared Mandates Injection
# ===========================================================================


class TestCrewOrchestratorMandateInjection:
    """Verify crew_orchestrator.py correctly loads and injects shared mandates."""

    def test_load_shared_mandates_returns_content(self) -> None:
        """_load_shared_mandates() should return a non-empty string."""
        from agents.core.crew_orchestrator import _load_shared_mandates, reset_mandates_cache  # type: ignore[attr-defined]

        # Reset the module-level cache so we load fresh from disk
        reset_mandates_cache()

        mandates = _load_shared_mandates()
        assert isinstance(mandates, str)
        assert len(mandates) > 500, (
            "_load_shared_mandates() returned a suspiciously short string. "
            "Verify that _shared_mandates.md is being read correctly."
        )

    def test_load_shared_mandates_contains_key_mandates(self) -> None:
        """Loaded mandates must contain the core Anti-Reductionism mandate."""
        from agents.core.crew_orchestrator import _load_shared_mandates, reset_mandates_cache  # type: ignore[attr-defined]

        reset_mandates_cache()

        mandates = _load_shared_mandates()
        assert "Anti-Reductionism" in mandates, (
            "The Anti-Reductionism mandate is not present in the loaded shared mandates content."
        )
        assert "Backup Before Modify" in mandates, (
            "The Backup Before Modify mandate is not present in the loaded shared mandates content."
        )

    def test_shared_mandates_injected_before_persona_prompt(self) -> None:
        """The shared mandates must appear before the persona's own content in the prompt."""
        from agents.core.crew_orchestrator import _build_persona_prompt  # type: ignore[attr-defined]

        prompt = _build_persona_prompt("sentinel", "audit security")
        mandate_pos = prompt.find("Anti-Reductionism")
        persona_pos = prompt.find("You are the **Sentinel**")

        assert mandate_pos != -1, "Anti-Reductionism mandate not found in built prompt."
        assert persona_pos != -1, "Sentinel persona content not found in built prompt."
        assert mandate_pos < persona_pos, (
            "Shared mandates must be injected BEFORE the persona's own content in the prompt. "
            "Check _build_persona_prompt() in crew_orchestrator.py."
        )


# ===========================================================================
# 6. ALIGN Ledger Integrity
# ===========================================================================


class TestAlignLedgerIntegrity:
    """Verify governance/ALIGN_LEDGER.yaml is well-formed and contains required rules."""

    def test_align_ledger_exists(self) -> None:
        assert _ALIGN_LEDGER.exists(), (
            f"ALIGN_LEDGER.yaml not found at {_ALIGN_LEDGER}."
        )

    def test_align_ledger_not_empty(self) -> None:
        content = _ALIGN_LEDGER.read_text(encoding="utf-8").strip()
        assert len(content) > 0, "ALIGN_LEDGER.yaml is empty."

    @pytest.mark.parametrize("rule_id", _REQUIRED_ALIGN_RULES)
    def test_required_align_rule_present(self, rule_id: str) -> None:
        content = _ALIGN_LEDGER.read_text(encoding="utf-8")
        assert rule_id in content, (
            f"Required ALIGN rule '{rule_id}' not found in ALIGN_LEDGER.yaml."
        )

    def test_align_ledger_parseable_as_yaml(self) -> None:
        content = _ALIGN_LEDGER.read_text(encoding="utf-8")
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, dict), "ALIGN_LEDGER.yaml must parse to a dict."
        assert "rules" in parsed, "ALIGN_LEDGER.yaml must contain a 'rules' key."
        assert isinstance(parsed["rules"], list), "ALIGN_LEDGER.yaml 'rules' must be a list."
        assert len(parsed["rules"]) >= len(_REQUIRED_ALIGN_RULES), (
            f"Expected at least {len(_REQUIRED_ALIGN_RULES)} rules, "
            f"found {len(parsed['rules'])}."
        )


# ===========================================================================
# 7. CDDA Persona — Full Compliance (regression guard)
# ===========================================================================


class TestCddaPersonaCompliance:
    """Regression tests specific to the CDDA persona after compliance fixes."""

    _CDDA = _PERSONAS_DIR / "cdda.md"

    def test_cdda_exists(self) -> None:
        assert self._CDDA.exists(), "cdda.md persona file is missing."

    def test_cdda_has_collaboration_protocol(self) -> None:
        content = self._CDDA.read_text(encoding="utf-8")
        assert "## Collaboration Protocol" in content, (
            "cdda.md is missing the '## Collaboration Protocol' section."
        )

    def test_cdda_collaboration_protocol_references_mandate_1(self) -> None:
        content = self._CDDA.read_text(encoding="utf-8")
        # Should reference Mandate 1 (Anti-Reductionism / Triple-Verification)
        assert "Mandate 1" in content or "Triple-Verification" in content, (
            "cdda.md's Collaboration Protocol should reference Mandate 1 "
            "(Anti-Reductionism / Triple-Verification Protocol) for no-silent-removal."
        )

    def test_cdda_collaboration_protocol_references_mandate_7(self) -> None:
        content = self._CDDA.read_text(encoding="utf-8")
        # Should reference Mandate 7 (Escalation Over Assumption)
        assert "Mandate 7" in content or "Escalation Over Assumption" in content, (
            "cdda.md's Collaboration Protocol should reference Mandate 7 "
            "(Escalation Over Assumption) for ambiguity handling."
        )

    def test_cdda_no_banned_model_names(self) -> None:
        content = self._CDDA.read_text(encoding="utf-8")
        for pattern in _BANNED_MODEL_PATTERNS:
            matches = pattern.findall(content)
            assert not matches, (
                f"cdda.md still contains banned model name matching '{pattern.pattern}': {matches}. "
                "Replace with config references (settings['models']['...'])."
            )

    def test_cdda_uses_config_model_references(self) -> None:
        content = self._CDDA.read_text(encoding="utf-8")
        # After the fix, model strategy should reference settings keys
        assert 'settings["models"]' in content or "settings[" in content, (
            "cdda.md Model Strategy should reference models via config "
            "(e.g. settings['models']['reasoning']) instead of hardcoded names."
        )
