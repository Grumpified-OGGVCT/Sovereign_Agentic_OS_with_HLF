"""
Sentinel Gate — deterministic, non-LLM ALIGN Ledger enforcer.
Loads ALIGN_LEDGER.yaml at startup and compiles all regex_block patterns.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

_LEDGER_PATH = Path(__file__).parent.parent.parent / "governance" / "ALIGN_LEDGER.yaml"

_compiled_rules: list[dict] = []


def _load_ledger() -> None:
    global _compiled_rules
    if not _LEDGER_PATH.exists():
        return
    with _LEDGER_PATH.open() as f:
        ledger = yaml.safe_load(f)
    rules = ledger.get("rules", [])
    compiled = []
    for rule in rules:
        entry = dict(rule)
        if "regex_block" in rule:
            entry["_pattern"] = re.compile(rule["regex_block"])
        compiled.append(entry)
    _compiled_rules = compiled


_load_ledger()


def enforce_align(payload: str | dict) -> tuple[bool, str]:
    """
    Scan *payload* against compiled ALIGN rules.
    If payload is an AST dict, it is dumped to JSON for structural regex checks.
    Returns (blocked: bool, rule_id: str).
    """
    text_to_scan = json.dumps(payload) if isinstance(payload, dict) else str(payload)

    for rule in _compiled_rules:
        pattern: re.Pattern | None = rule.get("_pattern")
        if pattern and pattern.search(text_to_scan):
            return True, rule.get("id", "unknown")
    return False, ""


class LLMJudge:
    """
    Evaluates proposed file diffs against ALIGN constraints before committing.
    Deterministic gate — does NOT call an LLM itself; validates structure only.
    """

    def evaluate(self, diff: str) -> tuple[bool, str]:
        blocked, rule_id = enforce_align(diff)
        return not blocked, rule_id if blocked else ""
