"""
HLF Nuanced Translator — Bidirectional English ↔ HLF with intent preservation.

Upgrade from raw bytecode op-lists to fluent, context-aware translation.

Features:
    - Chaos English → structured HLF AST (captures typos, ellipsis, sentiment)
    - HLF bytecode → fluent natural English (not op-list dumps)
    - Nuance glyphs (~frust{}, ~urgnt{}, ~ambi{}) for tone encoding
    - LLM-backed polishing via model gateway (optional)
    - Standalone mode with rule-based heuristics

Architecture:
    English → tokenize → detect_nuance → build_hlf_ast → emit_hlf
    HLF    → parse_hlf → resolve_glyphs → compose_english → polish
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ─── Nuance Glyphs ──────────────────────────────────────────────────────────

class Tone(str, Enum):
    """Detected tone/sentiment in user input."""

    NEUTRAL = "neutral"
    FRUSTRATED = "frustrated"
    URGENT = "urgent"
    AMBIGUOUS = "ambiguous"
    ENTHUSIASTIC = "enthusiastic"
    QUESTIONING = "questioning"
    SARCASTIC = "sarcastic"


_TONE_MARKERS: dict[Tone, list[str]] = {
    Tone.FRUSTRATED: ["stuck", "broken", "can't", "won't", "ugh", "wtf", "damn", "stupid", "hate"],
    Tone.URGENT: ["asap", "urgent", "now", "immediately", "critical", "emergency", "hurry"],
    Tone.ENTHUSIASTIC: ["amazing", "awesome", "love", "great", "perfect", "wow", "incredible", "!"],
    Tone.QUESTIONING: ["how", "why", "what", "where", "when", "?", "confused", "understand"],
    Tone.SARCASTIC: ["sure", "obviously", "clearly", "totally", "oh great", "wonderful"],
}


@dataclass
class NuanceGlyph:
    """Encodes detected nuance as an HLF-embeddable glyph.

    Format: ~tone{context}
    Example: ~frust{stuck on deploy}
    """

    tone: Tone
    context: str = ""

    def to_hlf(self) -> str:
        """Serialize to HLF glyph format."""
        short = {
            Tone.FRUSTRATED: "frust",
            Tone.URGENT: "urgnt",
            Tone.AMBIGUOUS: "ambi",
            Tone.ENTHUSIASTIC: "enthus",
            Tone.QUESTIONING: "quest",
            Tone.SARCASTIC: "sarc",
            Tone.NEUTRAL: "neut",
        }
        tag = short.get(self.tone, "neut")
        if self.context:
            return f"~{tag}{{{self.context}}}"
        return f"~{tag}{{}}"

    @classmethod
    def from_hlf(cls, glyph_str: str) -> NuanceGlyph | None:
        """Parse an HLF glyph string back to NuanceGlyph."""
        m = re.match(r"~(\w+)\{(.*?)\}", glyph_str)
        if not m:
            return None

        tag_map = {
            "frust": Tone.FRUSTRATED,
            "urgnt": Tone.URGENT,
            "ambi": Tone.AMBIGUOUS,
            "enthus": Tone.ENTHUSIASTIC,
            "quest": Tone.QUESTIONING,
            "sarc": Tone.SARCASTIC,
            "neut": Tone.NEUTRAL,
        }
        tone = tag_map.get(m.group(1), Tone.NEUTRAL)
        return cls(tone=tone, context=m.group(2))


# ─── Intent Detection ───────────────────────────────────────────────────────

@dataclass
class DetectedIntent:
    """Structured intent extracted from raw English."""

    action: str           # Primary action verb
    target: str           # What the action applies to
    tone: Tone            # Detected sentiment
    modifiers: list[str] = field(default_factory=list)
    raw_input: str = ""


def detect_nuance(text: str) -> DetectedIntent:
    """Analyze raw English input for intent and nuance.

    Handles typos, run-on sentences, ellipsis, and informal speech.

    Args:
        text: Raw user input (may be messy/informal).

    Returns:
        DetectedIntent with action, target, tone.
    """
    cleaned = text.strip().lower()

    # Detect tone
    tone = Tone.NEUTRAL
    tone_score: dict[Tone, int] = {t: 0 for t in Tone}

    for t, markers in _TONE_MARKERS.items():
        for marker in markers:
            if marker in cleaned:
                tone_score[t] += 1

    # Highest scoring tone wins
    max_tone = max(tone_score, key=lambda t: tone_score[t])
    if tone_score[max_tone] > 0:
        tone = max_tone

    # Multiple tones detected = ambiguous
    high_tones = [t for t, s in tone_score.items() if s > 0]
    if len(high_tones) > 2:
        tone = Tone.AMBIGUOUS

    # Extract action verb (first meaningful verb)
    action_verbs = [
        "fix", "build", "deploy", "install", "create", "add", "remove",
        "update", "refactor", "test", "debug", "run", "start", "stop",
        "configure", "setup", "integrate", "wire", "connect", "send",
        "check", "verify", "analyze", "research", "find", "search",
    ]
    action = "process"
    for verb in action_verbs:
        if verb in cleaned:
            action = verb
            break

    # Extract target (largest noun-ish segment after the verb)
    words = cleaned.split()
    target = ""
    if action in words:
        idx = words.index(action)
        target = " ".join(words[idx + 1:])
    elif len(words) > 1:
        target = " ".join(words[1:])

    # Clean up target
    target = re.sub(r"[.!?,;]+$", "", target).strip()

    modifiers = []
    if "..." in text or "…" in text:
        modifiers.append("trailing_thought")
    if text.count("!") > 1:
        modifiers.append("emphasis")
    if re.search(r"(.)\1{2,}", text):
        modifiers.append("repetition")

    return DetectedIntent(
        action=action,
        target=target,
        tone=tone,
        modifiers=modifiers,
        raw_input=text,
    )


# ─── Translator ─────────────────────────────────────────────────────────────

class HLFTranslator:
    """Bidirectional English ↔ HLF translator with nuance preservation.

    English → HLF: Detects intent, encodes nuance, generates HLF.
    HLF → English: Parses HLF, resolves glyphs, produces natural prose.
    """

    def __init__(self, llm_backend: Any | None = None) -> None:
        self._llm = llm_backend

    def english_to_hlf(self, text: str) -> dict[str, Any]:
        """Translate raw English to HLF with embedded nuance.

        Args:
            text: Raw English input (may be informal/messy).

        Returns:
            Dict with 'hlf' code string, 'intent', 'glyph'.
        """
        intent = detect_nuance(text)
        glyph = NuanceGlyph(tone=intent.tone, context=intent.target[:50] if intent.target else "")

        # Build HLF program
        hlf_lines = [
            f"; Auto-translated from English: {text[:80]}",
            f"; {glyph.to_hlf()}",
            f"",
            f"DEFINE TASK {intent.action}_{_safe_id(intent.target)} {{",
            f"    ACTION: {intent.action.upper()};",
        ]

        if intent.target:
            hlf_lines.append(f"    TARGET: \"{intent.target}\";")

        if intent.modifiers:
            hlf_lines.append(f"    MODIFIERS: {', '.join(intent.modifiers)};")

        hlf_lines.append(f"    TONE: {intent.tone.value};")
        hlf_lines.append(f"}}")
        hlf_lines.append(f"")
        hlf_lines.append(f"EXEC {intent.action}_{_safe_id(intent.target)};")

        hlf_code = "\n".join(hlf_lines)

        return {
            "hlf": hlf_code,
            "intent": {
                "action": intent.action,
                "target": intent.target,
                "tone": intent.tone.value,
                "modifiers": intent.modifiers,
            },
            "glyph": glyph.to_hlf(),
        }

    def hlf_to_english(self, hlf_code: str) -> dict[str, Any]:
        """Translate HLF code to fluent natural English.

        Unlike raw decompilation (which dumps op-lists), this produces
        readable prose that captures the program's intent.

        Args:
            hlf_code: HLF source code.

        Returns:
            Dict with 'english' prose, 'tone', 'summary'.
        """
        # Parse key elements
        action = ""
        target = ""
        tone = Tone.NEUTRAL
        glyph_str = ""

        for line in hlf_code.split("\n"):
            line = line.strip()

            # Extract glyph
            glyph_match = re.search(r"~\w+\{.*?\}", line)
            if glyph_match:
                glyph_str = glyph_match.group()
                glyph = NuanceGlyph.from_hlf(glyph_str)
                if glyph:
                    tone = glyph.tone

            # Extract action
            action_match = re.match(r"ACTION:\s*(\w+)", line)
            if action_match:
                action = action_match.group(1).lower()

            # Extract target
            target_match = re.match(r'TARGET:\s*"(.+?)"', line)
            if target_match:
                target = target_match.group(1)

        # Compose natural English
        if not action:
            action = "process"

        tone_prefix = {
            Tone.FRUSTRATED: "Needs urgent attention: ",
            Tone.URGENT: "Priority task: ",
            Tone.ENTHUSIASTIC: "Exciting task: ",
            Tone.QUESTIONING: "Investigation needed: ",
            Tone.SARCASTIC: "Task (with reservations): ",
            Tone.NEUTRAL: "",
            Tone.AMBIGUOUS: "Complex task requiring clarification: ",
        }

        prefix = tone_prefix.get(tone, "")
        verb = _humanize_verb(action)
        summary = f"{prefix}{verb} {target}" if target else f"{prefix}{verb} the system"

        return {
            "english": summary.strip(),
            "tone": tone.value,
            "action": action,
            "target": target,
            "glyph": glyph_str,
        }

    def roundtrip(self, text: str) -> dict[str, Any]:
        """English → HLF → English roundtrip test.

        Useful for validating translation fidelity.
        """
        to_hlf = self.english_to_hlf(text)
        back = self.hlf_to_english(to_hlf["hlf"])
        return {
            "original": text,
            "hlf": to_hlf["hlf"],
            "reconstructed": back["english"],
            "tone_preserved": to_hlf["intent"]["tone"] == back["tone"],
        }


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _safe_id(text: str) -> str:
    """Convert text to a safe HLF identifier."""
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", text.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned[:40] or "task"


def _humanize_verb(action: str) -> str:
    """Convert an HLF action verb to natural English."""
    verb_map = {
        "fix": "Fix",
        "build": "Build",
        "deploy": "Deploy",
        "install": "Install",
        "create": "Create",
        "add": "Add",
        "remove": "Remove",
        "update": "Update",
        "refactor": "Refactor",
        "test": "Test",
        "debug": "Debug",
        "run": "Run",
        "start": "Start",
        "stop": "Stop",
        "configure": "Configure",
        "setup": "Set up",
        "integrate": "Integrate",
        "wire": "Wire up",
        "connect": "Connect",
        "send": "Send to",
        "check": "Check",
        "verify": "Verify",
        "analyze": "Analyze",
        "research": "Research",
        "find": "Find",
        "search": "Search for",
        "process": "Process",
    }
    return verb_map.get(action, action.capitalize())
