"""
MAESTRO Router — Intent-driven model provider selection.

Connects MAESTRO's intent classification to the Model Gateway's provider
catalog. Given a user request, MAESTRO classifies intent, then this router
selects the optimal model/provider combination.

Routing strategies:
    1. Intent-to-capability mapping (CODE_GEN → code-optimized models)
    2. Cost-aware routing (prefer lower-cost providers for simple queries)
    3. Capability matching (vision, code, reasoning, etc.)
    4. Fallback chains (Gemini primary → OpenAI fallback → Ollama local)

Architecture:
    User request → MAESTROClassifier.classify(text) → ClassificationResult
      → MAESTRORouter.route(result) → ProviderSelection
      → ModelGateway.proxy(to_provider) → Response
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


# ─── Capabilities ───────────────────────────────────────────────────────────

class ModelCapability(StrEnum):
    """Capabilities a model/provider may support."""

    CHAT = "chat"
    CODE = "code"
    VISION = "vision"
    REASONING = "reasoning"
    EMBEDDINGS = "embeddings"
    IMAGE_GEN = "image_gen"
    VIDEO_GEN = "video_gen"
    TTS = "tts"
    STT = "stt"


# ─── Provider ───────────────────────────────────────────────────────────────

@dataclass
class ProviderProfile:
    """Profile of an AI provider for routing decisions.

    Describes what a provider can do, its cost tier, and priority.
    """

    name: str
    models: list[str] = field(default_factory=list)
    capabilities: list[ModelCapability] = field(default_factory=list)
    cost_tier: int = 2           # 1=cheap, 2=moderate, 3=expensive
    priority: int = 1            # Lower = preferred
    max_context: int = 128_000
    is_local: bool = False       # True for Ollama
    is_available: bool = True

    def supports(self, capability: ModelCapability) -> bool:
        return capability in self.capabilities

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "models": self.models,
            "capabilities": [c.value for c in self.capabilities],
            "cost_tier": self.cost_tier,
            "priority": self.priority,
            "is_local": self.is_local,
            "is_available": self.is_available,
        }


# ─── Route Result ───────────────────────────────────────────────────────────

@dataclass
class RouteResult:
    """Result of a routing decision."""

    provider: str
    model: str
    reasoning: str
    capabilities_matched: list[str] = field(default_factory=list)
    cost_tier: int = 2
    fallback_chain: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "reasoning": self.reasoning,
            "capabilities_matched": self.capabilities_matched,
            "cost_tier": self.cost_tier,
            "fallback_chain": self.fallback_chain,
        }


# ─── Intent → Capability Mapping ────────────────────────────────────────────

_INTENT_CAPABILITIES: dict[str, list[ModelCapability]] = {
    "query": [ModelCapability.CHAT],
    "code_gen": [ModelCapability.CODE, ModelCapability.REASONING],
    "code_edit": [ModelCapability.CODE],
    "build_test": [ModelCapability.CODE],
    "system_admin": [ModelCapability.CHAT, ModelCapability.REASONING],
    "delegation": [ModelCapability.CHAT, ModelCapability.REASONING],
    "security_scan": [ModelCapability.CODE, ModelCapability.REASONING],
    "deploy": [ModelCapability.CHAT],
    "research": [ModelCapability.CHAT, ModelCapability.REASONING],
    "unknown": [ModelCapability.CHAT],
}


# ─── Default Providers ──────────────────────────────────────────────────────

_DEFAULT_PROVIDERS: list[ProviderProfile] = [
    ProviderProfile(
        name="google",
        models=["gemini-3-pro", "gemini-3-flash", "gemini-2.5-flash"],
        capabilities=[
            ModelCapability.CHAT, ModelCapability.CODE,
            ModelCapability.VISION, ModelCapability.REASONING,
            ModelCapability.EMBEDDINGS, ModelCapability.IMAGE_GEN,
        ],
        cost_tier=2,
        priority=1,
    ),
    ProviderProfile(
        name="openai",
        models=["gpt-4.1", "gpt-4o", "o3"],
        capabilities=[
            ModelCapability.CHAT, ModelCapability.CODE,
            ModelCapability.VISION, ModelCapability.REASONING,
            ModelCapability.EMBEDDINGS, ModelCapability.IMAGE_GEN,
        ],
        cost_tier=3,
        priority=2,
    ),
    ProviderProfile(
        name="anthropic",
        models=["claude-4-sonnet", "claude-3.5-sonnet"],
        capabilities=[
            ModelCapability.CHAT, ModelCapability.CODE,
            ModelCapability.VISION, ModelCapability.REASONING,
        ],
        cost_tier=3,
        priority=3,
    ),
    ProviderProfile(
        name="ollama",
        models=["llama3.3", "codellama", "mixtral"],
        capabilities=[
            ModelCapability.CHAT, ModelCapability.CODE,
            ModelCapability.EMBEDDINGS,
        ],
        cost_tier=1,
        priority=4,
        is_local=True,
    ),
]


# ─── Router ─────────────────────────────────────────────────────────────────

class MAESTRORouter:
    """Routes intents to optimal model providers.

    Uses MAESTRO classification results to select the best provider
    and model for a given request. Supports fallback chains and
    cost-aware routing.

    Args:
        providers: List of provider profiles (uses defaults if None).
        prefer_local: If True, boost priority for local providers.
        cost_limit: Maximum cost tier (1-3). 0 = no limit.
    """

    def __init__(
        self,
        providers: list[ProviderProfile] | None = None,
        prefer_local: bool = False,
        cost_limit: int = 0,
    ) -> None:
        self._providers = list(providers or _DEFAULT_PROVIDERS)
        self._prefer_local = prefer_local
        self._cost_limit = cost_limit
        self._history: list[RouteResult] = []

    # ── Provider Management ─────────────────────────────────────────────

    def add_provider(self, provider: ProviderProfile) -> None:
        """Add a provider to the router."""
        self._providers.append(provider)

    def remove_provider(self, name: str) -> bool:
        """Remove a provider by name. Returns True if removed."""
        before = len(self._providers)
        self._providers = [p for p in self._providers if p.name != name]
        return len(self._providers) < before

    def list_providers(self) -> list[dict[str, Any]]:
        """List all registered providers."""
        return [p.to_dict() for p in self._providers]

    def set_provider_availability(self, name: str, available: bool) -> None:
        """Mark a provider as available/unavailable."""
        for p in self._providers:
            if p.name == name:
                p.is_available = available

    # ── Routing ─────────────────────────────────────────────────────────

    def route(
        self,
        intent: str,
        confidence: float = 1.0,
        context: dict[str, Any] | None = None,
    ) -> RouteResult:
        """Route an intent to the best provider/model.

        Args:
            intent: MAESTRO intent category string.
            confidence: Classification confidence (0-1).
            context: Optional context (explicit model request, etc.)

        Returns:
            RouteResult with provider, model, and reasoning.
        """
        context = context or {}

        # Check explicit model override
        explicit_model = context.get("model")
        if explicit_model:
            provider = self._find_provider_for_model(explicit_model)
            if provider:
                result = RouteResult(
                    provider=provider.name,
                    model=explicit_model,
                    reasoning=f"Explicit model request: {explicit_model}",
                    cost_tier=provider.cost_tier,
                )
                self._history.append(result)
                return result

        # Get required capabilities for this intent
        required = _INTENT_CAPABILITIES.get(intent, [ModelCapability.CHAT])

        # Score and rank providers
        candidates = self._score_providers(required, confidence)

        if not candidates:
            result = RouteResult(
                provider="none",
                model="none",
                reasoning="No available providers match requirements",
            )
            self._history.append(result)
            return result

        # Pick the best
        best = candidates[0]
        fallbacks = [c["provider"].name for c in candidates[1:4]]

        result = RouteResult(
            provider=best["provider"].name,
            model=best["provider"].models[0] if best["provider"].models else "default",
            reasoning=f"Best match for {intent} intent (score={best['score']:.1f})",
            capabilities_matched=[c.value for c in required if best["provider"].supports(c)],
            cost_tier=best["provider"].cost_tier,
            fallback_chain=fallbacks,
        )
        self._history.append(result)
        return result

    def route_from_classification(self, classification: dict[str, Any]) -> RouteResult:
        """Route directly from a MAESTRO ClassificationResult dict."""
        return self.route(
            intent=classification.get("intent", "unknown"),
            confidence=classification.get("confidence", 0.5),
            context=classification.get("metadata", {}),
        )

    # ── Stats ───────────────────────────────────────────────────────────

    @property
    def stats(self) -> dict[str, Any]:
        provider_counts: dict[str, int] = {}
        for r in self._history:
            provider_counts[r.provider] = provider_counts.get(r.provider, 0) + 1

        return {
            "total_routes": len(self._history),
            "provider_counts": provider_counts,
            "available_providers": sum(1 for p in self._providers if p.is_available),
        }

    def get_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get recent routing decisions."""
        return [r.to_dict() for r in self._history[-limit:]]

    # ── Internal ────────────────────────────────────────────────────────

    def _score_providers(
        self,
        required: list[ModelCapability],
        confidence: float,
    ) -> list[dict[str, Any]]:
        """Score and rank providers for the given requirements."""
        candidates = []

        for provider in self._providers:
            if not provider.is_available:
                continue

            if self._cost_limit and provider.cost_tier > self._cost_limit:
                continue

            # Capability match score (0-10)
            matched = sum(1 for c in required if provider.supports(c))
            capability_score = (matched / max(len(required), 1)) * 10

            # Priority score (lower priority = higher score)
            priority_score = max(0, 5 - provider.priority)

            # Cost score (cheaper = better for low-confidence)
            cost_score = max(0, (4 - provider.cost_tier) * (1 - confidence))

            # Local preference bonus
            local_bonus = 2 if self._prefer_local and provider.is_local else 0

            score = capability_score + priority_score + cost_score + local_bonus

            if capability_score > 0:  # Must match at least one capability
                candidates.append({"provider": provider, "score": score})

        candidates.sort(key=lambda c: c["score"], reverse=True)
        return candidates

    def _find_provider_for_model(self, model: str) -> ProviderProfile | None:
        """Find the provider that hosts a specific model."""
        for provider in self._providers:
            if model in provider.models:
                return provider
        return None
