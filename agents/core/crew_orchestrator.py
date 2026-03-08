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

import hashlib
import hmac
import json
import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
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

# ---------------------------------------------------------------------------
# SDD Lifecycle — Spec-Driven Development (Instinct integration)
# ---------------------------------------------------------------------------


class SDDPhase(Enum):
    """Spec-Driven Development lifecycle phases — strictly ordered."""
    SPECIFY = 1
    PLAN = 2
    EXECUTE = 3
    VERIFY = 4
    MERGE = 5


# Ordered phase list for validation
_SDD_PHASE_ORDER = list(SDDPhase)


@dataclass
class SDDSession:
    """Tracks the state of a Spec-Driven Development mission.

    Attributes:
        phase: Current lifecycle phase
        topic: Mission objective / topic
        spec: The living spec (compiled HLF AST or dict) created in SPECIFY
        task_dag: Decomposed tasks from PLAN phase
        verification_report: CoVE's adversarial check result from VERIFY
        phase_history: Ordered list of (phase, timestamp, notes) transitions
        responses: Accumulated persona responses across all phases
        sealed: Whether the mission has been completed (MERGE phase reached)
    """
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    phase: SDDPhase = SDDPhase.SPECIFY
    topic: str = ""
    spec: dict | None = None
    task_dag: list[dict] = field(default_factory=list)
    verification_report: dict | None = None
    phase_history: list[dict] = field(default_factory=list)
    realignment_events: list[dict] = field(default_factory=list)
    responses: list[PersonaResponse] = field(default_factory=list)
    sealed: bool = False

    def advance_to(self, target: SDDPhase, *, override: bool = False, notes: str = "") -> None:
        """Advance to the next phase with validation.

        Raises:
            ValueError: If the transition is invalid (skip or backward)
                       unless override=True.
        """
        if self.sealed:
            raise ValueError("SDD session is sealed — mission already merged")
        current_idx = _SDD_PHASE_ORDER.index(self.phase)
        target_idx = _SDD_PHASE_ORDER.index(target)

        if target_idx < current_idx and not override:
            raise ValueError(
                f"SDD backward transition {self.phase.name} → {target.name} "
                f"not allowed without override=True"
            )
        if target_idx > current_idx + 1 and not override:
            raise ValueError(
                f"SDD phase skip {self.phase.name} → {target.name} "
                f"not allowed — must progress sequentially"
            )

        self.phase_history.append({
            "from": self.phase.name,
            "to": target.name,
            "timestamp": time.time(),
            "notes": notes,
            "override": override,
        })
        self.phase = target

        if target == SDDPhase.MERGE:
            self.sealed = True

    def realign(self, event: SDDRealignmentEvent) -> None:
        """Record a re-alignment event and log to ALIGN ledger.

        Re-alignment events occur when an agent discovers a change
        during execution that requires updating the spec (e.g.,
        deprecated API, missing endpoint, new constraint).
        """
        if self.sealed:
            raise ValueError("Cannot realign a sealed session")

        self.realignment_events.append({
            "triggered_by": event.triggered_by,
            "change_type": event.change_type,
            "change_description": event.change_description,
            "affected_nodes": event.affected_nodes,
            "timestamp": event.timestamp,
        })

        # Update spec with re-alignment data
        if self.spec is not None:
            self.spec.setdefault("_realignments", []).append({
                "by": event.triggered_by,
                "type": event.change_type,
                "desc": event.change_description,
                "ts": event.timestamp,
            })

        self.phase_history.append({
            "from": self.phase.name,
            "to": self.phase.name,
            "timestamp": event.timestamp,
            "notes": f"REALIGNMENT: {event.change_type} — {event.change_description}",
            "override": False,
        })

        _sdd_log_transition(
            self.topic, self.phase.name, self.phase.name,
            f"realignment: {event.change_type}",
        )

    def to_dict(self) -> dict:
        """Serialize the session for persistence or transport."""
        return {
            "session_id": self.session_id,
            "phase": self.phase.name,
            "topic": self.topic,
            "spec": self.spec,
            "task_dag": self.task_dag,
            "verification_report": self.verification_report,
            "phase_history": self.phase_history,
            "realignment_events": self.realignment_events,
            "sealed": self.sealed,
            "response_count": len(self.responses),
        }

    @classmethod
    def from_dict(cls, data: dict) -> SDDSession:
        """Deserialize a session from a persisted dict."""
        session = cls(
            session_id=data.get("session_id", uuid.uuid4().hex[:12]),
            phase=SDDPhase[data["phase"]],
            topic=data.get("topic", ""),
            spec=data.get("spec"),
            task_dag=data.get("task_dag", []),
            verification_report=data.get("verification_report"),
            phase_history=data.get("phase_history", []),
            realignment_events=data.get("realignment_events", []),
            sealed=data.get("sealed", False),
        )
        return session


@dataclass
class SDDRealignmentEvent:
    """A mid-mission spec re-alignment event.

    Triggered when an agent discovers something that requires
    updating the Living Spec (deprecated library, missing API,
    new constraint discovered during execution).

    Attributes:
        triggered_by: Agent or persona that triggered the event.
        change_type: Category of change (e.g., 'deprecated_api',
                     'missing_endpoint', 'new_constraint').
        change_description: Human-readable description of the change.
        affected_nodes: List of DAG node_ids affected by this change.
        timestamp: When the re-alignment was triggered.
    """
    triggered_by: str
    change_type: str
    change_description: str
    affected_nodes: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


@dataclass
class ValidationToken:
    """Cryptographic gate token for SDD MERGE phase.

    Issued by the VERIFY phase when all checks pass. The MERGE
    phase requires a valid token before proceeding. The token
    contains an HMAC signature over the session state to prevent
    tampering.

    Attributes:
        session_id: ID of the session this token validates.
        spec_hash: SHA-256 hash of the spec at verification time.
        tests_passed: Whether all tests passed.
        lint_clean: Whether linting is clean.
        cove_approved: Whether CoVE adversarial review approved.
        issued_at: Timestamp of issuance.
        signature: HMAC-SHA256 of the token contents.
    """

    session_id: str
    spec_hash: str
    tests_passed: bool
    lint_clean: bool
    cove_approved: bool
    issued_at: float = field(default_factory=time.time)
    signature: str = ""

    _TOKEN_SECRET = b"instinct-sovereign-os-validation-key"

    def sign(self) -> None:
        """Compute HMAC signature over the token contents."""
        payload = (
            f"{self.session_id}:{self.spec_hash}:"
            f"{self.tests_passed}:{self.lint_clean}:"
            f"{self.cove_approved}:{self.issued_at}"
        )
        self.signature = hmac.new(
            self._TOKEN_SECRET,
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()

    def verify(self) -> bool:
        """Verify the HMAC signature is valid."""
        if not self.signature:
            return False
        payload = (
            f"{self.session_id}:{self.spec_hash}:"
            f"{self.tests_passed}:{self.lint_clean}:"
            f"{self.cove_approved}:{self.issued_at}"
        )
        expected = hmac.new(
            self._TOKEN_SECRET,
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(self.signature, expected)

    def is_valid(self) -> bool:
        """Check if the token is valid (signed + all checks passed)."""
        return (
            self.verify()
            and self.tests_passed
            and self.lint_clean
            and self.cove_approved
        )

    def to_dict(self) -> dict:
        """Serialize for persistence."""
        return {
            "session_id": self.session_id,
            "spec_hash": self.spec_hash,
            "tests_passed": self.tests_passed,
            "lint_clean": self.lint_clean,
            "cove_approved": self.cove_approved,
            "issued_at": self.issued_at,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ValidationToken:
        """Deserialize from persistence."""
        return cls(
            session_id=data["session_id"],
            spec_hash=data["spec_hash"],
            tests_passed=data["tests_passed"],
            lint_clean=data["lint_clean"],
            cove_approved=data["cove_approved"],
            issued_at=data.get("issued_at", time.time()),
            signature=data.get("signature", ""),
        )


class SDDSessionStore:
    """SQLite-backed persistence for SDD sessions.

    Allows missions to survive process restarts by saving session
    state to a local database. Auto-saves after each phase transition.

    Usage::

        store = SDDSessionStore(db_path="sdd_sessions.db")
        store.init_schema()

        session = SDDSession(topic="auth upgrade")
        store.save(session)

        # Later or after restart:
        restored = store.load(session.session_id)
        active = store.list_active()
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create SQLite connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def init_schema(self) -> None:
        """Create the sdd_sessions table."""
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sdd_sessions (
                session_id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                phase TEXT NOT NULL,
                sealed INTEGER NOT NULL DEFAULT 0,
                session_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sdd_sealed
                ON sdd_sessions(sealed)
        """)
        conn.commit()

    def save(self, session: SDDSession) -> None:
        """Save or update an SDD session."""
        conn = self._get_conn()
        now = time.time()
        session_json = json.dumps(session.to_dict(), sort_keys=True)

        conn.execute("""
            INSERT INTO sdd_sessions
                (session_id, topic, phase, sealed, session_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                phase = excluded.phase,
                sealed = excluded.sealed,
                session_json = excluded.session_json,
                updated_at = excluded.updated_at
        """, (
            session.session_id, session.topic, session.phase.name,
            1 if session.sealed else 0, session_json, now, now,
        ))
        conn.commit()

    def load(self, session_id: str) -> SDDSession | None:
        """Load a session by ID. Returns None if not found."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT session_json FROM sdd_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()

        if not row:
            return None

        data = json.loads(row["session_json"])
        return SDDSession.from_dict(data)

    def list_active(self) -> list[dict]:
        """List all unsealed (active) sessions."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT session_id, topic, phase, updated_at
               FROM sdd_sessions
               WHERE sealed = 0
               ORDER BY updated_at DESC""",
        ).fetchall()
        return [
            {
                "session_id": r["session_id"],
                "topic": r["topic"],
                "phase": r["phase"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]

    def list_all(self) -> list[dict]:
        """List all sessions (active and sealed)."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT session_id, topic, phase, sealed, updated_at
               FROM sdd_sessions
               ORDER BY updated_at DESC""",
        ).fetchall()
        return [
            {
                "session_id": r["session_id"],
                "topic": r["topic"],
                "phase": r["phase"],
                "sealed": bool(r["sealed"]),
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]

    def delete(self, session_id: str) -> bool:
        """Delete a session. Returns True if found and deleted."""
        conn = self._get_conn()
        result = conn.execute(
            "DELETE FROM sdd_sessions WHERE session_id = ?",
            (session_id,),
        )
        conn.commit()
        return result.rowcount > 0

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None


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


def run_sdd_mission(
    topic: str,
    conn: sqlite3.Connection | None = None,
) -> SDDSession:
    """Run a full Spec-Driven Development mission.

    Enforces the lifecycle: SPECIFY → PLAN → EXECUTE → VERIFY → MERGE.
    Each phase uses a specific subset of personas. CoVE gates the
    VERIFY → MERGE transition — if CoVE rejects, the mission halts.

    Args:
        topic: The mission objective (what to build/change)
        conn: Optional DB connection for persistence

    Returns:
        SDDSession with the full audit trail
    """
    session = SDDSession(topic=topic)
    _sdd_log_transition(session, "INIT", "SPECIFY", "Mission started")

    # ── Phase 1: SPECIFY ─────────────────────────────────────────────
    # Green Hat generates a spec from the user's intent
    specify_response = run_persona("strategist", f"[SDD-SPECIFY] Create a living specification for: {topic}")
    session.responses.append(specify_response)
    session.spec = {
        "topic": topic,
        "raw_spec": specify_response.content,
        "phase": "SPECIFY",
        "timestamp": time.time(),
    }
    session.advance_to(SDDPhase.PLAN, notes="Spec generated by strategist")
    _sdd_log_transition(session, "SPECIFY", "PLAN", "Spec generated")

    # ── Phase 2: PLAN ────────────────────────────────────────────────
    # Blue/Indigo decompose spec into task DAG
    plan_prompt = (
        f"[SDD-PLAN] Decompose this specification into an ordered task DAG. "
        f"List each task with dependencies.\n\nSpec:\n{specify_response.content}"
    )
    plan_response = run_persona(
        "strategist", plan_prompt, prior_responses=[specify_response]
    )
    session.responses.append(plan_response)
    session.task_dag = [{"raw_plan": plan_response.content, "timestamp": time.time()}]
    session.advance_to(SDDPhase.EXECUTE, notes="Task DAG created")
    _sdd_log_transition(session, "PLAN", "EXECUTE", "Task DAG created")

    # ── Phase 3: EXECUTE ─────────────────────────────────────────────
    # Specialist personas execute in DAG order
    exec_personas = ["sentinel", "catalyst", "scribe"]
    exec_prompt = (
        f"[SDD-EXECUTE] Execute your specialist analysis for this mission.\n\n"
        f"Topic: {topic}\n\n"
        f"Specification:\n{specify_response.content}\n\n"
        f"Task Plan:\n{plan_response.content}"
    )
    for persona_id in exec_personas:
        resp = run_persona(persona_id, exec_prompt, prior_responses=session.responses)
        session.responses.append(resp)

    session.advance_to(SDDPhase.VERIFY, notes="Execution complete")
    _sdd_log_transition(session, "EXECUTE", "VERIFY", "Specialist execution complete")

    # ── Phase 4: VERIFY ──────────────────────────────────────────────
    # CoVE adversarially validates against original spec
    verify_prompt = (
        f"[SDD-VERIFY] Adversarially verify this mission output against the spec.\n\n"
        f"Original Spec:\n{specify_response.content}\n\n"
        f"Execution Results:\n"
    )
    for resp in session.responses:
        verify_prompt += f"\n--- {resp.persona} ({resp.role}) ---\n{resp.content[:500]}\n"

    cove_response = run_persona("cove", verify_prompt, prior_responses=session.responses)
    session.responses.append(cove_response)
    session.verification_report = {
        "verifier": "cove",
        "content": cove_response.content,
        "timestamp": time.time(),
    }

    # Check for CoVE rejection — look for REJECT/FAIL indicators
    cove_lower = cove_response.content.lower()
    cove_rejected = any(kw in cove_lower for kw in ["reject", "fail", "blocked", "violation"])

    if cove_rejected:
        session.verification_report["verdict"] = "REJECTED"
        _sdd_log_transition(session, "VERIFY", "BLOCKED", "CoVE rejected — mission halted")
        logger.warning(f"SDD mission '{topic}' BLOCKED by CoVE verification")
        return session

    session.verification_report["verdict"] = "APPROVED"

    # ── Phase 5: MERGE ───────────────────────────────────────────────
    session.advance_to(SDDPhase.MERGE, notes="CoVE approved — mission complete")
    _sdd_log_transition(session, "VERIFY", "MERGE", "Mission approved and sealed")

    # Persist if connection available
    if conn:
        crew_report = CrewReport(
            topic=f"[SDD] {topic}",
            personas_used=[r.persona for r in session.responses],
            responses=session.responses,
        )
        _persist_crew_report(conn, crew_report)

    logger.info(f"SDD mission '{topic}' completed — {len(session.responses)} responses, "
                f"{len(session.phase_history)} phase transitions")
    return session


def _sdd_log_transition(
    session: SDDSession, from_phase: str, to_phase: str, notes: str,
) -> None:
    """Log an SDD phase transition to the ALIGN ledger."""
    try:
        from agents.core.als_logger import ALSLogger
        als = ALSLogger()
        als.log("SDD_PHASE_TRANSITION", {
            "topic": session.topic,
            "from": from_phase,
            "to": to_phase,
            "notes": notes,
        })
    except ImportError:
        pass  # Standalone mode — no ALIGN ledger available


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

