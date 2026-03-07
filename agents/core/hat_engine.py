"""
Fourteen Thinking Hats Engine — Automated system self-maintenance framework.

Each hat is a specialized Ollama prompt that analyzes different aspects of
the Sovereign OS.  Results are structured findings with severity, title,
description, and actionable recommendations.

Core Hats (De Bono):
  🔴 Red    — Fail-states, chaos engineering, cascading failures
  ⚫ Black  — Security exploits, ALIGN coverage, injection patterns
  ⚪ White  — Data efficiency, token budget, gas usage, wasted calls
  🟡 Yellow — Synergies, optimization opportunities
  🟢 Green  — Missing mechanisms, creative improvements
  🔵 Blue   — Process audit, spec completeness, internal consistency

Extended Hats (Sovereign OS):
  🟣 Indigo — Cross-feature architecture, pipeline consolidation, DRY
  🩵 Cyan   — Innovation & forward-looking features, feasibility checks
  🟪 Purple — AI safety, compliance, OWASP LLM Top 10, ALIGN coverage
  🟠 Orange — DevOps, CI/CD, Docker, Git state, deployment automation
  🪨 Silver — Context & token optimization, gas math, prompt compression
  💎 Azure  — MCP workflow integrity, tool schema validation, HITL gates
  ✨ Gold   — CoVE v3.0 terminal authority, 12-dimension adversarial QA
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
_OLLAMA_HOST_SECONDARY = os.environ.get("OLLAMA_HOST_SECONDARY", "")
_OLLAMA_SECONDARY_KEY = os.environ.get("OLLAMA_API_KEY_SECONDARY", "")
_OLLAMA_STRATEGY = os.environ.get("OLLAMA_LOAD_STRATEGY", "failover")
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
    severity: str  # CRITICAL | HIGH | MEDIUM | LOW | INFO
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
    error: str | None = None


# ---------------------------------------------------------------------------
# Hat definitions — each hat's system prompt and focus area
# ---------------------------------------------------------------------------

HAT_DEFINITIONS: dict[str, dict] = {
    "red": {
        "emoji": "🔴",
        "name": "Red Hat — Fail-States & Chaos",
        "agent_name": "sentinel",
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
        "agent_name": "scribe",
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
        "agent_name": "arbiter",
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
    "indigo": {
        "emoji": "🟣",
        "name": "Indigo Hat — Cross-Feature Architecture",
        "agent_name": "synthesizer",
        "focus": "Pipeline consolidation, redundant components, macro-level DRY violations, gate fusion",
        "system_prompt": (
            "You are the INDIGO HAT analyst for a Sovereign Agentic OS. "
            "Your role is CROSS-FEATURE SYNTHESIS — find redundancies and consolidation "
            "opportunities across the system's components. Focus on: overlapping "
            "functionality between the 6-gate pipeline stages, redundant data flows "
            "between MoMA routing and the Gateway, opportunities to fuse Dream State "
            "stages, reusable patterns in HLF compilation that could serve multiple "
            "subsystems, and macro-architecture DRY violations where similar logic "
            "exists in multiple agents or services. "
            "For each opportunity, provide: severity (HIGH/MEDIUM/LOW as priority), "
            "a concise title, description of the redundancy, and a consolidation plan. "
            "Output valid JSON array of objects with keys: severity, title, description, recommendation."
        ),
    },
    "cyan": {
        "emoji": "🩵",
        "name": "Cyan Hat — Innovation & Feasibility",
        "agent_name": "scout",
        "focus": "Forward-looking features, HLF extensions, technology validation, feasibility checks",
        "system_prompt": (
            "You are the CYAN HAT analyst for a Sovereign Agentic OS. "
            "Your role is INNOVATION SCOUTING — propose forward-looking features that "
            "are strictly feasible with current technology. Focus on: HLF grammar "
            "extensions (new statement types, string operations, pattern matching), "
            "bytecode VM optimizations for the Phase 5.2 .hlb format, new gate types "
            "for the security pipeline, A2A protocol integration opportunities, "
            "and LoRA fine-tuning strategies for HLF syntax acquisition. "
            "Every suggestion MUST be grounded in production-ready technology — "
            "do NOT hallucinate capabilities that don't exist yet. "
            "For each innovation, provide: severity (HIGH/MEDIUM/LOW as priority), "
            "a concise title, description of the opportunity, and a feasibility assessment. "
            "Output valid JSON array of objects with keys: severity, title, description, recommendation."
        ),
    },
    "purple": {
        "emoji": "🟪",
        "name": "Purple Hat — AI Safety & Compliance",
        "agent_name": "guardian",
        "focus": "OWASP LLM Top 10, ALIGN rule coverage, epistemic modifier abuse, PII leakage",
        "system_prompt": (
            "You are the PURPLE HAT analyst for a Sovereign Agentic OS. "
            "Your role is AI SAFETY & COMPLIANCE — find vulnerabilities specific to "
            "LLM-powered autonomous systems. Focus on: OWASP LLM Top 10 against all "
            "Ollama endpoints, ALIGN rule coverage gaps where agents could bypass "
            "governance, epistemic modifier abuse vectors (agents using [BELIEVE] to "
            "inflate confidence and reduce gas costs), PII leaking through rolling "
            "context or fact store, prompt injection via HLF variable values, "
            "and gas metering evasion through nested [DOUBT] blocks. "
            "For each vulnerability, provide: severity (CRITICAL/HIGH/MEDIUM/LOW), "
            "a concise title, description of the attack/compliance gap, and a specific mitigation. "
            "Output valid JSON array of objects with keys: severity, title, description, recommendation."
        ),
    },
    "orange": {
        "emoji": "🟠",
        "name": "Orange Hat — DevOps & Automation",
        "agent_name": "operator",
        "focus": "CI/CD pipeline health, Docker configuration, Git hygiene, deployment gaps",
        "system_prompt": (
            "You are the ORANGE HAT analyst for a Sovereign Agentic OS. "
            "Your role is DEVOPS AUDIT — verify the operational infrastructure is "
            "correct, automated, and reproducible. Focus on: GitHub Actions workflow "
            "correctness and completeness, Docker container health and resource limits, "
            "Git branch hygiene and orphaned branches, dependency pinning and version "
            "drift, deployment automation gaps, environment variable management, "
            "and build reproducibility (does docker-compose up produce identical results?). "
            "For each issue, provide: severity (CRITICAL/HIGH/MEDIUM/LOW), "
            "a concise title, description of the operational gap, and how to fix it. "
            "Output valid JSON array of objects with keys: severity, title, description, recommendation."
        ),
    },
    "silver": {
        "emoji": "🪨",
        "name": "Silver Hat — Context & Token Optimization",
        "agent_name": "compressor",
        "focus": "Token budgets, gas formula efficiency, context window utilization, prompt compression",
        "system_prompt": (
            "You are the SILVER HAT analyst for a Sovereign Agentic OS. "
            "Your role is TOKEN & CONTEXT OPTIMIZATION — ensure the system uses its "
            "cognitive resources efficiently. Focus on: system prompt sizes across all "
            "agents (are they bloated?), rolling context compression ratios, gas budget "
            "allocation between deployment tiers (hearth=20, forge=100, sovereign=1000), "
            "HLF vs JSON token savings in practice (target 84-86%% compression), "
            "opportunities to cache or deduplicate Ollama calls, context window waste "
            "from redundant system-level injections, and prompt engineering improvements "
            "that reduce token count without losing capability. Examine HLF macro "
            "[DEFINE]/[CALL] reuse patterns and InsAIts V2 human_readable field overhead. "
            "For each optimization, provide: severity (HIGH/MEDIUM/LOW as priority), "
            "a concise title, description of the waste, and a specific compression strategy. "
            "Output valid JSON array of objects with keys: severity, title, description, recommendation."
        ),
    },
    "azure": {
        "emoji": "💎",
        "name": "Azure Hat — MCP Workflow Integrity",
        "agent_name": "steward",
        "focus": (
            "MCP tool lifecycle, parameter schema validation, "
            "workflow ledger, HITL gates, state machine enforcement"
        ),
        "system_prompt": (
            "You are the AZURE HAT analyst for a Sovereign Agentic OS. "
            "Your role is MCP WORKFLOW INTEGRITY — ensure all Model Context Protocol "
            "tool executions are valid, deterministic, and auditable. Focus on: "
            "tool parameter schema validation (are all required params enforced?), "
            "workflow ledger completeness (is every tool execution logged with input/output?), "
            "tool hallucination prevention (can agents invoke tools not in their capsule "
            "allowed_tools set?), deterministic tool-to-tool data flow (are outputs from "
            "one tool correctly piped as inputs to the next?), Human-In-The-Loop gates "
            "for irreversible operations (DELETE, financial transactions, privilege escalation), "
            "state machine enforcement (are tool execution sequences valid per workflow spec?), "
            "and Model Matrix integration (does the CatalogService correctly route tool "
            "requests to available models?). Examine HLF [TOOL] statements (↦ τ), "
            "IntentCapsule.allowed_tools enforcement, and host_function_dispatcher.py dispatch logic. "
            "For each issue, provide: severity (CRITICAL/HIGH/MEDIUM/LOW), "
            "a concise title, description of the integrity gap, and a specific fix. "
            "Output valid JSON array of objects with keys: severity, title, description, recommendation."
        ),
    },
    # Hat #13 intentionally skipped
    "gold": {
        "emoji": "✨",
        "name": "Gold Hat — CoVE Terminal Authority",
        "agent_name": "cove",
        "focus": (
            "12-dimension adversarial QA: functional, security, data integrity, "
            "AI safety, a11y, performance, resilience, compliance, i18n, "
            "observability, infra, supply chain"
        ),
        "system_prompt": (
            "You are the GOLD HAT — the Final QA CoVE (Comprehensive Validation Engineer) "
            "and TERMINAL AUTHORITY for the Sovereign Agentic OS. You are the last line of "
            "defense before production. You have master-level proficiency across the full stack. "
            "Your mandate: DISMANTLE — assume every line of code contains a failure mode, every "
            "integration a cascade potential, and every agent is simultaneously malicious and compromised. "
            "You validate across 12 dimensions: "
            "1) Functional Correctness — trace every HLF statement handler in hlfrun.py, "
            "2) Security (Zero Trust) — OWASP Top 10 2025 + LLM Top 10 against 6-gate pipeline, "
            "3) Data Integrity — ALIGN Ledger hash chains, Infinite RAG dedup, SQLite WAL, "
            "4) AI Safety & Alignment — epistemic modifier abuse, BELIEVE inflation, capsule bypass, "
            "5) Accessibility — InsAIts V2 human_readable on every AST node, "
            "6) Performance Under Duress — gas metering thread safety under parallel (∥) execution, "
            "7) Resilience/Anti-Fragility — Dead Man's Switch, Redis failover, hot cache races, "
            "8) Regulatory Compliance — EU AI Act Art.52 transparency via InsAIts, GDPR right-to-erasure, "
            "9) Internationalization — Unicode NFKC normalization, homoglyph attack prevention, "
            "10) Observability — Merkle chain logging, hat finding persistence, dream cycle telemetry, "
            "11) Infrastructure Hardening — Docker healthchecks, dependency pinning, container security, "
            "12) Supply Chain Provenance — ALIGN Ledger cryptographic hashes, content_hash dedup. "
            "For CRITICAL findings (launch blockers): cite file:line, business impact, regulatory risk. "
            "For HIGH findings: provide mitigation workaround. Rate severity ruthlessly. "
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
                f"=== DB STATS ===\nRolling context rows: {row_count}\nFact store entries: {fact_count}"
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
                context_parts.append("=== RECENT DREAM CYCLES ===\n" + "\n".join(lines))
        except Exception:
            pass

    return "\n\n".join(context_parts) if context_parts else "No system context available."


# ---------------------------------------------------------------------------
# Agent registry — loads named agent profiles from config/agent_registry.json
# ---------------------------------------------------------------------------

_agent_registry_cache: dict | None = None


def _load_agent_registry() -> dict:
    """Load agent profiles from config/agent_registry.json.

    Returns a dict keyed by agent name (e.g. 'sentinel') with model,
    provider, tier, and restrictions fields.  Cached after first load.
    """
    global _agent_registry_cache
    if _agent_registry_cache is not None:
        return _agent_registry_cache

    from pathlib import Path

    registry_path = Path(os.environ.get("BASE_DIR", ".")) / "config" / "agent_registry.json"
    if not registry_path.exists():
        logger.warning("agent_registry.json not found — agents will use defaults")
        _agent_registry_cache = {}
        return _agent_registry_cache

    try:
        data = json.loads(registry_path.read_text())
        _agent_registry_cache = data.get("hat_agents", {})
        logger.info(f"Loaded {len(_agent_registry_cache)} agent profiles from registry")
        return _agent_registry_cache
    except Exception as exc:
        logger.error(f"Failed to load agent_registry.json: {exc}")
        _agent_registry_cache = {}
        return _agent_registry_cache


def _call_ollama(
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    restrictions: dict | None = None,
) -> str:
    """Call Ollama /api/chat for hat analysis.

    If *restrictions* is provided (from an agent profile), temperature
    and max_tokens are applied to the request.
    """
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

    # Apply agent profile restrictions if available
    options = {}
    if restrictions:
        if "temperature" in restrictions:
            options["temperature"] = restrictions["temperature"]
        if "max_tokens" in restrictions:
            options["num_ctx"] = restrictions["max_tokens"]

    payload_dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
    }
    if options:
        payload_dict["options"] = options

    payload = json.dumps(payload_dict).encode()

    # Build endpoint list based on load strategy
    endpoints: list[tuple[str, dict[str, str]]] = [(_OLLAMA_HOST, {})]
    if _OLLAMA_HOST_SECONDARY:
        sec_hdrs: dict[str, str] = {}
        if _OLLAMA_SECONDARY_KEY:
            sec_hdrs["Authorization"] = f"Bearer {_OLLAMA_SECONDARY_KEY}"
        if _OLLAMA_STRATEGY == "round_robin":
            import random

            if random.random() > 0.5:
                endpoints = [(_OLLAMA_HOST_SECONDARY, sec_hdrs), (_OLLAMA_HOST, {})]
            else:
                endpoints.append((_OLLAMA_HOST_SECONDARY, sec_hdrs))
        elif _OLLAMA_STRATEGY != "primary_only":
            endpoints.append((_OLLAMA_HOST_SECONDARY, sec_hdrs))

    last_error = None
    for host, extra_headers in endpoints:
        req = urllib.request.Request(
            f"{host}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json", **extra_headers},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode())
                logger.info(f"Hat analysis used Ollama endpoint: {host}")
                return data.get("message", {}).get("content", "")
        except Exception as e:
            logger.warning(f"Ollama endpoint {host} failed: {e}")
            last_error = e
    logger.error(f"All Ollama endpoints failed for hat analysis: {last_error}")
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
        text = text[start : end + 1]

    try:
        items = json.loads(text)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    findings.append(
                        HatFinding(
                            hat=hat_name,
                            severity=item.get("severity", "MEDIUM"),
                            title=item.get("title", "Untitled"),
                            description=item.get("description", ""),
                            recommendation=item.get("recommendation", ""),
                        )
                    )
    except json.JSONDecodeError:
        # If JSON parsing fails, create a single finding with the raw text
        logger.warning(f"Could not parse JSON from {hat_name} hat response")
        findings.append(
            HatFinding(
                hat=hat_name,
                severity="INFO",
                title=f"{hat_name.title()} Hat Analysis",
                description=raw_response[:500],
                recommendation="Review raw analysis output manually.",
            )
        )

    return findings


def run_hat(
    hat_name: str,
    conn: sqlite3.Connection | None = None,
    model: str | None = None,
) -> HatReport:
    """Run a single hat analysis and return its report.

    If the hat has a named agent in `agent_registry.json`, its model,
    provider, and restrictions are loaded automatically.  The *model*
    parameter still overrides if explicitly passed.
    """
    hat_def = HAT_DEFINITIONS.get(hat_name)
    if hat_def is None:
        return HatReport(
            hat=hat_name,
            emoji="❓",
            focus="unknown",
            error=f"Unknown hat: {hat_name}",
        )

    # Resolve agent profile from registry (if this hat has a named agent)
    agent_name = hat_def.get("agent_name")
    registry = _load_agent_registry()
    agent_profile = registry.get(agent_name, {}) if agent_name else {}

    # Agent profile provides model + restrictions; explicit param overrides
    effective_model = model or agent_profile.get("model")
    restrictions = agent_profile.get("restrictions", {})

    if agent_profile:
        logger.info(
            f"  Agent '{agent_name}' loaded: "
            f"model={agent_profile.get('model')}, "
            f"provider={agent_profile.get('provider')}, "
            f"temp={restrictions.get('temperature', 'default')}"
        )

    system_context = _build_system_context(conn)
    user_prompt = (
        f"Analyze the following Sovereign Agentic OS state and "
        f"identify issues from your {hat_def['name']} perspective.\n\n"
        f"Focus area: {hat_def['focus']}\n\n"
        f"{system_context}\n\n"
        f"Return your findings as a JSON array."
    )

    raw = _call_ollama(
        hat_def["system_prompt"],
        user_prompt,
        model=effective_model,
        restrictions=restrictions,
    )
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
        logger.info(f"  {report.emoji} {hat_name}: {len(report.findings)} findings")

    return reports


def persist_findings(
    conn: sqlite3.Connection,
    dream_cycle_id: int,
    reports: list[HatReport],
) -> int:
    """Save hat findings to the hat_findings table. Returns count saved."""
    count = 0
    for report in reports:
        for finding in report.findings:
            conn.execute(
                "INSERT INTO hat_findings "
                "(dream_cycle_id, hat, severity, title, description, recommendation, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    dream_cycle_id,
                    finding.hat,
                    finding.severity,
                    finding.title,
                    finding.description,
                    finding.recommendation,
                    finding.timestamp,
                ),
            )
            count += 1
    conn.commit()
    return count


def get_recent_findings(
    conn: sqlite3.Connection,
    limit: int = 20,
) -> list[dict]:
    """Fetch most recent hat findings for GUI display."""
    try:
        rows = conn.execute(
            "SELECT hat, severity, title, description, recommendation, resolved, timestamp "
            "FROM hat_findings ORDER BY id DESC LIMIT ?",
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
