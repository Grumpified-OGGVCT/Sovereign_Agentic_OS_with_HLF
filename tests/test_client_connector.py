"""
Tests for ClientConnector — auto-config for local AI clients.

Tests cover:
  - Client listing
  - Config generation per client
  - All clients have valid configs
  - Config export to file
  - Custom gateway URL propagation
  - Gateway URL rendering
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path

from agents.core.client_connector import (
    ClientConnector,
    SUPPORTED_CLIENTS,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def connector() -> ClientConnector:
    return ClientConnector()


@pytest.fixture
def custom_connector() -> ClientConnector:
    return ClientConnector(gateway_host="192.168.1.10", gateway_port=5000)


# ─── Client Listing ─────────────────────────────────────────────────────────

class TestClientListing:
    def test_supported_count(self) -> None:
        assert len(SUPPORTED_CLIENTS) >= 6

    def test_list_clients(self, connector: ClientConnector) -> None:
        clients = connector.list_clients()
        assert len(clients) >= 6
        names = [c["name"] for c in clients]
        assert "msty" in names
        assert "anythingllm" in names
        assert "lollms" in names
        assert "studiollm" in names
        assert "vscode_continue" in names
        assert "open_webui" in names

    def test_client_count(self, connector: ClientConnector) -> None:
        assert connector.client_count >= 6


# ─── Config Generation ──────────────────────────────────────────────────────

class TestConfigGeneration:
    def test_msty_config(self, connector: ClientConnector) -> None:
        cfg = connector.get_config("msty")
        assert cfg is not None
        assert cfg["client"] == "MSTY Studio"
        assert "127.0.0.1:4000" in cfg["gateway_url"]

    def test_anythingllm_config(self, connector: ClientConnector) -> None:
        cfg = connector.get_config("anythingllm")
        assert cfg is not None
        assert cfg["config"]["provider"] == "generic-openai"

    def test_lollms_config(self, connector: ClientConnector) -> None:
        cfg = connector.get_config("lollms")
        assert cfg is not None
        assert cfg["config_format"] == "yaml"

    def test_studiollm_config(self, connector: ClientConnector) -> None:
        cfg = connector.get_config("studiollm")
        assert cfg is not None
        assert cfg["config"]["provider_type"] == "openai_compatible"

    def test_vscode_config(self, connector: ClientConnector) -> None:
        cfg = connector.get_config("vscode_continue")
        assert cfg is not None
        assert "models" in cfg["config"]

    def test_open_webui_config(self, connector: ClientConnector) -> None:
        cfg = connector.get_config("open_webui")
        assert cfg is not None
        assert "OPENAI_API_BASE_URL" in cfg["config"]

    def test_unknown_client(self, connector: ClientConnector) -> None:
        assert connector.get_config("nonexistent") is None

    def test_all_configs(self, connector: ClientConnector) -> None:
        configs = connector.get_all_configs()
        assert len(configs) >= 6
        assert all(c is not None for c in configs)


# ─── Custom Gateway ─────────────────────────────────────────────────────────

class TestCustomGateway:
    def test_custom_url(self, custom_connector: ClientConnector) -> None:
        assert "192.168.1.10:5000" in custom_connector.gateway_url

    def test_custom_in_config(self, custom_connector: ClientConnector) -> None:
        cfg = custom_connector.get_config("msty")
        assert cfg is not None
        assert "192.168.1.10:5000" in cfg["gateway_url"]
        config = cfg["config"]
        assert "192.168.1.10:5000" in config["base_url"]


# ─── Export ──────────────────────────────────────────────────────────────────

class TestExport:
    def test_export_json(self, connector: ClientConnector, tmp_path: Path) -> None:
        out = tmp_path / "msty.json"
        result = connector.export_config("msty", out)
        assert result is True
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["provider"] == "custom"

    def test_export_env(self, connector: ClientConnector, tmp_path: Path) -> None:
        out = tmp_path / "webui.env"
        result = connector.export_config("open_webui", out)
        assert result is True
        assert "OPENAI_API_BASE_URL" in out.read_text()

    def test_export_yaml(self, connector: ClientConnector, tmp_path: Path) -> None:
        out = tmp_path / "lollms.yaml"
        result = connector.export_config("lollms", out)
        assert result is True
        assert out.exists()

    def test_export_unknown(self, connector: ClientConnector, tmp_path: Path) -> None:
        out = tmp_path / "bad.json"
        result = connector.export_config("nonexistent", out)
        assert result is False
