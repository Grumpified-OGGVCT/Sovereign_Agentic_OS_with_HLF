"""
Tests for z.AI Client and Tool Registration.

Covers:
  - ZAIClient construction and configuration
  - ZAIResponse and result dataclasses
  - ZAI tool registration in ToolRegistry
  - Input schemas and permissions
  - Model catalog and rate limits
  - Error handling

All tests are mock-based — no real API calls.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agents.core.zai_client import (
    ZAIClient,
    ZAIResponse,
    ZAIImageResult,
    ZAIVideoResult,
    ZAI_BASE_URL,
    ZAI_ENV_KEY,
    ZAI_MODELS,
    ZAI_RATE_LIMITS,
)
from agents.core.tool_registry import ToolCategory, ToolRegistry
from agents.core.zai_tools import register_zai_tools, list_zai_models


# ── ZAIClient Tests ──────────────────────────────────────────────────────────


class TestZAIClientConstruction:
    """Tests for client initialization and configuration."""

    def test_default_model(self) -> None:
        client = ZAIClient(api_key="test-key")
        assert client.default_model == "glm-4.7"

    def test_custom_model(self) -> None:
        client = ZAIClient(api_key="test-key", default_model="glm-5")
        assert client.default_model == "glm-5"

    def test_base_url_default(self) -> None:
        client = ZAIClient(api_key="test-key")
        assert client.base_url == ZAI_BASE_URL

    def test_custom_base_url(self) -> None:
        client = ZAIClient(api_key="test-key", base_url="https://custom.api/v1/")
        assert client.base_url == "https://custom.api/v1/"

    def test_api_key_from_env(self) -> None:
        with patch.dict(os.environ, {ZAI_ENV_KEY: "env-key-123"}):
            client = ZAIClient()
            assert client._api_key == "env-key-123"

    def test_explicit_key_overrides_env(self) -> None:
        with patch.dict(os.environ, {ZAI_ENV_KEY: "env-key"}):
            client = ZAIClient(api_key="explicit-key")
            assert client._api_key == "explicit-key"

    def test_is_configured_true(self) -> None:
        client = ZAIClient(api_key="test-key")
        assert client.is_configured is True

    def test_is_configured_false(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            client = ZAIClient()
            assert client.is_configured is False

    def test_lazy_client_not_created_on_init(self) -> None:
        client = ZAIClient(api_key="test-key")
        assert client._client is None


# ── ZAIResponse Tests ────────────────────────────────────────────────────────


class TestZAIResponse:
    """Tests for response dataclasses."""

    def test_success_response(self) -> None:
        resp = ZAIResponse(
            success=True,
            content="Hello world",
            model="glm-4.7",
            usage={"total_tokens": 10},
        )
        assert resp.success
        assert resp.content == "Hello world"
        assert resp.error is None

    def test_error_response(self) -> None:
        resp = ZAIResponse(success=False, error="API timeout", model="glm-5")
        assert not resp.success
        assert resp.error == "API timeout"

    def test_to_dict(self) -> None:
        resp = ZAIResponse(
            success=True,
            content="test",
            model="glm-4.7",
            duration_ms=123.4,
        )
        d = resp.to_dict()
        assert d["success"] is True
        assert d["content"] == "test"
        assert d["model"] == "glm-4.7"
        assert d["duration_ms"] == 123.4

    def test_thinking_field(self) -> None:
        resp = ZAIResponse(
            success=True,
            content="Answer",
            thinking="Let me think about this...",
            model="glm-5",
        )
        assert resp.thinking == "Let me think about this..."

    def test_image_result(self) -> None:
        result = ZAIImageResult(
            success=True,
            url="https://example.com/image.png",
            revised_prompt="A beautiful sunset",
        )
        assert result.success
        assert result.url is not None

    def test_video_result(self) -> None:
        result = ZAIVideoResult(
            success=True,
            task_id="task-123",
            status="submitted",
        )
        assert result.success
        assert result.task_id == "task-123"


# ── Message Building Tests ───────────────────────────────────────────────────


class TestMessageBuilding:
    """Tests for internal message construction."""

    def test_string_prompt(self) -> None:
        messages = ZAIClient._build_messages("Hello")
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello"

    def test_string_prompt_with_system(self) -> None:
        messages = ZAIClient._build_messages("Hello", system="You are helpful")
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    def test_list_prompt_passthrough(self) -> None:
        custom = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "msg"},
        ]
        result = ZAIClient._build_messages(custom)
        assert result is custom  # Passed through unchanged


# ── Model Catalog Tests ──────────────────────────────────────────────────────


class TestModelCatalog:
    """Tests for model information."""

    def test_available_models(self) -> None:
        models = ZAIClient.available_models()
        assert "reasoning" in models
        assert models["reasoning"] == "glm-5"
        assert "vision" in models
        assert models["vision"] == "glm-4.6v"
        assert "ocr" in models

    def test_rate_limits(self) -> None:
        assert ZAIClient.rate_limit("glm-5") == 5
        assert ZAIClient.rate_limit("glm-4.6v") == 10
        assert ZAIClient.rate_limit("unknown-model") == 1  # Default

    def test_list_zai_models(self) -> None:
        models = list_zai_models()
        assert len(models) >= 10  # We have 12+ models
        assert "image" in models


# ── Tool Registration Tests ──────────────────────────────────────────────────


class TestZAIToolRegistration:
    """Tests for z.AI tool registration in ToolRegistry."""

    def test_register_all_tools(self) -> None:
        registry = ToolRegistry()
        count = register_zai_tools(registry)
        assert count == 5

    def test_registered_tool_ids(self) -> None:
        registry = ToolRegistry()
        register_zai_tools(registry)
        ids = registry.list_tool_ids()
        assert "zai.complete" in ids
        assert "zai.vision" in ids
        assert "zai.image_gen" in ids
        assert "zai.video_gen" in ids
        assert "zai.ocr" in ids

    def test_tool_categories(self) -> None:
        registry = ToolRegistry()
        register_zai_tools(registry)

        complete = registry.get("zai.complete")
        assert complete is not None
        assert complete.category == ToolCategory.ANALYSIS

        image = registry.get("zai.image_gen")
        assert image is not None
        assert image.category == ToolCategory.HTTP

    def test_tool_has_description(self) -> None:
        registry = ToolRegistry()
        register_zai_tools(registry)
        tool = registry.get("zai.vision")
        assert tool is not None
        assert "GLM-4.6V" in tool.description

    def test_tool_has_input_schema(self) -> None:
        registry = ToolRegistry()
        register_zai_tools(registry)
        tool = registry.get("zai.complete")
        assert tool is not None
        schema = tool.input_schema
        assert "properties" in schema
        assert "prompt" in schema["properties"]

    def test_ocr_tool_schema(self) -> None:
        registry = ToolRegistry()
        register_zai_tools(registry)
        tool = registry.get("zai.ocr")
        assert tool is not None
        assert "image_path" in tool.input_schema.get("required", [])

    def test_video_tool_timeout(self) -> None:
        registry = ToolRegistry()
        register_zai_tools(registry)
        tool = registry.get("zai.video_gen")
        assert tool is not None
        assert tool.timeout == 120.0

    def test_complete_tool_timeout(self) -> None:
        registry = ToolRegistry()
        register_zai_tools(registry)
        tool = registry.get("zai.complete")
        assert tool is not None
        assert tool.timeout == 60.0
