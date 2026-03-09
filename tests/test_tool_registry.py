"""Tests for ToolRegistry — central tool catalog and permission system."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.core.tool_registry import (
    ToolCategory,
    ToolDefinition,
    ToolPermission,
    ToolRegistry,
)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _echo_tool(**kwargs):
    """Simple tool that echoes its arguments."""
    return {"echo": kwargs}


def _failing_tool(**kwargs):
    """Tool that always raises."""
    raise RuntimeError("intentional failure")


def _make_tool(
    tool_id: str, category: ToolCategory = ToolCategory.FILE, permission: ToolPermission = ToolPermission.READ
) -> ToolDefinition:
    return ToolDefinition(
        tool_id=tool_id,
        category=category,
        description=f"Test tool: {tool_id}",
        execute_fn=_echo_tool,
        required_permission=permission,
    )


# --------------------------------------------------------------------------- #
# Registration Tests
# --------------------------------------------------------------------------- #


class TestToolRegistration:
    def test_register_and_lookup(self):
        reg = ToolRegistry()
        tool = _make_tool("test.echo")
        reg.register(tool)
        assert reg.get("test.echo") is tool
        assert reg.tool_count == 1

    def test_lookup_missing_returns_none(self):
        reg = ToolRegistry()
        assert reg.get("nonexistent") is None

    def test_unregister(self):
        reg = ToolRegistry()
        reg.register(_make_tool("test.temp"))
        assert reg.unregister("test.temp") is True
        assert reg.get("test.temp") is None
        assert reg.unregister("test.temp") is False

    def test_overwrite_existing(self):
        reg = ToolRegistry()
        tool1 = _make_tool("test.dup")
        tool2 = _make_tool("test.dup")
        reg.register(tool1)
        reg.register(tool2)
        assert reg.get("test.dup") is tool2
        assert reg.tool_count == 1

    def test_list_tools_all(self):
        reg = ToolRegistry()
        reg.register(_make_tool("a.tool", ToolCategory.FILE))
        reg.register(_make_tool("b.tool", ToolCategory.TERMINAL))
        reg.register(_make_tool("c.tool", ToolCategory.GIT))
        assert len(reg.list_tools()) == 3

    def test_list_tools_by_category(self):
        reg = ToolRegistry()
        reg.register(_make_tool("a.tool", ToolCategory.FILE))
        reg.register(_make_tool("b.tool", ToolCategory.TERMINAL))
        reg.register(_make_tool("c.tool", ToolCategory.FILE))
        file_tools = reg.list_tools(ToolCategory.FILE)
        assert len(file_tools) == 2
        assert all(t.category == ToolCategory.FILE for t in file_tools)

    def test_list_tool_ids(self):
        reg = ToolRegistry()
        reg.register(_make_tool("x.one"))
        reg.register(_make_tool("x.two"))
        ids = reg.list_tool_ids()
        assert "x.one" in ids
        assert "x.two" in ids


# --------------------------------------------------------------------------- #
# Permission Tests
# --------------------------------------------------------------------------- #


class TestPermissions:
    def test_default_role_permissions(self):
        reg = ToolRegistry()
        reg.register(_make_tool("file.read", permission=ToolPermission.READ))
        # sentinel has READ + EXECUTE by default
        assert reg.can_use("sentinel", "file.read") is True

    def test_missing_permission_blocked(self):
        reg = ToolRegistry()
        reg.register(_make_tool("file.write", permission=ToolPermission.WRITE))
        # scribe only has READ by default
        assert reg.can_use("scribe", "file.write") is False

    def test_grant_permission(self):
        reg = ToolRegistry()
        reg.register(_make_tool("file.write", permission=ToolPermission.WRITE))
        assert reg.can_use("scribe", "file.write") is False
        reg.grant_permission("scribe", ToolPermission.WRITE)
        assert reg.can_use("scribe", "file.write") is True

    def test_revoke_permission(self):
        reg = ToolRegistry()
        reg.register(_make_tool("file.read", permission=ToolPermission.READ))
        assert reg.can_use("sentinel", "file.read") is True
        reg.revoke_permission("sentinel", ToolPermission.READ)
        assert reg.can_use("sentinel", "file.read") is False

    def test_unknown_tool_returns_false(self):
        reg = ToolRegistry()
        assert reg.can_use("sentinel", "nonexistent") is False

    def test_get_available_tools(self):
        reg = ToolRegistry()
        reg.register(_make_tool("f.read", permission=ToolPermission.READ))
        reg.register(_make_tool("f.write", permission=ToolPermission.WRITE))
        reg.register(_make_tool("f.exec", permission=ToolPermission.EXECUTE))
        # scribe has READ only
        available = reg.get_available_tools("scribe")
        assert len(available) == 1
        assert available[0].tool_id == "f.read"

    def test_developer_has_broad_access(self):
        reg = ToolRegistry()
        reg.register(_make_tool("f.read", permission=ToolPermission.READ))
        reg.register(_make_tool("f.write", permission=ToolPermission.WRITE))
        reg.register(_make_tool("f.exec", permission=ToolPermission.EXECUTE))
        available = reg.get_available_tools("developer")
        assert len(available) == 3


# --------------------------------------------------------------------------- #
# Execution Tests
# --------------------------------------------------------------------------- #


class TestExecution:
    def test_tool_execute_success(self):
        tool = _make_tool("test.echo")
        result = tool.execute(msg="hello")
        assert result.success is True
        assert result.output == {"echo": {"msg": "hello"}}
        assert result.tool_id == "test.echo"
        assert result.duration >= 0

    def test_tool_execute_failure(self):
        tool = ToolDefinition(
            tool_id="test.fail",
            category=ToolCategory.FILE,
            description="Always fails",
            execute_fn=_failing_tool,
        )
        result = tool.execute()
        assert result.success is False
        assert "intentional failure" in result.error

    def test_registry_execute_with_permission(self):
        reg = ToolRegistry()
        reg.register(_make_tool("test.echo", permission=ToolPermission.READ))
        result = reg.execute("test.echo", "sentinel", msg="hi")
        assert result.success is True
        assert result.output == {"echo": {"msg": "hi"}}

    def test_registry_execute_blocked(self):
        reg = ToolRegistry()
        reg.register(_make_tool("test.write", permission=ToolPermission.WRITE))
        result = reg.execute("test.write", "scribe")
        assert result.success is False
        assert "lacks permission" in result.error

    def test_registry_execute_not_found(self):
        reg = ToolRegistry()
        result = reg.execute("nonexistent", "sentinel")
        assert result.success is False
        assert "not found" in result.error

    def test_invocation_log(self):
        reg = ToolRegistry()
        reg.register(_make_tool("test.echo", permission=ToolPermission.READ))
        reg.execute("test.echo", "sentinel", msg="log this")
        log = reg.invocation_log
        assert len(log) == 1
        assert log[0]["tool_id"] == "test.echo"
        assert log[0]["agent_role"] == "sentinel"
        assert log[0]["success"] is True
