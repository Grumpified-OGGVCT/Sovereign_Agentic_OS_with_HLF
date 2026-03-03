"""
Tests for OllamaDispatcher — multi-provider inference engine.

Covers:
  - Complexity scoring (RFC 9004 𝕔)
  - InferenceRequest/Result data structures
  - StreamChunk SSE formatting
  - OllamaDispatcher routing and downshift
  - Provider selection logic
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import agents.gateway.ollama_dispatch as dispatch_mod
from agents.gateway.ollama_dispatch import (
    InferenceRequest,
    InferenceResult,
    OllamaDispatcher,
    StreamChunk,
    complexity_score,
    get_dispatcher,
)

# ─── Complexity Scoring ──────────────────────────────────────────────────────


class TestComplexityScore:
    """RFC 9004 complexity scoring for model tier selection."""

    def test_short_text_low_complexity(self) -> None:
        score = complexity_score("hello world")
        assert 0.0 <= score <= 0.3, f"Short text should be low complexity: {score}"

    def test_long_text_higher_complexity(self) -> None:
        long_text = " ".join(["word"] * 300)
        score = complexity_score(long_text)
        assert score >= 0.3, f"Long text should raise complexity: {score}"

    def test_complex_ast_raises_score(self) -> None:
        ast = {
            "program": [
                {"tag": "INTENT", "args": ["a"]},
                {"tag": "PARALLEL", "args": ["b"]},
                {"tag": "SPAWN", "args": ["c"]},
                {"tag": "IF", "args": ["d"], "then": [{"tag": "SET", "name": "x", "value": 1}]},
                {"tag": "WEB_SEARCH", "args": ["e"]},
                {"tag": "FUNCTION", "args": ["f"]},
                {"tag": "IMPORT", "args": ["g"]},
                {"tag": "SET", "name": "y", "value": 2},
                {"tag": "SET", "name": "z", "value": 3},
                {"tag": "SET", "name": "w", "value": 4},
                {"tag": "RESULT", "code": 0, "message": "ok"},
            ]
        }
        score = complexity_score("complex multi-step workflow", ast)
        assert score >= 0.5, f"Complex AST should be high complexity: {score}"

    def test_empty_ast_stays_low(self) -> None:
        score = complexity_score("simple", {"program": []})
        assert score <= 0.3

    def test_epistemic_modifiers_increase_score(self) -> None:
        ast = {
            "program": [
                {"tag": "INTENT", "args": ["a"], "epistemic_confidence": 0.85},
                {"tag": "RESULT", "code": 0, "message": "ok", "confidence": 0.9},
            ]
        }
        score = complexity_score("uncertain query", ast)
        # Epistemic modifiers should add at least some score
        assert score >= 0.15

    def test_score_capped_at_1(self) -> None:
        """Even extreme inputs should not exceed 1.0."""
        long_text = " ".join(["word"] * 500)
        ast = {
            "program": [
                {
                    "tag": "PARALLEL",
                    "args": [f"p{i}"],
                    "epistemic_confidence": 0.5,
                    "body": [{"tag": "SPAWN", "args": ["inner"]}],
                }
                for i in range(20)
            ]
        }
        score = complexity_score(long_text, ast)
        assert score <= 1.0


# ─── Data Structures ────────────────────────────────────────────────────────


class TestInferenceResult:
    """InferenceResult serialization and hashing."""

    def test_to_dict(self) -> None:
        result = InferenceResult(
            text="Hello, world!",
            model="qwen:7b",
            provider="ollama",
            latency_ms=150.5,
            tokens_eval=10,
            tokens_prompt=5,
        )
        d = result.to_dict()
        assert d["model"] == "qwen:7b"
        assert d["provider"] == "ollama"
        assert d["latency_ms"] == 150.5
        assert d["tokens"]["total"] == 15

    def test_log_hash_is_sha256(self) -> None:
        result = InferenceResult(
            text="secret response",
            model="test",
            provider="test",
            latency_ms=0,
        )
        assert len(result.log_hash) == 64  # SHA-256 hex digest

    def test_different_text_different_hash(self) -> None:
        r1 = InferenceResult(text="a", model="m", provider="p", latency_ms=0)
        r2 = InferenceResult(text="b", model="m", provider="p", latency_ms=0)
        assert r1.log_hash != r2.log_hash


class TestStreamChunk:
    """SSE formatting for stream chunks."""

    def test_to_sse_format(self) -> None:
        chunk = StreamChunk(text="Hello", done=False)
        sse = chunk.to_sse()
        assert sse.startswith("data: ")
        assert sse.endswith("\n\n")
        payload = json.loads(sse[6:].strip())
        assert payload["text"] == "Hello"
        assert payload["done"] is False

    def test_done_chunk(self) -> None:
        chunk = StreamChunk(text="", done=True, model="qwen:7b")
        sse = chunk.to_sse()
        payload = json.loads(sse[6:].strip())
        assert payload["done"] is True


# ─── Dispatcher ──────────────────────────────────────────────────────────────


class TestOllamaDispatcher:
    """OllamaDispatcher construction and configuration."""

    def test_default_config(self) -> None:
        dispatcher = OllamaDispatcher()
        assert "11434" in dispatcher.ollama_host
        assert dispatcher.fallback_model == "qwen:7b"

    def test_custom_config(self) -> None:
        dispatcher = OllamaDispatcher(
            ollama_host="http://gpu-server:11434",
            fallback_model="phi3:mini",
        )
        assert "gpu-server" in dispatcher.ollama_host
        assert dispatcher.fallback_model == "phi3:mini"

    def test_singleton_dispatcher(self) -> None:
        # Reset singleton state
        dispatch_mod._dispatcher = None
        try:
            d1 = get_dispatcher()
            d2 = get_dispatcher()
            assert d1 is d2
        finally:
            dispatch_mod._dispatcher = None

    @pytest.mark.asyncio
    async def test_generate_calls_ollama(self) -> None:
        """Test that generate() calls the Ollama API correctly."""
        dispatcher = OllamaDispatcher()
        req = InferenceRequest(
            prompt="What is 2+2?",
            model="qwen:7b",
            metadata={"provider": "ollama"},
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "response": "4",
            "model": "qwen:7b",
            "eval_count": 5,
            "prompt_eval_count": 10,
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await dispatcher.generate(req)

        assert result.text == "4"
        assert result.model == "qwen:7b"
        assert result.provider == "ollama"
        assert result.tokens_eval == 5
        assert result.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_generate_downshifts_on_failure(self) -> None:
        """Test automatic downshift when primary model fails."""
        dispatcher = OllamaDispatcher(fallback_model="phi3:mini")
        req = InferenceRequest(
            prompt="test",
            model="big-model:70b",
            metadata={"provider": "ollama"},
        )

        # First call fails, second (fallback) succeeds
        calls = []

        async def mock_generate(self_inner, req_inner):
            calls.append(req_inner.model)
            if req_inner.model == "big-model:70b":
                raise RuntimeError("Model too large for VRAM")
            return InferenceResult(
                text="fallback response",
                model=req_inner.model,
                provider="ollama",
                latency_ms=100,
            )

        with patch.object(
            OllamaDispatcher,
            "_ollama_generate",
            side_effect=lambda req: (
                (_ for _ in ()).throw(RuntimeError("VRAM"))
                if req.model == "big-model:70b"
                else InferenceResult(text="ok", model=req.model, provider="ollama", latency_ms=50)
            ),
        ):
            # The generate method's downshift should catch the error and retry
            # with fallback_model
            result = await dispatcher.generate(req)

        assert result.model == "phi3:mini"
        # Metadata should indicate downshift occurred
        assert "ok" in result.text


class TestInferenceRequest:
    """InferenceRequest data structure."""

    def test_defaults(self) -> None:
        req = InferenceRequest(prompt="test")
        assert req.model == ""
        assert req.temperature == 0.7
        assert req.max_tokens == 4096
        assert req.stream is False
        assert req.metadata == {}

    def test_custom_values(self) -> None:
        req = InferenceRequest(
            prompt="complex query",
            model="qwen-max",
            system="You are helpful.",
            temperature=0.2,
            max_tokens=8192,
            stream=True,
            metadata={"provider": "cloud"},
        )
        assert req.model == "qwen-max"
        assert req.stream is True
        assert req.metadata["provider"] == "cloud"
