"""
Tool Forge — auto-generates tools when an agent loops 3x on the same task.
Generates Python script + pytest test, validates through LLMJudge, registers dynamically.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import textwrap
from pathlib import Path
from typing import Any

import requests

from agents.gateway.sentinel_gate import LLMJudge

_registered_tools: dict[str, Any] = {}
_task_loop_counter: dict[str, int] = {}


def record_task_attempt(task_description: str) -> int:
    """Increment the loop counter for a given task description."""
    key = task_description.strip().lower()
    _task_loop_counter[key] = _task_loop_counter.get(key, 0) + 1
    return _task_loop_counter[key]


def should_forge(task_description: str, threshold: int = 3) -> bool:
    """Return True if the task has been attempted at least `threshold` times."""
    key = task_description.strip().lower()
    return _task_loop_counter.get(key, 0) >= threshold


def _get_storage_dir() -> Path:
    base = Path(os.environ.get("BASE_DIR", "/app"))
    path = base / "data" / "tool_forge"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _generate_via_llm(task_description: str, tool_name: str) -> str:
    """Call Ollama to generate real code."""
    _OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://ollama-matrix:11434")

    system_prompt = (
        f"You are ToolForge. Generate a standalone Python function named '{tool_name}' "
        f"that fulfills this task: {task_description}. "
        "Only output valid Python code. Include a full docstring."
    )
    try:
        resp = requests.post(
            f"{_OLLAMA_HOST}/api/generate",
            json={"model": "qwen:7b", "prompt": system_prompt, "stream": False},
            timeout=45.0,
        )
        resp.raise_for_status()
        generated_snippet = resp.json().get("response", "")
        # Clean markdown wrappers if any
        if "```python" in generated_snippet:
            generated_snippet = generated_snippet.split("```python")[1].split("```")[0].strip()
        elif "```" in generated_snippet:
            generated_snippet = generated_snippet.split("```")[1].split("```")[0].strip()
        return generated_snippet
    except Exception as e:
        # Fallback to stub if llm fails
        return textwrap.dedent(f"""
            def {tool_name}(*args, **kwargs):
                \"\"\"Auto-generated tool for: {task_description}\"\"\"
                raise NotImplementedError("Tool API call failed: {e}")
        """).strip()


def _validate_ast(code: str) -> bool:
    """Gate 1: Rejects forbidden Python AST patterns (os.system, etc.)."""
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "system"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"
                ):
                    return False
                if isinstance(node.func, ast.Name) and node.func.id == "eval":
                    return False
        return True
    except SyntaxError:
        return False


def _validate_align(code: str) -> bool:
    """Gate 2: Rejects code violating ALIGN security policies."""
    # R-004 blocks .env patterns
    return ".env" not in code


def forge_tool(task_description: str, loop_count: int = 3) -> dict[str, Any]:
    """
    If loop_count >= 3, attempt to auto-generate a Python utility tool.
    Returns the registered tool metadata or empty dict on failure.
    """
    if loop_count < 3:
        return {}

    tool_name = "tool_" + task_description[:20].strip().replace(" ", "_").lower()
    # Collapse multiple underscores
    import re

    tool_name = re.sub(r"_+", "_", tool_name)

    if tool_name in _registered_tools:
        return _registered_tools[tool_name]

    tool_code = _generate_via_llm(task_description, tool_name)

    # Gate 1: AST
    if not _validate_ast(tool_code):
        return {}

    # Gate 2: ALIGN
    if not _validate_align(tool_code):
        return {}

    # Gate 3: Sandbox load (Syntax check already done in _validate_ast)

    sha256 = hashlib.sha256(tool_code.encode()).hexdigest()

    test_code = textwrap.dedent(f"""
        import pytest
        from tools.{tool_name} import {tool_name}

        def test_{tool_name}_callable():
            assert callable({tool_name})
    """).strip()

    # LLM Judge evaluation
    judge = LLMJudge()
    approved, _ = judge.evaluate(tool_code)
    if not approved:
        return {}

    tool_meta = {
        "name": tool_name,
        "description": task_description,
        "code": tool_code,
        "test": test_code,
        "sha256": sha256,
        "version": "1.0.0",
        "approved": True,
        "human_readable": f"Auto-generated tool '{tool_name}' for task: {task_description}",
    }

    _registered_tools[tool_name] = tool_meta

    # Persist to disk
    storage_dir = _get_storage_dir()
    (storage_dir / f"{tool_name}.json").write_text(json.dumps(tool_meta))

    return tool_meta


def export_tool(name: str) -> dict[str, Any]:
    """Return the tool bundle for a registered tool (memory or disk)."""
    if name in _registered_tools:
        return _registered_tools[name]

    # Try loading from disk
    storage_dir = _get_storage_dir()
    path = storage_dir / f"{name}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def import_tool(bundle: dict[str, Any]) -> dict[str, Any]:
    """Import a tool bundle, validating integrity and security gates."""
    name = bundle.get("name")
    code = bundle.get("code")
    sha256 = bundle.get("sha256")

    if not name or not code or not sha256:
        return {}

    # Integrity check
    if hashlib.sha256(code.encode()).hexdigest() != sha256:
        return {}

    # Security gates
    if not _validate_ast(code) or not _validate_align(code):
        return {}

    bundle["approved"] = True
    _registered_tools[name] = bundle

    # Persist to disk
    storage_dir = _get_storage_dir()
    (storage_dir / f"{name}.json").write_text(json.dumps(bundle))

    return bundle


def list_tools() -> list[dict[str, Any]]:
    """List all registered tools (memory + disk summary)."""
    tools = {}

    # Scan disk
    storage_dir = _get_storage_dir()
    for path in storage_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text())
            name = data["name"]
            tools[name] = {
                "name": name,
                "description": data.get("description", ""),
                "sha256": data.get("sha256", ""),
            }
        except Exception:
            continue

    # Merge memory
    for name, data in _registered_tools.items():
        tools[name] = {
            "name": name,
            "description": data.get("description", ""),
            "sha256": data.get("sha256", ""),
        }

    return list(tools.values())
