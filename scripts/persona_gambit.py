#!/usr/bin/env python3
"""
persona_gambit.py — All-Persona Gambit: Unleash Every Agent on the Repo.

Creates GitHub issues for every persona, hat, and pipeline agent in the
Sovereign OS ecosystem, then assigns GitHub Copilot to each.

The "All-Persona Gambit" is the nuclear option: every cognitive role
reviews and improves the codebase simultaneously.

Usage:
    python scripts/persona_gambit.py --all               # Full gambit (all agents)
    python scripts/persona_gambit.py --all --dry-run      # Preview only
    python scripts/persona_gambit.py --persona sentinel   # Single persona
    python scripts/persona_gambit.py --hat black          # Single hat
    python scripts/persona_gambit.py --pipeline 3         # Single pipeline step
    python scripts/persona_gambit.py --list               # Show full roster
"""

# ═══════════════════════════════════════════════════════════════════
# ARCHITECTURE BOUNDARY — READ BEFORE MODIFYING
# ═══════════════════════════════════════════════════════════════════
# This script is a THIN SELECTOR/ACTIVATOR. It:
#   1. Discovers the persona inventory (static roster)
#   2. Groups/selects agents by category
#   3. Creates GitHub issues via `gh` CLI
#   4. Assigns Copilot to each issue
#   5. Records results
#
# It does NOT (and MUST NOT):
#   - Route tasks         → that's maestro / maestro_router
#   - Schedule execution  → that's jules_tasks.yaml / scheduler
#   - Orchestrate agents  → that's crew_orchestrator
#   - Resolve conflicts   → that's arbiter_agent
#   - Define authorities  → that's AGENTS.md / persona .md files
#
# Source-of-truth boundaries:
#   Persona definitions  → config/personas/*.md
#   Scheduling           → config/jules_tasks.yaml
#   Orchestration        → agents/core/crew_orchestrator.py
#   Adjudication         → agents/core/arbiter_agent.py
#
# If you need this script to do more, compose with existing
# primitives rather than re-implementing their responsibilities.
# ═══════════════════════════════════════════════════════════════════

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPO_OWNER = "Grumpified-OGGVCT"
REPO_NAME = "Sovereign_Agentic_OS_with_HLF"
REPO_FULL = f"{REPO_OWNER}/{REPO_NAME}"
PERSONAS_DIR = _PROJECT_ROOT / "config" / "personas"

# ═══════════════════════════════════════════════════════════════════
# ROSTER — Complete Agent/Persona/Hat Registry
# ═══════════════════════════════════════════════════════════════════

@dataclass
class GambitAgent:
    """One agent in the gambit roster."""
    id: str
    name: str
    category: str  # "persona", "hat", "pipeline", "agent_class", "core_module"
    scope: str     # what it reviews/improves
    targets: list[str] = field(default_factory=list)
    source_file: str = ""  # persona .md, agent .py, etc.


# ── 16 Persona Files ─────────────────────────────────────────────
PERSONA_AGENTS = [
    GambitAgent("sentinel", "Sentinel", "persona",
                "Security monitoring, anomaly detection, ALIGN enforcement",
                ["agents/core/sentinel_agent.py", "agents/core/daemons/sentinel.py", "governance/"],
                "config/personas/sentinel.md"),
    GambitAgent("scribe", "Scribe", "persona",
                "InsAIts prose generation, token budget enforcement, audit logging",
                ["agents/core/scribe_agent.py", "agents/core/daemons/scribe.py", "agents/core/logger.py"],
                "config/personas/scribe.md"),
    GambitAgent("arbiter", "Arbiter", "persona",
                "Inter-agent dispute resolution, immutable rules enforcement",
                ["agents/core/arbiter_agent.py", "agents/core/daemons/arbiter.py"],
                "config/personas/arbiter.md"),
    GambitAgent("strategist", "Strategist", "persona",
                "High-level planning, task decomposition, architecture decisions",
                ["agents/core/plan_executor.py", "agents/core/spindle.py"],
                "config/personas/strategist.md"),
    GambitAgent("catalyst", "Catalyst", "persona",
                "Innovation acceleration, creative problem-solving, new approaches",
                ["agents/core/dream_state.py", "agents/core/hat_engine.py"],
                "config/personas/catalyst.md"),
    GambitAgent("chronicler", "Chronicler", "persona",
                "Documentation, history tracking, knowledge preservation",
                ["docs/", "README.md", "AGENTS.md"],
                "config/personas/chronicler.md"),
    GambitAgent("consolidator", "Consolidator", "persona",
                "Code unification, deduplication, architectural cleanup",
                ["agents/core/", "hlf/"],
                "config/personas/consolidator.md"),
    GambitAgent("herald", "Herald", "persona",
                "Communication, status reporting, stakeholder updates",
                ["gui/app.py", "agents/core/native/notifications.py"],
                "config/personas/herald.md"),
    GambitAgent("oracle", "Oracle", "persona",
                "Prediction, trend analysis, future-proofing, risk assessment",
                ["agents/core/memory_anchor.py", "agents/core/memory_scribe.py"],
                "config/personas/oracle.md"),
    GambitAgent("palette", "Palette", "persona",
                "UI/UX design, visual consistency, accessibility",
                ["gui/", "docs/index.html", ".streamlit/"],
                "config/personas/palette.md"),
    GambitAgent("scout", "Scout", "persona",
                "Reconnaissance, dependency scanning, threat intelligence",
                ["agents/core/egl_monitor.py", "agents/gateway/sentinel_gate.py"],
                "config/personas/scout.md"),
    GambitAgent("steward", "Steward", "persona",
                "Resource management, cost optimization, infrastructure health",
                ["agents/core/scheduler.py", "agents/core/model_gateway.py"],
                "config/personas/steward.md"),
    GambitAgent("weaver", "Weaver", "persona",
                "Integration, cross-component wiring, pipeline connectivity",
                ["agents/core/crew_orchestrator.py", "agents/core/spindle_tool_bridge.py"],
                "config/personas/weaver.md"),
    GambitAgent("cove", "CoVE Validator", "persona",
                "Chain-of-Verification, adversarial validation, 14-hat review",
                ["agents/core/hat_engine.py", "governance/templates/"],
                "config/personas/cove.md"),
    GambitAgent("cdda", "Codebase Deep-Dive Analyst", "persona",
                "Saturation-level codebase analysis, clone detection, dead code",
                ["agents/", "hlf/", "tests/", "scripts/"],
                "config/personas/cdda.md"),
    GambitAgent("shared_mandates", "Shared Mandates Auditor", "persona",
                "Cross-persona mandate compliance, invariant enforcement",
                ["config/personas/", "governance/", "AGENTS.md"],
                "config/personas/_shared_mandates.md"),
]

# ── 14 Hats ──────────────────────────────────────────────────────
HAT_AGENTS = [
    GambitAgent("hat_red", "🔴 Red Hat", "hat",
                "Fail-states, chaos, resilience, error handling, UX integrity",
                ["agents/core/", "hlf/", "gui/"]),
    GambitAgent("hat_black", "⚫ Black Hat", "hat",
                "Security exploits, zero-trust compliance, injection, auth",
                ["agents/core/", "agents/gateway/", "governance/"]),
    GambitAgent("hat_white", "⚪ White Hat", "hat",
                "Efficiency, performance, data integrity, query optimization",
                ["agents/core/db.py", "agents/core/model_gateway.py"]),
    GambitAgent("hat_yellow", "🟡 Yellow Hat", "hat",
                "Synergies, optimization, strategic value, positive potential",
                ["agents/core/", "hlf/"]),
    GambitAgent("hat_green", "🟢 Green Hat", "hat",
                "Evolution, missing mechanisms, feature completeness, i18n",
                ["hlf/", "agents/core/", "gui/"]),
    GambitAgent("hat_blue", "🔵 Blue Hat", "hat",
                "Process, observability, operational readiness, CI/CD",
                [".github/workflows/", "agents/core/logger.py", "tests/"]),
    GambitAgent("hat_indigo", "🟣 Indigo Hat", "hat",
                "Cross-feature architecture, integration, API contracts",
                ["agents/core/", "agents/gateway/", "gui/"]),
    GambitAgent("hat_cyan", "🩵 Cyan Hat", "hat",
                "Innovation, AI/ML validation, bias, feasibility",
                ["agents/core/", "hlf/", "agents/gateway/router.py"]),
    GambitAgent("hat_purple", "🟪 Purple Hat", "hat",
                "AI safety, compliance, regulatory, GDPR, SOC2",
                ["governance/", "agents/core/", "hlf/"]),
    GambitAgent("hat_orange", "🟠 Orange Hat", "hat",
                "DevOps, infrastructure, license, governance, SBOM",
                [".github/", "pyproject.toml", "governance/"]),
    GambitAgent("hat_silver", "🪨 Silver Hat", "hat",
                "Context, token & resource optimization, O(1) bounding, gas",
                ["agents/core/context_pruner.py", "agents/core/context_tiering.py"]),
    GambitAgent("hat_azure", "🔷 Azure Hat", "hat",
                "MCP workflow integrity, task lifecycle, HITL gates",
                ["agents/core/tool_registry.py", "agents/core/tool_forge.py"]),
    GambitAgent("hat_gold", "🥇 Gold Hat", "hat",
                "Meta-review, overall quality gate, sign-off authority",
                ["AGENTS.md", "README.md"]),
    GambitAgent("hat_meta", "0️⃣ Meta-Hat Router", "hat",
                "Deterministic hat selection, regex routing, mandatory hat enforcement",
                ["agents/core/hat_engine.py"]),
]

# ── 10 Daily Pipeline Agents ────────────────────────────────────
PIPELINE_AGENTS = [
    GambitAgent("pipe_hlf_evolve", "HLF Grammar Evolver", "pipeline",
                "Evolve HLF grammar — additive syntax extensions",
                ["hlf/hlfc.py", "hlf/hlflint.py", "hlf/hlffmt.py", "hlf/hlfrun.py"]),
    GambitAgent("pipe_dream", "Dream Cycle Enhancer", "pipeline",
                "Enhance dream/hat engine cognitive loops",
                ["agents/core/dream_state.py", "agents/core/hat_engine.py"]),
    GambitAgent("pipe_align", "ALIGN Hardener", "pipeline",
                "Harden governance — tighten sentinel rules",
                ["governance/ALIGN_LEDGER.yaml", "agents/gateway/sentinel_gate.py"]),
    GambitAgent("pipe_transparency", "Transparency Assessor", "pipeline",
                "GUI agent visibility, HLF translation panels, ALS log streams",
                ["gui/app.py", "agents/core/logger.py"]),
    GambitAgent("pipe_gui", "GUI Feature Builder", "pipeline",
                "Build and enhance GUI features",
                ["gui/app.py", "gui/"]),
    GambitAgent("pipe_gemini", "Gemini Model Integrator", "pipeline",
                "Track and integrate upgraded Gemini models",
                ["agents/core/db.py", "agents/gateway/router.py"]),
    GambitAgent("pipe_capability", "Self-Capability Tracker", "pipeline",
                "Update capability manifest — CAN / CANNOT / COULD do",
                ["config/", "reports/"]),
    GambitAgent("pipe_ci", "CI Fixer", "pipeline",
                "Fix CI failures, workflow issues, dependency problems",
                [".github/workflows/", "pyproject.toml"]),
    GambitAgent("pipe_hlf_max", "HLF Maximizer", "pipeline",
                "Expand compiler test coverage, add linter rules, grow test corpus",
                ["hlf/", "tests/", "docs/"]),
    GambitAgent("pipe_readme", "README Updater (Capstone)", "pipeline",
                "Update README to reflect all changes — public face",
                ["README.md", "docs/"]),
]

# ── Core Agent Classes ───────────────────────────────────────────
CORE_AGENTS = [
    GambitAgent("build_agent", "Build Agent", "agent_class",
                "Code verification — tests, lint, syntax, dependency checks",
                ["agents/core/build_agent.py", "tests/"]),
    GambitAgent("code_agent", "Code Agent", "agent_class",
                "File operations — create, modify, refactor within sandbox",
                ["agents/core/code_agent.py", "agents/core/agent_sandbox.py"]),
    GambitAgent("canary_agent", "Canary Agent", "agent_class",
                "Early warning detection, anomaly flagging",
                ["agents/core/canary_agent.py"]),
    GambitAgent("plan_executor", "Plan Executor", "agent_class",
                "SDD plan → executable DAG nodes",
                ["agents/core/plan_executor.py", "agents/core/spindle.py"]),
    GambitAgent("maestro", "Maestro Orchestrator", "agent_class",
                "High-level task routing and agent coordination",
                ["agents/core/maestro.py", "agents/core/maestro_router.py"]),
    GambitAgent("crew_orch", "Crew Orchestrator", "agent_class",
                "Multi-agent crew management and task delegation",
                ["agents/core/crew_orchestrator.py"]),
    GambitAgent("formal_verifier", "Formal Verifier", "agent_class",
                "Formal methods verification, property checking",
                ["agents/core/formal_verifier.py"]),
    GambitAgent("gateway_router", "Gateway Router", "agent_class",
                "Model routing, tier walks, cost optimization",
                ["agents/gateway/router.py", "agents/gateway/bus.py"]),
]

# ── Specialized Core Modules ────────────────────────────────────
CORE_MODULE_AGENTS = [
    GambitAgent("acfs", "ACFS Worktree Isolation", "core_module",
                "Agent-Confined File System — sandbox isolation",
                ["agents/core/acfs.py"]),
    GambitAgent("spindle", "SpindleDAG Engine", "core_module",
                "Parallel DAG execution with Saga compensation",
                ["agents/core/spindle.py", "agents/core/spindle_tool_bridge.py"]),
    GambitAgent("tool_registry", "Tool Registry", "core_module",
                "Tool discovery, registration, gas accounting",
                ["agents/core/tool_registry.py", "agents/core/tool_forge.py"]),
    GambitAgent("credential_vault", "Credential Vault", "core_module",
                "Secret management, encryption, API key storage",
                ["agents/core/credential_vault.py", "agents/core/vault_decrypt.py"]),
    GambitAgent("event_bus", "Event Bus", "core_module",
                "Inter-agent event system, pub/sub",
                ["agents/core/event_bus.py", "agents/core/agent_bus.py"]),
    GambitAgent("hlf_runtime", "HLF Runtime Stack", "core_module",
                "Compiler, linter, formatter, runtime, bytecode VM",
                ["hlf/"]),
    GambitAgent("native_bridge", "Native OS Bridge", "core_module",
                "Platform-specific OS interaction, tray, notifications",
                ["agents/core/native/"]),
    GambitAgent("memory_system", "Memory System", "core_module",
                "InfiniteRAG, memory anchors, memory scribe",
                ["agents/core/memory_anchor.py", "agents/core/memory_scribe.py"]),
]

# ── Full Roster ──────────────────────────────────────────────────
ALL_AGENTS = PERSONA_AGENTS + HAT_AGENTS + PIPELINE_AGENTS + CORE_AGENTS + CORE_MODULE_AGENTS


# ═══════════════════════════════════════════════════════════════════
# ISSUE GENERATION
# ═══════════════════════════════════════════════════════════════════

INVARIANTS = """\
## Sovereign OS Invariants (NEVER VIOLATE)
1. No test deletion — test count must be >= baseline (2017+)
2. No coverage reduction
3. No simplification — all existing features preserved
4. Additive-only — new code alongside existing, never replacing
5. 4GB RAM constraint — Layer 1 ACFS compliance
6. Gas enforcement — every route consumes gas via consume_gas_async()
7. ALIGN enforcement — all outputs pass through enforce_align()
8. Fourteen-Hat Review required on all PRs
"""


def _build_issue_body(agent: GambitAgent) -> str:
    """Build a GitHub issue body for the given agent."""
    persona_context = ""
    if agent.source_file:
        persona_path = _PROJECT_ROOT / agent.source_file
        if persona_path.exists():
            content = persona_path.read_text(encoding="utf-8", errors="replace")
            # Truncate to avoid huge issues
            if len(content) > 3000:
                content = content[:3000] + "\n\n... (truncated, see full file)"
            persona_context = f"\n## Persona Definition\n\n```markdown\n{content}\n```\n"

    targets_list = "\n".join(f"- `{t}`" for t in agent.targets) if agent.targets else "- Full repository"

    return f"""\
## [{agent.category.upper()}] {agent.name} — Autonomous Review & Improvement

**Scope**: {agent.scope}

### Target Files
{targets_list}

### Instructions
1. Read `AGENTS.md` for full repository context
2. Apply your specialized lens ({agent.name}) to the target files
3. Identify improvements, bugs, missing tests, or architectural issues
4. Make ONLY additive changes — never delete or simplify
5. Run full test suite: `uv run python -m pytest tests/ -v`
6. Ensure all 2017+ tests pass before creating a PR
7. Apply the Fourteen-Hat Review: `governance/templates/fourteen_hat_review.md`

{INVARIANTS}
{persona_context}
### Acceptance Criteria
- [ ] All existing tests pass
- [ ] No test files deleted or simplified
- [ ] Changes are additive only
- [ ] PR includes descriptive commit message
- [ ] Fourteen-Hat review completed
"""


def _run_gh(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a `gh` CLI command."""
    return subprocess.run(
        ["gh"] + args,
        capture_output=True, text=True, timeout=30,
        check=check, cwd=str(_PROJECT_ROOT),
    )


def _create_issue(agent: GambitAgent, dry_run: bool = False) -> dict[str, Any]:
    """Create a GitHub issue for the given agent."""
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    title = f"[Gambit] [{agent.category}] {agent.name} — Autonomous Improvement ({timestamp})"
    body = _build_issue_body(agent)

    if dry_run:
        print(f"\n{'='*60}")
        print(f"DRY RUN — {agent.id}: {agent.name} [{agent.category}]")
        print(f"{'='*60}")
        print(f"Title: {title}")
        print(f"Scope: {agent.scope}")
        print(f"Targets: {', '.join(agent.targets[:3])}")
        print(f"Body: {len(body)} chars")
        return {"dry_run": True, "agent": agent.id}

    try:
        result = _run_gh([
            "issue", "create",
            "--repo", REPO_FULL,
            "--title", title,
            "--body", body,
            "--label", "copilot",
            "--label", f"gambit-{agent.category}",
        ])
        issue_url = result.stdout.strip()
        issue_number = issue_url.split("/")[-1] if "/" in issue_url else "unknown"
        print(f"  ✅ #{issue_number} {agent.name}")

        # Assign Copilot
        _run_gh([
            "issue", "edit", issue_number,
            "--repo", REPO_FULL,
            "--add-assignee", "copilot",
        ], check=False)

        return {"success": True, "issue_number": issue_number, "agent": agent.id}
    except subprocess.CalledProcessError as exc:
        print(f"  ❌ {agent.name}: {exc.stderr[:100]}")
        return {"success": False, "agent": agent.id, "error": str(exc.stderr[:200])}
    except FileNotFoundError:
        print("❌ `gh` CLI not found. Install: https://cli.github.com")
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════


def _list_roster() -> None:
    """Print the full gambit roster."""
    categories = {}
    for agent in ALL_AGENTS:
        categories.setdefault(agent.category, []).append(agent)

    total = 0
    for cat, agents in categories.items():
        print(f"\n{'='*50}")
        print(f"  {cat.upper()} ({len(agents)} agents)")
        print(f"{'='*50}")
        for a in agents:
            print(f"  {a.id:25} {a.name}")
            total += 1

    print(f"\n{'='*50}")
    print(f"  TOTAL: {total} agents in the gambit roster")
    print(f"{'='*50}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="All-Persona Gambit — Unleash every agent on the repo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Full gambit — all agents")
    group.add_argument("--persona", metavar="ID", help="Single persona by ID")
    group.add_argument("--hat", metavar="COLOR", help="Single hat by color (e.g. 'black')")
    group.add_argument("--pipeline", metavar="STEP", type=int, help="Single pipeline step (1-10)")
    group.add_argument("--category", metavar="CAT",
                       choices=["persona", "hat", "pipeline", "agent_class", "core_module"],
                       help="All agents in a category")
    group.add_argument("--list", action="store_true", help="Show full roster")
    parser.add_argument("--dry-run", action="store_true", help="Preview without creating issues")
    parser.add_argument("--delay", type=float, default=2.0,
                        help="Delay between issue creations (seconds, default 2)")

    args = parser.parse_args()

    if args.list:
        _list_roster()
        return

    # Resolve which agents to activate
    targets: list[GambitAgent] = []

    if args.all:
        targets = ALL_AGENTS
    elif args.persona:
        targets = [a for a in ALL_AGENTS if a.id == args.persona and a.category == "persona"]
    elif args.hat:
        hat_id = f"hat_{args.hat}"
        targets = [a for a in ALL_AGENTS if a.id == hat_id]
    elif args.pipeline:
        pipe_idx = args.pipeline - 1
        if 0 <= pipe_idx < len(PIPELINE_AGENTS):
            targets = [PIPELINE_AGENTS[pipe_idx]]
    elif args.category:
        targets = [a for a in ALL_AGENTS if a.category == args.category]

    if not targets:
        print("❌ No matching agents found. Use --list to see the roster.")
        sys.exit(1)

    print(f"\n🎲 ALL-PERSONA GAMBIT — Activating {len(targets)} agent(s)")
    print(f"   Repo: {REPO_FULL}")
    print(f"   Mode: {'DRY RUN' if args.dry_run else 'LIVE — Creating issues'}")
    print()

    results = []
    for i, agent in enumerate(targets, 1):
        print(f"[{i}/{len(targets)}] {agent.category}/{agent.id}...")
        result = _create_issue(agent, dry_run=args.dry_run)
        results.append(result)

        if not args.dry_run and i < len(targets):
            time.sleep(args.delay)  # Rate limit

    # Summary
    print(f"\n{'='*50}")
    if args.dry_run:
        print(f"  DRY RUN COMPLETE — {len(results)} issues would be created")
    else:
        created = sum(1 for r in results if r.get("success"))
        failed = sum(1 for r in results if not r.get("success") and not r.get("dry_run"))
        print(f"  GAMBIT COMPLETE — {created} issues created, {failed} failed")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
