"""
Tests for GatewayHFRBridge — vault capabilities → host function registration.

Tests cover:
  - Gateway host function definitions
  - Capability-to-HFR sync with vault
  - Sync without vault (registers all)
  - Duplicate prevention
  - PWA app listing
  - PWA launcher command generation
  - Bridge stats
  - host_functions.json persistence
  - Registry entry format
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.core.gateway_bridge import (
    GatewayHFRBridge,
    GatewayHostFunction,
    GATEWAY_HOST_FUNCTIONS,
    GOOGLE_SUITE_APPS,
    PWAApp,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def hf_path(tmp_path: Path) -> Path:
    """Create a temporary host_functions.json with base functions."""
    path = tmp_path / "host_functions.json"
    data = {
        "version": "1.2.0",
        "functions": [
            {"name": "READ", "args": [], "returns": "string", "tier": ["hearth"], "gas": 1, "backend": "dapr_file_read"},
            {"name": "WRITE", "args": [], "returns": "bool", "tier": ["hearth"], "gas": 2, "backend": "dapr_file_write"},
        ],
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def bridge(hf_path: Path) -> GatewayHFRBridge:
    return GatewayHFRBridge(host_functions_path=hf_path)


@pytest.fixture
def mock_vault() -> MagicMock:
    vault = MagicMock()
    vault.find_by_capability.return_value = [MagicMock()]  # Always has capability
    return vault


@pytest.fixture
def bridge_with_vault(hf_path: Path, mock_vault: MagicMock) -> GatewayHFRBridge:
    return GatewayHFRBridge(vault=mock_vault, host_functions_path=hf_path)


# ─── Host Function Definitions ──────────────────────────────────────────────

class TestGatewayHostFunctions:
    def test_function_count(self) -> None:
        assert len(GATEWAY_HOST_FUNCTIONS) == 7

    def test_generate_text_exists(self) -> None:
        names = [f.name for f in GATEWAY_HOST_FUNCTIONS]
        assert "GENERATE_TEXT" in names

    def test_generate_image_exists(self) -> None:
        names = [f.name for f in GATEWAY_HOST_FUNCTIONS]
        assert "GENERATE_IMAGE" in names

    def test_embed_text_exists(self) -> None:
        names = [f.name for f in GATEWAY_HOST_FUNCTIONS]
        assert "EMBED_TEXT" in names

    def test_to_registry_entry(self) -> None:
        ghf = GATEWAY_HOST_FUNCTIONS[0]
        entry = ghf.to_registry_entry()
        assert entry["name"] == "GENERATE_TEXT"
        assert entry["backend"] == "model_gateway"
        assert entry["sensitive"] is True
        assert "gas" in entry


# ─── Sync Without Vault ─────────────────────────────────────────────────────

class TestSyncNoVault:
    def test_sync_adds_all(self, bridge: GatewayHFRBridge, hf_path: Path) -> None:
        result = bridge.sync_capabilities()
        assert len(result["added"]) == 7
        assert "GENERATE_TEXT" in result["added"]
        assert "GENERATE_IMAGE" in result["added"]

    def test_sync_updates_json(self, bridge: GatewayHFRBridge, hf_path: Path) -> None:
        bridge.sync_capabilities()
        data = json.loads(hf_path.read_text(encoding="utf-8"))
        names = [f["name"] for f in data["functions"]]
        assert "GENERATE_TEXT" in names
        assert "READ" in names  # Original still there

    def test_sync_no_duplicates(self, bridge: GatewayHFRBridge) -> None:
        bridge.sync_capabilities()
        result2 = bridge.sync_capabilities()
        assert len(result2["added"]) == 0
        assert len(result2["skipped"]) == 7  # All already registered


# ─── Sync With Vault ────────────────────────────────────────────────────────

class TestSyncWithVault:
    def test_sync_checks_capabilities(self, bridge_with_vault: GatewayHFRBridge, mock_vault: MagicMock) -> None:
        result = bridge_with_vault.sync_capabilities()
        assert len(result["added"]) == 7
        # Vault was queried for each function's capability
        assert mock_vault.find_by_capability.call_count == 7

    def test_sync_skips_unsupported(self, hf_path: Path) -> None:
        vault = MagicMock()
        vault.find_by_capability.return_value = []  # No capabilities
        bridge = GatewayHFRBridge(vault=vault, host_functions_path=hf_path)
        result = bridge.sync_capabilities()
        assert len(result["added"]) == 0
        assert len(result["skipped"]) == 7


# ─── PWA Apps ────────────────────────────────────────────────────────────────

class TestPWAApps:
    def test_app_count(self) -> None:
        assert len(GOOGLE_SUITE_APPS) >= 10

    def test_launch_command(self) -> None:
        app = PWAApp("Test", "https://example.com")
        cmd = app.launch_command()
        assert "--app=https://example.com" in cmd
        assert "--new-window" in cmd

    def test_list_available_apps(self, bridge: GatewayHFRBridge) -> None:
        apps = bridge.list_available_apps()
        assert len(apps) >= 10
        names = [a["name"] for a in apps]
        assert "Google Docs" in names
        assert "AI Studio" in names

    def test_launch_pwa_unknown(self, bridge: GatewayHFRBridge) -> None:
        result = bridge.launch_pwa("NonexistentApp")
        assert result["success"] is False
        assert "available" in result

    def test_register_pwa_launcher(self, bridge: GatewayHFRBridge, hf_path: Path) -> None:
        bridge.register_pwa_launcher()
        data = json.loads(hf_path.read_text(encoding="utf-8"))
        names = [f["name"] for f in data["functions"]]
        assert "LAUNCH_PWA" in names


# ─── Stats ───────────────────────────────────────────────────────────────────

class TestStats:
    def test_stats_no_vault(self, bridge: GatewayHFRBridge) -> None:
        stats = bridge.stats
        assert stats["vault_connected"] is False
        assert stats["available_gateway_functions"] == 7
        assert stats["available_pwa_apps"] >= 10

    def test_stats_with_vault(self, bridge_with_vault: GatewayHFRBridge) -> None:
        stats = bridge_with_vault.stats
        assert stats["vault_connected"] is True

    def test_registered_functions_after_sync(self, bridge: GatewayHFRBridge) -> None:
        bridge.sync_capabilities()
        assert len(bridge.registered_functions) == 7


# ─── Edge Cases ──────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_host_functions_file(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.json"
        path.write_text("{}", encoding="utf-8")
        bridge = GatewayHFRBridge(host_functions_path=path)
        result = bridge.sync_capabilities()
        assert len(result["added"]) == 7

    def test_missing_host_functions_file(self, tmp_path: Path) -> None:
        path = tmp_path / "missing.json"
        bridge = GatewayHFRBridge(host_functions_path=path)
        result = bridge.sync_capabilities()
        assert len(result["added"]) == 7
        assert path.exists()  # Created by sync

    def test_total_count(self, bridge: GatewayHFRBridge) -> None:
        result = bridge.sync_capabilities()
        assert result["total"] == 9  # 2 existing + 7 new
