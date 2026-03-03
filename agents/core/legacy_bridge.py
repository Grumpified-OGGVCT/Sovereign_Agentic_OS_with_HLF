"""
Legacy bridge — decompress HLF payload to REST-style dict.
"""

from __future__ import annotations

import re
from typing import Any


def decompress_hlf_to_rest(hlf_payload: str) -> dict[str, Any]:
    """
    Parse HLF tags, map [INTENT] → action/target, [CONSTRAINT] → body params.
    Stops processing at Ω terminator.
    """
    try:
        result: dict[str, Any] = {"action": None, "target": None, "params": {}, "expect": None}
        for line in hlf_payload.splitlines():
            line = line.strip()
            if line == "Ω" or line == "\\u03a9":
                break
            if line.startswith("[HLF"):
                continue
            m = re.match(r"\[([A-Z_]+)\]\s*(.*)", line)
            if not m:
                continue
            tag, args = m.group(1), m.group(2).strip()
            if tag == "INTENT":
                parts = args.split(None, 1)
                result["action"] = parts[0] if parts else None
                result["target"] = parts[1].strip('"') if len(parts) > 1 else None
            elif tag == "CONSTRAINT":
                kv = re.match(r'(\w+)\s*=\s*"?([^"]*)"?', args)
                if kv:
                    result["params"][kv.group(1)] = kv.group(2)
            elif tag == "EXPECT":
                result["expect"] = args.strip('"')
        return result
    except Exception as exc:
        return {"error": str(exc)}
