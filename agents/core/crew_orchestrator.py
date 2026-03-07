"""
Crew Orchestrator — Multi-Persona Round-Robin Synthesis Engine.

Manages collaborative analysis sessions across named agents (Sentinel,
Scribe, Arbiter, Steward, CoVE, Palette, Consolidator).  Each persona
makes its own independent API call — no prompt sharing.

Invocation patterns:
    # Single persona analysis:
    result = run_persona("sentinel", topic="Review SSRF defenses")

    # Crew discussion (all personas, then consolidation):
    report = run_crew(topic="Pre-launch security audit", conn=db_conn)

    # Selective crew (subset of personas):
    report = run_crew(topic="UX review", personas=["palette", "cove"])

    # Dream Mode integration — deep analysis pass:
    report = run_crew_deep(topic="Full system audit", conn=db_conn)
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Agent registry loading
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, Any] | None = None


def _load_registry() -> dict[str, Any]:
    """Load agent definitions from agent_registry.json.

    Named agents live inside the 'hat_agents' section alongside hat-color
    entries.  We filter to only return agents that have a string 'role' field
    (hat-only entries use color names like 'blue', 'red').
    """
    global _REGISTRY
    if _REGISTRY is not None:
        return _REGISTRY

    base = Path(os.environ.get("BASE_DIR", "."))
    registry_path = base / "config" / "agent_registry.json"
    if registry_path.exists():
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        # Named agents coexist with hat-color entries in hat_agents
        all_agents = data.get("hat_agents", data.get("named_agents", {}))
        _REGISTRY = all_agents
    else:
        _REGISTRY = {}
        logger.warning(f"Agent registry not found at {registry_path}")
    return _REGISTRY


def reload_registry() -> None:
    """Force reload of agent registry (for hot-reloading)."""
    global _REGISTRY
    _REGISTRY = None
    _load_registry()


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class PersonaResponse:
    """Single persona's analysis output."""
    persona: str
    role: str
    hat: str
    model: str
    content: str
    timestamp: float = field(default_factory=time.time)
    duration_seconds: float = 0.0
    token_estimate: int = 0


@dataclass
class ConsolidationReport:
    """Consolidator's synthesis of all persona responses."""
    agreements: list[str] = field(default_factory=list)
    disagreements: list[str] = field(default_factory=list)
    evidence_gaps: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    confidence: float = 0.0
    raw_synthesis: str = ""


@dataclass
class CrewReport:
    """Complete crew discussion output."""
    topic: str
    personas_used: list[str] = field(default_factory=list)
    responses: list[PersonaResponse] = field(default_factory=list)
    consolidation: ConsolidationReport | None = None
    total_duration: float = 0.0
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Persona system prompts
# ---------------------------------------------------------------------------

def _load_persona_prompt_file(agent_id: str) -> str | None:
    """Load the full unreduced persona prompt from config/personas/{agent_id}.md.

    These files contain the complete, specification-grade system prompts
    for each persona — no summarization, no reduction from the originals.
    """
    base = Path(os.environ.get("BASE_DIR", "."))
    prompt_path = base / "config" / "personas" / f"{agent_id}.md"
    if prompt_path.exists():
        content = prompt_path.read_text(encoding="utf-8")
        logger.info(f"Loaded full persona prompt for {agent_id} ({len(content)} chars)")
        return content
    return None


# Cache for shared mandates (loaded once, reused for every persona)
_SHARED_MANDATES: str | None = None


def _load_shared_mandates() -> str:
    """Load the universal operating mandates that apply to ALL personas.

    Returns the content of config/personas/_shared_mandates.md. These mandates
    include the Anti-Reductionism Protocol, Backup-Before-Modify, Triple-Verification
    import removal, evidence-based reasoning, and other non-negotiable directives.
    Cached after first load.
    """
    global _SHARED_MANDATES
    if _SHARED_MANDATES is not None:
        return _SHARED_MANDATES
    base = Path(os.environ.get("BASE_DIR", "."))
    mandates_path = base / "config" / "personas" / "_shared_mandates.md"
    if mandates_path.exists():
        _SHARED_MANDATES = mandates_path.read_text(encoding="utf-8")
        logger.info(f"Loaded shared mandates ({len(_SHARED_MANDATES)} chars)")
    else:
        _SHARED_MANDATES = ""
        logger.warning(f"Shared mandates file not found at {mandates_path}")
    return _SHARED_MANDATES


def _build_persona_prompt(agent_id: str, topic: str, prior_responses: list[PersonaResponse] | None = None) -> str:
    """Build the system prompt for a persona, including cross-awareness context.

    Priority:
      1. Shared mandates (config/personas/_shared_mandates.md) — ALWAYS injected first
      2. Full prompt file from config/personas/{agent_id}.md (unreduced)
      3. Fallback: generated from registry metadata (condensed)

    Cross-awareness context and prior responses are always appended.
    """
    registry = _load_registry()
    agent = registry.get(agent_id, {})

    # UNIVERSAL PREAMBLE: Shared mandates are injected into EVERY persona
    mandates = _load_shared_mandates()
    prompt_parts = []
    if mandates:
        prompt_parts.append(mandates)
        prompt_parts.append("\n---\n")

    # Priority 1: Load the full unreduced prompt file
    full_prompt = _load_persona_prompt_file(agent_id)

    if full_prompt:
        prompt_parts.append(full_prompt)
    else:
        # Priority 2: Build from registry metadata (condensed fallback)
        role = agent.get("role", agent_id.title())
        description = agent.get("description", "")
        skills = agent.get("hard_skills", [])
        mapping = agent.get("sovereign_os_mapping", "")

        prompt_parts = [
            f"You are **{role}** in the Sovereign Agentic OS.",
            "",
            f"**Your mandate:** {description}",
            "",
            "**Your hard skills:**",
        ]
        for skill in skills:
            prompt_parts.append(f"- {skill}")

        if mapping:
            prompt_parts.extend(["", f"**Codebase scope:** {mapping}"])

    # Always append cross-awareness context (enriches both file and fallback)
    cross_aware = agent.get("cross_awareness", [])
    if cross_aware:
        aware_names = []
        for ca in cross_aware:
            ca_agent = registry.get(ca, {})
            ca_role = ca_agent.get("role", ca.title())
            aware_names.append(f"- **{ca}**: {ca_role}")
        prompt_parts.extend([
            "",
            "**Cross-awareness — you are context-aware of these collaborators:**",
            *aware_names,
            "",
            "You may reference their domains, suggest they investigate further,",
            "or flag disagreements with their perspectives.",
        ])

    # If prior responses exist (round-robin), include summaries
    if prior_responses:
        prompt_parts.extend([
            "",
            "---",
            "**Prior perspectives from the crew (reference, do NOT repeat):**",
        ])
        for pr in prior_responses:
            # Truncate long responses to keep context efficient
            summary = pr.content[:800] + "..." if len(pr.content) > 800 else pr.content
            prompt_parts.append(f"\n**{pr.persona}** ({pr.role}):\n{summary}")

    # Append structured output rules (only if using fallback — full prompts have their own)
    if not full_prompt:
        prompt_parts.extend([
            "",
            "---",
            "**Rules:**",
            "1. Respond ONLY within your domain expertise",
            "2. Be adversarial — assume everything is broken until proven otherwise",
            "3. Cite specific files, functions, or code patterns when possible",
            "4. Rate each finding as CRITICAL / HIGH / MEDIUM / LOW / INFO",
            "5. Provide actionable, concrete recommendations (not vague advice)",
            "6. If you disagree with a prior persona's assessment, state it explicitly",
            "7. Format your response as structured JSON:",
            "",
            "```json",
            '[{"severity": "HIGH", "title": "...", "description": "...", "recommendation": "..."}]',
            "```",
        ])

    return "\n".join(prompt_parts)


def _build_consolidator_prompt(topic: str, responses: list[PersonaResponse]) -> str:
    """Build the Consolidator's synthesis prompt."""
    registry = _load_registry()
    consolidator = registry.get("consolidator", {})

    prompt_parts = [
        f"You are **{consolidator.get('role', 'Consolidator')}** in the Sovereign Agentic OS.",
        "",
        f"**Your mandate:** {consolidator.get('description', '')}",
        "",
        "**You have received the following perspectives on this topic:**",
        f"**Topic:** {topic}",
        "",
    ]

    for resp in responses:
        prompt_parts.append(f"### {resp.persona} ({resp.role})")
        prompt_parts.append(resp.content)
        prompt_parts.append("")

    prompt_parts.extend([
        "---",
        "**Your task:**",
        "1. Identify AGREEMENTS — findings where 2+ personas converge",
        "2. Identify DISAGREEMENTS — findings where personas contradict",
        "3. Identify EVIDENCE GAPS — questions no persona addressed",
        "4. Produce PRIORITIZED RECOMMENDATIONS — ranked by cross-persona consensus",
        "5. Assign a CONFIDENCE SCORE (0.0 — 1.0) based on coverage completeness",
        "",
        "**Format your response as JSON:**",
        "```json",
        '{',
        '  "agreements": ["..."],',
        '  "disagreements": ["..."],',
        '  "evidence_gaps": ["..."],',
        '  "recommendations": ["..."],',
        '  "confidence": 0.85,',
        '  "executive_summary": "..."',
        '}',
        "```",
    ])

    return "\n".join(prompt_parts)


# ---------------------------------------------------------------------------
# Ollama interface – reuses hat_engine's _call_ollama
# ---------------------------------------------------------------------------

def _call_persona(system_prompt: str, user_prompt: str, agent_id: str) -> str:
    """Call Ollama for a single persona.  Each gets its own API call."""
    registry = _load_registry()
    agent = registry.get(agent_id, {})
    model = agent.get("model")
    restrictions = agent.get("restrictions")

    # Reuse hat_engine's _call_ollama for consistency
    try:
        from agents.core.hat_engine import _call_ollama
        return _call_ollama(system_prompt, user_prompt, model=model, restrictions=restrictions)
    except ImportError:
        logger.error("Cannot import hat_engine._call_ollama — persona call failed")
        return ""


# ---------------------------------------------------------------------------
# Core orchestration functions
# ---------------------------------------------------------------------------

# Default persona order — intentionally structured for progressive analysis
# Order follows the deliberation chain: research → plan → analyze → validate → meta
DEFAULT_PERSONA_ORDER = [
    "scout",        # Research first — external intelligence, new findings
    "strategist",   # Plan next — prioritize based on scout intelligence
    "sentinel",     # Security — find attack surfaces early
    "palette",      # UX — find usability/accessibility issues
    "catalyst",     # Performance — profile bottlenecks & latency budgets
    "cove",         # CoVE — adversarial 12-dimension validation
    "oracle",       # Predictions — model second-order effects of findings
    "steward",      # MCP integrity — tool workflow validation
    "scribe",       # Token/gas accounting — resource audit
    "chronicler",   # Tech debt — codebase health & drift tracking
    "herald",       # Documentation — doc-code accuracy & knowledge freshness
    "weaver",       # Meta-agent — prompt optimization & HLF self-improvement
    "arbiter",      # Governance — ALIGN rule adjudication
    "consolidator", # ALWAYS LAST — synthesizes all perspectives
]


def run_persona(
    agent_id: str,
    topic: str,
    prior_responses: list[PersonaResponse] | None = None,
) -> PersonaResponse:
    """Run a single persona's analysis.  Own API call, no prompt sharing.

    Args:
        agent_id: Registry key (e.g. "sentinel", "palette", "cove")
        topic: What to analyze
        prior_responses: Optional prior persona outputs for cross-awareness

    Returns:
        PersonaResponse with the persona's analysis
    """
    registry = _load_registry()
    agent = registry.get(agent_id, {})

    if not agent:
        logger.error(f"Persona '{agent_id}' not found in registry")
        return PersonaResponse(
            persona=agent_id, role="Unknown", hat="unknown",
            model="none", content=f"ERROR: Persona '{agent_id}' not registered"
        )

    system_prompt = _build_persona_prompt(agent_id, topic, prior_responses)
    user_prompt = f"Analyze the following topic from your specialist perspective:\n\n{topic}"

    start = time.time()
    content = _call_persona(system_prompt, user_prompt, agent_id)
    duration = time.time() - start

    response = PersonaResponse(
        persona=agent_id,
        role=agent.get("role", agent_id.title()),
        hat=agent.get("hat", "unknown"),
        model=agent.get("model", "unknown"),
        content=content,
        duration_seconds=round(duration, 2),
        token_estimate=len(content.split()),  # Rough estimate
    )

    logger.info(
        f"Persona {agent_id} completed in {duration:.1f}s "
        f"(~{response.token_estimate} tokens)"
    )
    return response


def run_crew(
    topic: str,
    personas: list[str] | None = None,
    conn: sqlite3.Connection | None = None,
    round_robin: bool = True,
) -> CrewReport:
    """Run a full crew discussion with optional consolidation.

    Each persona makes its own independent API call.  If round_robin is True,
    each subsequent persona receives summaries of prior responses for
    cross-awareness (the SUCE pattern).

    Args:
        topic: Subject of the crew discussion
        personas: Subset of personas to use (default: all in DEFAULT_PERSONA_ORDER)
        conn: Optional DB connection for persistence
        round_robin: If True, pass prior responses to each new persona

    Returns:
        CrewReport with all responses and optional consolidation
    """
    start = time.time()

    if personas is None:
        active_personas = DEFAULT_PERSONA_ORDER.copy()
    else:
        # Ensure consolidator is always last if included
        active_personas = [p for p in personas if p != "consolidator"]
        if "consolidator" in personas or personas is None:
            active_personas.append("consolidator")

    responses: list[PersonaResponse] = []
    report = CrewReport(topic=topic, personas_used=active_personas)

    # Phase 1: Run specialist personas (each gets own API call)
    for agent_id in active_personas:
        if agent_id == "consolidator":
            continue  # Consolidator runs last in Phase 2

        prior = responses if round_robin else None
        response = run_persona(agent_id, topic, prior_responses=prior)
        responses.append(response)
        report.responses.append(response)

    # Phase 2: Consolidation pass (Consolidator synthesizes everything)
    if "consolidator" in active_personas and responses:
        consolidation = _run_consolidation(topic, responses)
        report.consolidation = consolidation

        # Also add the raw consolidator response as a persona response
        consolidator_response = PersonaResponse(
            persona="consolidator",
            role="Consolidator — Multi-Agent Synthesis",
            hat="silver",
            model=_load_registry().get("consolidator", {}).get("model", "unknown"),
            content=consolidation.raw_synthesis,
        )
        report.responses.append(consolidator_response)

    report.total_duration = round(time.time() - start, 2)

    # Persist to database if connection provided
    if conn:
        _persist_crew_report(conn, report)

    logger.info(
        f"Crew discussion completed: {len(responses)} personas, "
        f"{report.total_duration:.1f}s total"
    )
    return report


def run_crew_deep(
    topic: str,
    conn: sqlite3.Connection | None = None,
) -> CrewReport:
    """Run a deep crew analysis — all personas + round-robin + consolidation.

    This is the maximum-effort mode: every persona runs, each sees prior
    responses, and the Consolidator produces a full synthesis.
    """
    return run_crew(
        topic=topic,
        personas=None,  # All personas
        conn=conn,
        round_robin=True,
    )


# ---------------------------------------------------------------------------
# Consolidation engine
# ---------------------------------------------------------------------------

def _run_consolidation(topic: str, responses: list[PersonaResponse]) -> ConsolidationReport:
    """Run the Consolidator persona to synthesize all perspectives."""
    system_prompt = _build_consolidator_prompt(topic, responses)
    user_prompt = (
        "Synthesize all perspectives above.  Identify agreements, "
        "disagreements, evidence gaps, and produce prioritized "
        "recommendations.  Be ruthlessly honest about confidence."
    )

    start = time.time()
    content = _call_persona(system_prompt, user_prompt, "consolidator")
    duration = time.time() - start

    # Attempt to parse structured consolidation
    report = ConsolidationReport(raw_synthesis=content)
    try:
        # Try to extract JSON from the response
        json_match = _extract_json(content)
        if json_match:
            data = json.loads(json_match)
            report.agreements = data.get("agreements", [])
            report.disagreements = data.get("disagreements", [])
            report.evidence_gaps = data.get("evidence_gaps", [])
            report.recommendations = data.get("recommendations", [])
            report.confidence = float(data.get("confidence", 0.0))
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logger.warning(f"Could not parse consolidation JSON: {e}")
        # Raw synthesis is still available in report.raw_synthesis

    logger.info(
        f"Consolidation completed in {duration:.1f}s — "
        f"confidence={report.confidence:.2f}, "
        f"{len(report.agreements)} agreements, "
        f"{len(report.disagreements)} disagreements, "
        f"{len(report.evidence_gaps)} gaps"
    )
    return report


def _extract_json(text: str) -> str | None:
    """Extract JSON from a response that may contain markdown wrapping."""
    # Check for ```json ... ``` blocks
    import re
    pattern = re.compile(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", re.MULTILINE)
    match = pattern.search(text)
    if match:
        return match.group(1).strip()

    # Try bare JSON object
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start_idx = text.find(start_char)
        end_idx = text.rfind(end_char)
        if start_idx != -1 and end_idx > start_idx:
            return text[start_idx : end_idx + 1]

    return None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _ensure_crew_tables(conn: sqlite3.Connection) -> None:
    """Create crew orchestration tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS crew_discussions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            topic TEXT NOT NULL,
            personas_used TEXT NOT NULL,
            total_duration REAL DEFAULT 0,
            consolidation_confidence REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS crew_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discussion_id INTEGER REFERENCES crew_discussions(id),
            persona TEXT NOT NULL,
            role TEXT NOT NULL,
            hat TEXT NOT NULL,
            model TEXT NOT NULL,
            content TEXT NOT NULL,
            duration_seconds REAL DEFAULT 0,
            timestamp REAL NOT NULL
        );
    """)


def _persist_crew_report(conn: sqlite3.Connection, report: CrewReport) -> int:
    """Persist a crew discussion to the database."""
    _ensure_crew_tables(conn)

    cursor = conn.execute(
        "INSERT INTO crew_discussions (timestamp, topic, personas_used, "
        "total_duration, consolidation_confidence) VALUES (?, ?, ?, ?, ?)",
        (
            report.timestamp,
            report.topic,
            json.dumps(report.personas_used),
            report.total_duration,
            report.consolidation.confidence if report.consolidation else 0.0,
        ),
    )
    discussion_id = cursor.lastrowid

    for resp in report.responses:
        conn.execute(
            "INSERT INTO crew_responses (discussion_id, persona, role, hat, "
            "model, content, duration_seconds, timestamp) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?)",
            (
                discussion_id,
                resp.persona,
                resp.role,
                resp.hat,
                resp.model,
                resp.content,
                resp.duration_seconds,
                resp.timestamp,
            ),
        )

    conn.commit()
    logger.info(f"Persisted crew discussion #{discussion_id} with {len(report.responses)} responses")
    return discussion_id


def get_recent_crew_discussions(
    conn: sqlite3.Connection,
    limit: int = 5,
) -> list[dict]:
    """Retrieve recent crew discussions from the database."""
    _ensure_crew_tables(conn)

    rows = conn.execute(
        "SELECT id, timestamp, topic, personas_used, total_duration, "
        "consolidation_confidence FROM crew_discussions "
        "ORDER BY timestamp DESC LIMIT ?",
        (limit,),
    ).fetchall()

    return [
        {
            "id": row[0],
            "timestamp": row[1],
            "topic": row[2],
            "personas_used": json.loads(row[3]),
            "total_duration": row[4],
            "consolidation_confidence": row[5],
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def list_personas() -> dict[str, dict]:
    """Return all registered personas with their metadata."""
    registry = _load_registry()
    return {
        agent_id: {
            "role": agent.get("role"),
            "hat": agent.get("hat"),
            "description": agent.get("description"),
            "cross_awareness": agent.get("cross_awareness", []),
            "model": agent.get("model"),
        }
        for agent_id, agent in registry.items()
    }


def get_cross_awareness_graph() -> dict[str, list[str]]:
    """Return the cross-awareness graph showing how personas are linked."""
    registry = _load_registry()
    return {
        agent_id: agent.get("cross_awareness", [])
        for agent_id, agent in registry.items()
    }


def get_system_status() -> dict[str, Any]:
    """Full observability snapshot — everything inspectable in one call.

    Returns:
        Dict with: persona_roster, cross_awareness_graph, persona_order,
        prompt_file_status, hat_to_persona_map, registry_hash
    """
    import hashlib

    registry = _load_registry()
    base = Path(os.environ.get("BASE_DIR", "."))
    personas_dir = base / "config" / "personas"

    # Persona roster with prompt file status
    roster = {}
    for agent_id, agent in registry.items():
        prompt_file = personas_dir / f"{agent_id}.md"
        has_full_prompt = prompt_file.exists()
        prompt_size = prompt_file.stat().st_size if has_full_prompt else 0

        roster[agent_id] = {
            "role": agent.get("role", "Unknown"),
            "hat": agent.get("hat", "unknown"),
            "model": agent.get("model", "unknown"),
            "cross_awareness": agent.get("cross_awareness", []),
            "has_full_prompt_file": has_full_prompt,
            "prompt_file_size_bytes": prompt_size,
            "hard_skills_count": len(agent.get("hard_skills", [])),
            "tier": agent.get("tier", "unknown"),
            "restrictions": agent.get("restrictions", {}),
        }

    # Registry hash for integrity verification
    registry_path = base / "config" / "agent_registry.json"
    registry_hash = ""
    if registry_path.exists():
        content = registry_path.read_bytes()
        registry_hash = hashlib.sha256(content).hexdigest()[:16]

    # Hat-to-persona mapping
    hat_map = {}
    for agent_id, agent in registry.items():
        hat = agent.get("hat", "unknown")
        if hat not in hat_map:
            hat_map[hat] = []
        hat_map[hat].append(agent_id)

    # Prompt file hashes for those that exist
    prompt_hashes = {}
    if personas_dir.exists():
        for pf in personas_dir.glob("*.md"):
            content = pf.read_bytes()
            prompt_hashes[pf.stem] = hashlib.sha256(content).hexdigest()[:16]

    return {
        "persona_roster": roster,
        "cross_awareness_graph": get_cross_awareness_graph(),
        "default_persona_order": DEFAULT_PERSONA_ORDER,
        "hat_to_persona_map": hat_map,
        "registry_hash": registry_hash,
        "prompt_file_hashes": prompt_hashes,
        "total_personas": len(roster),
        "personas_with_full_prompts": sum(
            1 for r in roster.values() if r["has_full_prompt_file"]
        ),
    }


# ---------------------------------------------------------------------------
# CLI entrypoint — system inspection
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys  # noqa: F401 — pre-staged for CLI arg handling

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    status = get_system_status()

    print("=" * 72)
    print("  SOVEREIGN OS — CREW ORCHESTRATOR STATUS")
    print("=" * 72)
    print()
    print(f"  Registry hash:  {status['registry_hash']}")
    print(f"  Total personas: {status['total_personas']}")
    print(f"  Full prompts:   {status['personas_with_full_prompts']}")
    print(f"  Persona order:  {' → '.join(status['default_persona_order'])}")
    print()

    print("  PERSONA ROSTER")
    print("  " + "-" * 68)
    for agent_id, meta in status["persona_roster"].items():
        prompt_indicator = "📄" if meta["has_full_prompt_file"] else "📋"
        size = f"({meta['prompt_file_size_bytes']}b)" if meta["has_full_prompt_file"] else "(registry)"
        print(f"  {prompt_indicator} {agent_id:<14} "
              f"hat={meta['hat']:<8} "
              f"model={meta['model']:<20} "
              f"skills={meta['hard_skills_count']:<3} "
              f"{size}")

    print()
    print("  CROSS-AWARENESS GRAPH")
    print("  " + "-" * 68)
    for agent_id, links in status["cross_awareness_graph"].items():
        if links:
            print(f"  {agent_id:<14} ← → {', '.join(links)}")

    print()
    print("  PROMPT FILE HASHES")
    print("  " + "-" * 68)
    for name, h in status["prompt_file_hashes"].items():
        print(f"  {name:<14} sha256={h}")

    print()
    print("=" * 72)

