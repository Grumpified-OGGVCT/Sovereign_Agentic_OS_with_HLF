"""
z.AI Client — OpenAI-SDK-compatible provider for the Sovereign OS.

Wraps the z.AI API (GLM-5, GLM-4.6V, CogView-4, GLM-OCR, etc.)
using the standard OpenAI Python SDK. Provides typed methods for:

  - Text completion (GLM-5, GLM-4.7)
  - Vision understanding (GLM-4.6V)
  - Image generation (CogView-4, GLM-Image)
  - Video generation (CogVideoX-3, Vidu2)
  - Document OCR (GLM-OCR)

All methods are OpenAI-compatible because z.AI mirrors the
`client.chat.completions.create()` interface exactly.

Security: API key is read from environment variable ZAI_API_KEY.
Never hardcode keys in source files.

Usage::

    from agents.core.zai_client import ZAIClient

    client = ZAIClient()
    result = client.complete("Explain quantum entanglement")
    vision = client.vision("Describe this screenshot", image_url="file://screenshot.png")
"""

from __future__ import annotations

import base64
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

ZAI_BASE_URL = "https://api.z.ai/api/paas/v4/"
ZAI_ENV_KEY = "ZAI_API_KEY"

# Model defaults by capability
ZAI_MODELS = {
    "reasoning": "glm-5",
    "general": "glm-4.7",
    "fast": "glm-4.7-flash",
    "vision": "glm-4.6v",
    "vision_fast": "glm-4.6v-flash",
    "image": "cogview-4-250304",
    "image_alt": "glm-image",
    "video": "cogvideox-3",
    "ocr": "glm-ocr",
    "asr": "glm-asr-2512",
    "high_concurrency": "glm-4-plus",
    "long_context": "glm-4-32b-0414-128k",
}

# Rate limits per model (max concurrent requests)
ZAI_RATE_LIMITS = {
    "glm-5": 5,
    "glm-4.7": 3,
    "glm-4.7-flash": 1,
    "glm-4.7-flashx": 3,
    "glm-4.6v": 10,
    "glm-4.6v-flash": 1,
    "glm-4.5": 10,
    "glm-4-plus": 20,
    "cogview-4-250304": 5,
    "glm-image": 1,
    "cogvideox-3": 1,
    "glm-ocr": 2,
}


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class ZAIResponse:
    """Standardized response from z.AI API calls."""

    success: bool
    content: str | None = None
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    thinking: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw: Any = None
    error: str | None = None
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "content": self.content,
            "model": self.model,
            "usage": self.usage,
            "thinking": self.thinking,
            "tool_calls": self.tool_calls,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


@dataclass
class ZAIImageResult:
    """Result from image generation."""

    success: bool
    url: str | None = None
    revised_prompt: str | None = None
    error: str | None = None


@dataclass
class ZAIVideoResult:
    """Result from video generation."""

    success: bool
    task_id: str | None = None
    video_url: str | None = None
    status: str = "pending"
    error: str | None = None


# ── Client ───────────────────────────────────────────────────────────────────


class ZAIClient:
    """OpenAI-SDK-compatible client for z.AI models.

    Reads API key from ZAI_API_KEY environment variable.
    All methods return typed dataclass responses.

    Attributes:
        default_model: Model used when none is specified.
        base_url: z.AI API endpoint.
    """

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "glm-4.7",
        base_url: str = ZAI_BASE_URL,
    ):
        self._api_key = api_key or os.environ.get(ZAI_ENV_KEY, "")
        self.default_model = default_model
        self.base_url = base_url
        self._client: Any = None  # Lazy-loaded OpenAI client

    @property
    def client(self) -> Any:
        """Lazy-load OpenAI client on first use."""
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise ImportError(
                    "openai package required for z.AI client. "
                    "Install with: pip install openai"
                )

            if not self._api_key:
                raise ValueError(
                    f"z.AI API key not found. Set {ZAI_ENV_KEY} environment variable."
                )

            self._client = OpenAI(
                api_key=self._api_key,
                base_url=self.base_url,
            )
        return self._client

    @property
    def is_configured(self) -> bool:
        """Check if the client has a valid API key."""
        return bool(self._api_key)

    # ── Text Completion ──────────────────────────────────────────────────

    def complete(
        self,
        prompt: str | list[dict[str, str]],
        *,
        model: str | None = None,
        system: str | None = None,
        thinking: bool = False,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> ZAIResponse:
        """Send a completion request to z.AI.

        Args:
            prompt: User message string or full messages list.
            model: Model ID (defaults to self.default_model).
            system: Optional system prompt.
            thinking: Enable thinking mode for reasoning chains.
            tools: OpenAI-compatible tool definitions.
            tool_choice: Tool selection strategy ("auto", "none", etc.).
            temperature: Sampling temperature.
            max_tokens: Max response tokens.
            stream: Whether to stream the response.

        Returns:
            ZAIResponse with content, usage, and optional thinking chain.
        """
        model = model or self.default_model
        messages = self._build_messages(prompt, system)

        extra_body: dict[str, Any] = {}
        if thinking:
            extra_body["thinking"] = {"type": "enabled"}

        call_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }

        if tools:
            call_kwargs["tools"] = tools
        if tool_choice:
            call_kwargs["tool_choice"] = tool_choice
        if temperature is not None:
            call_kwargs["temperature"] = temperature
        if max_tokens is not None:
            call_kwargs["max_tokens"] = max_tokens
        if extra_body:
            call_kwargs["extra_body"] = extra_body

        call_kwargs.update(kwargs)

        start = time.time()
        try:
            if stream:
                return self._stream_response(call_kwargs, model, start)

            response = self.client.chat.completions.create(**call_kwargs)
            duration = (time.time() - start) * 1000

            choice = response.choices[0] if response.choices else None
            content = choice.message.content if choice else None
            thinking_content = getattr(
                choice.message, "reasoning_content", None
            ) if choice else None
            tool_calls_raw = getattr(choice.message, "tool_calls", None) or []

            return ZAIResponse(
                success=True,
                content=content,
                model=response.model,
                usage={
                    "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),
                    "completion_tokens": getattr(response.usage, "completion_tokens", 0),
                    "total_tokens": getattr(response.usage, "total_tokens", 0),
                },
                thinking=thinking_content,
                tool_calls=[
                    {
                        "id": tc.id,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls_raw
                ],
                raw=response,
                duration_ms=round(duration, 2),
            )

        except Exception as e:
            duration = (time.time() - start) * 1000
            logger.error(f"z.AI completion failed ({model}): {e}")
            return ZAIResponse(
                success=False,
                error=str(e),
                model=model,
                duration_ms=round(duration, 2),
            )

    # ── Vision ───────────────────────────────────────────────────────────

    def vision(
        self,
        prompt: str,
        *,
        image_url: str | None = None,
        image_path: str | Path | None = None,
        model: str = "glm-4.6v",
        **kwargs: Any,
    ) -> ZAIResponse:
        """Send an image understanding request.

        Args:
            prompt: What to analyze about the image.
            image_url: URL of the image.
            image_path: Local path (will be base64-encoded).
            model: Vision model (default: glm-4.6v).

        Returns:
            ZAIResponse with visual analysis content.
        """
        content_parts: list[dict[str, Any]] = [
            {"type": "text", "text": prompt},
        ]

        if image_path:
            b64 = self._encode_image(Path(image_path))
            ext = Path(image_path).suffix.lstrip(".")
            mime = f"image/{ext}" if ext in ("png", "jpeg", "jpg", "gif", "webp") else "image/png"
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })
        elif image_url:
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": image_url},
            })
        else:
            return ZAIResponse(
                success=False,
                error="Either image_url or image_path must be provided",
                model=model,
            )

        messages = [{"role": "user", "content": content_parts}]

        return self.complete(messages, model=model, **kwargs)

    # ── Image Generation ─────────────────────────────────────────────────

    def generate_image(
        self,
        prompt: str,
        *,
        model: str = "cogview-4-250304",
        size: str = "1024x1024",
        **kwargs: Any,
    ) -> ZAIImageResult:
        """Generate an image from a text prompt.

        Args:
            prompt: Description of the image to generate.
            model: Image model (cogview-4-250304 or glm-image).
            size: Output size.

        Returns:
            ZAIImageResult with URL of generated image.
        """
        try:
            response = self.client.images.generate(
                model=model,
                prompt=prompt,
                size=size,
                **kwargs,
            )
            data = response.data[0] if response.data else None
            return ZAIImageResult(
                success=True,
                url=data.url if data else None,
                revised_prompt=getattr(data, "revised_prompt", None),
            )
        except Exception as e:
            logger.error(f"z.AI image generation failed ({model}): {e}")
            return ZAIImageResult(success=False, error=str(e))

    # ── Video Generation ─────────────────────────────────────────────────

    def generate_video(
        self,
        prompt: str,
        *,
        model: str = "cogvideox-3",
        **kwargs: Any,
    ) -> ZAIVideoResult:
        """Generate a video from a text prompt (async task).

        Args:
            prompt: Description of the video to generate.
            model: Video model.

        Returns:
            ZAIVideoResult with task_id for status polling.
        """
        try:
            # Video generation uses the completions API with special model
            response = self.client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                **kwargs,
            )
            return ZAIVideoResult(
                success=True,
                task_id=getattr(response, "id", None),
                status="submitted",
            )
        except Exception as e:
            logger.error(f"z.AI video generation failed ({model}): {e}")
            return ZAIVideoResult(success=False, error=str(e))

    # ── OCR ──────────────────────────────────────────────────────────────

    def ocr(
        self,
        image_path: str | Path,
        *,
        model: str = "glm-ocr",
    ) -> ZAIResponse:
        """Extract text from an image using GLM-OCR.

        Args:
            image_path: Path to the document/image.
            model: OCR model.

        Returns:
            ZAIResponse with extracted text content.
        """
        return self.vision(
            prompt="Extract all text from this document. Return the text exactly as written.",
            image_path=image_path,
            model=model,
        )

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _build_messages(
        prompt: str | list[dict[str, str]],
        system: str | None = None,
    ) -> list[dict[str, str]]:
        """Build messages list from prompt and optional system."""
        if isinstance(prompt, list):
            return prompt

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return messages

    @staticmethod
    def _encode_image(path: Path) -> str:
        """Base64-encode a local image file."""
        return base64.b64encode(path.read_bytes()).decode("utf-8")

    def _stream_response(
        self,
        call_kwargs: dict[str, Any],
        model: str,
        start: float,
    ) -> ZAIResponse:
        """Handle streaming response, collecting full content."""
        call_kwargs["stream"] = True
        chunks: list[str] = []
        thinking_chunks: list[str] = []

        try:
            stream = self.client.chat.completions.create(**call_kwargs)
            for chunk in stream:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        chunks.append(delta.content)
                    reasoning = getattr(delta, "reasoning_content", None)
                    if reasoning:
                        thinking_chunks.append(reasoning)

            duration = (time.time() - start) * 1000
            return ZAIResponse(
                success=True,
                content="".join(chunks),
                model=model,
                thinking="".join(thinking_chunks) if thinking_chunks else None,
                duration_ms=round(duration, 2),
            )
        except Exception as e:
            duration = (time.time() - start) * 1000
            return ZAIResponse(
                success=False,
                error=str(e),
                model=model,
                duration_ms=round(duration, 2),
            )

    # ── Info ──────────────────────────────────────────────────────────────

    @staticmethod
    def available_models() -> dict[str, str]:
        """Return the model catalog keyed by capability."""
        return dict(ZAI_MODELS)

    @staticmethod
    def rate_limit(model: str) -> int:
        """Return max concurrent requests for a model."""
        return ZAI_RATE_LIMITS.get(model, 1)
