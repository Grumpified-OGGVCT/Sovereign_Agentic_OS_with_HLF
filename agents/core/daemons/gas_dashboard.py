"""
Gas Dashboard — Per-Agent Gas Utilization Report API.

Exposes gas metrics from the DaemonBridge as a structured report, accessible
via FastAPI route or direct programmatic access. Supports user-configurable
per-agent budgets via settings.json.

Configuration (settings.json):
    {
        "gas_buckets": { "hearth": 1000, "forge": 10000, "sovereign": 100000 },
        "gas_agent_budgets": {
            "sentinel": 5000,
            "scribe": 3000,
            "arbiter": 8000
        }
    }

If per-agent budgets are specified, they override the tier-level default.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─── Config Loader ───────────────────────────────────────────────────────────

def _load_gas_config(config_path: Path | str | None = None) -> dict[str, Any]:
    """Load gas configuration from settings.json.

    Returns:
        Dict with 'tier_budget' (int), 'agent_budgets' (dict), and 'tier' (str).
    """
    result: dict[str, Any] = {
        "tier": "hearth",
        "tier_budget": 10_000,
        "agent_budgets": {},
    }

    if config_path is None:
        candidates = [
            Path("config/settings.json"),
            Path(__file__).parent.parent.parent.parent / "config" / "settings.json",
        ]
        for candidate in candidates:
            if candidate.exists():
                config_path = candidate
                break

    if config_path and Path(config_path).exists():
        try:
            data = json.loads(Path(config_path).read_text(encoding="utf-8"))
            tier = data.get("deployment_tier", "hearth")
            gas_buckets = data.get("gas_buckets", {})
            result["tier"] = tier
            result["tier_budget"] = gas_buckets.get(tier, result["tier_budget"])
            # Per-agent overrides
            result["agent_budgets"] = data.get("gas_agent_budgets", {})
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to load gas config: %s", e)

    return result


# ─── Agent Report ────────────────────────────────────────────────────────────

@dataclass
class AgentGasReport:
    """Gas utilization snapshot for a single agent."""

    agent_id: str
    total_gas: int = 0
    operation_count: int = 0
    budget: int = 10_000
    utilization_pct: float = 0.0
    is_over_budget: bool = False
    last_activity: float = 0.0
    budget_remaining: int = 10_000
    status: str = "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "total_gas": self.total_gas,
            "operation_count": self.operation_count,
            "budget": self.budget,
            "budget_remaining": self.budget_remaining,
            "utilization_pct": round(self.utilization_pct, 2),
            "is_over_budget": self.is_over_budget,
            "status": self.status,
            "last_activity": self.last_activity,
        }


# ─── Dashboard ───────────────────────────────────────────────────────────────

@dataclass
class GasDashboardAlert:
    """Alert triggered by gas budget violations."""

    agent_id: str
    alert_type: str  # "over_budget", "high_utilization", "idle"
    message: str
    severity: str  # "warning", "critical"
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "alert_type": self.alert_type,
            "message": self.message,
            "severity": self.severity,
            "timestamp": self.timestamp,
        }


class GasDashboard:
    """Gas utilization dashboard with configurable per-agent budgets.

    Reads metrics from a DaemonBridge's gas accounts and produces
    structured reports with alerts for budget violations.

    Args:
        bridge: DaemonBridge instance (optional — can set later via attach_bridge).
        config_path: Path to settings.json for budget configuration.
        high_utilization_threshold: Percentage at which to trigger warnings (default 80%).
    """

    def __init__(
        self,
        bridge: Any = None,
        config_path: Path | str | None = None,
        high_utilization_threshold: float = 80.0,
    ) -> None:
        self._bridge = bridge
        self._config = _load_gas_config(config_path)
        self._high_threshold = high_utilization_threshold
        self._custom_budgets: dict[str, int] = dict(self._config.get("agent_budgets", {}))

    def attach_bridge(self, bridge: Any) -> None:
        """Attach a DaemonBridge to pull gas data from."""
        self._bridge = bridge

    @property
    def tier(self) -> str:
        return self._config.get("tier", "hearth")

    @property
    def tier_budget(self) -> int:
        return self._config.get("tier_budget", 10_000)

    # ── Budget Management ────────────────────────────────────────────────

    def set_agent_budget(self, agent_id: str, budget: int) -> None:
        """Set a custom gas budget for a specific agent.

        This overrides the tier-level default for this agent.
        """
        if budget < 0:
            raise ValueError(f"Budget must be non-negative, got {budget}")
        self._custom_budgets[agent_id] = budget

    def get_agent_budget(self, agent_id: str) -> int:
        """Get the effective gas budget for an agent.

        Priority: custom per-agent → settings.json per-agent → tier default.
        """
        return self._custom_budgets.get(agent_id, self.tier_budget)

    def reset_agent_budget(self, agent_id: str) -> None:
        """Remove custom budget for agent, reverting to tier default."""
        self._custom_budgets.pop(agent_id, None)

    def list_custom_budgets(self) -> dict[str, int]:
        """Return all custom per-agent budget overrides."""
        return dict(self._custom_budgets)

    # ── Reports ──────────────────────────────────────────────────────────

    def get_agent_report(self, agent_id: str) -> AgentGasReport:
        """Generate a gas report for a single agent."""
        budget = self.get_agent_budget(agent_id)
        report = AgentGasReport(
            agent_id=agent_id,
            budget=budget,
            budget_remaining=budget,
        )

        if self._bridge is None:
            report.status = "no_bridge"
            return report

        # Pull data from bridge gas accounts
        bridge_report = self._bridge.get_gas_report()
        accounts = bridge_report.get("accounts", {})

        if agent_id in accounts:
            acct = accounts[agent_id]
            report.total_gas = acct.get("total_gas", 0)
            report.operation_count = acct.get("operation_count", 0)
            report.last_activity = acct.get("last_gas_time", 0.0)
            report.utilization_pct = (report.total_gas / budget * 100) if budget else 0.0
            report.is_over_budget = report.total_gas > budget
            report.budget_remaining = max(0, budget - report.total_gas)
            report.status = "over_budget" if report.is_over_budget else "ok"
        else:
            report.status = "inactive"

        return report

    def get_report(self) -> dict[str, Any]:
        """Generate a full dashboard report across all agents.

        Returns:
            Dict with 'agents', 'aggregate', 'alerts', 'config'.
        """
        if self._bridge is None:
            return {
                "agents": {},
                "aggregate": {
                    "total_agents": 0,
                    "total_gas": 0,
                    "total_operations": 0,
                    "budget_total": 0,
                    "overall_utilization_pct": 0.0,
                },
                "alerts": [],
                "config": {
                    "tier": self.tier,
                    "tier_budget": self.tier_budget,
                    "custom_budgets": self._custom_budgets,
                    "high_utilization_threshold": self._high_threshold,
                },
                "status": "no_bridge",
            }

        bridge_report = self._bridge.get_gas_report()
        accounts = bridge_report.get("accounts", {})

        # Build per-agent reports
        agent_reports: dict[str, dict[str, Any]] = {}
        alerts: list[dict[str, Any]] = []
        total_gas = 0
        total_ops = 0
        total_budget = 0

        for agent_id in accounts:
            report = self.get_agent_report(agent_id)
            agent_reports[agent_id] = report.to_dict()
            total_gas += report.total_gas
            total_ops += report.operation_count
            total_budget += report.budget

            # Generate alerts
            if report.is_over_budget:
                alerts.append(GasDashboardAlert(
                    agent_id=agent_id,
                    alert_type="over_budget",
                    message=f"{agent_id} exceeded gas budget: {report.total_gas}/{report.budget}",
                    severity="critical",
                ).to_dict())
            elif report.utilization_pct >= self._high_threshold:
                alerts.append(GasDashboardAlert(
                    agent_id=agent_id,
                    alert_type="high_utilization",
                    message=f"{agent_id} at {report.utilization_pct:.1f}% gas utilization",
                    severity="warning",
                ).to_dict())

        overall_util = (total_gas / total_budget * 100) if total_budget else 0.0

        return {
            "agents": agent_reports,
            "aggregate": {
                "total_agents": len(accounts),
                "total_gas": total_gas,
                "total_operations": total_ops,
                "budget_total": total_budget,
                "overall_utilization_pct": round(overall_util, 2),
            },
            "alerts": alerts,
            "config": {
                "tier": self.tier,
                "tier_budget": self.tier_budget,
                "custom_budgets": self._custom_budgets,
                "high_utilization_threshold": self._high_threshold,
            },
            "status": "ok",
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize the full report as JSON string."""
        return json.dumps(self.get_report(), indent=indent, default=str)


# ─── FastAPI Router ──────────────────────────────────────────────────────────

def create_gas_router(dashboard: GasDashboard) -> Any:
    """Create a FastAPI APIRouter for gas dashboard endpoints.

    Args:
        dashboard: GasDashboard instance.

    Returns:
        FastAPI APIRouter with /gas endpoints.
    """
    from fastapi import APIRouter, HTTPException

    router = APIRouter(prefix="/api/v1/gas", tags=["gas"])

    @router.get("/report")
    async def get_gas_report() -> dict[str, Any]:
        """Get full gas utilization report."""
        return dashboard.get_report()

    @router.get("/report/{agent_id}")
    async def get_agent_gas_report(agent_id: str) -> dict[str, Any]:
        """Get gas report for a specific agent."""
        report = dashboard.get_agent_report(agent_id)
        return report.to_dict()

    @router.put("/budget/{agent_id}")
    async def set_agent_budget(agent_id: str, budget: int) -> dict[str, Any]:
        """Set a custom gas budget for an agent."""
        if budget < 0:
            raise HTTPException(status_code=400, detail="Budget must be non-negative")
        dashboard.set_agent_budget(agent_id, budget)
        return {"agent_id": agent_id, "budget": budget, "status": "updated"}

    @router.delete("/budget/{agent_id}")
    async def reset_agent_budget(agent_id: str) -> dict[str, Any]:
        """Reset agent budget to tier default."""
        dashboard.reset_agent_budget(agent_id)
        return {"agent_id": agent_id, "budget": dashboard.tier_budget, "status": "reset_to_default"}

    @router.get("/budgets")
    async def list_budgets() -> dict[str, Any]:
        """List all custom budget overrides."""
        return {
            "tier": dashboard.tier,
            "tier_budget": dashboard.tier_budget,
            "custom_budgets": dashboard.list_custom_budgets(),
        }

    return router
