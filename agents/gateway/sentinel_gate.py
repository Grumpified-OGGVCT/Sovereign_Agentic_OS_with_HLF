"""
Sentinel Gate — deterministic, non-LLM ALIGN Ledger enforcer.
Loads ALIGN_LEDGER.yaml at startup and compiles all regex_block patterns.

All regex patterns are compiled with re.IGNORECASE to close case-variation
bypass vectors (e.g. EVAL(), SUDO, EXEC()).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import NamedTuple

import yaml

_LEDGER_PATH = Path(__file__).parent.parent.parent / "governance" / "ALIGN_LEDGER.yaml"
_logger = logging.getLogger("sovereign.sentinel_gate")

_compiled_rules: list[dict] = []


def _load_ledger(path: Path | None = None) -> None:
    """Load and compile ALIGN_LEDGER rules from *path* (defaults to the
    canonical governance ledger).  Raises ``RuntimeError`` on YAML parse
    failure so misconfiguration is surfaced immediately rather than silently
    leaving the gate unarmed.
    """
    global _compiled_rules
    target = path or _LEDGER_PATH
    if not target.exists():
        _logger.warning("ALIGN_LEDGER not found at %s — gate will pass all traffic", target)
        _compiled_rules = []
        return
    try:
        with target.open(encoding="utf-8") as f:
            ledger = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise RuntimeError(f"Failed to parse ALIGN_LEDGER at {target}: {exc}") from exc
    rules = ledger.get("rules", []) if isinstance(ledger, dict) else []
    compiled = []
    for rule in rules:
        entry = dict(rule)
        if "regex_block" in rule:
            try:
                # IGNORECASE closes case-variation bypass vectors (e.g. EVAL(), SUDO)
                entry["_pattern"] = re.compile(rule["regex_block"], re.IGNORECASE)
            except re.error as exc:
                _logger.error("Invalid regex in rule %s: %s — rule skipped", rule.get("id"), exc)
                continue
        compiled.append(entry)
    _compiled_rules = compiled
    _logger.debug("ALIGN_LEDGER loaded: %d rules from %s", len(_compiled_rules), target)


_load_ledger()


def reload_ledger(path: Path | None = None) -> int:
    """Hot-reload the ALIGN Ledger from *path* (or the canonical path).

    Returns the number of rules loaded.  Useful for tests and runtime
    rule updates without a full process restart.
    """
    _load_ledger(path)
    return len(_compiled_rules)


def get_loaded_rules() -> list[dict]:
    """Return a read-only snapshot of the currently compiled rules.

    Each entry is a plain ``dict`` with keys from the YAML rule (``id``,
    ``name``, ``action``, ``regex_block``, …) but *without* the internal
    ``_pattern`` key, making the result safe to serialise / log.
    """
    return [
        {k: v for k, v in rule.items() if k != "_pattern"}
        for rule in _compiled_rules
    ]


class AlignVerdict(NamedTuple):
    """Rich result from :func:`enforce_align_with_action`."""

    blocked: bool
    rule_id: str
    action: str  # e.g. "DROP", "DROP_AND_QUARANTINE", "ROUTE_TO_HUMAN_APPROVAL"


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


def enforce_align_with_action(payload: str | dict) -> AlignVerdict:
    """Extended version of :func:`enforce_align` that also returns the
    ALIGN *action* (e.g. ``DROP_AND_QUARANTINE``) so callers can make
    fine-grained routing decisions.

    Returns an :class:`AlignVerdict` named tuple.
    """
    text_to_scan = json.dumps(payload) if isinstance(payload, dict) else str(payload)

    for rule in _compiled_rules:
        pattern: re.Pattern | None = rule.get("_pattern")
        if pattern and pattern.search(text_to_scan):
            return AlignVerdict(
                blocked=True,
                rule_id=rule.get("id", "unknown"),
                action=rule.get("action", "DROP"),
            )
    return AlignVerdict(blocked=False, rule_id="", action="")


class LLMJudge:
    """
    Evaluates proposed file diffs against ALIGN constraints before committing.
    Deterministic gate — does NOT call an LLM itself; validates structure only.
    """

    def evaluate(self, diff: str) -> tuple[bool, str]:
        blocked, rule_id = enforce_align(diff)
        return not blocked, rule_id if blocked else ""
