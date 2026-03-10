"""
AI Output Validator — 🩵 Cyan Hat AI/ML Validation Layer

Validates AI model outputs before they enter the HLF execution pipeline.
Checks for:
  - Prompt injection patterns embedded in model responses
  - Hallucination indicators (overconfidence markers, contradictory qualifiers)
  - Safety-sensitive content patterns that should not reach the executor
  - Confidence adjustment recommendations based on output quality signals

All checks are deterministic (no LLM calls) and run in O(n) over output length.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    """Structured result from validate_output()."""

    safe: bool
    issues: list[str] = field(default_factory=list)
    confidence_adjustment: float = 0.0  # Negative = reduce confidence in the output
    flagged_patterns: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pattern libraries
# ---------------------------------------------------------------------------

# Prompt injection: sequences that attempt to override system instructions.
# These commonly appear in adversarial inputs and jailbreak attempts.
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(all\s+)?(?:previous|prior|above)\s+instructions?", re.I),
    re.compile(r"disregard\s+(all\s+)?(?:previous|prior|above)\s+instructions?", re.I),
    re.compile(r"forget\s+(all\s+)?(?:previous|prior|above)\s+instructions?", re.I),
    re.compile(r"new\s+instruction[s]?[:\s]", re.I),
    re.compile(r"you\s+are\s+now\s+(?:a\s+)?(?:an?\s+)?\w+\s+(?:that|who)\s+(?:does\s+not|never)", re.I),
    re.compile(r"\[SYSTEM\]\s*:", re.I),
    re.compile(r"(?:act|behave|respond)\s+as\s+(?:if\s+)?(?:you\s+(?:have\s+)?no\s+restrict)", re.I),
    re.compile(r"developer\s+mode\s*(?:enabled|on|activated)", re.I),
    re.compile(r"jailbreak", re.I),
    re.compile(r"DAN\s*(?:mode|prompt)", re.I),  # "Do Anything Now" jailbreak
]

# Hallucination indicators: phrases that signal overconfidence or
# factual claims that may not be grounded in the provided context.
_HALLUCINATION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(?:definitely|certainly|absolutely|guaranteed(?:ly)?)\s+(?:is|are|will|can)\b", re.I),
    re.compile(r"\b100\s*%\s+(?:sure|certain|accurate|correct|guaranteed)\b", re.I),
    re.compile(r"\bas\s+of\s+(?:today|now|this\s+moment)\b.*\blatest\b", re.I),
    re.compile(r"\bI\s+(?:know|confirm|guarantee)\s+(?:for\s+(?:a\s+)?fact)\b", re.I),
    re.compile(r"\bwithout\s+(?:any\s+)?(?:doubt|question)\b", re.I),
]

# Patterns indicating the model may be refusing or expressing uncertainty
# beyond acceptable bounds — a signal to flag for human review.
_REFUSAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bI\s+(?:cannot|can't|am\s+unable\s+to|refuse\s+to)\s+(?:help|assist|do|complete)\b", re.I),
    re.compile(r"\bI\s+(?:don't|do\s+not)\s+have\s+(?:access|the\s+ability)", re.I),
    re.compile(r"(?:harmful|dangerous|illegal|unethical)\s+(?:request|content|activity)", re.I),
]

# HLF-specific safety: patterns that should not appear in model-generated HLF
_HLF_SAFETY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\[INTENT\]\s+(?:delete|rm|remove|wipe|purge)\s+/", re.I),  # destructive file ops
    re.compile(r"\[ACTION\]\s+(?:exec|run|execute|spawn)\s+(?:bash|sh|cmd|powershell)", re.I),
    re.compile(r"\$\{\s*(?:env|ENV|secrets?|SECRETS?|password|PASSWORD|token|TOKEN)\s*\}", re.I),
    re.compile(r"(?:curl|wget)\s+.*\|\s*(?:bash|sh)", re.I),  # pipe-to-shell exfiltration
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_output(text: str) -> ValidationResult:
    """
    Validate an AI model output string before it enters the HLF executor.

    Returns a :class:`ValidationResult` with:
    - ``safe``:  False if any blocking issue is found (injection or HLF safety violation).
    - ``issues``: Human-readable list of detected problems.
    - ``confidence_adjustment``: Suggested delta to apply to the router's confidence score.
      Negative means reduce trust; positive means no change (clamped to [-1.0, 0.0]).
    - ``flagged_patterns``: The matched pattern descriptions for audit logging.
    """
    issues: list[str] = []
    flagged: list[str] = []
    confidence_delta = 0.0
    blocking = False

    # 1. Prompt injection check (blocking)
    for pat in _INJECTION_PATTERNS:
        if pat.search(text):
            issues.append(f"PROMPT_INJECTION: suspected injection pattern matched ({pat.pattern[:40]}…)")
            flagged.append(f"injection:{pat.pattern[:40]}")
            blocking = True
            confidence_delta -= 0.4

    # 2. HLF safety check (blocking)
    for pat in _HLF_SAFETY_PATTERNS:
        if pat.search(text):
            issues.append(f"HLF_SAFETY: unsafe HLF construct detected ({pat.pattern[:40]}…)")
            flagged.append(f"hlf_safety:{pat.pattern[:40]}")
            blocking = True
            confidence_delta -= 0.3

    # 3. Hallucination check (non-blocking, confidence penalty)
    hallucination_count = 0
    for pat in _HALLUCINATION_PATTERNS:
        if pat.search(text):
            issues.append(f"HALLUCINATION_RISK: overconfidence marker detected ({pat.pattern[:40]}…)")
            flagged.append(f"hallucination:{pat.pattern[:40]}")
            hallucination_count += 1
            confidence_delta -= 0.1

    # Extra penalty if multiple hallucination markers are present
    if hallucination_count >= 3:
        issues.append("HALLUCINATION_RISK: multiple overconfidence markers — treat output with caution")
        confidence_delta -= 0.15

    # 4. Refusal / capability gap (non-blocking, note for observability)
    for pat in _REFUSAL_PATTERNS:
        if pat.search(text):
            issues.append("REFUSAL_DETECTED: model declined or expressed inability — consider fallback model")
            flagged.append("refusal")
            confidence_delta -= 0.2
            break  # One refusal note is sufficient

    # 5. Minimum content check
    if len(text.strip()) < 5:
        issues.append("EMPTY_OUTPUT: model returned near-empty response")
        flagged.append("empty")
        blocking = True
        confidence_delta -= 0.5

    # Clamp confidence adjustment to [-1.0, 0.0]
    confidence_delta = max(-1.0, min(0.0, confidence_delta))

    return ValidationResult(
        safe=not blocking,
        issues=issues,
        confidence_adjustment=confidence_delta,
        flagged_patterns=flagged,
    )


def is_output_safe(text: str) -> bool:
    """Convenience wrapper — returns True only when the output passes all blocking checks."""
    return validate_output(text).safe
