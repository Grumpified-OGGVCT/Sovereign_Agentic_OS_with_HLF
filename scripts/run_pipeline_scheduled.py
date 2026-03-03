#!/usr/bin/env python
"""CLI wrapper to run the ollama-matrix-sync pipeline with registry persistence.

Usage::

    python scripts/run_pipeline_scheduled.py [--no-promote]

This is the entry-point used by the GitHub Actions ``pipeline-schedule.yml``
cron workflow (and can also be invoked manually for testing).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure the project root is on sys.path when run directly
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from agents.gateway.matrix_sync.pipeline import run_pipeline_scheduled  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the pipeline with registry persistence.")
    ap.add_argument(
        "--no-promote",
        dest="promote",
        action="store_false",
        default=True,
        help="Skip promoting the new snapshot to active.",
    )
    args = ap.parse_args()
    run_pipeline_scheduled(promote=args.promote)


if __name__ == "__main__":
    main()
