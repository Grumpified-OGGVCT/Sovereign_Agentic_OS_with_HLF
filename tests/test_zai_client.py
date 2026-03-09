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

    def test_video_result_with_url(self) -> None:
        result = ZAIVideoResult(
            success=True,
            task_id="task-456",
            video_url="https://cdn.z.ai/video/456.mp4",
            status="SUCCESS",
        )
        assert result.video_url is not None
        assert result.status == "SUCCESS"


# ── Video Polling Tests ──────────────────────────────────────────────────────


class TestVideoPolling:
    """Tests for video generation status polling."""

    def test_poll_success(self) -> None:
        client = ZAIClient(api_key="test-key")
        response_data = {
            "task_status": "SUCCESS",
            "video_result": [{"url": "https://cdn.z.ai/vid.mp4"}],
        }
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = __import__("json").dumps(response_data).encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            result = client.poll_video_status("task-abc")

        assert result.success
        assert result.status == "SUCCESS"
        assert result.video_url == "https://cdn.z.ai/vid.mp4"
        assert result.task_id == "task-abc"

    def test_poll_processing(self) -> None:
        client = ZAIClient(api_key="test-key")
        response_data = {"task_status": "PROCESSING"}
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = __import__("json").dumps(response_data).encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            result = client.poll_video_status("task-xyz")

        assert result.success  # PROCESSING is not a failure
        assert result.status == "PROCESSING"
        assert result.video_url is None

    def test_poll_fail(self) -> None:
        client = ZAIClient(api_key="test-key")
        response_data = {"task_status": "FAIL", "message": "Content policy violation"}
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = __import__("json").dumps(response_data).encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            result = client.poll_video_status("task-fail")

        assert not result.success
        assert result.status == "FAIL"
        assert "Content policy" in result.error

    def test_poll_http_error(self) -> None:
        import urllib.error
        client = ZAIClient(api_key="test-key")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                url="", code=404, msg="Not Found", hdrs=None, fp=None  # type: ignore[arg-type]
            )
            result = client.poll_video_status("bad-id")

        assert not result.success
        assert result.status == "POLL_ERROR"
        assert "404" in result.error

    def test_poll_constructs_correct_url(self) -> None:
        client = ZAIClient(api_key="test-key", base_url="https://api.z.ai/api/paas/v4/")
        response_data = {"task_status": "PROCESSING"}
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = __import__("json").dumps(response_data).encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            client.poll_video_status("task-123")

        # Verify the URL was constructed correctly
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        assert "async-result/task-123" in req.full_url

    def test_get_video_result_timeout(self) -> None:
        client = ZAIClient(api_key="test-key")
        # Mock poll_video_status to always return PROCESSING
        with patch.object(client, "poll_video_status") as mock_poll:
            mock_poll.return_value = ZAIVideoResult(
                success=True, task_id="t-1", status="PROCESSING"
            )
            result = client.get_video_result("t-1", max_attempts=3, interval=0.01)

        assert not result.success
        assert result.status == "TIMEOUT"
        assert mock_poll.call_count == 3

    def test_get_video_result_success_on_second_poll(self) -> None:
        client = ZAIClient(api_key="test-key")
        with patch.object(client, "poll_video_status") as mock_poll:
            mock_poll.side_effect = [
                ZAIVideoResult(success=True, task_id="t-2", status="PROCESSING"),
                ZAIVideoResult(
                    success=True, task_id="t-2",
                    status="SUCCESS", video_url="https://cdn.z.ai/t2.mp4",
                ),
            ]
            result = client.get_video_result("t-2", max_attempts=5, interval=0.01)

        assert result.success
        assert result.status == "SUCCESS"
        assert result.video_url == "https://cdn.z.ai/t2.mp4"
        assert mock_poll.call_count == 2


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
        assert count == 6

    def test_registered_tool_ids(self) -> None:
        registry = ToolRegistry()
        register_zai_tools(registry)
        ids = registry.list_tool_ids()
        assert "zai.complete" in ids
        assert "zai.vision" in ids
        assert "zai.image_gen" in ids
        assert "zai.video_gen" in ids
        assert "zai.video_status" in ids
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
