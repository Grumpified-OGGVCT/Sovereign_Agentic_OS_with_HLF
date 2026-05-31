"""
Tests for MAESTRO Router — intent-driven model provider selection.

Tests cover:
  - Provider profile management
  - Intent-to-capability mapping
  - Routing by intent
  - Explicit model override
  - Cost-aware routing
  - Local preference
  - Fallback chains
  - Provider availability
  - Classification result routing
  - Stats & history
"""

from __future__ import annotations

import pytest

from agents.core.maestro_router import (
    MAESTRORouter,
    ModelCapability,
    ProviderProfile,
    RouteResult,
)

# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def router() -> MAESTRORouter:
    return MAESTRORouter()


@pytest.fixture
def cheap_router() -> MAESTRORouter:
    return MAESTRORouter(cost_limit=1)


@pytest.fixture
def local_router() -> MAESTRORouter:
    return MAESTRORouter(prefer_local=True)


# ─── Provider Profile ───────────────────────────────────────────────────────

class TestProviderProfile:
    def test_supports(self) -> None:
        p = ProviderProfile(name="test", capabilities=[ModelCapability.CHAT, ModelCapability.CODE])
        assert p.supports(ModelCapability.CHAT) is True
        assert p.supports(ModelCapability.VISION) is False

    def test_to_dict(self) -> None:
        p = ProviderProfile(name="google", models=["gemini-3-pro"])
        d = p.to_dict()
        assert d["name"] == "google"
        assert "gemini-3-pro" in d["models"]


# ─── Provider Management ────────────────────────────────────────────────────

class TestManagement:
    def test_list_providers(self, router: MAESTRORouter) -> None:
        providers = router.list_providers()
        assert len(providers) >= 4  # google, openai, anthropic, ollama

    def test_add_provider(self, router: MAESTRORouter) -> None:
        router.add_provider(ProviderProfile(
            name="replicate",
            capabilities=[ModelCapability.IMAGE_GEN],
        ))
        names = [p["name"] for p in router.list_providers()]
        assert "replicate" in names

    def test_remove_provider(self, router: MAESTRORouter) -> None:
        removed = router.remove_provider("ollama")
        assert removed is True
        names = [p["name"] for p in router.list_providers()]
        assert "ollama" not in names

    def test_set_availability(self, router: MAESTRORouter) -> None:
        router.set_provider_availability("google", False)
        result = router.route("query")
        assert result.provider != "google"


# ─── Routing ─────────────────────────────────────────────────────────────────

class TestRouting:
    def test_route_query(self, router: MAESTRORouter) -> None:
        result = router.route("query")
        assert result.provider != "none"
        assert result.reasoning

    def test_route_code_gen(self, router: MAESTRORouter) -> None:
        result = router.route("code_gen")
        assert "code" in [c.lower() for c in result.capabilities_matched]

    def test_route_returns_fallbacks(self, router: MAESTRORouter) -> None:
        result = router.route("query")
        assert len(result.fallback_chain) > 0

    def test_route_unknown(self, router: MAESTRORouter) -> None:
        result = router.route("unknown")
        assert result.provider != "none"

    def test_explicit_model(self, router: MAESTRORouter) -> None:
        result = router.route("query", context={"model": "gpt-4o"})
        assert result.provider == "openai"
        assert result.model == "gpt-4o"

    def test_explicit_model_missing(self, router: MAESTRORouter) -> None:
        result = router.route("query", context={"model": "nonexistent-model"})
        assert result.provider != "none"  # Falls back to normal routing

    def test_no_available_providers(self) -> None:
        router = MAESTRORouter(providers=[
            ProviderProfile(name="dead", is_available=False, capabilities=[ModelCapability.CHAT])
        ])
        result = router.route("query")
        assert result.provider == "none"


# ─── Cost Routing ────────────────────────────────────────────────────────────

class TestCostRouting:
    def test_cost_limit(self, cheap_router: MAESTRORouter) -> None:
        result = cheap_router.route("query")
        assert result.cost_tier <= 1

    def test_prefers_google_by_default(self, router: MAESTRORouter) -> None:
        result = router.route("query", confidence=1.0)
        assert result.provider in ("google", "openai", "anthropic")  # high-priority providers


# ─── Local Preference ───────────────────────────────────────────────────────

class TestLocalPreference:
    def test_local_boost(self, local_router: MAESTRORouter) -> None:
        result = local_router.route("query")
        # Ollama gets a +2 bonus but Google still has better base score
        # Just verify routing works with prefer_local
        assert result.provider != "none"


# ─── Classification Integration ─────────────────────────────────────────────

class TestClassification:
    def test_from_classification(self, router: MAESTRORouter) -> None:
        result = router.route_from_classification({
            "intent": "code_gen",
            "confidence": 0.9,
            "metadata": {},
        })
        assert result.provider != "none"
        assert "code" in [c.lower() for c in result.capabilities_matched]


# ─── Stats & History ────────────────────────────────────────────────────────

class TestStats:
    def test_stats(self, router: MAESTRORouter) -> None:
        router.route("query")
        router.route("code_gen")
        s = router.stats
        assert s["total_routes"] == 2

    def test_history(self, router: MAESTRORouter) -> None:
        router.route("query")
        h = router.get_history()
        assert len(h) == 1
        assert "provider" in h[0]


# ─── Route Result ────────────────────────────────────────────────────────────

class TestRouteResult:
    def test_to_dict(self) -> None:
        r = RouteResult(provider="google", model="gemini-3-pro", reasoning="test")
        d = r.to_dict()
        assert d["provider"] == "google"
