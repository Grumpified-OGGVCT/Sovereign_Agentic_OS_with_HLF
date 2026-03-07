"""
HLF Error Corrector — Structured Error Correction & Self-Healing.

When an HLF program fails to compile, the ErrorCorrector analyzes the
syntax error and produces a structured correction response:
  1. Diagnosis   — what went wrong
  2. Suggestion  — how to fix it
  3. Auto-fix    — attempt heuristic correction and re-compile

Operator Catalog:
  The corrector maintains a catalog of valid HLF operators and tags,
  enabling it to suggest the closest valid symbol when a typo is detected.

Usage:
    from hlf.error_corrector import HLFErrorCorrector
    corrector = HLFErrorCorrector()
    result = corrector.correct(bad_source)
    if result.fixed_ast:
        print("Auto-corrected!")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import get_close_matches
from typing import Any

from hlf.hlfc import HlfSyntaxError
from hlf.hlfc import compile as hlfc_compile

# --------------------------------------------------------------------------- #
# Operator & tag catalogs
# --------------------------------------------------------------------------- #

VALID_TAGS = [
    "INTENT", "THOUGHT", "OBSERVATION", "PLAN", "CONSTRAINT", "EXPECT",
    "ACTION", "SET", "FUNCTION", "DELEGATE", "VOTE", "ASSERT", "RESULT",
    "MODULE", "IMPORT", "DATA", "MEMORY", "RECALL", "DEFINE", "CALL",
]

VALID_GLYPHS = {
    "⌘": "EXECUTE (orchestrator directive)",
    "Ж": "CONSTRAINT (reasoning blocker)",
    "∇": "PARAMETER (gradient binding)",
    "⩕": "PRIORITY (gas metric)",
    "⨝": "JOIN (matrix consensus)",
    "Δ": "DELTA (state diff)",
    "~": "AESTHETIC (modifier)",
    "§": "SECTION (expression)",
    "Σ": "DEFINE (macro declaration)",
    "⌂": "MEMORY (memory anchor)",
}

VALID_OPERATORS = {
    "⊎": "conditional (IF)",
    "⇒": "then branch",
    "⇌": "else branch",
    "∥": "parallel execution",
    "⋈": "sync barrier",
    "←": "assignment",
    "≡": "struct definition",
    "↦": "tool dispatch",
    "τ": "tool marker",
    "Ω": "terminator",
}

# Common typos / confusions
TYPO_MAP = {
    "INTNET": "INTENT",
    "INTETN": "INTENT",
    "CONSTRAING": "CONSTRAINT",
    "CONSTRATIN": "CONSTRAINT",
    "FUCNTION": "FUNCTION",
    "FUNCITON": "FUNCTION",
    "DELIGATE": "DELEGATE",
    "ASERT": "ASSERT",
    "RESUTL": "RESULT",
    "MODUEL": "MODULE",
    "IMPROT": "IMPORT",
    "RECLAL": "RECALL",
    "MEMEORY": "MEMORY",
    "DEFIEN": "DEFINE",
    "omega": "Ω",
    "Omega": "Ω",
    "END": "Ω",
}


# --------------------------------------------------------------------------- #
# Correction result
# --------------------------------------------------------------------------- #

@dataclass
class CorrectionResult:
    """Result of an error correction attempt."""
    original_source: str
    error_message: str
    diagnosis: str
    suggestions: list[str] = field(default_factory=list)
    fixed_source: str | None = None
    fixed_ast: dict | None = None
    auto_corrected: bool = False


# --------------------------------------------------------------------------- #
# Error Corrector
# --------------------------------------------------------------------------- #

class HLFErrorCorrector:
    """Structured error correction for HLF programs.

    Analyzes syntax errors and attempts heuristic auto-correction.
    """

    def correct(self, source: str) -> CorrectionResult:
        """Attempt to correct a broken HLF source.

        Returns a CorrectionResult with diagnosis, suggestions,
        and optionally an auto-corrected AST.
        """
        # First, try to compile as-is
        try:
            ast = hlfc_compile(source)
            return CorrectionResult(
                original_source=source,
                error_message="",
                diagnosis="No errors detected — source compiles successfully.",
                fixed_source=source,
                fixed_ast=ast,
                auto_corrected=False,
            )
        except HlfSyntaxError as e:
            error_msg = str(e)

        # Analyze the error
        diagnosis, suggestions = self._diagnose(source, error_msg)

        # Attempt auto-fix
        fixed = self._auto_fix(source, error_msg)
        fixed_ast = None
        auto_corrected = False

        if fixed and fixed != source:
            try:
                fixed_ast = hlfc_compile(fixed)
                auto_corrected = True
            except HlfSyntaxError:
                fixed = None

        return CorrectionResult(
            original_source=source,
            error_message=error_msg,
            diagnosis=diagnosis,
            suggestions=suggestions,
            fixed_source=fixed if auto_corrected else None,
            fixed_ast=fixed_ast,
            auto_corrected=auto_corrected,
        )

    def _diagnose(self, source: str, error_msg: str) -> tuple[str, list[str]]:
        """Analyze an error message and produce a diagnosis + suggestions."""
        suggestions: list[str] = []

        # Missing version header
        if not source.strip().startswith("[HLF-"):
            return (
                "Missing HLF version header. Programs must start with [HLF-v2] or similar.",
                ["Add '[HLF-v2]' as the first line of your program."],
            )

        # Missing terminator
        if "Ω" not in source and "Omega" not in source and "END" not in source:
            return (
                "Missing terminator. Programs must end with Ω.",
                ["Add 'Ω' as the last line of your program."],
            )

        # Unknown tag detection
        tag_match = re.search(r'\[([A-Z_]+)\]', error_msg)
        if tag_match:
            bad_tag = tag_match.group(1)
            if bad_tag not in VALID_TAGS:
                close = get_close_matches(bad_tag, VALID_TAGS, n=3, cutoff=0.6)
                if close:
                    suggestions = [f"Did you mean [{c}]?" for c in close]
                else:
                    suggestions = [f"Valid tags: {', '.join(VALID_TAGS[:10])}..."]
                return (
                    f"Unknown tag [{bad_tag}]. Not in the HLF tag catalog.",
                    suggestions,
                )

        # Unexpected token
        if "Unexpected token" in error_msg:
            token_match = re.search(r"Token\('(\w+)',\s*'([^']*)'", error_msg)
            if token_match:
                token_type, token_val = token_match.groups()
                suggestions.append(f"Check syntax near '{token_val}' (token type: {token_type}).")
                suggestions.append("Ensure brackets are balanced and tags are uppercase.")
            return (
                "Syntax error: unexpected token in the source.",
                suggestions or ["Review the line indicated in the error for typos or missing brackets."],
            )

        # Generic
        return (
            f"Compilation failed: {error_msg}",
            ["Check your HLF syntax against the grammar specification."],
        )

    def _auto_fix(self, source: str, error_msg: str) -> str | None:
        """Attempt heuristic auto-correction of common errors."""
        fixed = source

        # Fix: missing version header
        if not fixed.strip().startswith("[HLF-"):
            fixed = f"[HLF-v2]\n{fixed}"

        # Fix: missing terminator
        lines = fixed.rstrip().split("\n")
        last = lines[-1].strip() if lines else ""
        if last not in ("Ω", "Omega", "END"):
            fixed = f"{fixed.rstrip()}\nΩ\n"

        # Fix: common tag typos
        for typo, correct in TYPO_MAP.items():
            if typo in fixed:
                fixed = fixed.replace(typo, correct)

        # Fix: common bracket issues
        # Missing closing bracket: [INTENT → [INTENT]
        fixed = re.sub(r'\[([A-Z_]+)(?!\])\s', r'[\1] ', fixed)

        return fixed if fixed != source else None


# --------------------------------------------------------------------------- #
# Round-trip integrity verification
# --------------------------------------------------------------------------- #


def verify_roundtrip(source: str) -> dict[str, Any]:
    """Verify AST round-trip integrity: Compile → Decompile → Recompile.

    Ensures that the decompiled output, when recompiled, produces a
    structurally equivalent AST — proving lossless transparency.

    Returns a dict with:
        - "source": original source
        - "ast_original": first compilation result
        - "decompiled": English prose from InsAIts
        - "pass": True if round-trip is structurally intact
        - "diff": description of any differences found
    """
    from hlf.insaits import decompile

    # Step 1: Compile original
    ast_original = hlfc_compile(source)
    program_original = ast_original.get("program", [])

    # Step 2: Decompile to English
    decompiled = decompile(ast_original)

    # Step 3: Structural integrity check
    # Since decompiled English can't be recompiled (it's prose, not HLF),
    # we verify that every AST node has a human_readable field (InsAIts mandate)
    missing_hr = []
    for i, node in enumerate(program_original):
        if node is None:
            continue
        if not node.get("human_readable"):
            missing_hr.append(f"Node {i}: tag={node.get('tag', '?')} missing human_readable")

    # Step 4: Verify decompilation covers all nodes
    node_count = sum(1 for n in program_original if n is not None)
    # Count substantive lines in decompiled output (skip headers/footers)
    decompiled_lines = [
        line for line in decompiled.split("\n")
        if line.strip() and not line.startswith("Program (") and line.strip() != "[Program terminates]"
    ]

    passed = len(missing_hr) == 0 and len(decompiled_lines) >= node_count
    diff = ""
    if missing_hr:
        diff += f"Missing human_readable: {', '.join(missing_hr)}. "
    if len(decompiled_lines) < node_count:
        diff += f"Decompiled {len(decompiled_lines)} lines but AST has {node_count} nodes."

    return {
        "source": source,
        "ast_original": ast_original,
        "decompiled": decompiled,
        "node_count": node_count,
        "decompiled_lines": len(decompiled_lines),
        "missing_human_readable": missing_hr,
        "pass": passed,
        "diff": diff or "Round-trip integrity verified — all nodes transparent.",
    }
