#!/usr/bin/env python3
"""
Hat PR Review — 11-Hat Aegis-Nexus PR Review Engine.

Reusable script that runs the full 11-hat analysis against any GitHub PR
using the real hat_engine.py infrastructure and Ollama LLM backends.

Usage:
    python scripts/hat_pr_review.py --pr 50
    python scripts/hat_pr_review.py --pr 50 --hats black purple orange
    python scripts/hat_pr_review.py --pr 50 --model gemini-3-flash-preview:cloud
    python scripts/hat_pr_review.py --pr 50 --dry-run          # print only, don't post
    python scripts/hat_pr_review.py --pr 50 --post-as-comment   # post as issue comment instead of review

Environment:
    GITHUB_TOKEN  — GitHub personal access token (or uses `gh` CLI auth)
    OLLAMA_HOST   — Ollama endpoint (default: http://localhost:11434)
    BASE_DIR      — Project root (default: auto-detected)

The script:
  1) Fetches the PR diff + file list from GitHub API
  2) Gathers live system context via hat_engine._build_system_context()
  3) Runs each hat's analysis with PR context injected into the user prompt
  4) Formats structured findings into a Markdown review
  5) Posts the review back to the PR (or prints --dry-run output)

Designed for iterative use: Jules submits PR → hats review → Jules reads
findings → Jules corrects → re-submit → hats review again → until clean.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap — ensure project root is on sys.path for hat_engine import
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT))
os.environ.setdefault("BASE_DIR", str(_PROJECT_ROOT))

from agents.core.hat_engine import (  # noqa: E402
    HAT_DEFINITIONS,
    HatReport,
    _build_system_context,
    _call_ollama,
    _load_agent_registry,
    _parse_findings,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("hat_pr_review")

# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

REPO_OWNER = "Grumpified-OGGVCT"
REPO_NAME = "Sovereign_Agentic_OS_with_HLF"
GITHUB_API = "https://api.github.com"


def _get_github_token() -> str:
    """Resolve GitHub token from env or gh CLI."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        return token

    # Fallback: try gh CLI
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    logger.error("No GitHub token found. Set GITHUB_TOKEN env var or install/auth gh CLI.")
    sys.exit(1)


def _github_request(
    endpoint: str,
    token: str,
    method: str = "GET",
    data: dict | None = None,
    accept: str = "application/vnd.github.v3+json",
) -> dict | str:
    """Make an authenticated GitHub API request."""
    url = f"{GITHUB_API}{endpoint}" if endpoint.startswith("/") else endpoint
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": accept,
        "User-Agent": "SovereignOS-HatReview/1.0",
    }

    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read().decode()
            if accept == "application/vnd.github.v3.diff":
                return content
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        logger.error(f"GitHub API error {e.code}: {error_body[:500]}")
        raise


def fetch_pr_info(pr_number: int, token: str) -> dict:
    """Fetch PR metadata (title, body, state, author, etc)."""
    return _github_request(f"/repos/{REPO_OWNER}/{REPO_NAME}/pulls/{pr_number}", token)


def fetch_pr_diff(pr_number: int, token: str) -> str:
    """Fetch the raw unified diff for a PR."""
    return _github_request(
        f"/repos/{REPO_OWNER}/{REPO_NAME}/pulls/{pr_number}",
        token,
        accept="application/vnd.github.v3.diff",
    )


def fetch_pr_files(pr_number: int, token: str) -> list[dict]:
    """Fetch list of changed files with stats."""
    files = []
    page = 1
    while True:
        batch = _github_request(
            f"/repos/{REPO_OWNER}/{REPO_NAME}/pulls/{pr_number}/files?per_page=100&page={page}",
            token,
        )
        if not batch:
            break
        files.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return files


def fetch_pr_status(pr_number: int, token: str) -> dict:
    """Fetch CI/check status for the PR head commit."""
    pr_info = fetch_pr_info(pr_number, token)
    head_sha = pr_info.get("head", {}).get("sha", "")
    if not head_sha:
        return {"state": "unknown", "checks": []}

    try:
        status = _github_request(f"/repos/{REPO_OWNER}/{REPO_NAME}/commits/{head_sha}/status", token)
        checks = _github_request(f"/repos/{REPO_OWNER}/{REPO_NAME}/commits/{head_sha}/check-runs", token)
        return {
            "state": status.get("state", "unknown"),
            "statuses": status.get("statuses", []),
            "check_runs": checks.get("check_runs", []),
        }
    except Exception as e:
        logger.warning(f"Could not fetch CI status: {e}")
        return {"state": "unknown"}


# ---------------------------------------------------------------------------
# Hat-specific PR prompt builder
# ---------------------------------------------------------------------------


def _build_pr_user_prompt(
    hat_def: dict,
    pr_info: dict,
    diff_text: str,
    file_summary: str,
    system_context: str,
    ci_status: dict | None = None,
) -> str:
    """Build the user prompt that combines PR diff with hat focus area."""
    # Truncate diff if too large (keep most relevant 12K chars)
    max_diff = 12000
    if len(diff_text) > max_diff:
        diff_text = diff_text[:max_diff] + f"\n\n... [TRUNCATED — {len(diff_text) - max_diff} chars omitted] ..."

    ci_section = ""
    if ci_status:
        ci_state = ci_status.get("state", "unknown")
        check_runs = ci_status.get("check_runs", [])
        if check_runs:
            ci_lines = [f"  - {c['name']}: {c.get('conclusion', c.get('status', '?'))}" for c in check_runs[:10]]
            ci_section = f"\n=== CI STATUS ({ci_state}) ===\n" + "\n".join(ci_lines)
        else:
            ci_section = f"\n=== CI STATUS: {ci_state} ==="

    return (
        f"Review Pull Request #{pr_info.get('number', '?')} from your "
        f"{hat_def['name']} perspective.\n\n"
        f"PR Title: {pr_info.get('title', 'N/A')}\n"
        f"PR Author: {pr_info.get('user', {}).get('login', 'unknown')}\n"
        f"PR State: {pr_info.get('state', 'unknown')}\n"
        f"Mergeable: {pr_info.get('mergeable_state', 'unknown')}\n\n"
        f"Focus area: {hat_def['focus']}\n\n"
        f"=== CHANGED FILES ===\n{file_summary}\n"
        f"{ci_section}\n\n"
        f"=== SYSTEM CONTEXT ===\n{system_context}\n\n"
        f"=== PR DIFF ===\n{diff_text}\n\n"
        f"Analyze this PR for issues from your {hat_def['name']} perspective. "
        f"For each finding, rate severity and provide actionable recommendations.\n"
        f"Return your findings as a JSON array of objects with keys: "
        f"severity, title, description, recommendation."
    )


# ---------------------------------------------------------------------------
# Run all hats against a PR
# ---------------------------------------------------------------------------


def run_hat_pr_review(
    hat_name: str,
    hat_def: dict,
    pr_info: dict,
    diff_text: str,
    file_summary: str,
    system_context: str,
    ci_status: dict | None = None,
    model: str | None = None,
) -> HatReport:
    """Run a single hat analysis against a PR diff."""
    # Resolve agent profile
    agent_name = hat_def.get("agent_name")
    registry = _load_agent_registry()
    agent_profile = registry.get(agent_name, {}) if agent_name else {}

    effective_model = model or agent_profile.get("model")
    restrictions = agent_profile.get("restrictions", {})

    if agent_profile:
        logger.info(
            f"  Agent '{agent_name}': model={agent_profile.get('model')}, provider={agent_profile.get('provider')}"
        )

    user_prompt = _build_pr_user_prompt(hat_def, pr_info, diff_text, file_summary, system_context, ci_status)

    logger.info(f"  Sending {len(user_prompt)} chars to Ollama...")
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


def run_all_hats_pr(
    pr_info: dict,
    diff_text: str,
    file_summary: str,
    system_context: str,
    ci_status: dict | None = None,
    hats: list[str] | None = None,
    model: str | None = None,
) -> list[HatReport]:
    """Run all (or specified) hats against a PR."""
    if hats is None:
        hats = list(HAT_DEFINITIONS.keys())

    reports = []
    total = len(hats)
    for i, hat_name in enumerate(hats, 1):
        hat_def = HAT_DEFINITIONS.get(hat_name)
        if hat_def is None:
            logger.warning(f"Unknown hat: {hat_name}, skipping")
            continue

        logger.info(f"[{i}/{total}] Running {hat_def['emoji']} {hat_def['name']}...")
        start = time.time()

        report = run_hat_pr_review(
            hat_name,
            hat_def,
            pr_info,
            diff_text,
            file_summary,
            system_context,
            ci_status,
            model,
        )

        elapsed = time.time() - start
        logger.info(f"  {report.emoji} {hat_name}: {len(report.findings)} findings ({elapsed:.1f}s)")
        reports.append(report)

    return reports


# ---------------------------------------------------------------------------
# Format findings as GitHub Markdown
# ---------------------------------------------------------------------------

SEVERITY_ICONS = {
    "CRITICAL": "🔴",
    "HIGH": "🟠",
    "MEDIUM": "🟡",
    "LOW": "🟢",
    "INFO": "ℹ️",
}


def format_review_markdown(
    pr_info: dict,
    reports: list[HatReport],
    file_summary: str,
    ci_status: dict | None = None,
) -> str:
    """Format all hat reports into a structured GitHub review comment."""
    lines = [
        "## 🎩 11-Hat Aegis-Nexus Automated PR Review",
        "",
        f"**PR #{pr_info.get('number')}**: {pr_info.get('title', 'N/A')}",
        f"**Author**: @{pr_info.get('user', {}).get('login', 'unknown')}",
        f"**Mergeable**: {pr_info.get('mergeable_state', 'unknown')}",
        "",
    ]

    # CI status summary
    if ci_status:
        ci_state = ci_status.get("state", "unknown")
        state_icon = {"success": "✅", "failure": "❌", "pending": "⏳"}.get(ci_state, "❓")
        lines.append(f"**CI Status**: {state_icon} {ci_state}")
        for run in ci_status.get("check_runs", [])[:5]:
            conclusion = run.get("conclusion", run.get("status", "?"))
            c_icon = {"success": "✅", "failure": "❌", "neutral": "⚪"}.get(conclusion, "⏳")
            lines.append(f"  - {c_icon} {run['name']}: {conclusion}")
        lines.append("")

    lines.append("---")
    lines.append("")

    # Per-hat sections
    total_findings = 0
    critical_count = 0
    hat_verdicts = []

    for report in reports:
        hat_def = HAT_DEFINITIONS.get(report.hat, {})
        hat_title = hat_def.get("name", report.hat.title())
        lines.append(f"### {report.emoji} {hat_title}")
        lines.append(f"**Focus**: {report.focus}")
        lines.append("")

        if report.error:
            lines.append(f"> ⚠️ Error: {report.error}")
            lines.append("")
            hat_verdicts.append((report.emoji, report.hat, "❌ Error"))
            continue

        if not report.findings:
            lines.append("> ✅ No issues found from this perspective.")
            lines.append("")
            hat_verdicts.append((report.emoji, report.hat, "✅ Clean"))
            continue

        # Severity summary
        sev_counts = {}
        for f in report.findings:
            sev_counts[f.severity] = sev_counts.get(f.severity, 0) + 1
            total_findings += 1
            if f.severity == "CRITICAL":
                critical_count += 1

        sev_str = ", ".join(f"{SEVERITY_ICONS.get(s, '?')} {s}: {c}" for s, c in sorted(sev_counts.items()))
        lines.append(f"**Findings**: {len(report.findings)} ({sev_str})")
        lines.append("")

        # Finding details
        for _j, finding in enumerate(report.findings, 1):
            icon = SEVERITY_ICONS.get(finding.severity, "?")
            lines.append("<details>")
            lines.append(f"<summary>{icon} <b>[{finding.severity}]</b> {finding.title}</summary>")
            lines.append("")
            lines.append(f"**Description**: {finding.description}")
            lines.append("")
            lines.append(f"**Recommendation**: {finding.recommendation}")
            lines.append("")
            lines.append("</details>")
            lines.append("")

        # Hat verdict
        if any(f.severity == "CRITICAL" for f in report.findings):
            hat_verdicts.append((report.emoji, report.hat, "🔴 Critical Issues"))
        elif any(f.severity == "HIGH" for f in report.findings):
            hat_verdicts.append((report.emoji, report.hat, "🟠 High Issues"))
        elif report.findings:
            hat_verdicts.append((report.emoji, report.hat, "🟡 Minor Issues"))
        else:
            hat_verdicts.append((report.emoji, report.hat, "✅ Clean"))

        lines.append("---")
        lines.append("")

    # Summary table
    lines.append("### 🏆 Summary")
    lines.append("")
    lines.append("| Hat | Verdict |")
    lines.append("|-----|---------|")
    for emoji, name, verdict in hat_verdicts:
        lines.append(f"| {emoji} {name.title()} | {verdict} |")
    lines.append("")
    lines.append(f"**Total Findings**: {total_findings} | **Critical**: {critical_count}")
    lines.append("")

    # Overall recommendation
    if critical_count > 0:
        lines.append("> 🔴 **BLOCK MERGE** — Critical issues must be resolved before merging.")
    elif total_findings > 5:
        lines.append("> 🟠 **REQUEST CHANGES** — Multiple issues should be addressed.")
    elif total_findings > 0:
        lines.append("> 🟡 **COMMENT** — Minor issues noted; consider addressing before merge.")
    else:
        lines.append("> ✅ **APPROVE** — All hats passed. Ready to merge.")

    lines.append("")
    lines.append("---")
    lines.append(
        f"*Generated by `hat_pr_review.py` at {time.strftime('%Y-%m-%d %H:%M:%S')} using Sovereign OS Hat Engine*"
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Post review to GitHub
# ---------------------------------------------------------------------------


def post_pr_review(
    pr_number: int,
    review_body: str,
    token: str,
    as_comment: bool = False,
) -> str:
    """Post the review to GitHub. Returns the URL of the posted review."""
    if as_comment:
        # Post as issue comment (simpler, no review status)
        result = _github_request(
            f"/repos/{REPO_OWNER}/{REPO_NAME}/issues/{pr_number}/comments",
            token,
            method="POST",
            data={"body": review_body},
        )
        url = result.get("html_url", "")
        logger.info(f"Posted as comment: {url}")
        return url
    else:
        # Post as PR review (COMMENT event — no approve/reject)
        result = _github_request(
            f"/repos/{REPO_OWNER}/{REPO_NAME}/pulls/{pr_number}/reviews",
            token,
            method="POST",
            data={"body": review_body, "event": "COMMENT"},
        )
        url = result.get("html_url", "")
        logger.info(f"Posted as PR review: {url}")
        return url


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="11-Hat Aegis-Nexus PR Review — analyze any PR with the full hat engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/hat_pr_review.py --pr 50\n"
            "  python scripts/hat_pr_review.py --pr 50 --hats black purple orange\n"
            "  python scripts/hat_pr_review.py --pr 50 --dry-run\n"
            "  python scripts/hat_pr_review.py --pr 50 --model gemini-3-flash-preview:cloud\n"
        ),
    )
    parser.add_argument(
        "--pr",
        type=int,
        required=True,
        help="Pull request number to review",
    )
    parser.add_argument(
        "--hats",
        nargs="+",
        default=None,
        choices=list(HAT_DEFINITIONS.keys()),
        help="Run only specific hats (default: all 11)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override Ollama model for all hats (default: per-agent registry)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print review to stdout without posting to GitHub",
    )
    parser.add_argument(
        "--post-as-comment",
        action="store_true",
        help="Post as issue comment instead of PR review",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Save review to a local file in addition to posting",
    )
    parser.add_argument(
        "--diff-file",
        type=str,
        default=None,
        help="Read diff from a local file instead of GitHub API (for offline use)",
    )
    parser.add_argument(
        "--gh-token",
        type=str,
        default=None,
        help="Explicit GitHub token (overrides env/gh CLI)",
    )

    args = parser.parse_args()

    # 1) Resolve token (may not be needed for --diff-file + --dry-run)
    token = None
    if args.gh_token:
        token = args.gh_token
    elif not (args.diff_file and args.dry_run):
        logger.info("Resolving GitHub token...")
        token = _get_github_token()

    # 2) Fetch or load PR data
    if args.diff_file:
        # Offline mode — read diff from file
        logger.info(f"Reading diff from file: {args.diff_file}")
        diff_text = Path(args.diff_file).read_text(encoding="utf-8", errors="replace")
        logger.info(f"  Diff size: {len(diff_text)} chars")

        # Build minimal PR info from diff
        changed_files = set()
        for line in diff_text.splitlines():
            if line.startswith("diff --git"):
                parts = line.split()
                if len(parts) >= 4:
                    changed_files.add(parts[3].lstrip("b/"))

        pr_info = {
            "number": args.pr,
            "title": f"PR #{args.pr} (offline review)",
            "user": {"login": "jules[bot]"},
            "state": "open",
            "mergeable_state": "unknown",
        }
        file_summary = f"Files changed: {len(changed_files)}\n" + "\n".join(f"  {f}" for f in sorted(changed_files))
        ci_status = None
    else:
        logger.info(f"Fetching PR #{args.pr} from {REPO_OWNER}/{REPO_NAME}...")
        pr_info = fetch_pr_info(args.pr, token)
        logger.info(f"  Title: {pr_info.get('title')}")
        logger.info(f"  Author: {pr_info.get('user', {}).get('login')}")
        logger.info(f"  State: {pr_info.get('state')} | Mergeable: {pr_info.get('mergeable_state')}")

        logger.info("Fetching PR diff...")
        diff_text = fetch_pr_diff(args.pr, token)
        logger.info(f"  Diff size: {len(diff_text)} chars")

        logger.info("Fetching changed files...")
        files = fetch_pr_files(args.pr, token)
        file_summary_lines = []
        total_additions = 0
        total_deletions = 0
        for f in files:
            adds = f.get("additions", 0)
            dels = f.get("deletions", 0)
            total_additions += adds
            total_deletions += dels
            file_summary_lines.append(f"  {f['filename']} (+{adds}/-{dels}) [{f.get('status', '?')}]")
        file_summary = f"Files changed: {len(files)} | +{total_additions}/-{total_deletions}\n" + "\n".join(
            file_summary_lines
        )
        logger.info(f"  {len(files)} files changed (+{total_additions}/-{total_deletions})")

    if not args.diff_file:
        logger.info("Fetching CI status...")
        ci_status = fetch_pr_status(args.pr, token)
        logger.info(f"  CI state: {ci_status.get('state', 'unknown')}")

    # 3) Build system context from live project
    logger.info("Building system context from live project...")
    system_context = _build_system_context()
    logger.info(f"  System context: {len(system_context)} chars")

    # 4) Run hats
    logger.info("=" * 60)
    logger.info("STARTING 11-HAT PR REVIEW")
    logger.info("=" * 60)
    start_time = time.time()

    reports = run_all_hats_pr(
        pr_info=pr_info,
        diff_text=diff_text,
        file_summary=file_summary,
        system_context=system_context,
        ci_status=ci_status,
        hats=args.hats,
        model=args.model,
    )

    elapsed = time.time() - start_time
    total_findings = sum(len(r.findings) for r in reports)
    logger.info("=" * 60)
    logger.info(f"REVIEW COMPLETE: {total_findings} findings in {elapsed:.1f}s ({len(reports)} hats)")
    logger.info("=" * 60)

    # 5) Format review
    review_body = format_review_markdown(pr_info, reports, file_summary, ci_status)

    # 6) Output / Post
    if args.output:
        Path(args.output).write_text(review_body, encoding="utf-8")
        logger.info(f"Review saved to: {args.output}")

    if args.dry_run:
        print("\n" + "=" * 60)
        print("DRY RUN — Review would be posted as follows:")
        print("=" * 60)
        print(review_body)
    else:
        logger.info("Posting review to GitHub...")
        url = post_pr_review(
            args.pr,
            review_body,
            token,
            as_comment=args.post_as_comment,
        )
        logger.info(f"✅ Review posted: {url}")

    # Summary to stdout
    print(f"\n{'=' * 40}")
    print(f"  PR #{args.pr}: {total_findings} findings from {len(reports)} hats")
    print(f"  Time: {elapsed:.1f}s")
    for r in reports:
        crits = sum(1 for f in r.findings if f.severity == "CRITICAL")
        highs = sum(1 for f in r.findings if f.severity == "HIGH")
        tag = ""
        if crits:
            tag = f" 🔴 {crits} CRITICAL"
        elif highs:
            tag = f" 🟠 {highs} HIGH"
        print(f"  {r.emoji} {r.hat:8s}: {len(r.findings)} findings{tag}")
    print(f"{'=' * 40}")

    # Exit code: non-zero if critical findings
    critical_count = sum(1 for r in reports for f in r.findings if f.severity == "CRITICAL")
    if critical_count > 0:
        logger.warning(f"Exiting with code 1 ({critical_count} critical findings)")
        sys.exit(1)


if __name__ == "__main__":
    main()
