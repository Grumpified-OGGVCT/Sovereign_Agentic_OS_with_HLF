"""
Six Thinking Hats Engine — Automated system self-maintenance framework.

Each hat is a specialized Ollama prompt that analyzes different aspects of
the Sovereign OS.  Results are structured findings with severity, title,
description, and actionable recommendations.

Hats:
  🔴 Red   — Fail-states, chaos engineering, cascading failures
  ⚫ Black — Security exploits, ALIGN coverage, injection patterns
  ⚪ White — Data efficiency, token budget, gas usage, wasted calls
  🟡 Yellow — Synergies, optimization opportunities
  🟢 Green — Missing mechanisms, creative improvements
  🔵 Blue  — Process audit, spec completeness, internal consistency
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)

_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
# Normalize host — add scheme if missing
if _OLLAMA_HOST and not _OLLAMA_HOST.startswith("http"):
    _OLLAMA_HOST = f"http://{_OLLAMA_HOST}"
if "0.0.0.0" in _OLLAMA_HOST:
    _OLLAMA_HOST = _OLLAMA_HOST.replace("0.0.0.0", "localhost")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class HatFinding:
    hat: str
    severity: str        # CRITICAL | HIGH | MEDIUM | LOW | INFO
    title: str
    description: str
    recommendation: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class HatReport:
    hat: str
    emoji: str
    focus: str
    findings: list[HatFinding] = field(default_factory=list)
    raw_response: str = ""
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Hat definitions — each hat's system prompt and focus area
# ---------------------------------------------------------------------------

HAT_DEFINITIONS: dict[str, dict] = {
    "red": {
        "emoji": "🔴",
        "name": "Red Hat — Fail-States & Chaos",
        "focus": "Cascading failures, service crashes, database locking, single points of failure",
        "system_prompt": (
            "You are the RED HAT analyst for a Sovereign Agentic OS. "
            "Your role is CHAOS ENGINEERING — find every way the system can break. "
            "Focus on: cascading failures between services, database locking under "
            "concurrent writes, single points of failure, resource exhaustion, "
            "and what happens when any individual service (Redis, Ollama, Gateway) dies. "
            "For each issue found, provide: severity (CRITICAL/HIGH/MEDIUM/LOW), "
            "a concise title, description of the failure mode, and a specific fix. "
            "Output valid JSON array of objects with keys: severity, title, description, recommendation."
        ),
    },
    "black": {
        "emoji": "⚫",
        "name": "Black Hat — Security Exploits",
        "focus": "Prompt injection, ALIGN bypass, data exfiltration, privilege escalation",
        "system_prompt": (
            "You are the BLACK HAT analyst for a Sovereign Agentic OS. "
            "Your role is ADVERSARIAL EXPLOITATION — find security vulnerabilities. "
            "Focus on: prompt injection via user inputs, ALIGN rule bypass techniques, "
            "data exfiltration through side channels, privilege escalation between "
            "deployment tiers, and unencrypted sensitive data flows. "
            "For each vulnerability, provide: severity (CRITICAL/HIGH/MEDIUM/LOW), "
            "a concise title, description of the attack vector, and a specific mitigation. "
            "Output valid JSON array of objects with keys: severity, title, description, recommendation."
        ),
    },
    "white": {
        "emoji": "⚪",
        "name": "White Hat — Efficiency & Resources",
        "focus": "Token usage, gas budgets, wasted LLM calls, context sizes, DB bloat",
        "system_prompt": (
            "You are the WHITE HAT analyst for a Sovereign Agentic OS. "
            "Your role is EFFICIENCY AUDITING — find waste and optimize resources. "
            "Focus on: unnecessary LLM API calls, bloated prompts, excessive token usage, "
            "gas budget imbalances between tiers, database growth patterns, "
            "and opportunities to cache or memoize repeated operations. "
            "For each issue, provide: severity (CRITICAL/HIGH/MEDIUM/LOW), "
            "a concise title, description of the waste, and a specific optimization. "
            "Output valid JSON array of objects with keys: severity, title, description, recommendation."
        ),
    },
    "yellow": {
        "emoji": "🟡",
        "name": "Yellow Hat — Synergies & Optimization",
        "focus": "Cross-component synergies, hidden powers, 10x improvements",
        "system_prompt": (
            "You are the YELLOW HAT analyst for a Sovereign Agentic OS. "
            "Your role is SYNERGY DISCOVERY — find opportunities to combine existing "
            "components for dramatically better results. Focus on: combining the "
            "Dream State with other subsystems, zero-shot caching opportunities, "
            "model downshift intelligence, reusing existing data flows for new purposes, "
            "and quick wins that multiply capability. "
            "For each opportunity, provide: severity (HIGH/MEDIUM/LOW as priority), "
            "a concise title, description of the synergy, and how to implement it. "
            "Output valid JSON array of objects with keys: severity, title, description, recommendation."
        ),
    },
    "green": {
        "emoji": "🟢",
        "name": "Green Hat — Evolution & Missing Mechanisms",
        "focus": "Missing operational wiring, growth paths, emergent behaviors",
        "system_prompt": (
            "You are the GREEN HAT analyst for a Sovereign Agentic OS. "
            "Your role is GAP ANALYSIS — find missing operational mechanisms. "
            "Focus on: absent shutdown procedures, missing health-check contracts, "
            "no error recovery for critical paths, missing persistence configurations, "
            "growth mechanisms the system needs as it scales, and paths toward "
            "autonomous self-improvement. "
            "For each gap, provide: severity (CRITICAL/HIGH/MEDIUM/LOW), "
            "a concise title, description of what is missing, and how to add it. "
            "Output valid JSON array of objects with keys: severity, title, description, recommendation."
        ),
    },
    "blue": {
        "emoji": "🔵",
        "name": "Blue Hat — Process & Completeness",
        "focus": "Internal consistency, spec completeness, documentation accuracy",
        "system_prompt": (
            "You are the BLUE HAT analyst for a Sovereign Agentic OS. "
            "Your role is META-AUDIT — verify internal consistency and completeness. "
            "Focus on: referenced files that don't exist, dependency ordering issues, "
            "config values that contradict each other, documented features that aren't "
            "implemented, implemented features that aren't documented, and test coverage gaps. "
            "For each inconsistency, provide: severity (HIGH/MEDIUM/LOW), "
            "a concise title, description of the inconsistency, and how to resolve it. "
            "Output valid JSON array of objects with keys: severity, title, description, recommendation."
        ),
    },
}


def _build_system_context(conn: sqlite3.Connection | None = None) -> str:
    """
    Gather live system context that hats will analyze.
    Returns a structured summary of current system state.
    """
    from pathlib import Path

    base_dir = Path(os.environ.get("BASE_DIR", "."))
    context_parts = []

    # 1. ALIGN rules
    align_path = base_dir / "governance" / "align_ledger.yaml"
    if align_path.exists():
        context_parts.append(f"=== ALIGN RULES ===\n{align_path.read_text()}")

    # 2. Settings
    settings_path = base_dir / "config" / "settings.json"
    if settings_path.exists():
        context_parts.append(f"=== SETTINGS ===\n{settings_path.read_text()}")

    # 3. Host functions registry
    hf_path = base_dir / "governance" / "host_functions.json"
    if hf_path.exists():
        context_parts.append(f"=== HOST FUNCTIONS ===\n{hf_path.read_text()}")

    # 4. Recent rolling context stats (if DB available)
    if conn is not None:
        try:
            row_count = conn.execute("SELECT COUNT(*) FROM rolling_context").fetchone()[0]
            fact_count = conn.execute("SELECT COUNT(*) FROM fact_store").fetchone()[0]
            context_parts.append(
                f"=== DB STATS ===\n"
                f"Rolling context rows: {row_count}\n"
                f"Fact store entries: {fact_count}"
            )
        except Exception:
            pass

    # 5. Recent dream results
    if conn is not None:
        try:
            recent = conn.execute(
                "SELECT timestamp, cycle_type, hlf_practiced, hlf_passed, summary "
                "FROM dream_results ORDER BY timestamp DESC LIMIT 3"
            ).fetchall()
            if recent:
                lines = []
                for r in recent:
                    lines.append(f"  [{r[1]}] practiced={r[2]} passed={r[3]}: {r[4] or 'N/A'}")
                context_parts.append(f"=== RECENT DREAM CYCLES ===\n" + "\n".join(lines))
        except Exception:
            pass

    return "\n\n".join(context_parts) if context_parts else "No system context available."


def _call_ollama(system_prompt: str, user_prompt: str, model: str | None = None) -> str:
    """Call Ollama /api/chat for hat analysis."""
    if model is None:
        # Read from settings if available
        try:
            from pathlib import Path
            settings_path = Path(os.environ.get("BASE_DIR", ".")) / "config" / "settings.json"
            if settings_path.exists():
                settings = json.loads(settings_path.read_text())
                model = settings.get("dream_mode", {}).get("analysis_model", "kimi-k2.5:cloud")
            else:
                model = "kimi-k2.5:cloud"
        except Exception:
            model = "kimi-k2.5:cloud"

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
    }).encode()

    req = urllib.request.Request(
        f"{_OLLAMA_HOST}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
            return data.get("message", {}).get("content", "")
    except Exception as e:
        logger.error(f"Hat analysis Ollama call failed: {e}")
        return ""


def _parse_findings(hat_name: str, raw_response: str) -> list[HatFinding]:
    """Parse Ollama response JSON into HatFinding objects."""
    findings = []
    if not raw_response.strip():
        return findings

    # Try to extract JSON from the response (may be wrapped in markdown)
    text = raw_response.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    # Find JSON array boundaries
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        text = text[start:end + 1]

    try:
        items = json.loads(text)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    findings.append(HatFinding(
                        hat=hat_name,
                        severity=item.get("severity", "MEDIUM"),
                        title=item.get("title", "Untitled"),
                        description=item.get("description", ""),
                        recommendation=item.get("recommendation", ""),
                    ))
    except json.JSONDecodeError:
        # If JSON parsing fails, create a single finding with the raw text
        logger.warning(f"Could not parse JSON from {hat_name} hat response")
        findings.append(HatFinding(
            hat=hat_name,
            severity="INFO",
            title=f"{hat_name.title()} Hat Analysis",
            description=raw_response[:500],
            recommendation="Review raw analysis output manually.",
        ))

    return findings


def run_hat(
    hat_name: str,
    conn: sqlite3.Connection | None = None,
    model: str | None = None,
) -> HatReport:
    """Run a single hat analysis and return its report."""
    hat_def = HAT_DEFINITIONS.get(hat_name)
    if hat_def is None:
        return HatReport(
            hat=hat_name, emoji="❓", focus="unknown",
            error=f"Unknown hat: {hat_name}",
        )

    system_context = _build_system_context(conn)
    user_prompt = (
        f"Analyze the following Sovereign Agentic OS state and "
        f"identify issues from your {hat_def['name']} perspective.\n\n"
        f"Focus area: {hat_def['focus']}\n\n"
        f"{system_context}\n\n"
        f"Return your findings as a JSON array."
    )

    raw = _call_ollama(hat_def["system_prompt"], user_prompt, model=model)
    findings = _parse_findings(hat_name, raw)

    return HatReport(
        hat=hat_name,
        emoji=hat_def["emoji"],
        focus=hat_def["focus"],
        findings=findings,
        raw_response=raw,
    )


def run_all_hats(
    conn: sqlite3.Connection | None = None,
    hats: list[str] | None = None,
    model: str | None = None,
) -> list[HatReport]:
    """Run all (or specified) hats and return their reports."""
    if hats is None:
        hats = list(HAT_DEFINITIONS.keys())

    reports = []
    for hat_name in hats:
        logger.info(f"Running {hat_name} hat analysis...")
        report = run_hat(hat_name, conn=conn, model=model)
        reports.append(report)
        logger.info(
            f"  {report.emoji} {hat_name}: "
            f"{len(report.findings)} findings"
        )

    return reports


def persist_findings(
    conn: sqlite3.Connection,
    dream_cycle_id: int,
    reports: list[HatReport],
) -> int:
    """Save hat findings to the hat_findings table. Returns count saved."""

    # Batch inserts using executemany to avoid N+1 query overhead for better performance
    rows_to_insert = [
        (
            dream_cycle_id,
            finding.hat,
            finding.severity,
            finding.title,
            finding.description,
            finding.recommendation,
            finding.timestamp,
        )
        for report in reports
        for finding in report.findings
    ]

    if not rows_to_insert:
        return 0

    conn.executemany(
        "INSERT INTO hat_findings "
        "(dream_cycle_id, hat, severity, title, description, recommendation, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows_to_insert,
    )
    conn.commit()
    return len(rows_to_insert)


def get_recent_findings(
    conn: sqlite3.Connection,
    limit: int = 20,
) -> list[dict]:
    """Fetch most recent hat findings for GUI display."""
    try:
        rows = conn.execute(
            "SELECT hat, severity, title, description, recommendation, resolved, timestamp "
            "FROM hat_findings ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "hat": r[0],
                "severity": r[1],
                "title": r[2],
                "description": r[3],
                "recommendation": r[4],
                "resolved": bool(r[5]),
                "timestamp": r[6],
            }
            for r in rows
        ]
    except Exception:
        return []
