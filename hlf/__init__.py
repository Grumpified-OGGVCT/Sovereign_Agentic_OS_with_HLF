"""
HLF (Hieroglyphic Logic Framework) package.
Exports validate_hlf() for gateway middleware.
"""
from __future__ import annotations

import re

_TAG_RE = re.compile(r"^\s*\[[A-Z_]+\]")
_SPECIAL_LINES = {"Ω", "[HLF-v2]", ""}


def validate_hlf(line: str) -> bool:
    r"""
    Returns True if *line* is a valid HLF line:
    - Empty / whitespace-only lines
    - Version header [HLF-v2]
    - Terminator Ω
    - Any tag line matching ^\s*\[[A-Z_]+\]
    """
    stripped = line.strip()
    if stripped in _SPECIAL_LINES or stripped == "\u03a9":
        return True
    return bool(_TAG_RE.match(line))
