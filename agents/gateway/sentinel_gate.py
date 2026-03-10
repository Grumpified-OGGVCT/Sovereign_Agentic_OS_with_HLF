"""
Sentinel Gate — deterministic, non-LLM ALIGN Ledger enforcer.
Loads ALIGN_LEDGER.yaml at startup and compiles all regex_block patterns.

Scout additions (additive):
  - DependencyRiskEntry: represents a known-risky dependency pattern.
  - _DEPENDENCY_RISK_PATTERNS: catalog of risky/known-bad import signatures.
  - _THREAT_PATTERNS: additional threat signatures (SSRF, path traversal).
  - scan_for_dependency_risks(): Scout dependency scan.
  - get_threat_summary(): returns a summary dict of all known rules + threats.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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


# ─── Scout: Dependency Risk Scanning ────────────────────────────────────────


@dataclass
class DependencyRiskEntry:
    """A detected risky dependency or import pattern."""

    pattern_id: str
    description: str
    severity: str          # "critical" | "high" | "medium" | "low"
    matched_text: str = ""
    recommendation: str = ""


# Catalog of known-risky import/usage patterns for Scout dependency scanning.
# Each entry: (pattern_id, regex, description, severity, recommendation)
_DEPENDENCY_RISK_PATTERNS: list[tuple[str, re.Pattern[str], str, str, str]] = [
    (
        "DEP-001",
        re.compile(r"(?i)\bpickle\b"),
        "Pickle deserialization — arbitrary code execution risk",
        "high",
        "Replace pickle with json or msgpack for safe serialization.",
    ),
    (
        "DEP-002",
        re.compile(r"(?i)\byaml\.load\s*\((?![^)]*Loader\s*=)"),
        "yaml.load() without Loader= — arbitrary code execution",
        "critical",
        "Use yaml.safe_load() or pass Loader=yaml.SafeLoader.",
    ),
    (
        "DEP-003",
        re.compile(r"(?i)\bxml\.etree\b|\bElementTree\b|\bfrom\s+xml\b"),
        "XML parsing — potential XXE (XML External Entity) attack surface",
        "medium",
        "Use defusedxml to prevent XXE attacks.",
    ),
    (
        "DEP-004",
        re.compile(r"(?i)\bassert\s+\w"),
        "assert statement in production code — bypassed with -O flag",
        "low",
        "Replace assert with explicit if/raise for production guards.",
    ),
    (
        "DEP-005",
        re.compile(r"(?i)\bsha1\b|\bmd5\b|\brc4\b|\bdes\b"),
        "Weak/deprecated cryptographic algorithm",
        "high",
        "Use SHA-256 or higher; AES-256-GCM for symmetric encryption.",
    ),
    (
        "DEP-006",
        re.compile(r"(?i)\brandom\.random\b|\brandom\.randint\b"),
        "Non-cryptographically secure RNG used for potential security context",
        "medium",
        "Use secrets module or os.urandom() for security-sensitive randomness.",
    ),
    (
        "DEP-007",
        re.compile(r"(?i)\btelnetlib\b|\bftplib\b"),
        "Plaintext-protocol library — credentials transmitted in clear",
        "high",
        "Use SSH (paramiko) or SFTP; avoid telnet/FTP in production.",
    ),
    (
        "DEP-008",
        re.compile(r"(?i)\bos\.makedirs\s*\(|\bos\.chmod\s*\([^)]*0o?7[0-7][0-7]"),
        "Overly permissive file/directory creation",
        "medium",
        "Restrict directory permissions; use 0o700 or tighter.",
    ),
]

# Additional threat patterns for Scout threat intelligence
# (complement _INJECTION_PATTERNS in sentinel daemon)
_THREAT_PATTERNS: list[tuple[str, re.Pattern[str], str, str]] = [
    (
        "THREAT-SSRF",
        re.compile(
            r"(?i)(https?://(?:169\.254\.169\.254|metadata\.internal|"
            r"localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|::1))"
        ),
        "SSRF — request targeting metadata/loopback address",
        "critical",
    ),
    (
        "THREAT-PATH-TRAVERSAL",
        re.compile(r"(?i)(\.\./\.\./|\.\.\\\.\.\\|%2e%2e%2f|%252e%252e)"),
        "Path traversal attempt",
        "critical",
    ),
    (
        "THREAT-OPEN-REDIRECT",
        re.compile(r"(?i)(?:redirect|url|next|return_url)\s*[=:]\s*https?://(?!localhost)"),
        "Potential open-redirect parameter",
        "medium",
    ),
    (
        "THREAT-TEMPLATE-INJECTION",
        re.compile(r"(?i)(\$\{[^}]+\}|#\{[^}]+\}|\{\{[^}]+\}\}|<%[^%]+%>)"),
        "Template injection pattern",
        "high",
    ),
    (
        "THREAT-SSTI",
        re.compile(r"(?i)(jinja2|tornado\.template|mako\.template|chameleon)"),
        "Server-Side Template Injection library in payload",
        "medium",
    ),
]


def scan_for_dependency_risks(text: str) -> list[DependencyRiskEntry]:
    """
    Scout dependency scan — check *text* for known-risky dependency patterns.

    Returns a list of :class:`DependencyRiskEntry` for each match found.
    Multiple matches of the same pattern are deduplicated (one entry per
    pattern_id).
    """
    findings: list[DependencyRiskEntry] = []
    seen_ids: set[str] = set()

    for pattern_id, compiled, description, severity, recommendation in _DEPENDENCY_RISK_PATTERNS:
        if pattern_id in seen_ids:
            continue
        match = compiled.search(text)
        if match:
            seen_ids.add(pattern_id)
            findings.append(
                DependencyRiskEntry(
                    pattern_id=pattern_id,
                    description=description,
                    severity=severity,
                    matched_text=match.group()[:200],
                    recommendation=recommendation,
                )
            )

    return findings


def scan_for_threats(text: str) -> list[dict[str, Any]]:
    """
    Scout threat-intelligence scan — check *text* for threat patterns beyond
    the ALIGN Ledger (SSRF, path traversal, open-redirect, template injection).

    Returns a list of dicts with keys: threat_id, description, severity, matched.
    """
    findings: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for threat_id, compiled, description, severity in _THREAT_PATTERNS:
        if threat_id in seen_ids:
            continue
        match = compiled.search(text)
        if match:
            seen_ids.add(threat_id)
            findings.append(
                {
                    "threat_id": threat_id,
                    "description": description,
                    "severity": severity,
                    "matched": match.group()[:200],
                }
            )

    return findings


def get_threat_summary() -> dict[str, Any]:
    """
    Return a Scout-level summary of all active threat detection capabilities:
    ALIGN rules loaded, dependency risk patterns, and threat patterns.
    """
    return {
        "align_rules": [
            {
                "id": r.get("id", "unknown"),
                "name": r.get("name", ""),
                "action": r.get("action", ""),
                "has_regex": "_pattern" in r,
            }
            for r in _compiled_rules
        ],
        "dependency_risk_patterns": len(_DEPENDENCY_RISK_PATTERNS),
        "threat_patterns": len(_THREAT_PATTERNS),
        "total_detectors": len(_compiled_rules) + len(_DEPENDENCY_RISK_PATTERNS) + len(_THREAT_PATTERNS),
    }
