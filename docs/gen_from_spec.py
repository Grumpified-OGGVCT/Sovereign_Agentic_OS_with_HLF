#!/usr/bin/env python3
"""
Generate documentation from spec files (governance/, pyproject.toml).
Placeholder for Phase 5 doc generation pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent


def generate_api_docs() -> str:
    """Generate API docs from host_functions.json."""
    path = _REPO_ROOT / "governance" / "host_functions.json"
    with path.open() as f:
        data = json.load(f)

    lines = ["# Host Functions API Reference\n"]
    for fn in data.get("functions", []):
        lines.append(f"## {fn['name']}")
        lines.append(f"- **Gas**: {fn['gas']}")
        lines.append(f"- **Tiers**: {', '.join(fn['tier'])}")
        lines.append(f"- **Backend**: {fn['backend']}")
        lines.append(f"- **Sensitive**: {fn['sensitive']}")
        lines.append(f"- **Returns**: {fn['returns']}")
        args = fn.get("args", [])
        if args:
            lines.append("- **Arguments**:")
            for arg in args:
                lines.append(f"  - `{arg['name']}` ({arg['type']})")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    docs = generate_api_docs()
    output = _REPO_ROOT / "docs" / "host_functions_api.md"
    output.write_text(docs)
    print(f"Generated {output}")


if __name__ == "__main__":
    main()
