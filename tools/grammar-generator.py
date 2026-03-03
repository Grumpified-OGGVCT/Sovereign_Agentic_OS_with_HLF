#!/usr/bin/env python3
"""
Grammar generator — generates TextMate grammar from HLS spec.
Wrapper around scripts/generate_tm_grammar.py for tools/ placement.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.generate_tm_grammar import main

if __name__ == "__main__":
    main()
