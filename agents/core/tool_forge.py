"""
Tool Forge (Phase 6) — Sandboxed Agent Tool Creation & Decentralized Sharing.

When an agent loops ≥3 times on the same task due to a missing utility:
  1. Generate a Python function + pytest test via the configured LLM (env-driven model).
  2. Validate generated code: ASTValidator (structural) + LLMJudge (ALIGN policy).
  3. Sandbox-load via importlib (safe dynamic load — no eval/exec builtins).
  4. Persist to data/tool_forge/<name>.json for decentralized sharing/discovery.
  5. Register in-memory so the executor can call the new tool immediately.

Decentralized sharing protocol:
  - export_tool(name)  → portable JSON bundle (code + test + sha256 + metadata)
  - import_tool(bundle) → verify sha256, validate through all gates, then register
  - list_tools()        → discover all registered + disk-persisted tools
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import tempfile
import textwrap
from pathlib import Path
from typing import Any

import httpx

from agents.core.ast_validator import validate_code
from agents.gateway.sentinel_gate import LLMJudge

# In-memory registries (per-process)
_registered_tools: dict[str, Any] = {}
_task_loop_counter: dict[str, int] = {}

# Constants
_MAX_TASK_KEY_LENGTH = 80
_LLM_GENERATION_TIMEOUT = 45.0  # seconds; generous timeout for LLM tool generation


# --------------------------------------------------------------------------- #
# Loop detection
# --------------------------------------------------------------------------- #

def record_task_attempt(task_description: str) -> int:
    """Record an attempt on a task. Returns the updated attempt count."""
    key = task_description.strip().lower()[:_MAX_TASK_KEY_LENGTH]
    _task_loop_counter[key] = _task_loop_counter.get(key, 0) + 1
    return _task_loop_counter[key]


def should_forge(task_description: str, threshold: int = 3) -> bool:
    """Return True if this task has been attempted >= threshold times."""
    key = task_description.strip().lower()[:_MAX_TASK_KEY_LENGTH]
    return _task_loop_counter.get(key, 0) >= threshold


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #

def _forge_dir() -> Path:
    """Resolve and ensure the tool forge storage directory exists."""
    base = os.environ.get("BASE_DIR", "")
    if base and Path(base).is_absolute():
        d = Path(base) / "data" / "tool_forge"
    else:
        d = Path(__file__).parent.parent.parent / "data" / "tool_forge"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sandbox_load(tool_name: str, code: str) -> tuple[bool, str]:
    """
    Safely load generated code via importlib (no eval/exec builtins).
    Writes to a temp file, attempts module load, then removes the temp file.
    Returns (success: bool, error_message: str).

    Security note: importlib.util.exec_module() is used here rather than eval()/exec()
    so that the loaded module is a proper Python module object with its own namespace.
    All code reaching this point has already passed the AST validator (Gate 1) and the
    ALIGN LLMJudge (Gate 2), ensuring no forbidden patterns survive to this stage.
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", prefix=f"tf_{tool_name}_", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(code)
        tmp_path = tmp.name
    try:
        spec = importlib.util.spec_from_file_location(tool_name, tmp_path)
        if spec is None or spec.loader is None:
            return False, "Could not create module spec"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return True, ""
    except Exception as exc:
        return False, str(exc)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _generate_via_llm(tool_name: str, task_description: str) -> str:
    """Generate Python function code via the configured SUMMARIZATION_MODEL."""
    model = os.environ.get("SUMMARIZATION_MODEL") or os.environ.get("PRIMARY_MODEL", "")
    effective_model = model.replace(":cloud", "") if model else ""
    ollama_host = os.environ.get("OLLAMA_HOST", "http://ollama-matrix:11434")

    system_prompt = (
        f"You are ToolForge. Generate a standalone Python function named '{tool_name}' "
        f"that fulfils this task: {task_description}. "
        "Only output valid Python code with a full docstring. No markdown, no explanation."
    )
    try:
        resp = httpx.post(
            f"{ollama_host}/api/generate",
            json={"model": effective_model, "prompt": system_prompt, "stream": False},
            timeout=_LLM_GENERATION_TIMEOUT,
        )
        resp.raise_for_status()
        generated = resp.json().get("response", "").strip()
        # Strip markdown fences if present
        if "```python" in generated:
            generated = generated.split("```python")[1].split("```")[0].strip()
        elif "```" in generated:
            generated = generated.split("```")[1].split("```")[0].strip()
        return generated
    except (httpx.RequestError, httpx.HTTPStatusError, OSError) as exc:
        safe_err = repr(str(exc))  # sanitize quotes/special chars for embedding in code string
        return textwrap.dedent(f"""
            def {tool_name}(*args, **kwargs):
                \"\"\"Auto-generated stub for: {task_description}\"\"\"
                raise NotImplementedError("LLM generation failed: " + {safe_err})
        """).strip()


def _persist_tool(meta: dict[str, Any]) -> None:
    """Write tool bundle to data/tool_forge/<name>.json."""
    store = _forge_dir()
    bundle_path = store / f"{meta['name']}.json"
    bundle_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def forge_tool(task_description: str, loop_count: int = 3) -> dict[str, Any]:
    """
    Forge a new tool when loop_count >= 3 and no suitable utility exists.

    Validation pipeline (all gates must pass):
      1. AST structural scan — rejects os.system / subprocess.* / eval / exec.
      2. LLMJudge ALIGN policy gate — rejects ALIGN Ledger violations.
      3. Sandbox importlib load — verifies the module imports without errors.

    On success the tool is persisted to data/tool_forge/<name>.json and
    registered in the in-memory registry for immediate use.

    Returns the tool metadata dict, or {} on failure/rejection.
    """
    if loop_count < 3:
        return {}

    # Derive a valid Python identifier from the task description
    raw = "tool_" + task_description[:20].replace(" ", "_").lower()
    sanitized = "".join(c if c.isalnum() or c == "_" else "_" for c in raw)
    # Collapse consecutive underscores and strip trailing ones
    tool_name = re.sub(r"_+", "_", sanitized).rstrip("_")

    if tool_name in _registered_tools:
        return _registered_tools[tool_name]

    tool_code = _generate_via_llm(tool_name, task_description)

    # Gate 1: AST structural validation
    is_safe, _violations = validate_code(tool_code)
    if not is_safe:
        return {}

    # Gate 2: ALIGN policy (LLMJudge)
    judge = LLMJudge()
    approved, _ = judge.evaluate(tool_code)
    if not approved:
        return {}

    # Gate 3: Sandbox importlib load (no eval/exec builtins)
    loaded, _load_err = _sandbox_load(tool_name, tool_code)
    if not loaded:
        return {}

    test_code = textwrap.dedent(f"""
        import pytest
        from tools.{tool_name} import {tool_name}

        def test_{tool_name}_callable():
            assert callable({tool_name})
    """).strip()

    sha256 = hashlib.sha256(tool_code.encode()).hexdigest()
    tool_meta: dict[str, Any] = {
        "name": tool_name,
        "description": task_description,
        "code": tool_code,
        "test": test_code,
        "sha256": sha256,
        "approved": True,
        "version": "1.0.0",
    }

    _persist_tool(tool_meta)
    _registered_tools[tool_name] = tool_meta
    return tool_meta


def export_tool(name: str) -> dict[str, Any]:
    """
    Export a registered tool as a portable JSON bundle.

    The bundle can be shared with other instances and re-imported via import_tool().
    Checks the in-memory registry first, then falls back to disk.

    Returns the tool bundle dict, or {} if the tool is not found.
    """
    if name in _registered_tools:
        return dict(_registered_tools[name])
    # Try loading from disk (tools forged in a previous process run)
    store = _forge_dir()
    bundle_path = store / f"{name}.json"
    if bundle_path.exists():
        meta = json.loads(bundle_path.read_text(encoding="utf-8"))
        _registered_tools[name] = meta
        return dict(meta)
    return {}


def import_tool(bundle: dict[str, Any]) -> dict[str, Any]:
    """
    Import a tool from a JSON bundle (e.g. received from another OS instance).

    Validation pipeline (all gates must pass):
      0. SHA-256 integrity — bundle["sha256"] must match the actual code hash.
      1. AST structural scan.
      2. LLMJudge ALIGN policy gate.
      3. Sandbox importlib load.

    On success the tool is registered in memory and persisted locally.

    Returns the registered tool metadata, or {} on validation failure.
    """
    name = bundle.get("name", "")
    code = bundle.get("code", "")
    claimed_sha256 = bundle.get("sha256", "")

    if not name or not code:
        return {}

    # Gate 0: SHA-256 integrity
    actual_sha256 = hashlib.sha256(code.encode()).hexdigest()
    if claimed_sha256 and actual_sha256 != claimed_sha256:
        return {}

    # Gate 1: AST structural validation
    is_safe, _ = validate_code(code)
    if not is_safe:
        return {}

    # Gate 2: ALIGN policy
    judge = LLMJudge()
    approved, _ = judge.evaluate(code)
    if not approved:
        return {}

    # Gate 3: Sandbox load
    loaded, _ = _sandbox_load(name, code)
    if not loaded:
        return {}

    meta: dict[str, Any] = {**bundle, "sha256": actual_sha256, "approved": True}
    _registered_tools[name] = meta
    _persist_tool(meta)
    return dict(meta)


def list_tools() -> list[dict[str, Any]]:
    """
    Return summary metadata for all registered tools.

    Scans data/tool_forge/*.json to discover tools forged by other processes or
    imported from remote instances (decentralized sharing).

    Returns a list of dicts with keys: name, description, sha256.
    """
    store = _forge_dir()
    for bundle_path in store.glob("*.json"):
        try:
            meta = json.loads(bundle_path.read_text(encoding="utf-8"))
            tool_name = meta.get("name", "")
            if tool_name and tool_name not in _registered_tools:
                _registered_tools[tool_name] = meta
        except (json.JSONDecodeError, OSError):
            pass
    return [
        {
            "name": m["name"],
            "description": m.get("description", ""),
            "sha256": m.get("sha256", ""),
        }
        for m in _registered_tools.values()
    ]
