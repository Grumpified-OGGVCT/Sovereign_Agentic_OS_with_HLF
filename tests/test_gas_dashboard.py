"""
Tests for GasDashboard — per-agent gas utilization reporting.

Tests cover:
  - Dashboard construction and config loading
  - Per-agent budget management (set, get, reset, list)
  - Report generation with and without bridge
  - Alert generation (over_budget, high_utilization)
  - Agent report for specific agents
  - JSON serialization
  - FastAPI router construction
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from typing import Any

from agents.core.daemons.gas_dashboard import (
    GasDashboard,
    AgentGasReport,
    GasDashboardAlert,
    _load_gas_config,
    create_gas_router,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_bridge() -> MagicMock:
    """Create a mock DaemonBridge with gas accounts."""
    bridge = MagicMock()
    bridge.get_gas_report.return_value = {
        "accounts": {
            "sentinel": {
                "total_gas": 450,
                "operation_count": 12,
                "last_gas_time": 1709981000.0,
            },
            "scribe": {
                "total_gas": 200,
                "operation_count": 8,
                "last_gas_time": 1709981100.0,
            },
            "arbiter": {
                "total_gas": 9500,
                "operation_count": 30,
                "last_gas_time": 1709981200.0,
            },
        },
        "aggregate": {
            "total_agents": 3,
            "total_gas": 10150,
            "total_operations": 50,
            "agents_over_budget": ["arbiter"],
        },
        "bridge_running": True,
    }
    return bridge


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    """Create a temporary settings.json with gas config."""
    config = {
        "deployment_tier": "forge",
        "gas_buckets": {"hearth": 1000, "forge": 10000, "sovereign": 100000},
        "gas_agent_budgets": {
            "sentinel": 5000,
            "scribe": 3000,
        },
    }
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


@pytest.fixture
def dashboard(mock_bridge: MagicMock, config_file: Path) -> GasDashboard:
    """Create a dashboard with bridge and config."""
    d = GasDashboard(bridge=mock_bridge, config_path=config_file)
    return d


# ─── Config Loading ──────────────────────────────────────────────────────────

class TestConfigLoading:
    def test_load_config_from_file(self, config_file: Path) -> None:
        config = _load_gas_config(config_file)
        assert config["tier"] == "forge"
        assert config["tier_budget"] == 10000
        assert config["agent_budgets"]["sentinel"] == 5000

    def test_load_config_missing_file(self) -> None:
        config = _load_gas_config(Path("/nonexistent/settings.json"))
        assert config["tier"] == "hearth"
        assert config["tier_budget"] == 10_000
        assert config["agent_budgets"] == {}

    def test_load_config_invalid_json(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("not json", encoding="utf-8")
        config = _load_gas_config(bad)
        assert config["tier"] == "hearth"  # defaults

    def test_load_config_none_path(self) -> None:
        # Should not crash, uses fallback candidates
        config = _load_gas_config(None)
        assert "tier" in config


# ─── Dashboard Construction ──────────────────────────────────────────────────

class TestDashboardConstruction:
    def test_create_no_bridge(self) -> None:
        # Explicit nonexistent path to avoid auto-discovering real settings.json
        d = GasDashboard(config_path=Path("/nonexistent/settings.json"))
        assert d.tier == "hearth"
        assert d.tier_budget == 10_000

    def test_create_with_config(self, config_file: Path) -> None:
        d = GasDashboard(config_path=config_file)
        assert d.tier == "forge"
        assert d.tier_budget == 10000

    def test_attach_bridge(self, mock_bridge: MagicMock) -> None:
        d = GasDashboard()
        d.attach_bridge(mock_bridge)
        report = d.get_report()
        assert report["status"] == "ok"


# ─── Budget Management ───────────────────────────────────────────────────────

class TestBudgetManagement:
    def test_set_agent_budget(self) -> None:
        d = GasDashboard()
        d.set_agent_budget("test_agent", 5000)
        assert d.get_agent_budget("test_agent") == 5000

    def test_get_default_budget(self) -> None:
        d = GasDashboard()
        assert d.get_agent_budget("unknown") == d.tier_budget

    def test_get_config_budget(self, config_file: Path) -> None:
        d = GasDashboard(config_path=config_file)
        assert d.get_agent_budget("sentinel") == 5000  # from config
        assert d.get_agent_budget("unknown") == 10000  # tier default

    def test_set_overrides_config(self, config_file: Path) -> None:
        d = GasDashboard(config_path=config_file)
        d.set_agent_budget("sentinel", 9999)
        assert d.get_agent_budget("sentinel") == 9999

    def test_reset_budget(self) -> None:
        d = GasDashboard()
        d.set_agent_budget("agent_a", 2000)
        d.reset_agent_budget("agent_a")
        assert d.get_agent_budget("agent_a") == d.tier_budget

    def test_reset_nonexistent_budget(self) -> None:
        d = GasDashboard()
        d.reset_agent_budget("nope")  # should not crash

    def test_list_custom_budgets(self, config_file: Path) -> None:
        d = GasDashboard(config_path=config_file)
        budgets = d.list_custom_budgets()
        assert "sentinel" in budgets
        assert budgets["sentinel"] == 5000

    def test_negative_budget_raises(self) -> None:
        d = GasDashboard()
        with pytest.raises(ValueError, match="non-negative"):
            d.set_agent_budget("agent", -100)


# ─── Report Generation ───────────────────────────────────────────────────────

class TestReportGeneration:
    def test_report_no_bridge(self) -> None:
        d = GasDashboard()
        report = d.get_report()
        assert report["status"] == "no_bridge"
        assert report["agents"] == {}
        assert report["aggregate"]["total_agents"] == 0

    def test_full_report(self, dashboard: GasDashboard) -> None:
        report = dashboard.get_report()
        assert report["status"] == "ok"
        assert "sentinel" in report["agents"]
        assert "scribe" in report["agents"]
        assert "arbiter" in report["agents"]
        assert report["aggregate"]["total_agents"] == 3

    def test_report_aggregate_gas(self, dashboard: GasDashboard) -> None:
        report = dashboard.get_report()
        agg = report["aggregate"]
        assert agg["total_gas"] == 450 + 200 + 9500
        assert agg["total_operations"] == 12 + 8 + 30

    def test_report_config_section(self, dashboard: GasDashboard) -> None:
        report = dashboard.get_report()
        assert report["config"]["tier"] == "forge"
        assert report["config"]["tier_budget"] == 10000

    def test_agent_utilization_pct(self, dashboard: GasDashboard) -> None:
        report = dashboard.get_report()
        sentinel = report["agents"]["sentinel"]
        # sentinel: 450 gas, 5000 budget = 9%
        assert sentinel["utilization_pct"] == 9.0

    def test_agent_over_budget(self, dashboard: GasDashboard) -> None:
        report = dashboard.get_report()
        arbiter = report["agents"]["arbiter"]
        # arbiter: 9500 gas, 10000 tier budget = 95% — not over
        assert arbiter["is_over_budget"] is False


# ─── Agent Report ─────────────────────────────────────────────────────────────

class TestAgentReport:
    def test_known_agent(self, dashboard: GasDashboard) -> None:
        report = dashboard.get_agent_report("sentinel")
        assert report.agent_id == "sentinel"
        assert report.total_gas == 450
        assert report.budget == 5000
        assert report.status == "ok"

    def test_unknown_agent(self, dashboard: GasDashboard) -> None:
        report = dashboard.get_agent_report("nonexistent")
        assert report.status == "inactive"
        assert report.total_gas == 0

    def test_agent_report_no_bridge(self) -> None:
        d = GasDashboard()
        report = d.get_agent_report("any")
        assert report.status == "no_bridge"

    def test_agent_report_to_dict(self, dashboard: GasDashboard) -> None:
        report = dashboard.get_agent_report("scribe")
        d = report.to_dict()
        assert d["agent_id"] == "scribe"
        assert "budget_remaining" in d
        assert "utilization_pct" in d


# ─── Alerts ──────────────────────────────────────────────────────────────────

class TestAlerts:
    def test_over_budget_alert(self, mock_bridge: MagicMock, config_file: Path) -> None:
        # Set scribe budget very low so 200 exceeds it
        d = GasDashboard(bridge=mock_bridge, config_path=config_file)
        d.set_agent_budget("scribe", 100)
        report = d.get_report()
        alert_agents = [a["agent_id"] for a in report["alerts"]]
        assert "scribe" in alert_agents

    def test_high_utilization_alert(self, mock_bridge: MagicMock, config_file: Path) -> None:
        # Set sentinel budget so 450/500 = 90% triggers warning
        d = GasDashboard(bridge=mock_bridge, config_path=config_file, high_utilization_threshold=80.0)
        d.set_agent_budget("sentinel", 500)
        report = d.get_report()
        alert_types = {a["agent_id"]: a["alert_type"] for a in report["alerts"]}
        assert alert_types.get("sentinel") == "high_utilization"

    def test_no_alerts_healthy(self, mock_bridge: MagicMock) -> None:
        # Very high budgets → no alerts
        d = GasDashboard(bridge=mock_bridge)
        d.set_agent_budget("sentinel", 100_000)
        d.set_agent_budget("scribe", 100_000)
        d.set_agent_budget("arbiter", 100_000)
        report = d.get_report()
        assert len(report["alerts"]) == 0

    def test_alert_severity(self, mock_bridge: MagicMock) -> None:
        d = GasDashboard(bridge=mock_bridge)
        d.set_agent_budget("scribe", 50)  # 200 > 50 → critical
        report = d.get_report()
        scribe_alerts = [a for a in report["alerts"] if a["agent_id"] == "scribe"]
        assert scribe_alerts[0]["severity"] == "critical"


# ─── JSON Serialization ─────────────────────────────────────────────────────

class TestSerialization:
    def test_to_json(self, dashboard: GasDashboard) -> None:
        j = dashboard.to_json()
        data = json.loads(j)
        assert data["status"] == "ok"
        assert "agents" in data

    def test_to_json_no_bridge(self) -> None:
        d = GasDashboard()
        j = d.to_json()
        data = json.loads(j)
        assert data["status"] == "no_bridge"


# ─── FastAPI Router ──────────────────────────────────────────────────────────

class TestFastAPIRouter:
    def test_create_router(self, dashboard: GasDashboard) -> None:
        router = create_gas_router(dashboard)
        # Should have routes registered
        assert len(router.routes) > 0

    def test_router_has_gas_prefix(self, dashboard: GasDashboard) -> None:
        router = create_gas_router(dashboard)
        assert router.prefix == "/api/v1/gas"


# ─── AgentGasReport dataclass ────────────────────────────────────────────────

class TestAgentGasReportDataclass:
    def test_defaults(self) -> None:
        r = AgentGasReport(agent_id="test")
        assert r.total_gas == 0
        assert r.budget == 10_000
        assert r.status == "ok"

    def test_to_dict_keys(self) -> None:
        r = AgentGasReport(agent_id="x", total_gas=100, budget=500)
        d = r.to_dict()
        expected_keys = {
            "agent_id", "total_gas", "operation_count", "budget",
            "budget_remaining", "utilization_pct", "is_over_budget",
            "status", "last_activity",
        }
        assert set(d.keys()) == expected_keys


# ─── GasDashboardAlert dataclass ─────────────────────────────────────────────

class TestAlertDataclass:
    def test_alert_to_dict(self) -> None:
        a = GasDashboardAlert(
            agent_id="test",
            alert_type="over_budget",
            message="exceeded",
            severity="critical",
        )
        d = a.to_dict()
        assert d["agent_id"] == "test"
        assert d["severity"] == "critical"
        assert "timestamp" in d
