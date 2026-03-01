#!/usr/bin/env python3
"""
Copilot Agent Factory — Dynamic GitHub Copilot Agent Runner Management.

Creates, dispatches, and monitors GitHub Copilot agent tasks by generating
precisely-scoped GitHub issues that Copilot will pick up and resolve with PRs.

Usage:
    # Create an HLF grammar evolution task
    uv run python scripts/copilot_factory.py hlf-evolve

    # Create an ALIGN hardening task
    uv run python scripts/copilot_factory.py align-harden

    # Create a custom task
    uv run python scripts/copilot_factory.py custom --title "Fix X" --body "Details..."

    # List active Copilot tasks
    uv run python scripts/copilot_factory.py status

    # Dry-run (preview the issue without creating it)
    uv run python scripts/copilot_factory.py hlf-evolve --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Project root ──
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── GitHub config ──
REPO_OWNER = "Grumpified-OGGVCT"
REPO_NAME = "Sovereign_Agentic_OS_with_HLF"
REPO_FULL = f"{REPO_OWNER}/{REPO_NAME}"


# ═══════════════════════════════════════════════════════════════════
# AGENT TEMPLATES — Pre-built Copilot task configurations
# ═══════════════════════════════════════════════════════════════════


@dataclass
class AgentTemplate:
    """A pre-configured Copilot agent task template."""

    name: str
    title: str
    body: str
    labels: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)  # files to focus on
    invariants: list[str] = field(default_factory=list)  # rules to enforce

    def to_issue_body(self) -> str:
        """Generate the full issue body with invariants and targets."""
        sections = [self.body]

        if self.targets:
            sections.append("\n## Target Files\n")
            sections.extend(f"- `{t}`" for t in self.targets)

        if self.invariants:
            sections.append("\n## Invariants (Non-Negotiable)\n")
            sections.extend(f"- ❌ {inv}" for inv in self.invariants)

        sections.append("\n## Acceptance Criteria\n")
        sections.append("- [ ] All existing tests pass (`uv run pytest tests/ -v`)")
        sections.append("- [ ] No test files deleted or simplified")
        sections.append("- [ ] Changes are additive only")
        sections.append("- [ ] PR includes descriptive commit message")

        return "\n".join(sections)


# ── Template Registry ──

TEMPLATES: dict[str, AgentTemplate] = {
    "hlf-evolve": AgentTemplate(
        name="HLF Grammar Evolver",
        title="[Copilot] Evolve HLF Grammar — Additive Syntax Extensions",
        body=(
            "## Task: HLF Grammar Evolution\n\n"
            "Analyze the current HLF grammar and propose **additive** extensions:\n"
            "- New symbolic tags for emerging use cases\n"
            "- Enhanced type annotations for stronger validation\n"
            "- Improved error messages in the parser\n"
            "- Additional test fixtures for edge cases\n\n"
            "**DO NOT** remove any existing tags, syntax, or tests.\n"
            "**DO NOT** simplify the grammar — only expand it.\n\n"
            "After making changes, run:\n"
            "```bash\n"
            "uv run pytest tests/test_hlf.py tests/test_installation.py -v\n"
            "```"
        ),
        labels=["copilot", "hlf", "enhancement"],
        targets=[
            "hlf/hlfc.py",
            "hlf/hlflint.py",
            "hlf/hlffmt.py",
            "governance/hlf_grammar.lark",
            "tests/test_hlf.py",
        ],
        invariants=[
            "Do NOT delete or modify existing HLF tags",
            "Do NOT reduce grammar coverage",
            "Do NOT simplify error handling",
            "All 194+ tests must pass after changes",
        ],
    ),
    "hlf-test": AgentTemplate(
        name="HLF Grammar Test Agent",
        title="[Copilot] Deep HLF Grammar Validation + Translation Metrics",
        body=(
            "## Task: HLF Grammar Testing & Translation Metrics\n\n"
            "Enhance the HLF test suite with:\n"
            "1. **Translation metrics**: measure token compression ratio (HLF vs JSON)\n"
            "2. **Cross-model alignment tests**: verify same HLF compiles identically\n"
            "3. **Regression tests** for every symbolic tag in the grammar\n"
            "4. **Benchmark report**: output parse time and AST depth metrics\n\n"
            "Create a new test file `tests/test_hlf_metrics.py` with these tests.\n"
            "Print a summary table to stdout showing compression ratios."
        ),
        labels=["copilot", "hlf", "testing", "metrics"],
        targets=[
            "hlf/hlfc.py",
            "tests/test_hlf.py",
            "tests/test_hlf_metrics.py",
        ],
        invariants=[
            "Do NOT modify existing test files",
            "New tests must be additive",
            "All tests must pass",
        ],
    ),
    "align-harden": AgentTemplate(
        name="ALIGN Hardener",
        title="[Copilot] Harden ALIGN Governance — Rule Expansion & Validation",
        body=(
            "## Task: ALIGN Ledger Hardening\n\n"
            "Review and expand ALIGN governance:\n"
            "- Add new rules for emerging threat patterns\n"
            "- Validate all existing rules have proper test coverage\n"
            "- Check sentinel_gate.py enforces every rule in the ledger\n"
            "- Ensure no rule can be bypassed via edge-case formatting\n\n"
            "Run the full governance test suite after changes:\n"
            "```bash\n"
            "uv run pytest tests/test_policy.py tests/test_sentinel.py -v\n"
            "```"
        ),
        labels=["copilot", "governance", "security"],
        targets=[
            "governance/ALIGN_LEDGER.yaml",
            "agents/gateway/sentinel_gate.py",
            "tests/test_policy.py",
            "tests/test_sentinel.py",
        ],
        invariants=[
            "Do NOT remove any existing ALIGN rules",
            "Do NOT weaken any existing constraints",
            "All security boundaries must be preserved",
        ],
    ),
    "gui-feature": AgentTemplate(
        name="GUI Feature Builder",
        title="[Copilot] GUI Enhancement — New Dashboard Features",
        body=(
            "## Task: GUI Dashboard Enhancement\n\n"
            "Improve the C-SOC GUI dashboard:\n"
            "- Add new visualization widgets for agent activity\n"
            "- Enhance the real-time metrics display\n"
            "- Improve responsive layout for different screen sizes\n"
            "- Add tooltips and help text for new users\n\n"
            "**Theme**: Must use the dark mode palette defined in "
            "`.streamlit/config.toml`.\n"
            "**DO NOT** remove any existing UI components."
        ),
        labels=["copilot", "gui", "enhancement"],
        targets=["gui/app.py", ".streamlit/config.toml"],
        invariants=[
            "Do NOT remove existing UI sections",
            "Dark mode must remain the default theme",
            "All existing buttons and controls must work",
        ],
    ),
    "ci-fix": AgentTemplate(
        name="CI Fixer",
        title="[Copilot] CI Pipeline Fix & Hardening",
        body=(
            "## Task: CI Pipeline Fix\n\n"
            "Investigate and fix any CI failures:\n"
            "- Check `.github/workflows/ci.yml` for issues\n"
            "- Ensure all jobs pass on Ubuntu latest with Python 3.12\n"
            "- Add missing dependencies to `pyproject.toml` if needed\n"
            "- Verify the `deep-install-verify` job works correctly\n\n"
            "Run locally first:\n"
            "```bash\n"
            "uv run pytest tests/ -v && uv run python scripts/hlf_token_lint.py\n"
            "```"
        ),
        labels=["copilot", "ci", "bug"],
        targets=[".github/workflows/ci.yml", "pyproject.toml"],
        invariants=[
            "Do NOT remove any CI jobs",
            "Do NOT reduce test coverage",
            "All 194+ tests must pass after changes",
        ],
    ),
}


# ═══════════════════════════════════════════════════════════════════
# GH CLI INTERFACE — Creates issues and assigns Copilot
# ═══════════════════════════════════════════════════════════════════


def _run_gh(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a `gh` CLI command."""
    cmd = ["gh"] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
        check=check,
        cwd=str(_PROJECT_ROOT),
    )


def create_copilot_issue(template: AgentTemplate, dry_run: bool = False) -> dict[str, Any]:
    """Create a GitHub issue and assign Copilot to it."""
    body = template.to_issue_body()
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    title = f"{template.title} ({timestamp})"

    if dry_run:
        print("\n" + "=" * 60)
        print(f"DRY RUN — Would create issue: {title}")
        print("=" * 60)
        print(f"Labels: {', '.join(template.labels)}")
        print(f"\n{body}")
        print("=" * 60)
        return {"dry_run": True, "title": title}

    # Create the issue
    label_args = []
    for label in template.labels:
        label_args.extend(["--label", label])

    try:
        result = _run_gh(
            [
                "issue",
                "create",
                "--repo",
                REPO_FULL,
                "--title",
                title,
                "--body",
                body,
            ]
            + label_args,
            check=True,
        )
        issue_url = result.stdout.strip()
        issue_number = issue_url.split("/")[-1] if "/" in issue_url else "unknown"
        print(f"✅ Issue created: {issue_url}")

        # Attempt to assign Copilot
        try:
            _run_gh(
                [
                    "issue",
                    "edit",
                    issue_number,
                    "--repo",
                    REPO_FULL,
                    "--add-assignee",
                    "copilot",
                ],
                check=False,
            )
            print(f"🤖 Copilot assigned to issue #{issue_number}")
        except Exception:
            print(f"⚠️  Could not auto-assign Copilot. Assign manually: {issue_url}")

        return {
            "success": True,
            "issue_url": issue_url,
            "issue_number": issue_number,
        }

    except subprocess.CalledProcessError as exc:
        print(f"❌ Failed to create issue: {exc.stderr}", file=sys.stderr)
        return {"success": False, "error": exc.stderr}
    except FileNotFoundError:
        print("❌ `gh` CLI not found. Install from: https://cli.github.com", file=sys.stderr)
        return {"success": False, "error": "gh CLI not installed"}


def list_copilot_issues() -> None:
    """List all open issues with the 'copilot' label."""
    try:
        result = _run_gh(
            [
                "issue",
                "list",
                "--repo",
                REPO_FULL,
                "--label",
                "copilot",
                "--state",
                "open",
                "--json",
                "number,title,assignees,state,createdAt",
            ],
            check=True,
        )
        issues = json.loads(result.stdout)
        if not issues:
            print("No active Copilot tasks found.")
            return

        print(f"\n{'#':>5}  {'Created':12}  Title")
        print("-" * 60)
        for issue in issues:
            created = issue["createdAt"][:10]
            assignees = ", ".join(a["login"] for a in issue.get("assignees", []))
            status = f" [{assignees}]" if assignees else ""
            print(f"{issue['number']:>5}  {created}  {issue['title']}{status}")
        print()

    except subprocess.CalledProcessError as exc:
        print(f"❌ Failed to list issues: {exc.stderr}", file=sys.stderr)
    except FileNotFoundError:
        print("❌ `gh` CLI not found.", file=sys.stderr)


def create_custom_issue(title: str, body: str, labels: list[str] | None = None) -> dict:
    """Create a custom Copilot issue."""
    template = AgentTemplate(
        name="Custom",
        title=title,
        body=body,
        labels=["copilot"] + (labels or []),
    )
    return create_copilot_issue(template)


# ═══════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Copilot Agent Factory — Create and manage GitHub Copilot agent tasks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Available templates:
  hlf-evolve     Evolve HLF grammar with additive extensions
  hlf-test       Deep HLF validation + translation metrics
  align-harden   Harden ALIGN governance rules
  gui-feature    GUI dashboard enhancements
  ci-fix         CI pipeline fixes

Special commands:
  status         List active Copilot tasks
  custom         Create a custom Copilot task
  list-templates Show all available templates
        """,
    )
    parser.add_argument(
        "template",
        help="Template name or command (status, custom, list-templates)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without creating")
    parser.add_argument("--title", help="Custom issue title (for 'custom' template)")
    parser.add_argument("--body", help="Custom issue body (for 'custom' template)")
    parser.add_argument("--labels", help="Comma-separated labels (for 'custom' template)")

    args = parser.parse_args()

    if args.template == "status":
        list_copilot_issues()
    elif args.template == "list-templates":
        print("\nAvailable Copilot Agent Templates:")
        print("-" * 50)
        for key, tmpl in TEMPLATES.items():
            print(f"  {key:20} {tmpl.name}")
            print(f"  {'':20} Targets: {', '.join(tmpl.targets[:3])}")
            print()
    elif args.template == "custom":
        if not args.title:
            print("❌ --title is required for custom template", file=sys.stderr)
            sys.exit(1)
        labels = args.labels.split(",") if args.labels else None
        create_custom_issue(
            title=args.title,
            body=args.body or "Custom Copilot task",
            labels=labels,
        )
    elif args.template in TEMPLATES:
        create_copilot_issue(TEMPLATES[args.template], dry_run=args.dry_run)
    else:
        print(f"❌ Unknown template: {args.template}", file=sys.stderr)
        print(f"Available: {', '.join(TEMPLATES.keys())}, status, custom", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
