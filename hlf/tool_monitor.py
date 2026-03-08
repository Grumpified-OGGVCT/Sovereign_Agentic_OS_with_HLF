"""
HLF Tool Monitor — Health, gas, and freshness tracking for installed tools.

Scribe Hat: Track gas budgets and resource consumption.
Chronicler Hat: Freshness monitoring, CVE alerting.
Steward Hat: Auto-revoke unhealthy tools.

Usage::

    monitor = ToolMonitor(tools_dir="./tools/installed")
    report = monitor.full_audit()

    # Individual checks
    monitor.health_sweep()
    monitor.gas_report()
    stale = monitor.stale_tools(max_age_days=90)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TOOL_REGISTRY_PATH = _PROJECT_ROOT / "governance" / "tool_registry.json"


@dataclass
class ToolHealthReport:
    """Health report for a single tool."""

    name: str
    healthy: bool
    last_checked: float = field(default_factory=time.time)
    response_time_ms: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "healthy": self.healthy,
            "last_checked": self.last_checked,
            "response_time_ms": self.response_time_ms,
            "error": self.error,
        }


@dataclass
class ToolGasReport:
    """Gas usage report for a tool (Scribe Hat)."""

    name: str
    gas_cost_per_call: int = 0
    total_invocations: int = 0
    total_gas_consumed: int = 0
    budget_limit: int = 1000
    budget_remaining: int = 1000

    @property
    def budget_utilization(self) -> float:
        if self.budget_limit == 0:
            return 0.0
        return round(self.total_gas_consumed / self.budget_limit, 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "gas_cost_per_call": self.gas_cost_per_call,
            "total_invocations": self.total_invocations,
            "total_gas_consumed": self.total_gas_consumed,
            "budget_limit": self.budget_limit,
            "budget_remaining": self.budget_remaining,
            "budget_utilization": self.budget_utilization,
        }


@dataclass
class ToolAuditReport:
    """Full audit report across all installed tools."""

    timestamp: float = field(default_factory=time.time)
    total_tools: int = 0
    active_tools: int = 0
    healthy_tools: int = 0
    stale_tools: int = 0
    total_gas_budget: int = 0
    total_gas_consumed: int = 0
    health_reports: list[ToolHealthReport] = field(default_factory=list)
    gas_reports: list[ToolGasReport] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "total_tools": self.total_tools,
            "active_tools": self.active_tools,
            "healthy_tools": self.healthy_tools,
            "stale_tools": self.stale_tools,
            "total_gas_budget": self.total_gas_budget,
            "total_gas_consumed": self.total_gas_consumed,
            "health_reports": [r.to_dict() for r in self.health_reports],
            "gas_reports": [r.to_dict() for r in self.gas_reports],
            "warnings": self.warnings,
        }


class ToolMonitor:
    """Centralized monitoring for all installed tools.

    Combines the perspectives of:
      - Scribe (gas/cost accounting)
      - Chronicler (freshness/staleness)
      - Steward (MCP health + auto-revoke)
    """

    def __init__(self, registry_path: Path | str | None = None):
        self.registry_path = Path(registry_path) if registry_path else _TOOL_REGISTRY_PATH
        self._registry: dict[str, dict[str, Any]] = {}
        self._gas_usage: dict[str, dict[str, int]] = {}  # tool -> {invocations, gas}

        self._load_registry()

    def full_audit(self) -> ToolAuditReport:
        """Run a comprehensive audit of all installed tools.

        Called by `hlf audit` CLI command.
        """
        report = ToolAuditReport()
        report.total_tools = len(self._registry)
        report.active_tools = sum(
            1 for t in self._registry.values() if t.get("status") == "active"
        )

        # Health sweep
        for name in self._registry:
            health = self._check_health(name)
            report.health_reports.append(health)
            if health.healthy:
                report.healthy_tools += 1

        # Gas accounting
        for name, entry in self._registry.items():
            gas_report = self._gas_report(name, entry)
            report.gas_reports.append(gas_report)
            report.total_gas_budget += gas_report.budget_limit
            report.total_gas_consumed += gas_report.total_gas_consumed

        # Staleness check
        stale = self.stale_tools(max_age_days=90)
        report.stale_tools = len(stale)
        for tool_name in stale:
            report.warnings.append(
                f"⚠️ Tool '{tool_name}' not updated in 90+ days — consider upgrading"
            )

        # Unhealthy tools warning
        unhealthy = [r for r in report.health_reports if not r.healthy]
        for uh in unhealthy:
            report.warnings.append(
                f"❌ Tool '{uh.name}' is unhealthy: {uh.error or 'unknown error'}"
            )

        return report

    def health_sweep(self) -> list[ToolHealthReport]:
        """Check health of all active tools."""
        reports = []
        for name, entry in self._registry.items():
            if entry.get("status") == "active":
                reports.append(self._check_health(name))
        return reports

    def gas_report(self) -> list[ToolGasReport]:
        """Get gas usage reports for all tools (Scribe Hat)."""
        reports = []
        for name, entry in self._registry.items():
            reports.append(self._gas_report(name, entry))
        return reports

    def record_invocation(self, tool_name: str, gas_used: int = 0) -> None:
        """Record a tool invocation for gas tracking."""
        if tool_name not in self._gas_usage:
            self._gas_usage[tool_name] = {"invocations": 0, "gas": 0}
        self._gas_usage[tool_name]["invocations"] += 1
        self._gas_usage[tool_name]["gas"] += gas_used

    def stale_tools(self, max_age_days: int = 90) -> list[str]:
        """Find tools not updated in N days (Chronicler Hat)."""
        cutoff = time.time() - (max_age_days * 86400)
        stale = []
        for name, entry in self._registry.items():
            installed_at = entry.get("installed_at", 0)
            if installed_at and installed_at < cutoff:
                stale.append(name)
        return stale

    def auto_revoke_unhealthy(self) -> list[str]:
        """Auto-revoke tools that fail health checks (Steward Hat).

        Sets status to 'suspended' — does NOT uninstall.
        """
        revoked = []
        for name, entry in self._registry.items():
            if entry.get("status") != "active":
                continue

            health = self._check_health(name)
            if not health.healthy:
                entry["status"] = "suspended"
                entry["suspended_at"] = time.time()
                entry["suspend_reason"] = health.error or "health check failed"
                revoked.append(name)
                logger.warning(f"🛑 Auto-revoked tool '{name}': {health.error}")

        if revoked:
            self._save_registry()
            self._log_align("TOOL_AUTO_REVOKE", {"tools": revoked})

        return revoked

    # ── Internal ─────────────────────────────────────────────────────────

    def _check_health(self, tool_name: str) -> ToolHealthReport:
        """Check health of a single tool."""
        entry = self._registry.get(tool_name, {})
        start = time.time()

        try:
            install_path = Path(entry.get("install_path", ""))
            if not install_path.exists():
                return ToolHealthReport(
                    name=tool_name, healthy=False,
                    error="Install path does not exist",
                )

            # Check that entrypoint exists
            entrypoint = install_path / entry.get("entrypoint", "main.py")
            if not entrypoint.exists():
                return ToolHealthReport(
                    name=tool_name, healthy=False,
                    error="Entrypoint file missing",
                )

            # Check sandbox metadata
            sandbox = install_path / ".sandbox.json"
            if not sandbox.exists():
                return ToolHealthReport(
                    name=tool_name, healthy=False,
                    error="Sandbox metadata missing",
                )

            duration = (time.time() - start) * 1000
            return ToolHealthReport(
                name=tool_name, healthy=True,
                response_time_ms=round(duration, 2),
            )

        except Exception as e:
            duration = (time.time() - start) * 1000
            return ToolHealthReport(
                name=tool_name, healthy=False,
                response_time_ms=round(duration, 2),
                error=str(e),
            )

    def _gas_report(self, name: str, entry: dict[str, Any]) -> ToolGasReport:
        """Generate gas usage report for a tool."""
        usage = self._gas_usage.get(name, {"invocations": 0, "gas": 0})
        gas_per_call = entry.get("gas_cost", 1)
        budget_limit = gas_per_call * 1000  # Default budget: 1000 calls worth

        return ToolGasReport(
            name=name,
            gas_cost_per_call=gas_per_call,
            total_invocations=usage["invocations"],
            total_gas_consumed=usage["gas"],
            budget_limit=budget_limit,
            budget_remaining=max(0, budget_limit - usage["gas"]),
        )

    def _load_registry(self) -> None:
        if self.registry_path.exists():
            try:
                data = json.loads(self.registry_path.read_text(encoding="utf-8"))
                self._registry = data.get("tools", {})
            except (json.JSONDecodeError, KeyError):
                self._registry = {}

    def _save_registry(self) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": "1.0.0",
            "tools": self._registry,
            "updated_at": time.time(),
        }
        self.registry_path.write_text(
            json.dumps(data, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    @staticmethod
    def _log_align(action: str, details: dict[str, Any]) -> None:
        try:
            from agents.core.logger import ALSLogger
            ALSLogger().log(action, details)
        except ImportError:
            pass
