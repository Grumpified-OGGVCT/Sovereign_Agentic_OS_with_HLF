"""Hieroglyphic Logic Framework (HLF) Toolkit."""

from __future__ import annotations

import re

# Export core utilities
from .hlfc import HlfRuntimeError, HlfSyntaxError, compile
from .hlffmt import format_hlf

__all__ = ["compile", "format_hlf", "validate_hlf", "validate_hlf_heuristic", "HlfSyntaxError", "HlfRuntimeError"]

# Fast pre-validation regex for Agent Service Bus (ASB)
# Rejects grossly malformed text before handing off to Lark parser to save CPU/gas
_HLF_HEURISTIC_RE = re.compile(r"^\[HLF-v[234]\].*?(?:\u03a9|\bOmega\b)\s*$", re.DOTALL)

# Per-line validation: uppercase tags, version header, terminator, or empty lines are valid
_HLF_LINE_RE = re.compile(
    r"^\s*(?:"
    r"\[[A-Z_][A-Z0-9_]*\]"  # uppercase tag e.g. [INTENT], [RESULT]
    r"|\[HLF-v[234]\]"  # version header e.g. [HLF-v2], [HLF-v3] or [HLF-v4]
    r"|[\u03a9]"  # Ω terminator
    r"|\bOmega\b"  # Omega terminator
    r")\s*.*$"
    r"|^\s*$",  # empty / whitespace-only lines
    re.DOTALL,
)


def validate_hlf(line: str) -> bool:
    """Validate a single line of HLF source.

    Returns True if the line is structurally valid HLF:
    an uppercase-tag statement, version header, terminator, or empty line.
    Returns False for plain prose, lowercase tags, or other non-HLF content.
    """
    return bool(_HLF_LINE_RE.match(line))


def validate_hlf_heuristic(text: str) -> bool:
    """Fast regex check if text looks structurally like HLF.

    Used by ASB router to instantly drop non-HLF text without invoking full parser.
    Requires [HLF-v2] header and Ω terminator.
    """
    text = text.strip()
    return bool(_HLF_HEURISTIC_RE.match(text))
