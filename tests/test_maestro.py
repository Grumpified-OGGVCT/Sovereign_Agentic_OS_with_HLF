"""
Tests for MAESTROClassifier — P5 SAFE Architecture intent classification.

Tests cover:
  - Intent categories and tier mapping
  - Rule-based classification (keyword patterns)
  - LLM fallback with mock backend
  - Classification confidence thresholds
  - Batch classification
  - History and stats tracking
  - Capsule tier integration
  - Edge cases (empty input, ambiguous requests)
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from agents.core.maestro import (
    MAESTROClassifier,
    IntentCategory,
    ClassificationResult,
    INTENT_TO_TIER,
    _classify_by_rules,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def classifier() -> MAESTROClassifier:
    return MAESTROClassifier(
        config_path=Path("/nonexistent"),
        llm_enabled=False,
    )


@pytest.fixture
def llm_classifier() -> MAESTROClassifier:
    """Classifier with mock LLM backend."""
    def mock_llm(prompt: str) -> str:
        return json.dumps({
            "intent": "code_gen",
            "confidence": 0.85,
            "reasoning": "User wants to create something",
        })

    return MAESTROClassifier(
        config_path=Path("/nonexistent"),
        llm_enabled=True,
        llm_backend=mock_llm,
    )


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    cfg = {
        "maestro_confidence_threshold": 0.8,
        "maestro_llm_enabled": False,
    }
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


# ─── Intent Categories ──────────────────────────────────────────────────────

class TestIntentCategories:
    def test_all_categories_have_tiers(self) -> None:
        for cat in IntentCategory:
            assert cat in INTENT_TO_TIER

    def test_tier_values(self) -> None:
        assert INTENT_TO_TIER[IntentCategory.QUERY] == "hearth"
        assert INTENT_TO_TIER[IntentCategory.CODE_GEN] == "forge"
        assert INTENT_TO_TIER[IntentCategory.DEPLOY] == "sovereign"
        assert INTENT_TO_TIER[IntentCategory.UNKNOWN] == "hearth"

    def test_security_scan_is_sovereign(self) -> None:
        assert INTENT_TO_TIER[IntentCategory.SECURITY_SCAN] == "sovereign"


# ─── Rule-Based Classification ──────────────────────────────────────────────

class TestRuleClassification:
    def test_query_intent(self, classifier: MAESTROClassifier) -> None:
        result = classifier.classify("What is the current system status?")
        assert result.intent == IntentCategory.QUERY
        assert result.tier == "hearth"
        assert result.confidence >= 0.7

    def test_code_gen_intent(self, classifier: MAESTROClassifier) -> None:
        result = classifier.classify("Create a new authentication module")
        assert result.intent == IntentCategory.CODE_GEN
        assert result.tier == "forge"

    def test_code_edit_intent(self, classifier: MAESTROClassifier) -> None:
        result = classifier.classify("Fix the bug in the login handler")
        assert result.intent == IntentCategory.CODE_EDIT
        assert result.tier == "forge"

    def test_build_test_intent(self, classifier: MAESTROClassifier) -> None:
        result = classifier.classify("Run the tests for the auth module")
        assert result.intent == IntentCategory.BUILD_TEST
        assert result.tier == "forge"

    def test_system_admin_intent(self, classifier: MAESTROClassifier) -> None:
        result = classifier.classify("Install and configure the Redis server")
        assert result.intent == IntentCategory.SYSTEM_ADMIN
        assert result.tier == "sovereign"

    def test_delegation_intent(self, classifier: MAESTROClassifier) -> None:
        result = classifier.classify("Delegate this task to the code agent")
        assert result.intent == IntentCategory.DELEGATION
        assert result.tier == "forge"

    def test_security_scan_intent(self, classifier: MAESTROClassifier) -> None:
        result = classifier.classify("Run an OWASP security audit on the API")
        assert result.intent == IntentCategory.SECURITY_SCAN
        assert result.tier == "sovereign"

    def test_deploy_intent(self, classifier: MAESTROClassifier) -> None:
        result = classifier.classify("Deploy the latest version to staging")
        assert result.intent == IntentCategory.DEPLOY
        assert result.tier == "sovereign"

    def test_research_intent(self, classifier: MAESTROClassifier) -> None:
        result = classifier.classify("Research the best approach for caching")
        assert result.intent == IntentCategory.RESEARCH
        assert result.tier == "hearth"

    def test_unknown_intent(self, classifier: MAESTROClassifier) -> None:
        result = classifier.classify("xyzzy plugh")
        assert result.intent == IntentCategory.UNKNOWN
        assert result.confidence < 0.5

    def test_empty_input(self, classifier: MAESTROClassifier) -> None:
        result = classifier.classify("")
        assert result.intent == IntentCategory.UNKNOWN

    def test_method_is_rule(self, classifier: MAESTROClassifier) -> None:
        result = classifier.classify("Show me the logs")
        assert result.method == "rule"


# ─── LLM Fallback ───────────────────────────────────────────────────────────

class TestLLMFallback:
    def test_llm_fallback_on_low_confidence(self, llm_classifier: MAESTROClassifier) -> None:
        # Ambiguous input should trigger LLM fallback
        result = llm_classifier.classify("do the thing with the stuff")
        # LLM returns code_gen with 0.85 confidence
        assert result.intent == IntentCategory.CODE_GEN
        assert result.method == "llm"

    def test_llm_backend_json_response(self) -> None:
        def dict_backend(prompt: str) -> dict:
            return {
                "intent": "security_scan",
                "confidence": 0.9,
                "reasoning": "Security audit request",
            }

        c = MAESTROClassifier(
            config_path=Path("/nonexistent"),
            llm_enabled=True,
            llm_backend=dict_backend,
        )
        result = c.classify("check the thing")
        # Dict backend should work too
        assert result.confidence > 0

    def test_llm_backend_failure(self) -> None:
        def failing_backend(prompt: str) -> str:
            raise RuntimeError("LLM down")

        c = MAESTROClassifier(
            config_path=Path("/nonexistent"),
            llm_enabled=True,
            llm_backend=failing_backend,
        )
        # Should not crash — falls back to rule-based
        result = c.classify("mysterious ambiguous request xyz")
        assert result is not None

    def test_llm_not_called_when_disabled(self) -> None:
        backend = MagicMock(return_value='{"intent":"query","confidence":0.9}')
        c = MAESTROClassifier(
            config_path=Path("/nonexistent"),
            llm_enabled=False,
            llm_backend=backend,
        )
        c.classify("something ambiguous xyz")
        backend.assert_not_called()


# ─── Classification Result ──────────────────────────────────────────────────

class TestClassificationResult:
    def test_to_dict(self) -> None:
        r = ClassificationResult(
            intent=IntentCategory.QUERY,
            confidence=0.85,
            tier="hearth",
            reasoning="test",
        )
        d = r.to_dict()
        assert d["intent"] == "query"
        assert d["confidence"] == 0.85
        assert d["tier"] == "hearth"
        assert d["is_high_confidence"] is True

    def test_high_confidence_threshold(self) -> None:
        high = ClassificationResult(intent=IntentCategory.QUERY, confidence=0.8, tier="hearth")
        low = ClassificationResult(intent=IntentCategory.QUERY, confidence=0.5, tier="hearth")
        assert high.is_high_confidence is True
        assert low.is_high_confidence is False


# ─── Config Loading ──────────────────────────────────────────────────────────

class TestConfig:
    def test_config_from_file(self, config_file: Path) -> None:
        c = MAESTROClassifier(config_path=config_file)
        assert c._confidence_threshold == 0.8
        assert c._llm_enabled is False

    def test_config_missing_file(self) -> None:
        c = MAESTROClassifier(config_path=Path("/nonexistent"))
        assert c._confidence_threshold == 0.7  # default


# ─── Batch Classification ───────────────────────────────────────────────────

class TestBatch:
    def test_classify_batch(self, classifier: MAESTROClassifier) -> None:
        requests = [
            "What is the system status?",
            "Create a new module",
            "Deploy to production",
        ]
        results = classifier.classify_batch(requests)
        assert len(results) == 3
        assert results[0].intent == IntentCategory.QUERY
        assert results[2].intent == IntentCategory.DEPLOY


# ─── History & Stats ─────────────────────────────────────────────────────────

class TestHistoryAndStats:
    def test_stats_tracking(self, classifier: MAESTROClassifier) -> None:
        classifier.classify("What is X?")
        classifier.classify("Build the app")
        stats = classifier.stats
        assert stats["total_classifications"] == 2
        assert stats["llm_fallbacks"] == 0

    def test_history(self, classifier: MAESTROClassifier) -> None:
        classifier.classify("Show me the logs")
        history = classifier.get_history()
        assert len(history) == 1
        assert history[0]["intent"] == "query"

    def test_history_filter(self, classifier: MAESTROClassifier) -> None:
        classifier.classify("What is the status?")
        classifier.classify("Create a module")
        history = classifier.get_history(intent=IntentCategory.QUERY)
        assert all(h["intent"] == "query" for h in history)

    def test_distribution(self, classifier: MAESTROClassifier) -> None:
        classifier.classify("What is X?")
        classifier.classify("What is Y?")
        classifier.classify("Create Z")
        dist = classifier.get_distribution()
        assert dist.get("query", 0) >= 2


# ─── Capsule Integration ────────────────────────────────────────────────────

class TestCapsuleIntegration:
    def test_get_capsule_tier(self, classifier: MAESTROClassifier) -> None:
        tier = classifier.get_capsule_tier("Run pytest on all modules")
        assert tier == "forge"

    def test_get_tier_for_intent(self, classifier: MAESTROClassifier) -> None:
        assert classifier.get_tier_for_intent(IntentCategory.DEPLOY) == "sovereign"
        assert classifier.get_tier_for_intent(IntentCategory.QUERY) == "hearth"

    def test_unknown_defaults_to_hearth(self, classifier: MAESTROClassifier) -> None:
        tier = classifier.get_capsule_tier("xyzzy random nonsense")
        assert tier == "hearth"  # safe default
