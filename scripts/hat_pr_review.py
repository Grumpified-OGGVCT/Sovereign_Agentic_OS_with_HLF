#!/usr/bin/env python3
"""
Hat PR Review — 11-Hat Aegis-Nexus PR Review Engine.

Reusable script that runs the full 11-hat analysis against any GitHub PR
using the real hat_engine.py infrastructure. Supports both Ollama (local)
and cloud Gemini API backends.

Usage:
    python scripts/hat_pr_review.py --pr 50
    python scripts/hat_pr_review.py --pr 50 --hats black purple orange
    python scripts/hat_pr_review.py --pr 50 --cloud-backend gemini
    python scripts/hat_pr_review.py --pr 50 --dry-run
    python scripts/hat_pr_review.py --pr 50 --diff-file pr_diff.txt --dry-run

Environment:
    GEMINI_API_KEY  — Google Gemini API key (for --cloud-backend gemini)
    GITHUB_TOKEN    — GitHub token with models scope (for --cloud-backend github)
    OLLAMA_HOST     — Ollama endpoint (default: http://localhost:11434)
    BASE_DIR        — Project root (default: auto-detected)

Circuit Breakers (--cloud-backend only):
  - API Failure: HTTP 4xx/5xx → skip hat, report error
  - Rate Limit: 429 → stop remaining hats, post partial + manual request
  - Timeout: >120s per hat → skip hat, continue others
  - Parse Failure: Non-JSON response 3x → skip hat with warning
  - Budget Guard: >$X API cost → abort remaining hats
  - All Failed: 0 successful hats → full manual review request

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
from dataclasses import asdict
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
    HatFinding,
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
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    logger.error(
        "No GitHub token found. Set GITHUB_TOKEN env var or install/auth gh CLI."
    )
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
    return _github_request(
        f"/repos/{REPO_OWNER}/{REPO_NAME}/pulls/{pr_number}", token
    )


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
        status = _github_request(
            f"/repos/{REPO_OWNER}/{REPO_NAME}/commits/{head_sha}/status", token
        )
        checks = _github_request(
            f"/repos/{REPO_OWNER}/{REPO_NAME}/commits/{head_sha}/check-runs", token
        )
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
# Cloud Gemini backend with circuit breakers
# ---------------------------------------------------------------------------

_GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"
_GEMINI_MODEL = "gemini-2.0-flash"  # fast, cheap, good for structured analysis
_GITHUB_MODELS_URL = "https://models.github.ai/inference/chat/completions"
_GITHUB_MODELS_DEFAULT = "openai/gpt-4o-mini"  # fast + included in Copilot quota
_CIRCUIT_BREAKER_STATE = {
    "rate_limited": False,
    "budget_exhausted": False,
    "consecutive_failures": 0,
    "total_calls": 0,
    "total_input_chars": 0,
    "failed_hats": [],
    "skipped_hats": [],
}


def _call_gemini(
    system_prompt: str,
    user_prompt: str,
    api_key: str,
    model: str | None = None,
    max_retries: int = 3,
    timeout: int = 120,
) -> str:
    """Call Google Gemini API with circuit breaker protections.

    Returns the text response, or empty string on failure.
    Raises CircuitBreakerTripped if rate limited or budget exhausted.
    """
    cb = _CIRCUIT_BREAKER_STATE
    model = model or _GEMINI_MODEL

    # Budget guard: rough estimate — ~4 chars per token, $0.10/M input tokens
    cb["total_input_chars"] += len(system_prompt) + len(user_prompt)
    estimated_cost = (cb["total_input_chars"] / 4) / 1_000_000 * 0.10
    max_budget = float(os.environ.get("HAT_REVIEW_BUDGET_USD", "1.00"))
    if estimated_cost > max_budget:
        cb["budget_exhausted"] = True
        logger.warning(
            f"CIRCUIT BREAKER: Budget guard tripped — estimated ${estimated_cost:.4f} > ${max_budget}"
        )
        raise _CircuitBreakerTripped("budget_exhausted", f"Estimated cost ${estimated_cost:.4f} exceeds budget ${max_budget}")

    url = f"{_GEMINI_API_URL}/{model}:generateContent?key={api_key}"
    payload = json.dumps({
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 4096,
            "responseMimeType": "text/plain",
        },
    }).encode()

    last_error = None
    for attempt in range(1, max_retries + 1):
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
                text = (
                    data.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                )
                cb["total_calls"] += 1
                cb["consecutive_failures"] = 0
                return text
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else ""
            if e.code == 429:
                cb["rate_limited"] = True
                logger.warning("CIRCUIT BREAKER: Rate limited by Gemini API")
                raise _CircuitBreakerTripped("rate_limited", f"HTTP 429: {error_body[:200]}")
            logger.warning(
                f"  Gemini API error {e.code} (attempt {attempt}/{max_retries}): {error_body[:200]}"
            )
            last_error = e
        except Exception as e:
            logger.warning(f"  Gemini call failed (attempt {attempt}/{max_retries}): {e}")
            last_error = e

        # Exponential backoff
        if attempt < max_retries:
            wait = 2 ** attempt
            logger.info(f"  Retrying in {wait}s...")
            time.sleep(wait)

    cb["consecutive_failures"] += 1
    logger.error(f"  All {max_retries} Gemini attempts failed: {last_error}")
    return ""


class _CircuitBreakerTripped(Exception):
    """Raised when a circuit breaker trips (rate limit, budget, etc)."""
    def __init__(self, breaker_type: str, detail: str):
        self.breaker_type = breaker_type
        self.detail = detail
        super().__init__(f"Circuit breaker [{breaker_type}]: {detail}")


def _format_manual_review_request(
    pr_info: dict,
    cb_state: dict,
    partial_reports: list,
) -> str:
    """Format a manual review request when circuit breakers trip."""
    lines = [
        "## ⚡ Circuit Breaker Tripped — Manual Hat Review Required",
        "",
        f"**PR #{pr_info.get('number')}**: {pr_info.get('title', 'N/A')}",
        "",
        "The automated 11-Hat review could not complete. A human reviewer or ",
        "local Ollama-based review is needed.",
        "",
        "### What Happened",
        "",
    ]

    if cb_state.get("rate_limited"):
        lines.append("🔴 **Rate Limited** — The Gemini API returned HTTP 429 (too many requests).")
        lines.append("This typically happens when multiple PRs trigger reviews simultaneously.")
        lines.append("")
    if cb_state.get("budget_exhausted"):
        lines.append(f"🔴 **Budget Guard** — Estimated API cost exceeded the configured budget.")
        lines.append(f"Adjust `HAT_REVIEW_BUDGET_USD` env var to increase the limit.")
        lines.append("")
    if cb_state.get("failed_hats"):
        lines.append(f"🟠 **Failed Hats** ({len(cb_state['failed_hats'])}): {', '.join(cb_state['failed_hats'])}")
        lines.append("")
    if cb_state.get("skipped_hats"):
        lines.append(f"⚪ **Skipped Hats** ({len(cb_state['skipped_hats'])}): {', '.join(cb_state['skipped_hats'])}")
        lines.append("")

    lines.extend([
        "### How to Complete the Review Manually",
        "",
        "Run the hat review locally with Ollama (no cloud API needed):",
        "",
        "```bash",
        f"python scripts/hat_pr_review.py --pr {pr_info.get('number', '?')} --post-as-comment",
        "```",
        "",
        "Or run specific hats that failed:",
        "",
        "```bash",
    ])
    failed = cb_state.get("failed_hats", []) + cb_state.get("skipped_hats", [])
    if failed:
        lines.append(f"python scripts/hat_pr_review.py --pr {pr_info.get('number', '?')} --hats {' '.join(failed)} --post-as-comment")
    lines.extend([
        "```",
        "",
        "### Stats",
        f"- API calls made: {cb_state.get('total_calls', 0)}",
        f"- Consecutive failures: {cb_state.get('consecutive_failures', 0)}",
        f"- Input chars processed: {cb_state.get('total_input_chars', 0):,}",
        "",
        "---",
        f"*Generated by `hat_pr_review.py` circuit breaker at {time.strftime('%Y-%m-%d %H:%M:%S')}*",
    ])

    return "\n".join(lines)


def _call_github_models(
    system_prompt: str,
    user_prompt: str,
    token: str,
    model: str | None = None,
    max_retries: int = 3,
    timeout: int = 120,
) -> str:
    """Call GitHub Models API (OpenAI-compatible) with circuit breakers.

    Uses the Copilot monthly quota. Returns text response or empty string.
    Raises CircuitBreakerTripped on rate limit.
    """
    cb = _CIRCUIT_BREAKER_STATE
    model = model or _GITHUB_MODELS_DEFAULT

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 4096,
    }).encode()

    last_error = None
    for attempt in range(1, max_retries + 1):
        req = urllib.request.Request(
            _GITHUB_MODELS_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
                text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                cb["total_calls"] += 1
                cb["consecutive_failures"] = 0
                return text
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else ""
            if e.code == 429:
                cb["rate_limited"] = True
                logger.warning("CIRCUIT BREAKER: Rate limited by GitHub Models API")
                raise _CircuitBreakerTripped("rate_limited", f"HTTP 429: {error_body[:200]}")
            logger.warning(
                f"  GitHub Models API error {e.code} (attempt {attempt}/{max_retries}): {error_body[:200]}"
            )
            last_error = e
        except Exception as e:
            logger.warning(f"  GitHub Models call failed (attempt {attempt}/{max_retries}): {e}")
            last_error = e

        if attempt < max_retries:
            wait = 2 ** attempt
            logger.info(f"  Retrying in {wait}s...")
            time.sleep(wait)

    cb["consecutive_failures"] += 1
    logger.error(f"  All {max_retries} GitHub Models attempts failed: {last_error}")
    return ""


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
    cloud_backend: str | None = None,
    gemini_api_key: str | None = None,
) -> HatReport:
    """Run a single hat analysis against a PR diff.

    Uses Ollama by default, or --cloud-backend gemini for cloud execution.
    Circuit breakers may raise _CircuitBreakerTripped.
    """
    # Resolve agent profile
    agent_name = hat_def.get("agent_name")
    registry = _load_agent_registry()
    agent_profile = registry.get(agent_name, {}) if agent_name else {}

    effective_model = model or agent_profile.get("model")
    restrictions = agent_profile.get("restrictions", {})

    if agent_profile:
        logger.info(
            f"  Agent '{agent_name}': model={agent_profile.get('model')}, "
            f"provider={agent_profile.get('provider')}"
        )

    user_prompt = _build_pr_user_prompt(
        hat_def, pr_info, diff_text, file_summary, system_context, ci_status
    )

    if cloud_backend == "gemini" and gemini_api_key:
        logger.info(f"  Sending {len(user_prompt)} chars to Gemini API...")
        raw = _call_gemini(
            hat_def["system_prompt"],
            user_prompt,
            api_key=gemini_api_key,
            model=effective_model if effective_model and "gemini" in effective_model else None,
        )
    elif cloud_backend == "github" and gemini_api_key:  # gemini_api_key reused as GH token
        logger.info(f"  Sending {len(user_prompt)} chars to GitHub Models API...")
        raw = _call_github_models(
            hat_def["system_prompt"],
            user_prompt,
            token=gemini_api_key,  # actually the GitHub token
            model=effective_model if effective_model and "/" in str(effective_model) else None,
        )
    else:
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
    cloud_backend: str | None = None,
    gemini_api_key: str | None = None,
) -> list[HatReport]:
    """Run all (or specified) hats against a PR.

    Circuit breakers may cause partial results. Check _CIRCUIT_BREAKER_STATE
    after calling to determine if manual review is needed.
    """
    if hats is None:
        hats = list(HAT_DEFINITIONS.keys())

    cb = _CIRCUIT_BREAKER_STATE
    reports = []
    total = len(hats)
    for i, hat_name in enumerate(hats, 1):
        # Check circuit breakers before each hat
        if cb["rate_limited"]:
            remaining = [h for h in hats[i-1:] if h not in [r.hat for r in reports]]
            cb["skipped_hats"].extend(remaining)
            logger.warning(f"CIRCUIT BREAKER: Skipping {len(remaining)} hats due to rate limit")
            break
        if cb["budget_exhausted"]:
            remaining = [h for h in hats[i-1:] if h not in [r.hat for r in reports]]
            cb["skipped_hats"].extend(remaining)
            logger.warning(f"CIRCUIT BREAKER: Skipping {len(remaining)} hats due to budget")
            break

        hat_def = HAT_DEFINITIONS.get(hat_name)
        if hat_def is None:
            logger.warning(f"Unknown hat: {hat_name}, skipping")
            continue

        logger.info(f"[{i}/{total}] Running {hat_def['emoji']} {hat_def['name']}...")
        start = time.time()

        try:
            report = run_hat_pr_review(
                hat_name, hat_def, pr_info, diff_text, file_summary,
                system_context, ci_status, model,
                cloud_backend=cloud_backend, gemini_api_key=gemini_api_key,
            )
            elapsed = time.time() - start
            logger.info(
                f"  {report.emoji} {hat_name}: "
                f"{len(report.findings)} findings ({elapsed:.1f}s)"
            )
            reports.append(report)

        except _CircuitBreakerTripped as e:
            logger.warning(f"  CIRCUIT BREAKER tripped on {hat_name}: {e}")
            cb["failed_hats"].append(hat_name)
            # Don't continue if rate limited or budget blown
            if e.breaker_type in ("rate_limited", "budget_exhausted"):
                remaining = [h for h in hats[i:] if h != hat_name]
                cb["skipped_hats"].extend(remaining)
                break

        except Exception as e:
            elapsed = time.time() - start
            logger.error(f"  {hat_name} failed ({elapsed:.1f}s): {e}")
            cb["failed_hats"].append(hat_name)
            reports.append(HatReport(
                hat=hat_name,
                emoji=hat_def["emoji"],
                focus=hat_def["focus"],
                error=str(e)[:200],
            ))

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

        sev_str = ", ".join(
            f"{SEVERITY_ICONS.get(s, '?')} {s}: {c}"
            for s, c in sorted(sev_counts.items())
        )
        lines.append(f"**Findings**: {len(report.findings)} ({sev_str})")
        lines.append("")

        # Finding details
        for j, finding in enumerate(report.findings, 1):
            icon = SEVERITY_ICONS.get(finding.severity, "?")
            lines.append(f"<details>")
            lines.append(f"<summary>{icon} <b>[{finding.severity}]</b> {finding.title}</summary>")
            lines.append("")
            lines.append(f"**Description**: {finding.description}")
            lines.append("")
            lines.append(f"**Recommendation**: {finding.recommendation}")
            lines.append("")
            lines.append(f"</details>")
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
    lines.append(f"*Generated by `hat_pr_review.py` at {time.strftime('%Y-%m-%d %H:%M:%S')} "
                 f"using Sovereign OS Hat Engine*")

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
        "--pr", type=int, required=True,
        help="Pull request number to review",
    )
    parser.add_argument(
        "--hats", nargs="+", default=None,
        choices=list(HAT_DEFINITIONS.keys()),
        help="Run only specific hats (default: all 11)",
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="Override LLM model for all hats",
    )
    parser.add_argument(
        "--cloud-backend", type=str, default=None,
        choices=["gemini", "github"],
        help="Use cloud LLM instead of Ollama. 'github' uses GitHub Models (Copilot quota). 'gemini' uses Google Gemini API.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print review to stdout without posting to GitHub",
    )
    parser.add_argument(
        "--post-as-comment", action="store_true",
        help="Post as issue comment instead of PR review",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Save review to a local file in addition to posting",
    )
    parser.add_argument(
        "--diff-file", type=str, default=None,
        help="Read diff from a local file instead of GitHub API (for offline use)",
    )
    parser.add_argument(
        "--gh-token", type=str, default=None,
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
        file_summary = f"Files changed: {len(changed_files)}\n" + "\n".join(
            f"  {f}" for f in sorted(changed_files)
        )
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
            file_summary_lines.append(
                f"  {f['filename']} (+{adds}/-{dels}) [{f.get('status', '?')}]"
            )
        file_summary = (
            f"Files changed: {len(files)} | +{total_additions}/-{total_deletions}\n"
            + "\n".join(file_summary_lines)
        )
        logger.info(f"  {len(files)} files changed (+{total_additions}/-{total_deletions})")

    if not args.diff_file:
        logger.info("Fetching CI status...")
        ci_status = fetch_pr_status(args.pr, token)
        logger.info(f"  CI state: {ci_status.get('state', 'unknown')}")

    # 3) Resolve cloud backend API key
    gemini_api_key = None  # also reused for GitHub token in --cloud-backend github
    if args.cloud_backend == "gemini":
        gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
        if not gemini_api_key:
            logger.error("GEMINI_API_KEY env var required for --cloud-backend gemini")
            sys.exit(1)
        logger.info("Using cloud backend: Gemini API (circuit breakers enabled)")
    elif args.cloud_backend == "github":
        gemini_api_key = os.environ.get("GITHUB_TOKEN", "") or (token or "")
        if not gemini_api_key:
            logger.error("GITHUB_TOKEN env var required for --cloud-backend github")
            sys.exit(1)
        logger.info("Using cloud backend: GitHub Models API (Copilot quota, circuit breakers enabled)")

    # 4) Build system context from live project
    logger.info("Building system context from live project...")
    system_context = _build_system_context()
    logger.info(f"  System context: {len(system_context)} chars")

    # 5) Run hats
    logger.info("=" * 60)
    backend_label = f"cloud:{args.cloud_backend}" if args.cloud_backend else "ollama"
    logger.info(f"STARTING 11-HAT PR REVIEW (backend: {backend_label})")
    logger.info("=" * 60)
    start_time = time.time()

    # Reset circuit breaker state for this run
    _CIRCUIT_BREAKER_STATE.update({
        "rate_limited": False,
        "budget_exhausted": False,
        "consecutive_failures": 0,
        "total_calls": 0,
        "total_input_chars": 0,
        "failed_hats": [],
        "skipped_hats": [],
    })

    reports = run_all_hats_pr(
        pr_info=pr_info,
        diff_text=diff_text,
        file_summary=file_summary,
        system_context=system_context,
        ci_status=ci_status,
        hats=args.hats,
        model=args.model,
        cloud_backend=args.cloud_backend,
        gemini_api_key=gemini_api_key,
    )

    elapsed = time.time() - start_time
    total_findings = sum(len(r.findings) for r in reports)
    cb = _CIRCUIT_BREAKER_STATE
    logger.info("=" * 60)
    logger.info(
        f"REVIEW COMPLETE: {total_findings} findings in {elapsed:.1f}s "
        f"({len(reports)} hats, {len(cb['failed_hats'])} failed, {len(cb['skipped_hats'])} skipped)"
    )
    logger.info("=" * 60)

    # 6) Determine if circuit breakers tripped → manual review needed
    breaker_tripped = (
        cb["rate_limited"]
        or cb["budget_exhausted"]
        or len(cb["failed_hats"]) + len(cb["skipped_hats"]) > 0
    )
    all_failed = len(reports) == 0 or all(r.error for r in reports)

    if all_failed:
        # Total failure — post manual review request only
        review_body = _format_manual_review_request(pr_info, cb, reports)
        logger.warning("ALL HATS FAILED — posting manual review request")
    elif breaker_tripped:
        # Partial success — post partial review + manual request for remainder
        review_body = format_review_markdown(pr_info, reports, file_summary, ci_status)
        manual_notice = _format_manual_review_request(pr_info, cb, reports)
        review_body = review_body + "\n\n" + manual_notice
        logger.warning(
            f"CIRCUIT BREAKERS TRIPPED — partial review posted with manual request "
            f"(failed: {cb['failed_hats']}, skipped: {cb['skipped_hats']})"
        )
    else:
        # Full success — normal review
        review_body = format_review_markdown(pr_info, reports, file_summary, ci_status)

    # 7) Output / Post
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
            args.pr, review_body, token,
            as_comment=args.post_as_comment,
        )
        logger.info(f"✅ Review posted: {url}")

    # Summary to stdout
    print(f"\n{'=' * 40}")
    print(f"  PR #{args.pr}: {total_findings} findings from {len(reports)} hats")
    print(f"  Time: {elapsed:.1f}s")
    if breaker_tripped:
        print(f"  ⚡ Circuit breakers: {len(cb['failed_hats'])} failed, {len(cb['skipped_hats'])} skipped")
    for r in reports:
        crits = sum(1 for f in r.findings if f.severity == "CRITICAL")
        highs = sum(1 for f in r.findings if f.severity == "HIGH")
        tag = ""
        if r.error:
            tag = " ❌ ERROR"
        elif crits:
            tag = f" 🔴 {crits} CRITICAL"
        elif highs:
            tag = f" 🟠 {highs} HIGH"
        print(f"  {r.emoji} {r.hat:8s}: {len(r.findings)} findings{tag}")
    print(f"{'=' * 40}")

    # Exit code: non-zero if critical findings or total failure
    critical_count = sum(
        1 for r in reports for f in r.findings if f.severity == "CRITICAL"
    )
    if all_failed:
        logger.warning("Exiting with code 2 (all hats failed — manual review required)")
        sys.exit(2)
    elif critical_count > 0:
        logger.warning(f"Exiting with code 1 ({critical_count} critical findings)")
        sys.exit(1)


if __name__ == "__main__":
    main()
