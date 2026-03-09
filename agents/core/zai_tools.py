"""
z.AI Tool Registration — Registers z.AI capabilities in the ToolRegistry.

Provides a factory function `register_zai_tools()` that adds z.AI-backed
tools to the Sovereign OS ToolRegistry, making them available to agents
during DAG execution and to users via HLF τ() functions.

Registered tools:
  - zai.complete      — LLM text completion (GLM-5, GLM-4.7)
  - zai.vision        — Image/screenshot understanding (GLM-4.6V)
  - zai.image_gen     — Image generation (CogView-4, GLM-Image)
  - zai.video_gen     — Video generation (CogVideoX-3, Vidu2)
  - zai.video_status  — Poll async video generation task status
  - zai.ocr           — Document text extraction (GLM-OCR)

Usage::

    from agents.core.tool_registry import ToolRegistry
    from agents.core.zai_tools import register_zai_tools

    registry = ToolRegistry()
    count = register_zai_tools(registry)
    print(f"Registered {count} z.AI tools")
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agents.core.tool_registry import (
    ToolCategory,
    ToolDefinition,
    ToolPermission,
    ToolRegistry,
    ToolResult,
)
from agents.core.zai_client import ZAIClient, ZAI_MODELS

logger = logging.getLogger(__name__)

# Shared client instance (lazy — not created until a tool is actually called)
_zai_client: ZAIClient | None = None


def _get_client() -> ZAIClient:
    """Get or create the shared z.AI client."""
    global _zai_client
    if _zai_client is None:
        _zai_client = ZAIClient()
    return _zai_client


# ── Tool Handlers ────────────────────────────────────────────────────────────


def _handle_complete(
    prompt: str,
    model: str | None = None,
    system: str | None = None,
    thinking: bool = False,
    **kwargs: Any,
) -> ToolResult:
    """Handler for zai.complete tool."""
    client = _get_client()
    response = client.complete(
        prompt, model=model, system=system, thinking=thinking, **kwargs
    )
    if response.success:
        return ToolResult(
            success=True,
            output=response.content,
            tool_id="zai.complete",
            metadata={
                "model": response.model,
                "usage": response.usage,
                "thinking": response.thinking,
                "duration_ms": response.duration_ms,
            },
        )
    return ToolResult(
        success=False,
        error=response.error,
        tool_id="zai.complete",
    )


def _handle_vision(
    prompt: str,
    image_path: str | None = None,
    image_url: str | None = None,
    model: str = "glm-4.6v",
    **kwargs: Any,
) -> ToolResult:
    """Handler for zai.vision tool."""
    client = _get_client()
    response = client.vision(
        prompt,
        image_path=image_path,
        image_url=image_url,
        model=model,
        **kwargs,
    )
    if response.success:
        return ToolResult(
            success=True,
            output=response.content,
            tool_id="zai.vision",
            metadata={
                "model": response.model,
                "duration_ms": response.duration_ms,
            },
        )
    return ToolResult(
        success=False,
        error=response.error,
        tool_id="zai.vision",
    )


def _handle_image_gen(
    prompt: str,
    model: str = "cogview-4-250304",
    size: str = "1024x1024",
    **kwargs: Any,
) -> ToolResult:
    """Handler for zai.image_gen tool."""
    client = _get_client()
    result = client.generate_image(prompt, model=model, size=size, **kwargs)
    if result.success:
        return ToolResult(
            success=True,
            output=result.url,
            tool_id="zai.image_gen",
            metadata={"revised_prompt": result.revised_prompt},
        )
    return ToolResult(
        success=False,
        error=result.error,
        tool_id="zai.image_gen",
    )


def _handle_video_gen(
    prompt: str,
    model: str = "cogvideox-3",
    **kwargs: Any,
) -> ToolResult:
    """Handler for zai.video_gen tool."""
    client = _get_client()
    result = client.generate_video(prompt, model=model, **kwargs)
    if result.success:
        return ToolResult(
            success=True,
            output={"task_id": result.task_id, "status": result.status},
            tool_id="zai.video_gen",
        )
    return ToolResult(
        success=False,
        error=result.error,
        tool_id="zai.video_gen",
    )


def _handle_video_status(
    task_id: str,
    **kwargs: Any,
) -> ToolResult:
    """Handler for zai.video_status tool."""
    client = _get_client()
    result = client.poll_video_status(task_id)
    return ToolResult(
        success=result.success,
        output={
            "task_id": result.task_id,
            "status": result.status,
            "video_url": result.video_url,
        },
        error=result.error,
        tool_id="zai.video_status",
    )


def _handle_ocr(
    image_path: str,
    model: str = "glm-ocr",
) -> ToolResult:
    """Handler for zai.ocr tool."""
    client = _get_client()
    response = client.ocr(image_path, model=model)
    if response.success:
        return ToolResult(
            success=True,
            output=response.content,
            tool_id="zai.ocr",
            metadata={"model": response.model, "duration_ms": response.duration_ms},
        )
    return ToolResult(
        success=False,
        error=response.error,
        tool_id="zai.ocr",
    )


# ── Registration ─────────────────────────────────────────────────────────────

# Tool definitions: (tool_id, category, description, handler, gas, permissions)
_ZAI_TOOL_DEFS: list[tuple[str, ToolCategory, str, Any, int, set[ToolPermission]]] = [
    (
        "zai.complete",
        ToolCategory.ANALYSIS,
        "LLM text completion via z.AI (GLM-5, GLM-4.7). "
        "Supports thinking mode, function calling, and streaming.",
        _handle_complete,
        5,
        {ToolPermission.EXECUTE},
    ),
    (
        "zai.vision",
        ToolCategory.ANALYSIS,
        "Image/screenshot understanding via z.AI GLM-4.6V. "
        "Analyzes UI screenshots, diagrams, documents, and photos.",
        _handle_vision,
        5,
        {ToolPermission.EXECUTE},
    ),
    (
        "zai.image_gen",
        ToolCategory.HTTP,
        "Image generation via z.AI CogView-4 or GLM-Image. "
        "Creates images from text prompts.",
        _handle_image_gen,
        8,
        {ToolPermission.EXECUTE},
    ),
    (
        "zai.video_gen",
        ToolCategory.HTTP,
        "Video generation via z.AI CogVideoX-3 or Vidu2. "
        "Creates short videos from text or image prompts.",
        _handle_video_gen,
        15,
        {ToolPermission.EXECUTE},
    ),
    (
        "zai.video_status",
        ToolCategory.HTTP,
        "Poll the status of an async z.AI video generation task. "
        "Returns PROCESSING, SUCCESS (with video_url), or FAIL.",
        _handle_video_status,
        2,
        {ToolPermission.EXECUTE},
    ),
    (
        "zai.ocr",
        ToolCategory.ANALYSIS,
        "Document OCR via z.AI GLM-OCR. "
        "Extracts text from images, PDFs, and scanned documents.",
        _handle_ocr,
        3,
        {ToolPermission.READ, ToolPermission.EXECUTE},
    ),
]


def register_zai_tools(registry: ToolRegistry) -> int:
    """Register all z.AI tools into the ToolRegistry.

    Args:
        registry: The ToolRegistry to add tools to.

    Returns:
        Number of tools registered.
    """
    count = 0
    for tool_id, category, description, handler, gas, permissions in _ZAI_TOOL_DEFS:
        # ToolDefinition uses execute_fn, required_permission (singular), timeout
        # Pick the highest permission level from the set for the single field
        perm = ToolPermission.EXECUTE if ToolPermission.EXECUTE in permissions else ToolPermission.READ
        tool = ToolDefinition(
            tool_id=tool_id,
            category=category,
            description=description,
            execute_fn=handler,
            required_permission=perm,
            input_schema=_get_input_schema(tool_id),
            timeout=120.0 if "video" in tool_id else 60.0,
        )
        registry.register(tool)
        count += 1
        logger.info(f"Registered z.AI tool: {tool_id} (gas={gas})")

    return count


def list_zai_models() -> dict[str, str]:
    """Return the full z.AI model catalog keyed by capability."""
    return dict(ZAI_MODELS)


def _get_input_schema(tool_id: str) -> dict[str, Any]:
    """Return input schema for a z.AI tool."""
    schemas: dict[str, dict[str, Any]] = {
        "zai.complete": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "User message or full messages list"},
                "model": {"type": "string", "description": "Model ID (default: glm-4.7)"},
                "system": {"type": "string", "description": "Optional system prompt"},
                "thinking": {"type": "boolean", "description": "Enable reasoning chain"},
            },
            "required": ["prompt"],
        },
        "zai.vision": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "What to analyze"},
                "image_path": {"type": "string", "description": "Local path to image"},
                "image_url": {"type": "string", "description": "URL of image"},
                "model": {"type": "string", "description": "Vision model (default: glm-4.6v)"},
            },
            "required": ["prompt"],
        },
        "zai.image_gen": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Image description"},
                "model": {"type": "string", "description": "Image model"},
                "size": {"type": "string", "description": "Output size (default: 1024x1024)"},
            },
            "required": ["prompt"],
        },
        "zai.video_gen": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Video description"},
                "model": {"type": "string", "description": "Video model"},
            },
            "required": ["prompt"],
        },
        "zai.video_status": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID from video_gen"},
            },
            "required": ["task_id"],
        },
        "zai.ocr": {
            "type": "object",
            "properties": {
                "image_path": {"type": "string", "description": "Path to document/image"},
                "model": {"type": "string", "description": "OCR model (default: glm-ocr)"},
            },
            "required": ["image_path"],
        },
    }
    return schemas.get(tool_id, {})
