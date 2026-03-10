"""
Model Gateway — Unified OpenAI-compatible proxy for all LLM providers.

Routes requests through the credential vault to the best available provider.
Runs as a lightweight FastAPI daemon (tray icon eligible).

Architecture:
    Client (MSTY/AnythingLLM/VS Code/HLF) → Gateway :4000/v1/...
        → Credential Vault (pick best provider for capability)
        → Provider API (Google/OpenAI/Anthropic/Ollama/etc.)

The gateway exposes a standard OpenAI-compatible API surface:
    POST /v1/chat/completions  → chat
    GET  /v1/models            → list available models across all providers
    GET  /v1/health            → gateway health check

Configuration (settings.json → "gateway"):
    {
        "enabled": true,
        "port": 4000,
        "host": "127.0.0.1",
        "default_model": "gemini/gemini-3-pro",
        "fallback_model": "qwen3-vl:235b-cloud",
        "rate_limit_rpm": 60,
        "log_requests": true
    }
"""

from __future__ import annotations

import json
import logging
import os
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Cloud Model Detection ────────────────────────────────────────────────────

def is_cloud_model(model_id: str) -> bool:
    """Detect Ollama Cloud models by naming convention.

    Patterns:
      - model:cloud        (e.g., glm-4.6:cloud, kimi-k2.5:cloud)
      - model:tag-cloud    (e.g., qwen3-coder:480b-cloud, qwen3-vl:32b-cloud)
      - model-cloud        (e.g., legacy naming)
    """
    m = model_id.strip().lower()
    if ":" in m:
        tag = m.split(":")[-1]
        return tag == "cloud" or tag.endswith("-cloud")
    return m.endswith("-cloud")


# ── API Key Rotation ─────────────────────────────────────────────────────────

class ApiKeyRotator:
    """Round-robin API key rotator for load sharing across multiple accounts.

    Reads keys from env vars.  Falls back gracefully if only one key exists.
    Thread-safe via itertools.cycle.
    """

    def __init__(self, env_vars: list[str] | None = None) -> None:
        self._env_vars = env_vars or ["OLLAMA_API_KEY", "OLLAMA_2_API_KEY"]
        self._keys: list[str] = []
        for var in self._env_vars:
            val = os.environ.get(var, "").strip()
            if val:
                self._keys.append(val)
        self._index = 0
        self._lock = threading.Lock()

    @property
    def key_count(self) -> int:
        return len(self._keys)

    def next_key(self) -> str:
        """Return the next API key in round-robin order."""
        if not self._keys:
            return ""
        with self._lock:
            key = self._keys[self._index % len(self._keys)]
            self._index += 1
            return key

    def all_keys(self) -> list[str]:
        """Return all loaded keys (masked for logging)."""
        return [k[:8] + "..." for k in self._keys]


# Module-level rotator (loaded once from env)
_ollama_key_rotator = ApiKeyRotator()


# ── Gateway Config ───────────────────────────────────────────────────────────

@dataclass
class GatewayConfig:
    """Configuration for the model gateway."""

    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 4000
    default_model: str = "gemini/gemini-3-pro"
    fallback_model: str = "qwen3-vl:235b-cloud"
    rate_limit_rpm: int = 60
    log_requests: bool = True
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_settings(cls, config_path: Path | str | None = None) -> GatewayConfig:
        """Load gateway config from settings.json."""
        cfg = cls()
        if config_path is None:
            candidates = [
                Path("config/settings.json"),
                Path(__file__).parent.parent.parent / "config" / "settings.json",
            ]
            for c in candidates:
                if c.exists():
                    config_path = c
                    break

        if config_path and Path(config_path).exists():
            try:
                data = json.loads(Path(config_path).read_text(encoding="utf-8"))
                gw = data.get("gateway", {})
                cfg.enabled = gw.get("enabled", cfg.enabled)
                cfg.host = gw.get("host", cfg.host)
                cfg.port = gw.get("port", cfg.port)
                cfg.default_model = gw.get("default_model", cfg.default_model)
                cfg.fallback_model = gw.get("fallback_model", cfg.fallback_model)
                cfg.rate_limit_rpm = gw.get("rate_limit_rpm", cfg.rate_limit_rpm)
                cfg.log_requests = gw.get("log_requests", cfg.log_requests)
                cfg.providers = gw.get("providers", cfg.providers)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Failed to load gateway config: %s", e)

        return cfg


# ─── Model Registry ─────────────────────────────────────────────────────────

@dataclass
class ModelInfo:
    """Info about an available model."""

    id: str                     # e.g. "gemini/gemini-3-pro"
    provider: str               # e.g. "google"
    base_url: str = ""
    capabilities: list[str] = field(default_factory=list)
    is_local: bool = False


class ModelRegistry:
    """Tracks all available models across providers."""

    def __init__(self) -> None:
        self._models: dict[str, ModelInfo] = {}
        self._lock = threading.Lock()

    def register(self, model: ModelInfo) -> None:
        with self._lock:
            self._models[model.id] = model

    def unregister(self, model_id: str) -> None:
        with self._lock:
            self._models.pop(model_id, None)

    def get(self, model_id: str) -> ModelInfo | None:
        return self._models.get(model_id)

    def list_models(self) -> list[dict[str, Any]]:
        """List models in OpenAI-compatible format."""
        return [
            {
                "id": m.id,
                "object": "model",
                "created": 0,
                "owned_by": m.provider,
                "permission": [],
                "root": m.id,
                "parent": None,
            }
            for m in self._models.values()
        ]

    @property
    def count(self) -> int:
        return len(self._models)

    def find_by_provider(self, provider: str) -> list[ModelInfo]:
        return [m for m in self._models.values() if m.provider == provider]


# ─── Request Router ──────────────────────────────────────────────────────────

@dataclass
class RoutingDecision:
    """How a request should be routed."""

    model_id: str
    provider: str
    base_url: str
    api_key: str = ""
    is_fallback: bool = False


class RequestRouter:
    """Routes requests to the appropriate provider based on model ID."""

    def __init__(
        self,
        config: GatewayConfig,
        registry: ModelRegistry,
        vault: Any = None,  # CredentialVault
    ) -> None:
        self._config = config
        self._registry = registry
        self._vault = vault
        self._request_count = 0
        self._error_count = 0
        self._last_requests: list[float] = []  # timestamps for rate limiting

    def route(self, model_id: str | None = None) -> RoutingDecision:
        """Determine routing for a request.

        Args:
            model_id: Requested model (e.g. "gemini/gemini-3-pro").
                       Uses default if None.

        Returns:
            RoutingDecision with provider details.
        """
        self._request_count += 1

        # Rate limit check
        now = time.time()
        self._last_requests = [t for t in self._last_requests if now - t < 60]
        if len(self._last_requests) >= self._config.rate_limit_rpm:
            logger.warning("Rate limit exceeded (%d RPM)", self._config.rate_limit_rpm)

        self._last_requests.append(now)

        # Resolve model
        target = model_id or self._config.default_model
        model_info = self._registry.get(target)

        if model_info is None:
            # Try fallback
            target = self._config.fallback_model
            model_info = self._registry.get(target)
            is_fallback = True
        else:
            is_fallback = False

        # Determine provider and base_url
        if model_info:
            provider = model_info.provider
            base_url = model_info.base_url
        else:
            # Detect provider: explicit "provider/model" format, or cloud model
            parts = target.split("/", 1)
            if len(parts) > 1:
                provider = parts[0]
            elif is_cloud_model(target):
                provider = "ollama-cloud"
            else:
                provider = "ollama-cloud"  # Cloud-first default
            base_url = self._config.providers.get(provider, {}).get("base_url", "https://ollama.com/")

        # Get API key — use rotator for Ollama Cloud, vault for others
        api_key = ""
        if provider == "ollama-cloud" and _ollama_key_rotator.key_count > 0:
            api_key = _ollama_key_rotator.next_key()
        elif self._vault:
            try:
                from agents.core.credential_vault import ProviderType
                prov_type = ProviderType(provider)
                entries = self._vault.find_by_provider(prov_type)
                if entries:
                    api_key = self._vault.get_key(entries[0].key_hash) or ""
            except (ValueError, ImportError):
                pass

        return RoutingDecision(
            model_id=target,
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            is_fallback=is_fallback,
        )

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "total_requests": self._request_count,
            "errors": self._error_count,
            "rate_limit_rpm": self._config.rate_limit_rpm,
            "requests_last_minute": len(self._last_requests),
        }


# ─── Gateway Daemon ──────────────────────────────────────────────────────────

class ModelGateway:
    """Unified model gateway daemon.

    Provides an OpenAI-compatible API surface that routes to any provider
    through the credential vault.

    Usage:
        gateway = ModelGateway.from_config()
        gateway.start()  # Runs FastAPI on :4000
    """

    def __init__(
        self,
        config: GatewayConfig | None = None,
        vault: Any = None,
    ) -> None:
        self._config = config or GatewayConfig()
        self._registry = ModelRegistry()
        self._router = RequestRouter(self._config, self._registry, vault)
        self._vault = vault
        self._running = False
        self._thread: threading.Thread | None = None

        # Register default models from config
        self._register_provider_models()

    @classmethod
    def from_config(
        cls,
        config_path: Path | str | None = None,
        vault: Any = None,
    ) -> ModelGateway:
        config = GatewayConfig.from_settings(config_path)
        return cls(config=config, vault=vault)

    def _register_provider_models(self) -> None:
        """Register models from configured providers."""
        for provider, settings in self._config.providers.items():
            if not settings.get("enabled", False):
                continue

            base_url = settings.get("base_url", "")
            is_local = provider in ("ollama",)

            # Register a default model per provider
            model_id = f"{provider}/{self._config.default_model.split('/')[-1]}"
            if provider == self._config.default_model.split("/")[0]:
                model_id = self._config.default_model

            self._registry.register(ModelInfo(
                id=model_id,
                provider=provider,
                base_url=base_url,
                is_local=is_local,
            ))

        # Always register fallback
        fb_parts = self._config.fallback_model.split("/", 1)
        fb_model = self._config.fallback_model
        # Detect cloud models by :cloud or :tag-cloud patterns
        if is_cloud_model(fb_model):
            fb_provider = "ollama-cloud"
            fb_base = self._config.providers.get("ollama-cloud", {}).get("base_url", "https://ollama.com/")
            fb_local = False
        elif len(fb_parts) > 1:
            fb_provider = fb_parts[0]
            fb_base = self._config.providers.get(fb_provider, {}).get("base_url", "https://ollama.com/")
            fb_local = fb_provider == "ollama"
        else:
            fb_provider = "ollama-cloud"
            fb_base = "https://ollama.com/"
            fb_local = False
        self._registry.register(ModelInfo(
            id=fb_model,
            provider=fb_provider,
            base_url=fb_base,
            is_local=fb_local,
        ))

    def register_model(self, model: ModelInfo) -> None:
        """Dynamically register a model."""
        self._registry.register(model)

    # ── API Surface ─────────────────────────────────────────────────────

    def handle_chat_completion(self, request: dict[str, Any]) -> dict[str, Any]:
        """Handle a /v1/chat/completions request.

        Routes to the appropriate provider and returns the response.
        For now, returns a structured response showing the routing decision.
        Full proxy forwarding requires httpx (added in production).
        """
        model = request.get("model")
        decision = self._router.route(model)

        if self._config.log_requests:
            logger.info(
                "Gateway routing: %s → %s (%s)",
                model, decision.model_id, decision.provider,
            )

        # Build response showing routing (stub — real impl forwards to provider)
        return {
            "id": f"chatcmpl-gw-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": decision.model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": f"[Gateway] Routed to {decision.provider} ({decision.model_id})",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "_gateway": {
                "provider": decision.provider,
                "base_url": decision.base_url,
                "is_fallback": decision.is_fallback,
                "has_api_key": bool(decision.api_key),
            },
        }

    def handle_list_models(self) -> dict[str, Any]:
        """Handle a /v1/models request."""
        return {
            "object": "list",
            "data": self._registry.list_models(),
        }

    def handle_health(self) -> dict[str, Any]:
        """Handle a /v1/health request."""
        return {
            "status": "healthy" if self._config.enabled else "disabled",
            "gateway_port": self._config.port,
            "models_registered": self._registry.count,
            "router_stats": self._router.stats,
            "vault_connected": self._vault is not None,
        }

    # ── FastAPI App Factory ─────────────────────────────────────────────

    def create_app(self) -> Any:
        """Create a FastAPI app for the gateway."""
        try:
            from fastapi import FastAPI, Request
            from fastapi.responses import JSONResponse
        except ImportError:
            logger.error("FastAPI required: pip install fastapi uvicorn")
            return None

        app = FastAPI(
            title="Sovereign Model Gateway",
            description="Unified OpenAI-compatible proxy for all LLM providers",
            version="1.0.0",
        )

        gw = self

        @app.get("/v1/models")
        async def list_models():
            return JSONResponse(gw.handle_list_models())

        @app.post("/v1/chat/completions")
        async def chat_completions(request: Request):
            body = await request.json()
            return JSONResponse(gw.handle_chat_completion(body))

        @app.get("/v1/health")
        async def health():
            return JSONResponse(gw.handle_health())

        @app.get("/")
        async def root():
            return JSONResponse({
                "name": "Sovereign Model Gateway",
                "version": "1.0.0",
                "endpoints": ["/v1/models", "/v1/chat/completions", "/v1/health"],
            })

        return app

    # ── Lifecycle ────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "config": {
                "host": self._config.host,
                "port": self._config.port,
                "default_model": self._config.default_model,
                "fallback_model": self._config.fallback_model,
            },
            "models": self._registry.count,
            "router": self._router.stats,
        }
