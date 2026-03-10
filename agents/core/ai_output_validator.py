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
# Tuning constants
# ---------------------------------------------------------------------------

# Confidence penalty applied per injection pattern match.
_INJECTION_CONFIDENCE_PENALTY: float = 0.4

# Confidence penalty per HLF safety violation.
_HLF_SAFETY_CONFIDENCE_PENALTY: float = 0.3

# Confidence penalty per hallucination marker.
_HALLUCINATION_CONFIDENCE_PENALTY: float = 0.1

# Number of hallucination markers before an additional compound penalty fires.
_HALLUCINATION_COMPOUND_THRESHOLD: int = 3

# Additional penalty when >= _HALLUCINATION_COMPOUND_THRESHOLD markers found.
_HALLUCINATION_COMPOUND_PENALTY: float = 0.15

# Confidence penalty for a model refusal.
_REFUSAL_CONFIDENCE_PENALTY: float = 0.2

# Minimum meaningful response length in characters.
_MIN_CONTENT_LENGTH: int = 5

# Confidence penalty for an empty/near-empty response.
_EMPTY_CONFIDENCE_PENALTY: float = 0.5


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
# Each pattern tuple is (category_label, compiled_regex).
# Using labels instead of raw pattern text in issue messages prevents
# exposing detection logic to potential adversaries.
# ---------------------------------------------------------------------------

# Prompt injection: sequences that attempt to override system instructions.
_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ignore_previous_instructions", re.compile(r"ignore\s+(all\s+)?(?:previous|prior|above)\s+instructions?", re.I)),
    ("disregard_instructions", re.compile(r"disregard\s+(all\s+)?(?:previous|prior|above)\s+instructions?", re.I)),
    ("forget_instructions", re.compile(r"forget\s+(all\s+)?(?:previous|prior|above)\s+instructions?", re.I)),
    ("new_instruction_override", re.compile(r"new\s+instruction[s]?[:\s]", re.I)),
    ("persona_no_restrictions", re.compile(
        r"you\s+are\s+now\s+(?:a\s+)?(?:an?\s+)?\w+\s+(?:that|who)\s+(?:does\s+not|never)", re.I)),
    ("system_tag_injection", re.compile(r"\[SYSTEM\]\s*:", re.I)),
    ("act_no_restrictions", re.compile(
        r"(?:act|behave|respond)\s+as\s+(?:if\s+)?(?:you\s+(?:have\s+)?no\s+restrict)", re.I)),
    ("developer_mode", re.compile(r"developer\s+mode\s*(?:enabled|on|activated)", re.I)),
    ("jailbreak_keyword", re.compile(r"jailbreak", re.I)),
    ("dan_mode", re.compile(r"DAN\s*(?:mode|prompt)", re.I)),  # "Do Anything Now" jailbreak
]

# Hallucination indicators: phrases that signal overconfidence or
# factual claims that may not be grounded in the provided context.
_HALLUCINATION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("overconfidence_adverb", re.compile(
        r"\b(?:definitely|certainly|absolutely|guaranteed(?:ly)?)\s+(?:is|are|will|can)\b", re.I)),
    ("hundred_percent_certain", re.compile(r"\b100\s*%\s+(?:sure|certain|accurate|correct|guaranteed)\b", re.I)),
    ("stale_as_of_now", re.compile(r"\bas\s+of\s+(?:today|now|this\s+moment)\b.*\blatest\b", re.I)),
    ("know_for_a_fact", re.compile(r"\bI\s+(?:know|confirm|guarantee)\s+(?:for\s+(?:a\s+)?fact)\b", re.I)),
    ("without_doubt", re.compile(r"\bwithout\s+(?:any\s+)?(?:doubt|question)\b", re.I)),
]

# Patterns indicating the model may be refusing or expressing uncertainty
# beyond acceptable bounds — a signal to flag for human review.
_REFUSAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("capability_refusal", re.compile(
        r"\bI\s+(?:cannot|can't|am\s+unable\s+to|refuse\s+to)\s+(?:help|assist|do|complete)\b", re.I)),
    ("no_access", re.compile(r"\bI\s+(?:don't|do\s+not)\s+have\s+(?:access|the\s+ability)", re.I)),
    ("harmful_content_refusal", re.compile(
        r"(?:harmful|dangerous|illegal|unethical)\s+(?:request|content|activity)", re.I)),
]

# HLF-specific safety: constructs that should not appear in model-generated HLF
_HLF_SAFETY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("destructive_file_op", re.compile(r"\[INTENT\]\s+(?:delete|rm|remove|wipe|purge)\s+/", re.I)),
    ("spawn_shell", re.compile(r"\[ACTION\]\s+(?:exec|run|execute|spawn)\s+(?:bash|sh|cmd|powershell)", re.I)),
    ("secret_env_ref", re.compile(
        r"\$\{\s*(?:env|ENV|secrets?|SECRETS?|password|PASSWORD|token|TOKEN)\s*\}", re.I)),
    ("pipe_to_shell", re.compile(r"(?:curl|wget)\s+.*\|\s*(?:bash|sh)", re.I)),
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
    - ``flagged_patterns``: The matched pattern category labels for audit logging.
    """
    issues: list[str] = []
    flagged: list[str] = []
    confidence_delta = 0.0
    blocking = False

    # 1. Prompt injection check (blocking)
    for label, pat in _INJECTION_PATTERNS:
        if pat.search(text):
            issues.append(f"PROMPT_INJECTION: suspected injection pattern matched [{label}]")
            flagged.append(f"injection:{label}")
            blocking = True
            confidence_delta -= _INJECTION_CONFIDENCE_PENALTY

    # 2. HLF safety check (blocking)
    for label, pat in _HLF_SAFETY_PATTERNS:
        if pat.search(text):
            issues.append(f"HLF_SAFETY: unsafe HLF construct detected [{label}]")
            flagged.append(f"hlf_safety:{label}")
            blocking = True
            confidence_delta -= _HLF_SAFETY_CONFIDENCE_PENALTY

    # 3. Hallucination check (non-blocking, confidence penalty)
    hallucination_count = 0
    for label, pat in _HALLUCINATION_PATTERNS:
        if pat.search(text):
            issues.append(f"HALLUCINATION_RISK: overconfidence marker detected [{label}]")
            flagged.append(f"hallucination:{label}")
            hallucination_count += 1
            confidence_delta -= _HALLUCINATION_CONFIDENCE_PENALTY

    # Extra penalty if multiple hallucination markers are present
    if hallucination_count >= _HALLUCINATION_COMPOUND_THRESHOLD:
        issues.append("HALLUCINATION_RISK: multiple overconfidence markers — treat output with caution")
        confidence_delta -= _HALLUCINATION_COMPOUND_PENALTY

    # 4. Refusal / capability gap (non-blocking, note for observability)
    for _label, pat in _REFUSAL_PATTERNS:
        if pat.search(text):
            issues.append("REFUSAL_DETECTED: model declined or expressed inability — consider fallback model")
            flagged.append("refusal")
            confidence_delta -= _REFUSAL_CONFIDENCE_PENALTY
            break  # One refusal note is sufficient

    # 5. Minimum content check
    if len(text.strip()) < _MIN_CONTENT_LENGTH:
        issues.append("EMPTY_OUTPUT: model returned near-empty response")
        flagged.append("empty")
        blocking = True
        confidence_delta -= _EMPTY_CONFIDENCE_PENALTY

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
