"""
Tests for ModelGateway — unified OpenAI-compatible proxy.

Tests cover:
  - Config loading from settings.json
  - Model registry (register, unregister, list)
  - Request routing (default, fallback, explicit model)
  - Chat completion handling
  - Model listing (OpenAI format)
  - Health endpoint
  - Rate limiting tracking
  - Vault integration for API key lookup
  - FastAPI app creation
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agents.core.model_gateway import (
    GatewayConfig,
    ModelGateway,
    ModelInfo,
    ModelRegistry,
    RequestRouter,
)

# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def config() -> GatewayConfig:
    return GatewayConfig(
        port=4001,
        default_model="gemini/gemini-3-pro",
        fallback_model="qwen3-vl:235b-cloud",
        providers={
            "google": {"enabled": True, "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/"},
            "ollama-cloud": {"enabled": True, "base_url": "https://ollama.com/"},
            "ollama": {"enabled": True, "base_url": "http://localhost:11434/v1/"},
        },
    )


@pytest.fixture
def registry() -> ModelRegistry:
    r = ModelRegistry()
    r.register(ModelInfo(id="gemini/gemini-3-pro", provider="google", base_url="https://api.google.com"))
    r.register(ModelInfo(id="qwen3-vl:235b-cloud", provider="ollama-cloud",
                         base_url="https://ollama.com/", is_local=False))
    return r


@pytest.fixture
def gateway(config: GatewayConfig) -> ModelGateway:
    return ModelGateway(config=config)


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    cfg = {
        "gateway": {
            "enabled": True,
            "port": 5000,
            "host": "0.0.0.0",
            "default_model": "openai/gpt-4o",
            "fallback_model": "ollama/llama3:8b",
            "rate_limit_rpm": 30,
            "providers": {
                "openai": {"enabled": True, "base_url": "https://api.openai.com/v1/"},
            },
        }
    }
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


# ─── Config ──────────────────────────────────────────────────────────────────

class TestConfig:
    def test_defaults(self) -> None:
        cfg = GatewayConfig()
        assert cfg.port == 4000
        assert cfg.host == "127.0.0.1"
        assert cfg.default_model == "gemini/gemini-3-pro"

    def test_from_settings(self, config_file: Path) -> None:
        cfg = GatewayConfig.from_settings(config_file)
        assert cfg.port == 5000
        assert cfg.host == "0.0.0.0"
        assert cfg.default_model == "openai/gpt-4o"
        assert cfg.rate_limit_rpm == 30

    def test_from_missing_file(self) -> None:
        cfg = GatewayConfig.from_settings(Path("/nonexistent"))
        assert cfg.port == 4000  # defaults


# ─── Model Registry ─────────────────────────────────────────────────────────

class TestModelRegistry:
    def test_register(self) -> None:
        r = ModelRegistry()
        r.register(ModelInfo(id="test/model", provider="test"))
        assert r.count == 1

    def test_unregister(self, registry: ModelRegistry) -> None:
        registry.unregister("gemini/gemini-3-pro")
        assert registry.count == 1

    def test_get(self, registry: ModelRegistry) -> None:
        m = registry.get("gemini/gemini-3-pro")
        assert m is not None
        assert m.provider == "google"

    def test_get_missing(self, registry: ModelRegistry) -> None:
        assert registry.get("nonexistent") is None

    def test_list_models_openai_format(self, registry: ModelRegistry) -> None:
        models = registry.list_models()
        assert len(models) == 2
        assert all(m["object"] == "model" for m in models)
        ids = [m["id"] for m in models]
        assert "gemini/gemini-3-pro" in ids

    def test_find_by_provider(self, registry: ModelRegistry) -> None:
        google_models = registry.find_by_provider("google")
        assert len(google_models) == 1
        assert google_models[0].id == "gemini/gemini-3-pro"


# ─── Request Router ─────────────────────────────────────────────────────────

class TestRequestRouter:
    def test_route_default(self, config: GatewayConfig, registry: ModelRegistry) -> None:
        router = RequestRouter(config, registry)
        decision = router.route()
        assert decision.model_id == "gemini/gemini-3-pro"
        assert decision.is_fallback is False

    def test_route_explicit(self, config: GatewayConfig, registry: ModelRegistry) -> None:
        router = RequestRouter(config, registry)
        decision = router.route("qwen3-vl:235b-cloud")
        assert decision.model_id == "qwen3-vl:235b-cloud"
        assert decision.provider == "ollama-cloud"

    def test_route_fallback(self, config: GatewayConfig, registry: ModelRegistry) -> None:
        router = RequestRouter(config, registry)
        decision = router.route("nonexistent/model")
        assert decision.model_id == "qwen3-vl:235b-cloud"
        assert decision.is_fallback is True

    def test_route_tracks_requests(self, config: GatewayConfig, registry: ModelRegistry) -> None:
        router = RequestRouter(config, registry)
        router.route()
        router.route()
        assert router.stats["total_requests"] == 2

    def test_route_with_vault(self, config: GatewayConfig, registry: ModelRegistry) -> None:
        mock_vault = MagicMock()
        mock_vault.find_by_provider.return_value = [MagicMock(key_hash="abc")]
        mock_vault.get_key.return_value = "AIzaFakeKey"
        router = RequestRouter(config, registry, vault=mock_vault)
        decision = router.route("gemini/gemini-3-pro")
        assert decision.api_key == "AIzaFakeKey"

    def test_route_cloud_uses_key_rotator(self, config: GatewayConfig, registry: ModelRegistry) -> None:
        """Cloud models use the ApiKeyRotator instead of the vault."""
        from agents.core.model_gateway import ApiKeyRotator
        rotator = ApiKeyRotator.__new__(ApiKeyRotator)
        rotator._keys = ["key_AAA", "key_BBB"]
        rotator._index = 0
        rotator._lock = __import__("threading").Lock()
        import agents.core.model_gateway as gw_mod
        original = gw_mod._ollama_key_rotator
        try:
            gw_mod._ollama_key_rotator = rotator
            router = RequestRouter(config, registry)
            d1 = router.route("qwen3-vl:235b-cloud")
            d2 = router.route("qwen3-vl:235b-cloud")
            assert d1.api_key == "key_AAA"
            assert d2.api_key == "key_BBB"
            # Wraps around
            d3 = router.route("qwen3-vl:235b-cloud")
            assert d3.api_key == "key_AAA"
        finally:
            gw_mod._ollama_key_rotator = original


class TestApiKeyRotator:
    def test_round_robin(self) -> None:
        from agents.core.model_gateway import ApiKeyRotator
        rotator = ApiKeyRotator.__new__(ApiKeyRotator)
        rotator._keys = ["key1", "key2", "key3"]
        rotator._index = 0
        rotator._lock = __import__("threading").Lock()
        assert rotator.next_key() == "key1"
        assert rotator.next_key() == "key2"
        assert rotator.next_key() == "key3"
        assert rotator.next_key() == "key1"  # wraps

    def test_empty(self) -> None:
        from agents.core.model_gateway import ApiKeyRotator
        rotator = ApiKeyRotator.__new__(ApiKeyRotator)
        rotator._keys = []
        rotator._index = 0
        rotator._lock = __import__("threading").Lock()
        assert rotator.next_key() == ""

    def test_single_key(self) -> None:
        from agents.core.model_gateway import ApiKeyRotator
        rotator = ApiKeyRotator.__new__(ApiKeyRotator)
        rotator._keys = ["only_one"]
        rotator._index = 0
        rotator._lock = __import__("threading").Lock()
        assert rotator.next_key() == "only_one"
        assert rotator.next_key() == "only_one"

    def test_key_count(self) -> None:
        from agents.core.model_gateway import ApiKeyRotator
        rotator = ApiKeyRotator.__new__(ApiKeyRotator)
        rotator._keys = ["a", "b"]
        rotator._index = 0
        rotator._lock = __import__("threading").Lock()
        assert rotator.key_count == 2

    def test_masked_keys(self) -> None:
        from agents.core.model_gateway import ApiKeyRotator
        rotator = ApiKeyRotator.__new__(ApiKeyRotator)
        rotator._keys = ["super_secret_key_123"]
        rotator._index = 0
        rotator._lock = __import__("threading").Lock()
        masked = rotator.all_keys()
        assert masked == ["super_se..."]


# ─── Gateway ─────────────────────────────────────────────────────────────────

class TestGateway:
    def test_create(self, gateway: ModelGateway) -> None:
        assert gateway.is_running is False
        assert gateway.stats["models"] > 0

    def test_from_config(self, config_file: Path) -> None:
        gw = ModelGateway.from_config(config_file)
        assert gw.stats["config"]["port"] == 5000

    def test_register_model(self, gateway: ModelGateway) -> None:
        initial = gateway.stats["models"]
        gateway.register_model(ModelInfo(id="custom/model", provider="custom"))
        assert gateway.stats["models"] == initial + 1


# ─── Chat Completion ─────────────────────────────────────────────────────────

class TestChatCompletion:
    def test_handle_chat(self, gateway: ModelGateway) -> None:
        req = {
            "model": "gemini/gemini-3-pro",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        resp = gateway.handle_chat_completion(req)
        assert resp["object"] == "chat.completion"
        assert "choices" in resp
        assert resp["_gateway"]["provider"] in ("google", "ollama-cloud", "ollama")

    def test_handle_chat_no_model(self, gateway: ModelGateway) -> None:
        req = {"messages": [{"role": "user", "content": "Hello"}]}
        resp = gateway.handle_chat_completion(req)
        # Should resolve to some model (default or fallback)
        assert resp["model"] is not None

    def test_handle_chat_unknown_model(self, gateway: ModelGateway) -> None:
        req = {"model": "unknown/model", "messages": []}
        resp = gateway.handle_chat_completion(req)
        assert resp["_gateway"]["is_fallback"] is True


# ─── List Models ─────────────────────────────────────────────────────────────

class TestListModels:
    def test_handle_list(self, gateway: ModelGateway) -> None:
        resp = gateway.handle_list_models()
        assert resp["object"] == "list"
        assert len(resp["data"]) > 0
        assert all(m["object"] == "model" for m in resp["data"])


# ─── Health ──────────────────────────────────────────────────────────────────

class TestHealth:
    def test_handle_health(self, gateway: ModelGateway) -> None:
        resp = gateway.handle_health()
        assert resp["status"] == "healthy"
        assert resp["gateway_port"] == 4001
        assert resp["models_registered"] > 0

    def test_health_disabled(self) -> None:
        cfg = GatewayConfig(enabled=False)
        gw = ModelGateway(config=cfg)
        resp = gw.handle_health()
        assert resp["status"] == "disabled"


# ─── FastAPI App ─────────────────────────────────────────────────────────────

class TestFastAPIApp:
    def test_create_app(self, gateway: ModelGateway) -> None:
        app = gateway.create_app()
        assert app is not None
        # Check routes exist
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/v1/models" in routes
        assert "/v1/chat/completions" in routes
        assert "/v1/health" in routes
