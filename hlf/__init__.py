"""Hieroglyphic Logic Framework (HLF) Toolkit."""
from __future__ import annotations

import re

# Export core utilities
from .hlfc import compile, HlfSyntaxError, HlfRuntimeError
from .hlffmt import format_hlf

__all__ = ["compile", "format_hlf", "validate_hlf_heuristic", "HlfSyntaxError", "HlfRuntimeError"]

# Fast pre-validation regex for Agent Service Bus (ASB)
# Rejects grossly malformed text before handing off to Lark parser to save CPU/gas
_HLF_HEURISTIC_RE = re.compile(r"^\[HLF-v2\].*?(?:\u03a9|\bOmega\b)\s*$", re.DOTALL)

def validate_hlf_heuristic(text: str) -> bool:
    """Fast regex check if text looks structurally like HLF.
    
    Used by ASB router to instantly drop non-HLF text without invoking full parser.
    Requires [HLF-v2] header and Ω terminator.
    """
    text = text.strip()
    return bool(_HLF_HEURISTIC_RE.match(text))
