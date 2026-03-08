"""
Tests for Tool Dispatch Bridge, Tool Lockfile, and Tool Monitor.

Covers:
  - ToolDispatchBridge: dispatch, lazy-loading, registry integration
  - ToolLockfile: lock, unlock, integrity, staleness
  - ToolMonitor: health sweep, gas reporting, auto-revoke
  - ToolScaffold: project generation
"""

from __future__ import annotations

import json
import textwrap
import time
from pathlib import Path

import pytest

from hlf.tool_dispatch import ToolDispatchBridge, ToolDispatchResult
from hlf.tool_lockfile import LockEntry, ToolLockfile
from hlf.tool_monitor import ToolMonitor, ToolHealthReport, ToolGasReport, ToolAuditReport
from hlf.tool_scaffold import scaffold_tool


# ─── ToolDispatchBridge ──────────────────────────────────────────────────────


class TestToolDispatchBridge:
    """Tests for the lazy-loading dispatch bridge."""

    def test_dispatch_unknown_tool(self) -> None:
        bridge = ToolDispatchBridge(tools_dir="/nonexistent")
        result = bridge.dispatch("nope", {"x": 1})
        assert not result.success
        assert "not found" in result.error

    def test_dispatch_result_to_dict(self) -> None:
        result = ToolDispatchResult(
            tool="test", success=True, value=42, gas_used=3
        )
        d = result.to_dict()
        assert d["tool"] == "test"
        assert d["success"] is True
        assert d["value"] == 42
        assert d["gas_used"] == 3

    def test_list_active_empty(self) -> None:
        bridge = ToolDispatchBridge(tools_dir="/nonexistent")
        assert bridge.list_active() == []

    def test_get_tool_info_missing(self) -> None:
        bridge = ToolDispatchBridge(tools_dir="/nonexistent")
        assert bridge.get_tool_info("nope") is None


# ─── ToolLockfile ────────────────────────────────────────────────────────────


class TestToolLockfile:
    """Tests for the lockfile system."""

    def test_lock_and_retrieve(self, tmp_path: Path) -> None:
        lf = ToolLockfile(path=tmp_path / "lock.json")
        entry = lf.lock("my_tool", "1.0.0", "https://github.com/user/my_tool")
        assert entry.name == "my_tool"
        assert entry.version == "1.0.0"
        assert lf.is_locked("my_tool")

    def test_unlock(self, tmp_path: Path) -> None:
        lf = ToolLockfile(path=tmp_path / "lock.json")
        lf.lock("tool_a", "1.0", "https://example.com")
        assert lf.unlock("tool_a")
        assert not lf.is_locked("tool_a")

    def test_unlock_nonexistent(self, tmp_path: Path) -> None:
        lf = ToolLockfile(path=tmp_path / "lock.json")
        assert not lf.unlock("nope")

    def test_save_and_load(self, tmp_path: Path) -> None:
        lf = ToolLockfile(path=tmp_path / "lock.json")
        lf.lock("tool_x", "2.1.0", "https://git.example.com/tool_x")
        lf.save()

        # Load in new instance
        lf2 = ToolLockfile(path=tmp_path / "lock.json")
        lf2.load()
        assert lf2.is_locked("tool_x")
        entry = lf2.get("tool_x")
        assert entry is not None
        assert entry.version == "2.1.0"

    def test_integrity_check_passes(self, tmp_path: Path) -> None:
        lf = ToolLockfile(path=tmp_path / "lock.json")
        manifest_content = "name: test\nversion: 1.0\n"
        lf.lock("test", "1.0", "https://example.com", manifest_content=manifest_content)
        assert lf.verify_integrity("test", manifest_content)

    def test_integrity_check_fails(self, tmp_path: Path) -> None:
        lf = ToolLockfile(path=tmp_path / "lock.json")
        manifest_content = "name: test\nversion: 1.0\n"
        lf.lock("test", "1.0", "https://example.com", manifest_content=manifest_content)
        assert not lf.verify_integrity("test", "name: MODIFIED\n")

    def test_stale_tools_detection(self, tmp_path: Path) -> None:
        lf = ToolLockfile(path=tmp_path / "lock.json")
        entry = lf.lock("old_tool", "0.1", "https://example.com")
        # Simulate 100 days ago
        entry.locked_at = time.time() - (100 * 86400)
        stale = lf.stale_tools(max_age_days=90)
        assert len(stale) == 1
        assert stale[0].name == "old_tool"

    def test_entries_list(self, tmp_path: Path) -> None:
        lf = ToolLockfile(path=tmp_path / "lock.json")
        lf.lock("a", "1.0", "url_a")
        lf.lock("b", "2.0", "url_b")
        assert len(lf.entries()) == 2

    def test_lock_entry_serialization(self) -> None:
        entry = LockEntry(name="test", version="1.0", source_url="https://x.com")
        d = entry.to_dict()
        restored = LockEntry.from_dict(d)
        assert restored.name == "test"
        assert restored.version == "1.0"


# ─── ToolMonitor ─────────────────────────────────────────────────────────────


class TestToolMonitor:
    """Tests for tool health, gas, and freshness monitoring."""

    def test_empty_audit(self, tmp_path: Path) -> None:
        reg_path = tmp_path / "registry.json"
        reg_path.write_text(json.dumps({"tools": {}}), encoding="utf-8")
        monitor = ToolMonitor(registry_path=reg_path)
        report = monitor.full_audit()
        assert report.total_tools == 0
        assert report.active_tools == 0

    def test_gas_recording(self, tmp_path: Path) -> None:
        reg_path = tmp_path / "registry.json"
        reg_path.write_text(
            json.dumps({"tools": {"agent_x": {"gas_cost": 2, "status": "active"}}}),
            encoding="utf-8"
        )
        monitor = ToolMonitor(registry_path=reg_path)
        monitor.record_invocation("agent_x", gas_used=2)
        monitor.record_invocation("agent_x", gas_used=2)
        reports = monitor.gas_report()
        assert len(reports) == 1
        assert reports[0].total_invocations == 2
        assert reports[0].total_gas_consumed == 4

    def test_gas_budget_utilization(self) -> None:
        report = ToolGasReport(
            name="test",
            gas_cost_per_call=5,
            total_gas_consumed=500,
            budget_limit=1000,
            budget_remaining=500,
        )
        assert report.budget_utilization == 0.5

    def test_stale_detection(self, tmp_path: Path) -> None:
        reg_path = tmp_path / "registry.json"
        old_ts = time.time() - (100 * 86400)  # 100 days ago
        reg_path.write_text(
            json.dumps({"tools": {
                "fresh_tool": {"installed_at": time.time(), "status": "active"},
                "old_tool": {"installed_at": old_ts, "status": "active"},
            }}),
            encoding="utf-8",
        )
        monitor = ToolMonitor(registry_path=reg_path)
        stale = monitor.stale_tools(max_age_days=90)
        assert "old_tool" in stale
        assert "fresh_tool" not in stale

    def test_health_report_serialization(self) -> None:
        report = ToolHealthReport(name="test", healthy=True, response_time_ms=5.2)
        d = report.to_dict()
        assert d["name"] == "test"
        assert d["healthy"] is True
        assert d["response_time_ms"] == 5.2

    def test_audit_report_serialization(self) -> None:
        report = ToolAuditReport(total_tools=3, active_tools=2, healthy_tools=1)
        d = report.to_dict()
        assert d["total_tools"] == 3
        assert d["active_tools"] == 2


# ─── ToolScaffold ────────────────────────────────────────────────────────────


class TestToolScaffold:
    """Tests for tool project scaffolding."""

    def test_scaffold_creates_structure(self, tmp_path: Path) -> None:
        path = scaffold_tool("my_agent", output_dir=tmp_path, author="gerry")
        assert (path / "tool.hlf.yaml").exists()
        assert (path / "main.py").exists()
        assert (path / "tests" / "test_tool.py").exists()
        assert (path / "README.md").exists()
        assert (path / ".gitignore").exists()
        assert (path / "data").is_dir()

    def test_scaffold_manifest_content(self, tmp_path: Path) -> None:
        scaffold_tool("test_agent", output_dir=tmp_path, description="Test desc")
        manifest = (tmp_path / "test_agent" / "tool.hlf.yaml").read_text()
        assert 'name: "test_agent"' in manifest
        assert 'description: "Test desc"' in manifest

    def test_scaffold_custom_adapter(self, tmp_path: Path) -> None:
        scaffold_tool("docker_tool", output_dir=tmp_path, adapter="docker")
        manifest = (tmp_path / "docker_tool" / "tool.hlf.yaml").read_text()
        assert 'adapter: "docker"' in manifest

    def test_scaffold_readme_contains_name(self, tmp_path: Path) -> None:
        scaffold_tool("cool_agent", output_dir=tmp_path)
        readme = (tmp_path / "cool_agent" / "README.md").read_text()
        assert "cool_agent" in readme
        assert "COOL_AGENT" in readme
