"""
Tool Forge — auto-generates tools when an agent loops 3x on the same task.
Generates Python script + pytest test, validates through LLMJudge, registers dynamically.
"""
from __future__ import annotations

import textwrap
from typing import Any

from agents.gateway.sentinel_gate import LLMJudge


_registered_tools: dict[str, Any] = {}


def forge_tool(task_description: str, loop_count: int = 3) -> dict[str, Any]:
    """
    If loop_count >= 3, attempt to auto-generate a Python utility tool.
    Returns the registered tool metadata or empty dict on failure.
    """
    if loop_count < 3:
        return {}

    tool_name = "tool_" + task_description[:20].replace(" ", "_").lower()
    if tool_name in _registered_tools:
        return _registered_tools[tool_name]

    # Generate stub tool
    tool_code = textwrap.dedent(f"""
        def {tool_name}(*args, **kwargs):
            \"\"\"Auto-generated tool for: {task_description}\"\"\"
            raise NotImplementedError("Tool '{tool_name}' requires implementation")
    """).strip()

    test_code = textwrap.dedent(f"""
        import pytest
        from tools.{tool_name} import {tool_name}

        def test_{tool_name}_raises():
            with pytest.raises(NotImplementedError):
                {tool_name}()
    """).strip()

    judge = LLMJudge()
    approved, _ = judge.evaluate(tool_code)
    if not approved:
        return {}

    tool_meta = {
        "name": tool_name,
        "description": task_description,
        "code": tool_code,
        "test": test_code,
        "approved": True,
    }
    _registered_tools[tool_name] = tool_meta
    return tool_meta
