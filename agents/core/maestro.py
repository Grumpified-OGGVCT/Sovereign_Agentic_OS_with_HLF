"""
MAESTRO Intent Classifier — P5 SAFE Architecture, Tier 1.

Classifies incoming requests into intent categories that map to
IntentCapsule security tiers. Determines the appropriate execution
scope before any HLF program runs.

Architecture:
    User Request → MAESTRO → IntentCategory → IntentCapsule → CapsuleInterpreter

MAESTRO uses a two-tier classification strategy:
    1. Rule-based fast path: keyword/pattern matching for common intents
    2. LLM-backed deep classification: for ambiguous or complex requests

Intent categories map to SAFE security tiers:
    - QUERY → hearth (read-only, no side effects)
    - CODE_GEN → forge (file writes, sandbox execution)
    - SYSTEM_ADMIN → sovereign (full permissions, audit required)
    - DELEGATION → forge+ (agent-to-agent, gas budget aware)
    - SECURITY_SCAN → sovereign (security tooling, elevated permissions)
    - UNKNOWN → hearth (safe default)

Configuration (settings.json):
    {
        "maestro_model": "qwen3:8b",
        "maestro_confidence_threshold": 0.7,
        "maestro_llm_enabled": true,
        "maestro_max_retries": 2
    }
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─── Intent Categories ───────────────────────────────────────────────────────

class IntentCategory(StrEnum):
    """Classification categories for incoming requests."""

    QUERY = "query"                   # Information retrieval, read-only
    CODE_GEN = "code_gen"             # Code generation, file creation
    CODE_EDIT = "code_edit"           # Code modification, refactoring
    BUILD_TEST = "build_test"         # Build, test, lint operations
    SYSTEM_ADMIN = "system_admin"     # System configuration, process management
    DELEGATION = "delegation"         # Agent-to-agent task delegation
    SECURITY_SCAN = "security_scan"   # Security audits, vulnerability checks
    DEPLOY = "deploy"                 # Deployment, release operations
    RESEARCH = "research"             # Web search, documentation lookup
    UNKNOWN = "unknown"               # Unclassified — safe default


# ─── SAFE Tier Mapping ───────────────────────────────────────────────────────

INTENT_TO_TIER: dict[IntentCategory, str] = {
    IntentCategory.QUERY: "hearth",
    IntentCategory.RESEARCH: "hearth",
    IntentCategory.CODE_GEN: "forge",
    IntentCategory.CODE_EDIT: "forge",
    IntentCategory.BUILD_TEST: "forge",
    IntentCategory.DELEGATION: "forge",
    IntentCategory.DEPLOY: "sovereign",
    IntentCategory.SYSTEM_ADMIN: "sovereign",
    IntentCategory.SECURITY_SCAN: "sovereign",
    IntentCategory.UNKNOWN: "hearth",  # safe default
}


# ─── Classification Result ───────────────────────────────────────────────────

@dataclass
class ClassificationResult:
    """Result of intent classification."""

    intent: IntentCategory
    confidence: float
    tier: str
    reasoning: str = ""
    method: str = "rule"  # "rule" or "llm"
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_high_confidence(self) -> bool:
        return self.confidence >= 0.7

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "confidence": round(self.confidence, 3),
            "tier": self.tier,
            "reasoning": self.reasoning,
            "method": self.method,
            "is_high_confidence": self.is_high_confidence,
            "timestamp": self.timestamp,
        }


# ─── Rule-Based Classifier ──────────────────────────────────────────────────

# Pattern → (IntentCategory, confidence)
_RULE_PATTERNS: list[tuple[re.Pattern[str], IntentCategory, float]] = [
    # Query patterns (read-only)
    (re.compile(r"\b(what|how|explain|describe|show|list|get|find|search|look up)\b", re.I),
     IntentCategory.QUERY, 0.75),
    (re.compile(r"\b(tell me|can you explain|what is|what are|who is)\b", re.I),
     IntentCategory.QUERY, 0.8),

    # Research
    (re.compile(r"\b(research|investigate|analyze|study|compare|evaluate)\b", re.I),
     IntentCategory.RESEARCH, 0.75),

    # Code generation
    (re.compile(r"\b(create|generate|write|build|implement|add|new file|scaffold)\b", re.I),
     IntentCategory.CODE_GEN, 0.7),
    (re.compile(r"\b(create a|write a|generate a|build a|implement a)\b", re.I),
     IntentCategory.CODE_GEN, 0.85),

    # Code editing
    (re.compile(r"\b(fix|refactor|modify|update|change|edit|rename|move|replace)\b", re.I),
     IntentCategory.CODE_EDIT, 0.75),
    (re.compile(r"\b(fix the|refactor the|update the|change the)\b", re.I),
     IntentCategory.CODE_EDIT, 0.85),

    # Build and test
    (re.compile(r"\b(test|run tests|pytest|lint|build|compile|check)\b", re.I),
     IntentCategory.BUILD_TEST, 0.8),
    (re.compile(r"\b(run the tests|run pytest|npm test|make test)\b", re.I),
     IntentCategory.BUILD_TEST, 0.9),

    # System admin
    (re.compile(r"\b(install|configure|setup|restart|stop|start|kill|process)\b", re.I),
     IntentCategory.SYSTEM_ADMIN, 0.7),
    (re.compile(r"\b(system|service|daemon|server|port|network|firewall)\b", re.I),
     IntentCategory.SYSTEM_ADMIN, 0.65),

    # Delegation
    (re.compile(r"\b(delegate|assign|dispatch|orchestrate|coordinate|agent)\b", re.I),
     IntentCategory.DELEGATION, 0.75),
    (re.compile(r"\b(have .+ agent|assign to|delegate to|run agent)\b", re.I),
     IntentCategory.DELEGATION, 0.85),

    # Security
    (re.compile(r"\b(security|vulnerability|scan|audit|pentest|cve|owasp)\b", re.I),
     IntentCategory.SECURITY_SCAN, 0.8),

    # Deploy
    (re.compile(r"\b(deploy|release|publish|ship|push to prod|staging)\b", re.I),
     IntentCategory.DEPLOY, 0.8),
]


def _classify_by_rules(text: str) -> ClassificationResult:
    """Rule-based classification using pattern matching.

    Returns the highest-confidence match, or UNKNOWN if no patterns match.
    """
    best: ClassificationResult | None = None

    for pattern, intent, base_confidence in _RULE_PATTERNS:
        matches = pattern.findall(text)
        if matches:
            # Boost confidence with more keyword hits
            confidence = min(base_confidence + len(matches) * 0.02, 0.95)
            if best is None or confidence > best.confidence:
                best = ClassificationResult(
                    intent=intent,
                    confidence=confidence,
                    tier=INTENT_TO_TIER[intent],
                    reasoning=f"Matched pattern: {pattern.pattern[:40]}...",
                    method="rule",
                )

    if best is not None:
        return best

    return ClassificationResult(
        intent=IntentCategory.UNKNOWN,
        confidence=0.3,
        tier=INTENT_TO_TIER[IntentCategory.UNKNOWN],
        reasoning="No rule patterns matched",
        method="rule",
    )


# ─── MAESTRO Classifier ─────────────────────────────────────────────────────

class MAESTROClassifier:
    """Multi-Agent Execution Security Through Reasoning and Orchestration.

    Two-tier intent classification:
        1. Rule-based fast path for common patterns
        2. LLM fallback for ambiguous requests

    Args:
        config_path: Path to settings.json
        confidence_threshold: Minimum confidence for rule-based to be accepted
        llm_enabled: Whether to use LLM fallback for low-confidence results
        llm_backend: Optional callable for LLM classification
    """

    def __init__(
        self,
        config_path: Path | str | None = None,
        confidence_threshold: float = 0.7,
        llm_enabled: bool = True,
        llm_backend: Any = None,
    ) -> None:
        self._confidence_threshold = confidence_threshold
        self._llm_enabled = llm_enabled
        self._llm_backend = llm_backend
        self._classification_count = 0
        self._llm_fallback_count = 0
        self._history: list[ClassificationResult] = []
        self._history_limit = 200

        # Load config
        self._load_config(config_path)

    def _load_config(self, config_path: Path | str | None) -> None:
        """Load MAESTRO config from settings.json."""
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
                self._confidence_threshold = data.get(
                    "maestro_confidence_threshold", self._confidence_threshold
                )
                self._llm_enabled = data.get("maestro_llm_enabled", self._llm_enabled)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Failed to load MAESTRO config: %s", e)

    # ── Core Classification ─────────────────────────────────────────────

    def classify(self, text: str, context: dict[str, Any] | None = None) -> ClassificationResult:
        """Classify a request into an intent category.

        Args:
            text: The user's request text.
            context: Optional context (agent_id, session history, etc.)

        Returns:
            ClassificationResult with intent, confidence, and tier mapping.
        """
        self._classification_count += 1

        # Stage 1: Rule-based fast path
        result = _classify_by_rules(text)

        # Stage 2: LLM fallback if confidence is low
        if result.confidence < self._confidence_threshold and self._llm_enabled:
            llm_result = self._classify_with_llm(text, context)
            if llm_result is not None and llm_result.confidence > result.confidence:
                result = llm_result
                self._llm_fallback_count += 1

        # Record history
        if context:
            result.metadata = context
        self._history.append(result)
        if len(self._history) > self._history_limit:
            self._history = self._history[-self._history_limit:]

        return result

    def classify_batch(
        self, requests: list[str], context: dict[str, Any] | None = None
    ) -> list[ClassificationResult]:
        """Classify multiple requests."""
        return [self.classify(text, context) for text in requests]

    def _classify_with_llm(
        self, text: str, context: dict[str, Any] | None = None
    ) -> ClassificationResult | None:
        """LLM-backed classification for ambiguous requests."""
        if not self._llm_backend:
            return None

        try:
            # Build prompt
            categories = ", ".join(c.value for c in IntentCategory if c != IntentCategory.UNKNOWN)
            prompt = (
                f"Classify this request into exactly one category: {categories}\n"
                f"Request: {text}\n"
                f"Respond with JSON: {{\"intent\": \"<category>\", \"confidence\": 0.0-1.0, "
                f"\"reasoning\": \"<brief explanation>\"}}"
            )

            # Call LLM backend
            response = self._llm_backend(prompt)

            # Parse response
            if isinstance(response, str):
                # Try to extract JSON from response
                json_match = re.search(r"\{[^}]+\}", response)
                if json_match:
                    data = json.loads(json_match.group())
                else:
                    return None
            elif isinstance(response, dict):
                data = response
            else:
                return None

            intent_str = data.get("intent", "unknown")
            try:
                intent = IntentCategory(intent_str)
            except ValueError:
                intent = IntentCategory.UNKNOWN

            return ClassificationResult(
                intent=intent,
                confidence=float(data.get("confidence", 0.5)),
                tier=INTENT_TO_TIER[intent],
                reasoning=data.get("reasoning", "LLM classification"),
                method="llm",
            )

        except Exception as e:
            logger.warning("LLM classification failed: %s", e)
            return None

    # ── Capsule Integration ──────────────────────────────────────────────

    def get_capsule_tier(self, text: str, context: dict[str, Any] | None = None) -> str:
        """Convenience: classify and return just the SAFE tier string."""
        result = self.classify(text, context)
        return result.tier

    def get_tier_for_intent(self, intent: IntentCategory) -> str:
        """Get the SAFE tier for a given intent category."""
        return INTENT_TO_TIER.get(intent, "hearth")

    # ── Stats & History ──────────────────────────────────────────────────

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "total_classifications": self._classification_count,
            "llm_fallbacks": self._llm_fallback_count,
            "rule_only": self._classification_count - self._llm_fallback_count,
            "llm_enabled": self._llm_enabled,
            "confidence_threshold": self._confidence_threshold,
            "history_size": len(self._history),
        }

    def get_history(
        self, intent: IntentCategory | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Get recent classification history."""
        filtered = self._history
        if intent is not None:
            filtered = [r for r in filtered if r.intent == intent]
        return [r.to_dict() for r in filtered[-limit:]]

    def get_distribution(self) -> dict[str, int]:
        """Get intent distribution from history."""
        dist: dict[str, int] = {}
        for r in self._history:
            dist[r.intent.value] = dist.get(r.intent.value, 0) + 1
        return dist
