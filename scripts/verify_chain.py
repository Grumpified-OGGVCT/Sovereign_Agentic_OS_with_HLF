#!/usr/bin/env python3
"""
Verify Merkle chain integrity in observability/openllmetry/last_hash.txt.
Reads ALS log entries from stdin (one JSON per line) and validates chain.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_LAST_HASH_FILE = _REPO_ROOT / "observability" / "openllmetry" / "last_hash.txt"


def verify_chain(entries: list[dict]) -> tuple[bool, list[str]]:
    errors = []
    if not entries:
        return True, []

    prev_hash = "0" * 64
    for i, entry in enumerate(entries):
        payload = json.dumps({"event": entry.get("event", ""), "data": entry.get("data", {})}, sort_keys=True)
        expected_trace_id = hashlib.sha256(f"{prev_hash}{payload}".encode()).hexdigest()
        actual_trace_id = entry.get("trace_id", "")
        if actual_trace_id != expected_trace_id:
            errors.append(
                f"Entry {i}: trace_id mismatch. "
                f"Expected {expected_trace_id[:16]}... got {actual_trace_id[:16]}..."
            )
        prev_hash = actual_trace_id if actual_trace_id else expected_trace_id

    return len(errors) == 0, errors


def main() -> None:
    entries = []
    for line in sys.stdin:
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    ok, errors = verify_chain(entries)
    if ok:
        print(f"Chain verified: {len(entries)} entries OK.")
        sys.exit(0)
    else:
        for err in errors:
            print(f"CHAIN_ERROR: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
