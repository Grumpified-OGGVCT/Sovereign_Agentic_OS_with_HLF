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


def _make_tool(tool_id: str, category: ToolCategory = ToolCategory.FILE,
               permission: ToolPermission = ToolPermission.READ) -> ToolDefinition:
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


# --------------------------------------------------------------------------- #
# Azure Hat — Lifecycle Tests
# --------------------------------------------------------------------------- #


class TestToolLifecycle:
    """Azure Hat: ToolLifecycleState transitions and enforcement."""

    def test_new_tool_defaults_to_active(self):
        from agents.core.tool_registry import ToolLifecycleState
        tool = _make_tool("life.default")
        assert tool.lifecycle_state == ToolLifecycleState.ACTIVE

    def test_set_lifecycle_to_deprecated(self):
        from agents.core.tool_registry import ToolLifecycleState
        reg = ToolRegistry()
        reg.register(_make_tool("life.dep"))
        success = reg.set_lifecycle("life.dep", ToolLifecycleState.DEPRECATED)
        assert success is True
        assert reg.get("life.dep").lifecycle_state == ToolLifecycleState.DEPRECATED

    def test_set_lifecycle_unknown_tool_returns_false(self):
        from agents.core.tool_registry import ToolLifecycleState
        reg = ToolRegistry()
        assert reg.set_lifecycle("no.such.tool", ToolLifecycleState.REVOKED) is False

    def test_revoked_tool_blocked(self):
        from agents.core.tool_registry import ToolLifecycleState
        reg = ToolRegistry()
        reg.register(_make_tool("life.revoked"))
        reg.set_lifecycle("life.revoked", ToolLifecycleState.REVOKED)
        result = reg.execute("life.revoked", "sentinel")
        assert result.success is False
        assert "revoked" in result.error

    def test_pending_approval_tool_blocked(self):
        from agents.core.tool_registry import ToolLifecycleState
        reg = ToolRegistry()
        reg.register(_make_tool("life.pending"))
        reg.set_lifecycle("life.pending", ToolLifecycleState.PENDING_APPROVAL)
        result = reg.execute("life.pending", "sentinel")
        assert result.success is False
        assert "pending human approval" in result.error

    def test_active_tool_after_lifecycle_promotion(self):
        from agents.core.tool_registry import ToolLifecycleState
        reg = ToolRegistry()
        tool = ToolDefinition(
            tool_id="life.promoted",
            category=ToolCategory.FILE,
            description="Test",
            execute_fn=_echo_tool,
            lifecycle_state=ToolLifecycleState.PENDING_APPROVAL,
        )
        reg.register(tool)
        reg.set_lifecycle("life.promoted", ToolLifecycleState.ACTIVE)
        result = reg.execute("life.promoted", "sentinel")
        assert result.success is True


# --------------------------------------------------------------------------- #
# Azure Hat — HITL Gate Tests
# --------------------------------------------------------------------------- #


class TestHITLGates:
    """Azure Hat: Human-in-the-Loop approval token enforcement."""

    def _make_hitl_tool(self, tool_id: str) -> ToolDefinition:
        return ToolDefinition(
            tool_id=tool_id,
            category=ToolCategory.TERMINAL,
            description="Requires HITL approval",
            execute_fn=_echo_tool,
            required_permission=ToolPermission.EXECUTE,
            requires_hitl=True,
        )

    def test_hitl_tool_blocked_without_token(self):
        import pytest
        from agents.core.tool_registry import HITLRequiredError
        reg = ToolRegistry()
        reg.register(self._make_hitl_tool("hitl.guarded"))
        with pytest.raises(HITLRequiredError):
            reg.execute("hitl.guarded", "sentinel")

    def test_hitl_tool_blocked_with_wrong_token(self):
        import pytest
        from agents.core.tool_registry import HITLRequiredError
        reg = ToolRegistry()
        reg.register(self._make_hitl_tool("hitl.guarded"))
        reg.grant_hitl_approval("hitl.guarded", "correct-token")
        with pytest.raises(HITLRequiredError):
            reg.execute("hitl.guarded", "sentinel", hitl_token="wrong-token")

    def test_hitl_tool_passes_with_valid_token(self):
        reg = ToolRegistry()
        reg.register(self._make_hitl_tool("hitl.approved"))
        reg.grant_hitl_approval("hitl.approved", "tok-abc")
        result = reg.execute("hitl.approved", "sentinel", hitl_token="tok-abc", msg="hi")
        assert result.success is True

    def test_hitl_token_is_single_use(self):
        import pytest
        from agents.core.tool_registry import HITLRequiredError
        reg = ToolRegistry()
        reg.register(self._make_hitl_tool("hitl.single"))
        reg.grant_hitl_approval("hitl.single", "use-once")
        # First call succeeds
        result = reg.execute("hitl.single", "sentinel", hitl_token="use-once")
        assert result.success is True
        # Second call fails — token was consumed
        with pytest.raises(HITLRequiredError):
            reg.execute("hitl.single", "sentinel", hitl_token="use-once")

    def test_revoke_hitl_approval(self):
        import pytest
        from agents.core.tool_registry import HITLRequiredError
        reg = ToolRegistry()
        reg.register(self._make_hitl_tool("hitl.revoke"))
        reg.grant_hitl_approval("hitl.revoke", "tok-xyz")
        assert reg.revoke_hitl_approval("hitl.revoke", "tok-xyz") is True
        # Token revoked — execution should fail
        with pytest.raises(HITLRequiredError):
            reg.execute("hitl.revoke", "sentinel", hitl_token="tok-xyz")

    def test_revoke_nonexistent_token_returns_false(self):
        reg = ToolRegistry()
        reg.register(self._make_hitl_tool("hitl.noop"))
        assert reg.revoke_hitl_approval("hitl.noop", "ghost-token") is False

    def test_non_hitl_tool_executes_without_token(self):
        reg = ToolRegistry()
        reg.register(_make_tool("hitl.normal", permission=ToolPermission.READ))
        result = reg.execute("hitl.normal", "sentinel")
        assert result.success is True

    def test_hitl_approval_empty_token_raises_value_error(self):
        import pytest
        reg = ToolRegistry()
        reg.register(self._make_hitl_tool("hitl.bad"))
        with pytest.raises(ValueError):
            reg.grant_hitl_approval("hitl.bad", "")

    def test_has_hitl_approval_returns_false_when_absent(self):
        reg = ToolRegistry()
        assert reg.has_hitl_approval("any.tool", None) is False
        assert reg.has_hitl_approval("any.tool", "nonexistent") is False


# --------------------------------------------------------------------------- #
# Azure Hat — Workflow Ledger Tests
# --------------------------------------------------------------------------- #


class TestWorkflowLedger:
    """Azure Hat: step-ID tracking and sequential workflow ledger."""

    def test_ledger_populated_on_execute(self):
        reg = ToolRegistry()
        reg.register(_make_tool("ledger.tool", permission=ToolPermission.READ))
        reg.execute("ledger.tool", "sentinel", msg="one")
        ledger = reg.workflow_ledger
        assert len(ledger) == 1
        assert ledger[0].tool_id == "ledger.tool"
        assert ledger[0].agent_role == "sentinel"
        assert ledger[0].success is True

    def test_ledger_step_ids_are_unique(self):
        reg = ToolRegistry()
        reg.register(_make_tool("ledger.multi", permission=ToolPermission.READ))
        reg.execute("ledger.multi", "sentinel", msg="a")
        reg.execute("ledger.multi", "sentinel", msg="b")
        reg.execute("ledger.multi", "sentinel", msg="c")
        ids = [e.step_id for e in reg.workflow_ledger]
        assert len(ids) == len(set(ids))  # all unique

    def test_ledger_is_sequential(self):
        reg = ToolRegistry()
        reg.register(_make_tool("ledger.seq", permission=ToolPermission.READ))
        for i in range(5):
            reg.execute("ledger.seq", "sentinel", idx=i)
        # Step IDs embed an increasing counter in the second segment ("step-{N:06d}-{uuid}")
        # Verify the documented format and that counters are strictly increasing
        import re
        pattern = re.compile(r"^step-(\d+)-[0-9a-f]+$")
        counters = []
        for e in reg.workflow_ledger:
            m = pattern.match(e.step_id)
            assert m is not None, f"step_id '{e.step_id}' does not match expected format"
            counters.append(int(m.group(1)))
        assert counters == sorted(counters)

    def test_result_carries_step_id(self):
        reg = ToolRegistry()
        reg.register(_make_tool("ledger.result", permission=ToolPermission.READ))
        result = reg.execute("ledger.result", "sentinel")
        assert result.step_id != ""

    def test_ledger_records_hitl_approved_flag(self):
        reg = ToolRegistry()
        hitl_tool = ToolDefinition(
            tool_id="ledger.hitl",
            category=ToolCategory.TERMINAL,
            description="HITL",
            execute_fn=_echo_tool,
            required_permission=ToolPermission.EXECUTE,
            requires_hitl=True,
        )
        reg.register(hitl_tool)
        reg.grant_hitl_approval("ledger.hitl", "tok-ledger")
        reg.execute("ledger.hitl", "sentinel", hitl_token="tok-ledger")
        assert reg.workflow_ledger[0].hitl_approved is True

    def test_ledger_is_read_only_copy(self):
        reg = ToolRegistry()
        reg.register(_make_tool("ledger.copy", permission=ToolPermission.READ))
        reg.execute("ledger.copy", "sentinel")
        ledger_copy = reg.workflow_ledger
        ledger_copy.clear()  # modify the copy
        assert len(reg.workflow_ledger) == 1  # original unchanged
