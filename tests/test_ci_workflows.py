"""
Blue Hat — CI workflow hygiene tests.

Validates that GitHub Actions workflow files:
 - Use canonical (non-hypothetical) action versions — no ``@v5+`` for actions
   where only v4 (checkout) or v5 (setup-python) exist.
 - Specify ``timeout-minutes`` on every job so runaway CI doesn't consume
   unlimited runner minutes.
 - Include a ``concurrency`` block on the main CI workflow so redundant PR
   runs are cancelled automatically.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml  # PyYAML is present via multiple transitive deps (lark, dspy, etc.)
            # and is listed in uv.lock — safe to use in tests.

WORKFLOWS_DIR = Path(__file__).parent.parent / ".github" / "workflows"

# Canonical versions for the two most-used community actions.
# Before bumping a version here, verify the new major version actually
# exists at https://github.com/actions/{action}/releases — the entire
# purpose of these tests is to catch copy-paste errors where a version
# that does not exist is referenced (e.g. @v6 when only @v4 is released).
# Update these when a new major release is validated by the team.
_KNOWN_GOOD = {
    "actions/checkout": "v4",
    "actions/setup-python": "v5",
}

# Workflow files that are expected to declare ``concurrency``.
_CONCURRENCY_REQUIRED = {"ci.yml"}


def _load_workflow(name: str) -> dict:
    path = WORKFLOWS_DIR / name
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _workflow_files() -> list[str]:
    return [p.name for p in WORKFLOWS_DIR.glob("*.yml")]


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _iter_step_uses(workflow: dict):
    """Yield every ``uses:`` string found in any job step."""
    for job in (workflow.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if "uses" in step:
                yield step["uses"]


def _iter_jobs(workflow: dict):
    """Yield (job_name, job_dict) pairs."""
    for name, job in (workflow.get("jobs") or {}).items():
        yield name, job


# ─── Tests ───────────────────────────────────────────────────────────────────

class TestActionVersions:
    """Every community action must use a known-good pinned major version."""

    @pytest.mark.parametrize("wf_file", _workflow_files())
    def test_no_nonexistent_action_versions(self, wf_file: str):
        """
        actions/checkout and actions/setup-python must use their canonical versions.
        Versions beyond what is publicly available (e.g. @v6) indicate copy-paste
        errors and would silently break CI once the runner tries to resolve them.
        """
        workflow = _load_workflow(wf_file)
        errors: list[str] = []
        for uses in _iter_step_uses(workflow):
            for action, good_version in _KNOWN_GOOD.items():
                if not uses.startswith(action + "@"):
                    continue
                actual_version = uses.split("@", 1)[1]
                if actual_version != good_version:
                    errors.append(
                        f"{wf_file}: '{uses}' — expected '@{good_version}'"
                    )
        assert not errors, "Non-canonical action versions detected:\n" + "\n".join(errors)


class TestJobTimeouts:
    """Every job must declare ``timeout-minutes`` to bound runaway CI costs."""

    @pytest.mark.parametrize("wf_file", _workflow_files())
    def test_jobs_have_timeout(self, wf_file: str):
        workflow = _load_workflow(wf_file)
        missing: list[str] = []
        for job_name, job in _iter_jobs(workflow):
            if "timeout-minutes" not in job:
                missing.append(f"{wf_file}: job '{job_name}' is missing timeout-minutes")
        assert not missing, (
            "Jobs without timeout-minutes found (runaway CI risk):\n"
            + "\n".join(missing)
        )


class TestConcurrencyControl:
    """The main CI workflow must declare ``concurrency`` to cancel stale PR runs."""

    @pytest.mark.parametrize("wf_file", sorted(_CONCURRENCY_REQUIRED))
    def test_concurrency_declared(self, wf_file: str):
        workflow = _load_workflow(wf_file)
        assert "concurrency" in workflow, (
            f"{wf_file}: missing top-level 'concurrency' block — "
            "redundant PR builds will waste runner minutes"
        )

    @pytest.mark.parametrize("wf_file", sorted(_CONCURRENCY_REQUIRED))
    def test_concurrency_cancel_in_progress(self, wf_file: str):
        workflow = _load_workflow(wf_file)
        concurrency = workflow.get("concurrency") or {}
        assert "cancel-in-progress" in concurrency, (
            f"{wf_file}: 'concurrency' block must include 'cancel-in-progress'"
        )
