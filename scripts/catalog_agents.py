#!/usr/bin/env python3
"""
catalog_agents.py — Universal AI Artifact Discovery & Documentation Tool.

Scans any repository and produces a comprehensive, developer-ready catalog
of every agent, persona, hat, skill, workflow, daemon, and AI capability
found — regardless of framework (LangChain, CrewAI, AutoGen, HLF, custom).

PORTABILITY: Zero external dependencies. Uses only Python 3.10+ stdlib.
              Drop this single file into any project's scripts/ directory
              and run it immediately.

Usage:
    # Scan current directory, write docs/AGENTS_CATALOG.md
    python scripts/catalog_agents.py

    # Scan a different repo
    python scripts/catalog_agents.py --repo /path/to/project

    # Write to a custom output file
    python scripts/catalog_agents.py --output docs/MY_AGENTS.md

    # Emit JSON instead of Markdown (for CI / downstream tooling)
    python scripts/catalog_agents.py --format json

    # Verbose: show discovery log while running
    python scripts/catalog_agents.py --verbose

    # Include raw source snippets in output (longer but richer)
    python scripts/catalog_agents.py --snippets

What it finds (framework-agnostic):
    * Python classes:  class *Agent, *Daemon, *Persona, *Worker, *Specialist
    * Dataclass roles: @dataclass with name/role/description fields
    * Markdown profiles: config/personas/*.md, agents/*.md, any file with
                         "## Core Identity" or "## Purpose" headings
    * JSON/YAML registries: files with "hat_agents", "agents", "personas",
                             "roles", "workers" keys
    * AGENTS.md / README hat sections: "## Hat", "## Persona", "## Role"
    * HLF stdlib modules:  hlf/stdlib/*.hlf, any *.hlf with [MODULE]
    * GitHub Actions workflows: .github/workflows/*.yml
    * Task/pipeline configs: jules_tasks.yaml, pipeline.yaml, tasks.yaml
    * Host function registries: host_functions.json, tools.json
    * Capability enums:  class Capability(Enum), class Skill(Enum)

Output structure:
    1. Summary table (all artifacts at a glance)
    2. Mermaid architecture diagram (auto-generated)
    3. Per-category deep sections with full descriptions
    4. Skills matrix (who can do what)
    5. Invocation guide (how to activate each artifact)
    6. Cross-reference index

Designed for:
    Full-stack developers, AI engineers, and agent architects who need to
    onboard quickly, audit capabilities, or decide which persona to invoke.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ─────────────────────────────────────────────────────────────────
# VERSION
# ─────────────────────────────────────────────────────────────────

__version__ = "1.3.0"

# ─────────────────────────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────

CATEGORIES = [
    "hat",
    "persona",
    "daemon",
    "agent_class",
    "workflow",
    "pipeline_step",
    "skill",
    "capability",
    "host_function",
    "hlf_module",
    "core_module",
    "config_registry",
]

CATEGORY_EMOJI = {
    "hat": "🎩",
    "persona": "🧑",
    "daemon": "👻",
    "agent_class": "🤖",
    "workflow": "⚙️",
    "pipeline_step": "🔗",
    "skill": "🛠️",
    "capability": "⚡",
    "host_function": "🔌",
    "hlf_module": "📜",
    "core_module": "🧩",
    "config_registry": "📋",
}

HAT_COLORS = {
    "red": "🔴", "black": "⚫", "white": "⚪", "yellow": "🟡",
    "green": "🟢", "blue": "🔵", "indigo": "🟣", "cyan": "🩵",
    "purple": "🟪", "orange": "🟠", "silver": "🪨", "azure": "💎",
    "gold": "✨", "meta": "0️⃣",
}


@dataclass
class ArtifactSkill:
    """A single skill associated with an artifact."""
    name: str
    category: str = ""  # e.g., "security", "frontend", "data", "ai"


@dataclass
class AIArtifact:
    """One discovered AI artifact — a hat, persona, daemon, class, etc."""
    id: str
    name: str
    category: str                            # from CATEGORIES above
    source_files: list[str] = field(default_factory=list)
    description: str = ""
    role: str = ""                           # professional title / function
    model: str = ""                          # assigned LLM model
    temperature: float | None = None         # sampling temperature
    max_tokens: int | None = None
    tier: str = ""                           # hearth | forge | sovereign
    hat_color: str = ""                      # for hat category
    hat_emoji: str = ""
    skills: list[ArtifactSkill] = field(default_factory=list)
    cross_aware: list[str] = field(default_factory=list)  # collaborators
    invocation: str = ""                     # how to call/activate it
    operating_principles: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    raw_snippet: str = ""                    # source excerpt (optional)
    tags: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def category_emoji(self) -> str:
        return CATEGORY_EMOJI.get(self.category, "•")

    @property
    def display_name(self) -> str:
        emoji = self.hat_emoji or self.category_emoji
        return f"{emoji} {self.name}"


# ─────────────────────────────────────────────────────────────────
# DISCOVERY ENGINE — Strategy Pattern
# ─────────────────────────────────────────────────────────────────

class _DiscoveryLog:
    """Collects discovery log messages for verbose output."""
    def __init__(self, verbose: bool = False):
        self._verbose = verbose
        self._entries: list[str] = []

    def log(self, msg: str) -> None:
        self._entries.append(msg)
        if self._verbose:
            print(f"  [catalog] {msg}", file=sys.stderr)

    @property
    def entries(self) -> list[str]:
        return list(self._entries)


def _safe_read(path: Path) -> str:
    """Read a file, returning empty string on any error."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


# ── Strategy 1: JSON registries (agent_registry.json, tools.json, etc.) ─────

_REGISTRY_KEYS = {
    "hat_agents", "agents", "personas", "named_agents",
    "roles", "workers", "specialists", "crew", "team",
}
_REGISTRY_FILENAME_HINTS = {
    "agent_registry", "agents", "personas", "roles",
    "crew", "team", "workers", "registry",
}


def _discover_json_registry(repo: Path, log: _DiscoveryLog) -> list[AIArtifact]:
    artifacts: list[AIArtifact] = []
    for json_path in repo.rglob("*.json"):
        if any(p in json_path.parts for p in (".git", "node_modules", "__pycache__", ".venv", "dist")):
            continue
        stem = json_path.stem.lower()
        # Only parse files with a name that looks like an agent registry
        if not any(hint in stem for hint in _REGISTRY_FILENAME_HINTS):
            # Still parse if it contains the right keys at root level
            text = _safe_read(json_path)
            if not any(f'"{k}"' in text for k in _REGISTRY_KEYS):
                continue
        try:
            data = json.loads(_safe_read(json_path))
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        for key in _REGISTRY_KEYS:
            section = data.get(key)
            if not isinstance(section, dict):
                continue
            log.log(f"JSON registry: {json_path.relative_to(repo)} [{key}] — {len(section)} entries")
            for entry_id, entry in section.items():
                if not isinstance(entry, dict):
                    continue
                category = _guess_category_from_entry(entry_id, entry)
                hat_color = entry.get("hat", "")
                artifact = AIArtifact(
                    id=entry_id,
                    name=entry.get("name", entry_id.replace("_", " ").title()),
                    category=category,
                    source_files=[str(json_path.relative_to(repo))],
                    description=entry.get("description", ""),
                    role=entry.get("role", ""),
                    model=entry.get("model", ""),
                    temperature=entry.get("temperature"),
                    max_tokens=entry.get("max_tokens"),
                    tier=entry.get("tier", ""),
                    hat_color=hat_color,
                    hat_emoji=HAT_COLORS.get(hat_color, ""),
                    skills=[ArtifactSkill(s) for s in entry.get("hard_skills", [])],
                    cross_aware=entry.get("cross_aware", []),
                    invocation=entry.get("invocation", ""),
                    tags=entry.get("tags", []),
                )
                artifacts.append(artifact)
    return artifacts


def _guess_category_from_entry(entry_id: str, entry: dict) -> str:
    hat = entry.get("hat", "").lower()
    if hat in HAT_COLORS:
        return "hat"
    role = (entry.get("role", "") + " " + entry_id).lower()
    if any(w in role for w in ("daemon", "monitor", "watcher")):
        return "daemon"
    if any(w in role for w in ("persona", "weaver", "oracle", "herald", "scout", "strategist", "chronicler", "catalyst", "consolidator", "palette", "steward", "cdda", "cove", "sentinel", "scribe", "arbiter")):
        return "persona"
    if any(w in role for w in ("workflow", "pipeline")):
        return "workflow"
    return "persona"


# ── Strategy 2: Markdown persona files ───────────────────────────────────────

_PERSONA_HEADING_RE = re.compile(
    r"^#{1,3}\s+(?:Core Identity|Purpose|Operating Principles?|Mission|Role|Persona)",
    re.MULTILINE | re.IGNORECASE,
)
_PERSONA_IDENTITY_FIELDS = {
    "name": re.compile(r"\*\*Name\*\*[:\s]+(.+)", re.IGNORECASE),
    "hat": re.compile(r"\*\*Hat\*\*[:\s]+(.+)", re.IGNORECASE),
    "model": re.compile(r"\*\*Model\*\*[:\s]+(.+)", re.IGNORECASE),
    "temperature": re.compile(r"\*\*Temperature\*\*[:\s]+([0-9.]+)", re.IGNORECASE),
    "role": re.compile(r"\*\*Role\*\*[:\s]+(.+)", re.IGNORECASE),
}
_PRINCIPLE_BULLET_RE = re.compile(r"^\d+\.\s+\*\*(.+?)\*\*[:\s]*(.*)", re.MULTILINE)
_DOMAIN_HEADING_RE = re.compile(r"^#{2,4}\s+Domain\s+\d+[:\s]+(.+)", re.MULTILINE)


_PERSONA_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist", "build"}
_PERSONA_KNOWN_NON_PERSONA_STEMS = {
    "CHANGELOG", "LICENSE", "SECURITY", "CONTRIBUTING", "CODE_OF_CONDUCT",
    "TODO", "TODO_MASTER", "benchmark", "metrics", "result",
    "RFC_9000_SERIES", "HLF_PROGRESS", "README_UPDATE_INSTRUCTIONS",
    "SESSION_HANDOVER", "JULES_COORDINATION", "Automated_Runner_Setup_Guide",
    "WALKTHROUGH", "openclaw_integration", "handoff_zai_api_integration",
    "UNIFIED_ECOSYSTEM_ROADMAP", "hat_review_pr50",
}


def _discover_markdown_personas(repo: Path, log: _DiscoveryLog) -> list[AIArtifact]:
    artifacts: list[AIArtifact] = []
    search_dirs = [
        repo / "config" / "personas",
        repo / "agents",
        repo / "personas",
        repo / "roles",
        repo / "prompts",
        repo / "governance",
    ]
    # Also scan any *.md that looks like a persona spec
    for md_path in repo.rglob("*.md"):
        if any(p in md_path.parts for p in _PERSONA_SKIP_DIRS):
            continue
        if md_path.stem in _PERSONA_KNOWN_NON_PERSONA_STEMS:
            continue
        text = _safe_read(md_path)
        # Must have a Core Identity / Purpose heading to qualify OR be in a persona dir
        in_persona_dir = any(md_path.is_relative_to(d) for d in search_dirs if d.exists())
        if not _PERSONA_HEADING_RE.search(text) and not in_persona_dir:
            continue
        if len(text) < 100:
            continue
        log.log(f"Markdown persona: {md_path.relative_to(repo)}")
        artifact = _parse_markdown_persona(md_path, text, repo)
        if artifact:
            artifacts.append(artifact)
    return artifacts


def _parse_markdown_persona(path: Path, text: str, repo: Path) -> AIArtifact | None:
    stem = path.stem.lstrip("_")
    # Extract title from first heading
    title_match = re.search(r"^#\s+(.+)", text, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else stem.replace("_", " ").title()
    # Remove markdown formatting from title
    title = re.sub(r"\*+|`", "", title).strip()

    fields: dict[str, str] = {}
    for field_name, pattern in _PERSONA_IDENTITY_FIELDS.items():
        m = pattern.search(text)
        if m:
            val = re.sub(r"\*+|`|\(.*?\)", "", m.group(1)).strip()
            fields[field_name] = val

    # Extract principles
    principles = [
        m.group(1).strip()
        for m in _PRINCIPLE_BULLET_RE.finditer(text)
    ][:6]

    # Extract domains
    domains = [
        m.group(1).strip()
        for m in _DOMAIN_HEADING_RE.finditer(text)
    ]

    # Extract description — first paragraph after title
    paragraphs = re.split(r"\n\n+", text)
    description = ""
    for para in paragraphs[1:]:
        para = para.strip()
        if para and not para.startswith("#") and not para.startswith("```") and len(para) > 30:
            description = re.sub(r"\*+|`|>", "", para).strip()
            description = re.sub(r"\s+", " ", description)
            if len(description) > 400:
                description = description[:400] + "…"
            break

    hat_raw = fields.get("hat", "")
    hat_color = re.search(r"\b(red|black|white|yellow|green|blue|indigo|cyan|purple|orange|silver|azure|gold|meta)\b",
                          hat_raw.lower())
    hat_c = hat_color.group(1) if hat_color else ""

    try:
        temp = float(fields.get("temperature", "")) if fields.get("temperature") else None
    except ValueError:
        temp = None

    return AIArtifact(
        id=stem,
        name=title,
        category="persona",
        source_files=[str(path.relative_to(repo))],
        description=description,
        role=fields.get("role", ""),
        model=fields.get("model", ""),
        temperature=temp,
        hat_color=hat_c,
        hat_emoji=HAT_COLORS.get(hat_c, "🧑"),
        operating_principles=principles,
        domains=domains,
        raw_snippet=text[:600],
    )


# ── Strategy 3: Python class scanner (AST-based) ─────────────────────────────

_AGENT_CLASS_SUFFIXES = (
    "Agent", "Daemon", "Persona", "Worker", "Specialist",
    "Executor", "Analyzer", "Monitor", "Scanner", "Validator",
    "Orchestrator", "Router", "Gateway", "Engine", "Classifier",
    "Dispatcher", "Processor", "Handler", "Manager",
)
_AGENT_CLASS_RE = re.compile(
    r"class\s+(\w+(?:" + "|".join(_AGENT_CLASS_SUFFIXES) + r"))\s*[\(:]",
)
_DATACLASS_AGENT_RE = re.compile(
    r"@dataclass.*\nclass\s+(\w+)\s*[\(:]",
    re.DOTALL,
)


def _discover_python_classes(repo: Path, log: _DiscoveryLog) -> list[AIArtifact]:
    artifacts: list[AIArtifact] = []
    seen_classes: set[str] = set()

    for py_path in repo.rglob("*.py"):
        if any(p in py_path.parts for p in (".git", "node_modules", "__pycache__", ".venv", "dist", "build")):
            continue
        text = _safe_read(py_path)
        if not text:
            continue

        # Fast pre-filter — must contain class + agent-like suffix
        if not any(s in text for s in _AGENT_CLASS_SUFFIXES):
            continue

        classes_in_file: list[str] = []
        try:
            tree = ast.parse(text, filename=str(py_path))
        except SyntaxError:
            # Fallback: regex scan
            for m in _AGENT_CLASS_RE.finditer(text):
                cls_name = m.group(1)
                if cls_name not in seen_classes:
                    seen_classes.add(cls_name)
                    classes_in_file.append(cls_name)
        else:
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    if any(node.name.endswith(s) for s in _AGENT_CLASS_SUFFIXES):
                        if node.name not in seen_classes:
                            seen_classes.add(node.name)
                            classes_in_file.append(node.name)

        for cls_name in classes_in_file:
            docstring = _extract_class_docstring(text, cls_name)
            category = _class_name_to_category(cls_name)
            description = _extract_module_docstring(text) if not docstring else docstring
            log.log(f"Python class: {py_path.relative_to(repo)}::{cls_name}")
            artifacts.append(AIArtifact(
                id=cls_name,
                name=_camel_to_readable(cls_name),
                category=category,
                source_files=[str(py_path.relative_to(repo))],
                description=(description or "")[:400],
                raw_snippet=_get_class_snippet(text, cls_name),
            ))

    return artifacts


def _class_name_to_category(name: str) -> str:
    n = name.lower()
    if "daemon" in n:
        return "daemon"
    if "orchestrator" in n or "dispatcher" in n or "router" in n or "gateway" in n:
        return "core_module"
    if "agent" in n:
        return "agent_class"
    if "engine" in n or "manager" in n or "executor" in n:
        return "core_module"
    return "agent_class"


def _camel_to_readable(name: str) -> str:
    s1 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", name)
    return re.sub(r"([a-z\d])([A-Z])", r"\1 \2", s1)


def _extract_module_docstring(text: str) -> str:
    m = re.match(r'\s*"""(.*?)"""', text, re.DOTALL)
    if m:
        doc = m.group(1).strip()
        # Get first paragraph
        first_para = doc.split("\n\n")[0].strip()
        return re.sub(r"\s+", " ", first_para)[:300]
    return ""


def _extract_class_docstring(text: str, cls_name: str) -> str:
    pattern = re.compile(
        r"class\s+" + re.escape(cls_name) + r"\b.*?:\s*\n\s+\"\"\"(.*?)\"\"\"",
        re.DOTALL
    )
    m = pattern.search(text)
    if m:
        doc = m.group(1).strip()
        return re.sub(r"\s+", " ", doc.split("\n\n")[0])[:300]
    return ""


def _get_class_snippet(text: str, cls_name: str) -> str:
    pattern = re.compile(r"(class\s+" + re.escape(cls_name) + r"\b.{0,2000})", re.DOTALL)
    m = pattern.search(text)
    if m:
        snippet = m.group(1)[:600]
        return snippet
    return ""


# ── Strategy 4: YAML/TOML role definitions ───────────────────────────────────

_YAML_TASK_KEYS = {"steps", "jobs", "tasks", "pipeline", "workflow"}


_YAML_SKIP_STEMS = {
    "hls", "bytecode_spec", "seccomp", "redis", "config", "dapr",
    "acfs.manifest", "pubsub", "statestore", "web_proxy",
    "service_contracts", "module_import_rules", "openclaw_strategies",
    "align_ledger", "ALIGN_LEDGER",
}
_YAML_REQUIRE_AGENT_KEYS = {"role:", "model:", "persona:", "hat:", "description:"}


def _discover_yaml_roles(repo: Path, log: _DiscoveryLog) -> list[AIArtifact]:
    """Parse YAML files for agent/persona/workflow definitions."""
    artifacts: list[AIArtifact] = []
    for yaml_path in repo.rglob("*.yaml"):
        if any(p in yaml_path.parts for p in (".git", "node_modules", "__pycache__", ".venv")):
            continue
        # Skip known grammar/config files that aren't agent definitions
        if yaml_path.stem in _YAML_SKIP_STEMS:
            continue
        # Skip governance spec files (grammar rules, policies, seccomp, etc.)
        if "governance" in yaml_path.parts and yaml_path.stem not in (
            "hls",  # already skipped
        ):
            continue
        text = _safe_read(yaml_path)
        if not text or len(text) < 20:
            continue
        # Must contain *multiple* agent-like keys to avoid false positives
        matching = sum(1 for k in _YAML_REQUIRE_AGENT_KEYS if k in text)
        has_workflow = any(k in text for k in ("workflow:", "pipeline:", "steps:"))
        if matching < 2 and not has_workflow:
            continue
        log.log(f"YAML file: {yaml_path.relative_to(repo)}")
        arts = _parse_yaml_for_agents(yaml_path, text, repo)
        artifacts.extend(arts)
    return artifacts


def _parse_yaml_for_agents(path: Path, text: str, repo: Path) -> list[AIArtifact]:
    """Minimal YAML parser for agent-like structures (no pyyaml dependency)."""
    artifacts: list[AIArtifact] = []
    rel = str(path.relative_to(repo))

    # Detect GitHub Actions workflows
    if ".github/workflows" in rel or "github/workflows" in rel:
        # Extract job names
        for m in re.finditer(r"^  ([a-zA-Z_-]+):\s*$", text, re.MULTILINE):
            job_name = m.group(1)
            if job_name in ("on", "name", "env", "permissions", "defaults"):
                continue
            artifacts.append(AIArtifact(
                id=f"workflow_{job_name}",
                name=f"CI Workflow: {job_name.replace('-', ' ').replace('_', ' ').title()}",
                category="workflow",
                source_files=[rel],
                description=f"GitHub Actions job '{job_name}' in {path.name}",
            ))
        return artifacts

    # Detect jules_tasks / pipeline configs
    if any(k in path.stem for k in ("task", "pipeline", "schedule", "workflow")):
        for m in re.finditer(r"- name:\s+(.+)", text):
            step_name = m.group(1).strip().strip('"').strip("'")
            artifacts.append(AIArtifact(
                id=f"pipeline_{re.sub(r'[^a-z0-9_]', '_', step_name.lower())}",
                name=step_name,
                category="pipeline_step",
                source_files=[rel],
                description=f"Pipeline step defined in {path.name}",
            ))
        if artifacts:
            return artifacts

    # Generic YAML agent/persona blocks
    # Pattern: key followed by role/description/model sub-keys
    block_pattern = re.compile(
        r"^([a-zA-Z_-]+):\s*\n((?:  .+\n)*)",
        re.MULTILINE
    )
    for m in block_pattern.finditer(text):
        block_id = m.group(1)
        block_body = m.group(2)
        if not any(k in block_body for k in ("role:", "persona:", "model:", "description:", "hat:")):
            continue
        role_m = re.search(r"role:\s+(.+)", block_body)
        desc_m = re.search(r"description:\s+(.+)", block_body)
        model_m = re.search(r"model:\s+(.+)", block_body)
        hat_m = re.search(r"hat:\s+(.+)", block_body)
        artifacts.append(AIArtifact(
            id=block_id,
            name=block_id.replace("_", " ").title(),
            category="persona",
            source_files=[rel],
            description=(desc_m.group(1) if desc_m else ""),
            role=(role_m.group(1) if role_m else ""),
            model=(model_m.group(1) if model_m else ""),
            hat_color=(hat_m.group(1).strip() if hat_m else ""),
        ))

    return artifacts


# ── Strategy 5: AGENTS.md / README hat section parser ────────────────────────

_HAT_SECTION_RE = re.compile(
    r"###\s+(?:[🎩🔴⚫⚪🟡🟢🔵🟣🩵🟪🟠🪨💎✨0️⃣]\s*)?(.+?)(?:\s*[—\-]\s*(.+))?\s*$",
    re.MULTILINE
)
_APPLY_WHEN_RE = re.compile(r"\*\*When to apply\*\*[:\s]+(.+)", re.IGNORECASE)
_DIMENSIONS_RE = re.compile(r"\*\*Validation Dimensions\*\*[:\s]+(.+)", re.IGNORECASE)


def _discover_agents_md(repo: Path, log: _DiscoveryLog) -> list[AIArtifact]:
    artifacts: list[AIArtifact] = []
    candidates = [
        repo / "AGENTS.md",
        repo / "agents.md",
        repo / "CONTRIBUTING.md",
        repo / "README.md",
    ]
    for md_path in candidates:
        if not md_path.exists():
            continue
        text = _safe_read(md_path)
        if "hat" not in text.lower() and "persona" not in text.lower():
            continue
        log.log(f"AGENTS.md/README hats: {md_path.name}")
        # Split into hat sections
        sections = re.split(r"\n(?=###\s)", text)
        for section in sections:
            if not re.match(r"###\s", section):
                continue
            hm = _HAT_SECTION_RE.match(section)
            if not hm:
                continue
            hat_title = hm.group(1).strip()
            hat_subtitle = (hm.group(2) or "").strip()
            # Only pick up hat/persona sections
            hat_c = _detect_hat_color(hat_title)
            if not hat_c and "hat" not in hat_title.lower() and "persona" not in hat_title.lower():
                continue
            when_m = _APPLY_WHEN_RE.search(section)
            dim_m = _DIMENSIONS_RE.search(section)
            description = hat_subtitle or ""
            if when_m:
                description = when_m.group(1).strip()
            dimensions = [d.strip() for d in dim_m.group(1).split(",")] if dim_m else []
            artifacts.append(AIArtifact(
                id=f"hat_{hat_c or re.sub(r'[^a-z0-9_]', '_', hat_title.lower())}",
                name=hat_title,
                category="hat",
                source_files=[str(md_path.relative_to(repo))],
                description=description,
                hat_color=hat_c,
                hat_emoji=HAT_COLORS.get(hat_c, "🎩"),
                domains=dimensions,
                raw_snippet=section[:500],
            ))
    return artifacts


def _detect_hat_color(text: str) -> str:
    text_lower = text.lower()
    for color in HAT_COLORS:
        if color in text_lower:
            return color
    return ""


# ── Strategy 6: HLF stdlib / *.hlf modules ───────────────────────────────────

def _discover_hlf_modules(repo: Path, log: _DiscoveryLog) -> list[AIArtifact]:
    artifacts: list[AIArtifact] = []
    for hlf_path in repo.rglob("*.hlf"):
        if any(p in hlf_path.parts for p in (".git", "node_modules")):
            continue
        text = _safe_read(hlf_path)
        # Only stdlib/agent-definition files
        mod_m = re.search(r"\[MODULE\]\s+(\w+)", text)
        intent_m = re.search(r"\[INTENT\]\s+(?:\w+\s+)?\"(.+?)\"", text)
        funcs = re.findall(r"\[FUNCTION\]\s+(\w+)", text)
        if not mod_m and not funcs:
            continue
        mod_name = mod_m.group(1) if mod_m else hlf_path.stem
        intent_desc = intent_m.group(1) if intent_m else ""
        log.log(f"HLF module: {hlf_path.relative_to(repo)}")
        artifacts.append(AIArtifact(
            id=f"hlf_{mod_name}",
            name=f"HLF Module: {mod_name}",
            category="hlf_module",
            source_files=[str(hlf_path.relative_to(repo))],
            description=intent_desc or f"HLF standard library module '{mod_name}'",
            skills=[ArtifactSkill(f) for f in funcs],
            raw_snippet=text[:400],
        ))
    return artifacts


# ── Strategy 7: Host function registries ─────────────────────────────────────

def _discover_host_functions(repo: Path, log: _DiscoveryLog) -> list[AIArtifact]:
    artifacts: list[AIArtifact] = []
    candidates = list(repo.rglob("host_functions.json")) + list(repo.rglob("tools.json"))
    for hf_path in candidates:
        if any(p in hf_path.parts for p in (".git", "node_modules")):
            continue
        try:
            data = json.loads(_safe_read(hf_path))
        except json.JSONDecodeError:
            continue
        functions = data.get("functions", data if isinstance(data, list) else [])
        if not isinstance(functions, list):
            continue
        log.log(f"Host functions: {hf_path.relative_to(repo)} — {len(functions)} entries")
        for fn in functions:
            if not isinstance(fn, dict):
                continue
            name = fn.get("name", "?")
            tiers = fn.get("tier", [])
            if isinstance(tiers, str):
                tiers = [tiers]
            artifacts.append(AIArtifact(
                id=f"hfn_{name.lower()}",
                name=f"Host Fn: {name}",
                category="host_function",
                source_files=[str(hf_path.relative_to(repo))],
                description=(
                    f"Returns {fn.get('returns', '?')}. "
                    f"Gas cost: {fn.get('gas', '?')}. "
                    f"Tiers: {', '.join(tiers)}. "
                    f"Backend: {fn.get('backend', '?')}."
                    + (" [sensitive]" if fn.get("sensitive") else "")
                ),
                tier=", ".join(tiers),
                extra={
                    "gas": fn.get("gas"),
                    "backend": fn.get("backend"),
                    "sensitive": fn.get("sensitive", False),
                    "args": fn.get("args", []),
                    "returns": fn.get("returns"),
                },
            ))
    return artifacts


# ── Strategy 8: Capability enums ─────────────────────────────────────────────

def _discover_capability_enums(repo: Path, log: _DiscoveryLog) -> list[AIArtifact]:
    artifacts: list[AIArtifact] = []
    cap_re = re.compile(
        r"class\s+(Capability|Skill|Permission|Role)\s*\(.*Enum.*\).*?:\s*\n((?:\s+\w+\s*=.+\n)+)",
        re.DOTALL
    )
    for py_path in repo.rglob("*.py"):
        if any(p in py_path.parts for p in (".git", "node_modules", "__pycache__", ".venv")):
            continue
        text = _safe_read(py_path)
        for m in cap_re.finditer(text):
            cls_name = m.group(1)
            body = m.group(2)
            members = re.findall(r"(\w+)\s*=", body)
            log.log(f"Capability enum: {py_path.relative_to(repo)}::{cls_name}")
            artifacts.append(AIArtifact(
                id=f"cap_{cls_name.lower()}_{py_path.stem}",
                name=f"{cls_name} Enum ({py_path.stem})",
                category="capability",
                source_files=[str(py_path.relative_to(repo))],
                description=f"Defines {len(members)} {cls_name.lower()} values: {', '.join(members)}",
                skills=[ArtifactSkill(m) for m in members],
            ))
    return artifacts


# ─────────────────────────────────────────────────────────────────
# DEDUPLICATION
# ─────────────────────────────────────────────────────────────────

def _deduplicate(artifacts: list[AIArtifact]) -> list[AIArtifact]:
    """
    Merge artifacts that describe the same thing.
    Priority: JSON registry > markdown persona > AGENTS.md > Python class
    """
    priority = {
        "config_registry": 5,
        "hat": 4,
        "persona": 3,
        "daemon": 3,
        "agent_class": 2,
        "workflow": 2,
        "pipeline_step": 2,
        "hlf_module": 2,
        "host_function": 1,
        "capability": 1,
        "skill": 1,
        "core_module": 1,
    }
    # Build a normalised key per artifact
    by_key: dict[str, AIArtifact] = {}
    for art in artifacts:
        key = _normalise_id(art.id)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = art
        else:
            # Merge: keep the higher-priority artifact, augment with extra files
            if priority.get(art.category, 0) > priority.get(existing.category, 0):
                art.source_files = list(dict.fromkeys(art.source_files + existing.source_files))
                art.skills = art.skills or existing.skills
                art.description = art.description or existing.description
                art.cross_aware = art.cross_aware or existing.cross_aware
                by_key[key] = art
            else:
                for src in art.source_files:
                    if src not in existing.source_files:
                        existing.source_files.append(src)
                if not existing.description and art.description:
                    existing.description = art.description
                if not existing.skills and art.skills:
                    existing.skills = art.skills

    return list(by_key.values())


def _normalise_id(id_str: str) -> str:
    """Canonical key for deduplication."""
    s = id_str.lower()
    s = re.sub(r"^(hat_|hlf_|hfn_|cap_|pipe_|workflow_)", "", s)
    s = re.sub(r"[^a-z0-9]", "", s)
    return s


# ─────────────────────────────────────────────────────────────────
# MAIN DISCOVERY RUNNER
# ─────────────────────────────────────────────────────────────────

def discover(repo: Path, verbose: bool = False) -> tuple[list[AIArtifact], list[str]]:
    """Run all discovery strategies and return deduplicated artifacts + log."""
    log = _DiscoveryLog(verbose)
    all_artifacts: list[AIArtifact] = []

    strategies = [
        ("JSON registries", _discover_json_registry),
        ("Markdown personas", _discover_markdown_personas),
        ("Python classes", _discover_python_classes),
        ("YAML roles/workflows", _discover_yaml_roles),
        ("AGENTS.md / README hats", _discover_agents_md),
        ("HLF stdlib modules", _discover_hlf_modules),
        ("Host function registries", _discover_host_functions),
        ("Capability/Skill enums", _discover_capability_enums),
    ]

    for strategy_name, fn in strategies:
        try:
            found = fn(repo, log)
            log.log(f"Strategy '{strategy_name}': {len(found)} raw artifacts")
            all_artifacts.extend(found)
        except Exception as exc:
            log.log(f"Strategy '{strategy_name}' error: {exc}")

    deduped = _deduplicate(all_artifacts)
    log.log(f"Total after dedup: {len(deduped)} artifacts")
    return deduped, log.entries


# ─────────────────────────────────────────────────────────────────
# MARKDOWN RENDERER
# ─────────────────────────────────────────────────────────────────

def _render_markdown(artifacts: list[AIArtifact], repo: Path, log_entries: list[str],
                     include_snippets: bool = False) -> str:
    by_cat: dict[str, list[AIArtifact]] = defaultdict(list)
    for a in artifacts:
        by_cat[a.category].append(a)

    # Sort within category
    for cat in by_cat:
        by_cat[cat].sort(key=lambda x: x.name)

    total = len(artifacts)
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    repo_name = repo.name

    lines: list[str] = []

    # ── Header ──────────────────────────────────────────────────
    lines += [
        f"# 🗺️ AI Artifact Catalog — `{repo_name}`",
        "",
        f"> **Auto-generated** by `scripts/catalog_agents.py v{__version__}` on {now}  ",
        f"> **{total} artifacts** discovered across {len(by_cat)} categories  ",
        "> Drop `scripts/catalog_agents.py` into any project — zero external dependencies.",
        "",
        "---",
        "",
    ]

    # ── What This File Is ────────────────────────────────────────
    lines += [
        "## Purpose of This Document",
        "",
        "This catalog is the **single source of truth** for every AI agent, persona, hat,",
        "skill, workflow, daemon, and capability in this repository.",
        "Use it to:",
        "",
        "- **Onboard fast** — understand the system's cognitive architecture in minutes",
        "- **Pick the right persona** — find who to invoke for any task",
        "- **Audit capabilities** — see what each agent can and cannot do",
        "- **Debug failures** — trace which artifact is responsible for which behavior",
        "- **Extend the system** — know where to add new agents without duplicating logic",
        "",
        "---",
        "",
    ]

    # ── Quick Summary Table ──────────────────────────────────────
    lines += [
        "## Summary Table",
        "",
        "| # | Artifact | Category | Role / Purpose | Source |",
        "|---|----------|----------|----------------|--------|",
    ]
    for i, a in enumerate(artifacts, 1):
        role_text = (a.role or a.description or "")[:80].replace("|", "·")
        src = a.source_files[0] if a.source_files else "—"
        lines.append(f"| {i} | {a.display_name} | `{a.category}` | {role_text} | `{src}` |")
    lines += ["", "---", ""]

    # ── Mermaid Architecture Diagram ─────────────────────────────
    lines += [
        "## Architecture Diagram",
        "",
        "> Mermaid diagram — renders in GitHub, Obsidian, and most markdown viewers.",
        "",
        "```mermaid",
        "graph TD",
        "    User([👤 User / Intent]) --> GW[🚪 Gateway / Bus]",
        "    GW --> Router[🔀 MoMA Router]",
        "    Router --> Maestro[🎭 Maestro Orchestrator]",
        "    Maestro --> Crew[👥 Crew Orchestrator]",
        "    Maestro --> Sandbox[📦 Agent Sandbox]",
        "",
    ]
    # Add persona nodes
    personas = by_cat.get("persona", [])[:8]
    for p in personas:
        safe_id = re.sub(r"[^a-zA-Z0-9]", "_", p.id)
        lines.append(f"    Crew --> P_{safe_id}[{p.hat_emoji or '🧑'} {p.name.split(' —')[0][:20]}]")
    lines += [
        "",
        "    Router --> HatEngine[🎩 Hat Engine]",
    ]
    # Add hat nodes
    hats = by_cat.get("hat", [])[:6]
    for h in hats:
        safe_id = re.sub(r"[^a-zA-Z0-9]", "_", h.id)
        lines.append(f"    HatEngine --> H_{safe_id}[{h.hat_emoji or '🎩'} {h.name[:20]}]")
    lines += [
        "",
        "    Maestro --> Daemon1[👻 Sentinel Daemon]",
        "    Maestro --> Daemon2[👻 Scribe Daemon]",
        "    Maestro --> Daemon3[👻 Arbiter Daemon]",
        "    Sandbox --> Tools[🔌 Host Functions]",
        "    Sandbox --> HLF[📜 HLF Runtime]",
        "    HLF --> Memory[🧠 Memory System]",
        "```",
        "",
        "---",
        "",
    ]

    # ── Per-Category Sections ────────────────────────────────────
    cat_order = [
        "hat", "persona", "daemon", "agent_class",
        "workflow", "pipeline_step",
        "hlf_module", "host_function",
        "capability", "skill", "core_module", "config_registry",
    ]

    for cat in cat_order:
        entries = by_cat.get(cat)
        if not entries:
            continue
        emoji = CATEGORY_EMOJI.get(cat, "•")
        cat_title = cat.replace("_", " ").title()
        lines += [
            f"## {emoji} {cat_title}s ({len(entries)})",
            "",
        ]

        # Category-level explanation
        cat_intro = _category_intro(cat)
        if cat_intro:
            lines += [cat_intro, ""]

        for a in entries:
            lines += _render_artifact(a, include_snippets)

    # ── Skills Matrix ────────────────────────────────────────────
    lines += _render_skills_matrix(artifacts)

    # ── Invocation Guide ─────────────────────────────────────────
    lines += _render_invocation_guide(artifacts, repo)

    # ── Cross-Reference Index ────────────────────────────────────
    lines += _render_cross_reference(artifacts)

    # ── Discovery Log ─────────────────────────────────────────────
    lines += [
        "## Discovery Log",
        "",
        "<details>",
        "<summary>Click to expand — shows what the scanner found and where</summary>",
        "",
        "```",
    ]
    lines += log_entries
    lines += ["```", "", "</details>", "", "---", ""]

    # ── Footer ───────────────────────────────────────────────────
    lines += [
        f"*Generated by [catalog_agents.py v{__version__}]"
        f"(scripts/catalog_agents.py) — "
        "portable, zero-dependency, framework-agnostic AI artifact discovery.*",
    ]

    return "\n".join(lines) + "\n"


def _category_intro(cat: str) -> str:
    intros = {
        "hat": (
            "Hats are **adversarial review lenses** — each one analyzes the codebase "
            "from a different dimension (security, performance, compliance, etc.). "
            "Hats are activated by the Hat Engine (`agents/core/hat_engine.py`) and "
            "run as LLM prompts against the current system state. "
            "The Meta-Hat Router selects which hats to activate based on diff content (zero LLM tokens)."
        ),
        "persona": (
            "Personas are **named specialist agents** with a defined role, operating philosophy, "
            "domain expertise, and cross-awareness links. They are activated via the Crew Orchestrator "
            "(`agents/core/crew_orchestrator.py`) and run as independent LLM calls that each contribute "
            "to a multi-perspective analysis report. The Consolidator then synthesizes their outputs."
        ),
        "daemon": (
            "Daemons are **always-on background processes** that run continuously alongside the main "
            "agent system. They monitor, audit, and adjudicate in real-time — not on-demand. "
            "They communicate via the `DaemonEventBus` and write to structured log files."
        ),
        "agent_class": (
            "Agent classes are **Python implementations** of autonomous task executors. "
            "They extend the abstract agent pattern with concrete task-handling logic "
            "(file operations, build execution, health probing, etc.). "
            "Each exposes `execute_task()` and `get_agent_profile()` for registry integration."
        ),
        "workflow": (
            "Workflows are **CI/CD automation pipelines** (GitHub Actions) or scheduled "
            "task sequences that orchestrate agents, hats, and tools in a defined order."
        ),
        "pipeline_step": (
            "Pipeline steps are **named stages** in the daily autonomous improvement pipeline "
            "(`config/jules_tasks.yaml`). Each step targets specific files and runs a specialized "
            "agent to improve a particular dimension of the codebase."
        ),
        "hlf_module": (
            "HLF stdlib modules are **pre-compiled HLF programs** that define reusable functions, "
            "constants, and agent lifecycle primitives. They are imported via `[IMPORT]` in HLF programs "
            "and provide the language-level API for agent orchestration."
        ),
        "host_function": (
            "Host functions are **privileged capabilities** exposed to HLF programs from the host OS. "
            "They are the only way HLF programs can interact with external systems (files, network, AI models). "
            "Each has a gas cost, tier requirement, and sensitivity flag."
        ),
        "capability": (
            "Capability enums define **what a provider or agent can do** at the type-system level. "
            "They are used for routing (find the right provider for a given capability) "
            "and for permission checking (does this agent have this capability?)."
        ),
        "core_module": (
            "Core modules are **infrastructure components** — not agents themselves, but systems "
            "that agents depend on (message buses, execution engines, memory stores, sandboxes). "
            "Understanding these is essential for debugging agent behavior."
        ),
    }
    return intros.get(cat, "")


def _render_artifact(a: AIArtifact, include_snippets: bool) -> list[str]:
    lines: list[str] = []
    header = f"### {a.display_name}"
    if a.role and a.role != a.name:
        header += f" — *{a.role}*"
    lines += [header, ""]

    if a.description:
        lines += [a.description, ""]

    # Metadata table
    meta_rows: list[tuple[str, str]] = []
    if a.category:
        meta_rows.append(("Category", f"`{a.category}`"))
    if a.hat_color:
        meta_rows.append(("Hat", f"{a.hat_emoji} {a.hat_color.title()}"))
    if a.model:
        meta_rows.append(("Model", f"`{a.model}`"))
    if a.temperature is not None:
        meta_rows.append(("Temperature", str(a.temperature)))
    if a.max_tokens:
        meta_rows.append(("Max Tokens", str(a.max_tokens)))
    if a.tier:
        meta_rows.append(("Tier", a.tier))
    if a.source_files:
        srcs = " · ".join(f"`{s}`" for s in a.source_files[:3])
        meta_rows.append(("Source", srcs))
    if a.extra.get("gas") is not None:
        meta_rows.append(("Gas Cost", str(a.extra["gas"])))
    if a.extra.get("backend"):
        meta_rows.append(("Backend", f"`{a.extra['backend']}`"))
    if a.extra.get("sensitive"):
        meta_rows.append(("Sensitive", "⚠️ Yes — returns SHA-256 hashed"))

    if meta_rows:
        lines += ["| Property | Value |", "|----------|-------|"]
        for k, v in meta_rows:
            lines.append(f"| **{k}** | {v} |")
        lines.append("")

    if a.operating_principles:
        lines += ["**Operating Principles:**", ""]
        for i, p in enumerate(a.operating_principles, 1):
            lines.append(f"{i}. {p}")
        lines.append("")

    if a.domains:
        lines += ["**Domains / Validation Dimensions:**", ""]
        for d in a.domains:
            lines.append(f"- {d}")
        lines.append("")

    if a.skills:
        lines += ["**Skills / Capabilities:**", ""]
        skill_names = [s.name for s in a.skills]
        # Format as a comma-separated inline list if < 6 skills, else bullets
        if len(skill_names) <= 5:
            lines += [", ".join(f"`{s}`" for s in skill_names), ""]
        else:
            # Two-column layout
            half = (len(skill_names) + 1) // 2
            col1, col2 = skill_names[:half], skill_names[half:]
            lines += ["| Skill | Skill |", "|-------|-------|"]
            for s1, s2 in zip(col1, col2 + [""]):
                lines.append(f"| {s1} | {s2} |")
            lines.append("")

    if a.cross_aware:
        lines += [f"**Cross-Aware With:** {', '.join(a.cross_aware)}", ""]

    if a.invocation:
        lines += [f"**Invocation:** `{a.invocation}`", ""]

    if include_snippets and a.raw_snippet:
        lang = "python" if any(s.endswith(".py") for s in a.source_files) else "text"
        lines += [
            "<details>",
            "<summary>Source Snippet</summary>",
            "",
            f"```{lang}",
            a.raw_snippet[:500],
            "```",
            "",
            "</details>",
            "",
        ]

    lines.append("---")
    lines.append("")
    return lines


def _render_skills_matrix(artifacts: list[AIArtifact]) -> list[str]:
    lines: list[str] = [
        "## 🛠️ Skills Matrix",
        "",
        "All unique skills across all agents and personas, grouped by domain.",
        "",
    ]
    # Collect skills with their owners
    skill_owners: dict[str, list[str]] = defaultdict(list)
    for a in artifacts:
        for s in a.skills:
            if s.name and len(s.name) > 2:
                skill_owners[s.name].append(a.name)

    if not skill_owners:
        lines += ["*No structured skills found.*", ""]
        return lines

    # Group skills heuristically
    buckets: dict[str, list[str]] = {
        "Security & Compliance": [],
        "AI / LLM": [],
        "Frontend & UX": [],
        "Backend & APIs": [],
        "Data & Databases": [],
        "DevOps & Infrastructure": [],
        "Testing & QA": [],
        "Memory & Context": [],
        "HLF & Language": [],
        "Other": [],
    }
    classify_map = {
        "security": "Security & Compliance",
        "owasp": "Security & Compliance",
        "gdpr": "Security & Compliance",
        "inject": "Security & Compliance",
        "crypto": "Security & Compliance",
        "align": "Security & Compliance",
        "sentinel": "Security & Compliance",
        "llm": "AI / LLM",
        "model": "AI / LLM",
        "prompt": "AI / LLM",
        "embedding": "AI / LLM",
        "rag": "AI / LLM",
        "bias": "AI / LLM",
        "react": "Frontend & UX",
        "typescript": "Frontend & UX",
        "css": "Frontend & UX",
        "wcag": "Frontend & UX",
        "aria": "Frontend & UX",
        "ui": "Frontend & UX",
        "ux": "Frontend & UX",
        "accessibility": "Frontend & UX",
        "fastapi": "Backend & APIs",
        "rest": "Backend & APIs",
        "graphql": "Backend & APIs",
        "grpc": "Backend & APIs",
        "websocket": "Backend & APIs",
        "api": "Backend & APIs",
        "sql": "Data & Databases",
        "sqlite": "Data & Databases",
        "redis": "Data & Databases",
        "vector": "Data & Databases",
        "database": "Data & Databases",
        "merkle": "Data & Databases",
        "docker": "DevOps & Infrastructure",
        "kubernetes": "DevOps & Infrastructure",
        "ci": "DevOps & Infrastructure",
        "git": "DevOps & Infrastructure",
        "pipeline": "DevOps & Infrastructure",
        "pytest": "Testing & QA",
        "jest": "Testing & QA",
        "test": "Testing & QA",
        "coverage": "Testing & QA",
        "fuzzing": "Testing & QA",
        "token": "Memory & Context",
        "context": "Memory & Context",
        "gas": "Memory & Context",
        "memory": "Memory & Context",
        "compression": "Memory & Context",
        "hlf": "HLF & Language",
        "grammar": "HLF & Language",
        "compiler": "HLF & Language",
        "bytecode": "HLF & Language",
        "ast": "HLF & Language",
    }
    for skill in sorted(skill_owners.keys()):
        skill_l = skill.lower()
        bucket = "Other"
        for kw, bkt in classify_map.items():
            if kw in skill_l:
                bucket = bkt
                break
        buckets[bucket].append(skill)

    for bucket, skills in buckets.items():
        if not skills:
            continue
        lines += [f"### {bucket}", ""]
        lines += ["| Skill | Used By |", "|-------|---------|"]
        for skill in sorted(skills):
            owners = ", ".join(skill_owners[skill][:3])
            if len(skill_owners[skill]) > 3:
                owners += f" +{len(skill_owners[skill]) - 3}"
            lines.append(f"| {skill} | {owners} |")
        lines.append("")

    return lines


def _render_invocation_guide(artifacts: list[AIArtifact], repo: Path) -> list[str]:
    lines: list[str] = [
        "## 🚀 Invocation Guide",
        "",
        "How to activate, invoke, or run each category of artifact.",
        "",
        "### Hats — Run via Hat Engine",
        "",
        "```python",
        "from agents.core.hat_engine import run_hat, run_all_hats, HAT_DEFINITIONS",
        "",
        "# Run a single hat",
        'report = run_hat("black")   # security analysis',
        'report = run_hat("purple")  # AI safety / compliance',
        "",
        "# Run all hats (full CoVE audit)",
        "reports = run_all_hats()",
        "",
        "# Available hats:",
    ]
    for a in artifacts:
        if a.category == "hat" and a.hat_color:
            lines.append(f'#   "{a.hat_color}" — {a.name}')
    lines += [
        "```",
        "",
        "### Personas — Run via Crew Orchestrator",
        "",
        "```python",
        "from agents.core.crew_orchestrator import run_persona, run_crew",
        "",
        "# Single persona",
        'result = run_persona("sentinel", topic="Review SSRF defenses")',
        "",
        "# Full crew discussion",
        'report = run_crew(topic="Pre-launch security audit")',
        "",
        "# Selective crew (pick personas)",
        'report = run_crew(topic="UX review", personas=["palette", "cove"])',
        "",
        "# Available personas:",
    ]
    for a in artifacts:
        if a.category == "persona":
            lines.append(f'#   "{a.id}" — {a.name}')
    lines += [
        "```",
        "",
        "### All-Persona Gambit — Unleash every agent simultaneously",
        "",
        "```bash",
        "# Preview what would happen",
        "python scripts/persona_gambit.py --list",
        "",
        "# Launch a single persona",
        "python scripts/persona_gambit.py --persona sentinel",
        "",
        "# Launch a single hat",
        "python scripts/persona_gambit.py --hat black",
        "",
        "# Full gambit (creates GitHub issues for every agent)",
        "python scripts/persona_gambit.py --all",
        "```",
        "",
        "### Daemons — Start via DaemonManager",
        "",
        "```python",
        "from agents.core.daemons import DaemonManager, DaemonEventBus",
        "",
        "bus = DaemonEventBus()",
        "manager = DaemonManager(bus)",
        "manager.start_all()  # Starts Sentinel, Scribe, Arbiter daemons",
        "```",
        "",
        "### HLF Programs — Run via hlfc + hlfrun",
        "",
        "```bash",
        "# Compile an HLF program to JSON AST",
        "hlfc my_program.hlf output.json",
        "",
        "# Run an HLF program",
        "hlfrun my_program.hlf",
        "",
        "# Import stdlib modules",
        "# In your .hlf file:",
        "# [IMPORT] agent",
        "# [IMPORT] io",
        "```",
        "",
        "### Catalog This Repo — Re-run the scanner",
        "",
        "```bash",
        "# Regenerate this document",
        "python scripts/catalog_agents.py",
        "",
        "# Scan a different repo",
        "python scripts/catalog_agents.py --repo /path/to/other/project --output docs/AGENTS.md",
        "",
        "# Output JSON for CI tooling",
        "python scripts/catalog_agents.py --format json > agents.json",
        "```",
        "",
    ]
    return lines


def _render_cross_reference(artifacts: list[AIArtifact]) -> list[str]:
    lines: list[str] = [
        "## 🔗 Cross-Reference Index",
        "",
        "Files that define multiple artifacts — useful for understanding coupling.",
        "",
        "| Source File | Artifacts Defined |",
        "|-------------|-------------------|",
    ]
    file_to_arts: dict[str, list[str]] = defaultdict(list)
    for a in artifacts:
        for src in a.source_files[:1]:
            file_to_arts[src].append(a.name)

    for src in sorted(file_to_arts.keys()):
        names = file_to_arts[src]
        if len(names) > 1:
            names_str = ", ".join(names[:4])
            if len(names) > 4:
                names_str += f" +{len(names) - 4}"
            lines.append(f"| `{src}` | {names_str} |")

    lines += ["", "---", ""]
    return lines


# ─────────────────────────────────────────────────────────────────
# JSON RENDERER
# ─────────────────────────────────────────────────────────────────

def _render_json(artifacts: list[AIArtifact], repo: Path) -> str:
    def art_to_dict(a: AIArtifact) -> dict:
        return {
            "id": a.id,
            "name": a.name,
            "category": a.category,
            "source_files": a.source_files,
            "description": a.description,
            "role": a.role,
            "model": a.model,
            "temperature": a.temperature,
            "max_tokens": a.max_tokens,
            "tier": a.tier,
            "hat_color": a.hat_color,
            "skills": [s.name for s in a.skills],
            "cross_aware": a.cross_aware,
            "domains": a.domains,
            "operating_principles": a.operating_principles,
            "tags": a.tags,
            "extra": a.extra,
        }

    payload = {
        "generator": f"catalog_agents.py v{__version__}",
        "generated_at": datetime.now(UTC).isoformat(),
        "repo": repo.name,
        "total": len(artifacts),
        "by_category": {
            cat: len([a for a in artifacts if a.category == cat])
            for cat in CATEGORIES
        },
        "artifacts": [art_to_dict(a) for a in artifacts],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────
# CLI ENTRY POINT
# ─────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[1].strip(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  python scripts/catalog_agents.py\n"
               "  python scripts/catalog_agents.py --repo /other/project --output docs/AGENTS.md\n"
               "  python scripts/catalog_agents.py --format json > catalog.json\n"
               "  python scripts/catalog_agents.py --verbose --snippets",
    )
    parser.add_argument(
        "--repo", type=Path, default=Path.cwd(),
        help="Path to the repository root (default: current directory)",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Write output to file (default: docs/AGENTS_CATALOG.md or stdout)",
    )
    parser.add_argument(
        "--format", choices=["markdown", "json"], default="markdown",
        help="Output format (default: markdown)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print discovery log to stderr while scanning",
    )
    parser.add_argument(
        "--snippets", action="store_true",
        help="Include raw source code snippets in the output (makes output larger)",
    )
    parser.add_argument(
        "--version", action="version", version=f"catalog_agents.py v{__version__}",
    )
    args = parser.parse_args()

    repo = args.repo.resolve()
    if not repo.is_dir():
        print(f"Error: '{repo}' is not a directory", file=sys.stderr)
        sys.exit(1)

    print(f"🔍 Scanning {repo} ...", file=sys.stderr)
    artifacts, log_entries = discover(repo, verbose=args.verbose)
    print(f"✅ Found {len(artifacts)} artifacts", file=sys.stderr)

    if args.format == "json":
        output = _render_json(artifacts, repo)
    else:
        output = _render_markdown(artifacts, repo, log_entries, include_snippets=args.snippets)

    # Determine output target
    if args.output:
        out_path = args.output
    elif args.format == "markdown":
        out_path = repo / "docs" / "AGENTS_CATALOG.md"
    else:
        out_path = None  # stdout

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"📄 Written to {out_path}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
