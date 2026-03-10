"""Hieroglyphic Logic Framework (HLF) Toolkit."""

from __future__ import annotations

import re

# Export core utilities
from .hlfc import (
    HlfAlignViolation,
    HlfArityError,
    HlfRuntimeError,
    HlfSyntaxError,
    HlfTypeError,
    compile,
)
from .hlffmt import format_hlf
from .insaits import decompile, decompile_bytecode, decompile_live
from .intent_capsule import (
    CapsuleViolation,
    IntentCapsule,
    forge_capsule,
    hearth_capsule,
    sovereign_capsule,
)
from .memory_node import HLFMemoryNode

__all__ = [
    # Compiler
    "compile",
    "HlfSyntaxError",
    "HlfRuntimeError",
    "HlfAlignViolation",
    "HlfArityError",
    "HlfTypeError",
    # Formatter
    "format_hlf",
    # Validation
    "validate_hlf",
    "validate_hlf_heuristic",
    # Gas estimation
    "quick_gas_estimate",
    # InsAIts translation layer
    "decompile",
    "decompile_live",
    "decompile_bytecode",
    # Memory
    "HLFMemoryNode",
    # Intent Capsules
    "IntentCapsule",
    "CapsuleViolation",
    "hearth_capsule",
    "forge_capsule",
    "sovereign_capsule",
]

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


# Matches every statement that maps to one AST node in the compiled program.
# Counts tag statements [TAG], SET, FUNCTION, RESULT, CALL, SPEC_* tags,
# tool executions (↦), assignments (←), conditionals (⊎), parallel (∥),
# sync barriers (⋈), memory ops ([MEMORY]/[RECALL]), struct (≡), and macros (Σ).
_GAS_ESTIMATE_RE = re.compile(
    r"(?:"
    r"\[[A-Z_][A-Z0-9_]*\]"   # [TAG] — any uppercase bracket tag
    r"|↦"                       # tool execution
    r"|←"                       # assignment
    r"|⊎"                       # conditional
    r"|∥\s*\["                  # parallel block
    r"|⋈\s*\["                  # sync barrier
    r"|≡\s*\{"                  # struct definition
    r"|Σ\s*\["                  # macro definition
    r")"
)


def quick_gas_estimate(source: str) -> int:
    """Fast O(n) heuristic gas estimate from raw HLF source.

    Counts statement-level tokens (tags, operators) without invoking the
    full Lark compiler — useful for pre-flight budget checks in the ASB
    router before the expensive parse step.

    The count deliberately excludes the ``[HLF-v*]`` version header and Ω
    terminator because those are control markers, not runnable AST nodes.

    Counted statement types:
        - ``[TAG]`` — any uppercase bracket tag (INTENT, RESULT, ACTION, etc.)
        - ``↦`` — tool execution (RFC 9005 §4.1)
        - ``←`` — assignment operator (RFC 9005 §5.1)
        - ``⊎`` — conditional start (RFC 9005 §3.2)
        - ``∥ [`` — parallel block opener (RFC 9005 §6.1)
        - ``⋈ [`` — sync barrier opener (RFC 9005 §6.2)
        - ``≡ {`` — struct definition (RFC 9007 §2.1)
        - ``Σ [`` — macro definition

    Args:
        source: Raw HLF source text.

    Returns:
        Estimated AST node count (>= 0).  The true count from
        ``hlfc.compile()`` may differ slightly for complex nested
        constructs, but this estimate is always a lower bound.
    """
    # Strip the version header so it doesn't inflate the count
    cleaned = re.sub(r"\[HLF-v[^\]]*\]", "", source)
    return len(_GAS_ESTIMATE_RE.findall(cleaned))
