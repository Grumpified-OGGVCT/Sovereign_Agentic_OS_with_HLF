"""
External App Tool Registration — MSTY Studio + AnythingLLM → Native Layer.

Registers external AI application integrations into the ToolRegistry,
making them discoverable by agents and governed by:
  - Feature flags (config/settings.json → native.features.msty_studio / .anythingllm)
  - Health checks via port connectivity
  - Rate limiting (shared token bucket)
  - Structured errors (NativeBridgeError hierarchy)
  - ALSLogger audit trail
  - Gas metering per operation

Architecture note:
  This module follows the same registration pattern as ai_tools.py and
  cli_tools.py. The actual execution logic lives in host_function_dispatcher.py
  (_msty_bridge / _anythingllm_api backends). This module wraps those backends
  with native-layer governance and makes them ToolRegistry-discoverable.

Usage:
    from agents.core.native.external_app_tools import register_external_app_tools
    register_external_app_tools(tool_registry)
"""

from __future__ import annotations

import json
import os
import socket
from typing import Any

from agents.core.logger import ALSLogger

_logger = ALSLogger(agent_role="native-external-apps", goal_id="registration")


def _check_port(host: str, port: int, timeout: float = 1.0) -> bool:
    """Quick port check for external app health detection."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (ConnectionRefusedError, TimeoutError, OSError):
        return False


def _parse_host_port(env_key: str, default: str) -> tuple[str, int]:
    """Parse host:port from env var or default URL."""
    from urllib.parse import urlparse
    url = os.environ.get(env_key, default)
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, port


def register_external_app_tools(registry: Any) -> None:
    """Register MSTY Studio + AnythingLLM tools into the ToolRegistry.

    Respects feature flags — disabled apps are not registered.
    Each tool wraps the corresponding host_function_dispatcher backend
    with native-layer governance.
    """
    from agents.core.native import _load_native_config
    from agents.core.native.bridge import NativeBridgeError, TokenBucketRateLimiter

    config = _load_native_config()
    features = config.get("features", {})
    registered = 0

    # Shared rate limiter for external app API calls
    _ext_app_limiter = TokenBucketRateLimiter(max_tokens=30, refill_seconds=60.0)

    # ── MSTY Studio Tools ────────────────────────────────────────────────

    if features.get("msty_studio", True):
        msty_host, msty_port = _parse_host_port("MSTY_HOST", "http://localhost:11434")

        def _msty_health() -> dict:
            """Check MSTY Studio connectivity."""
            reachable = _check_port(msty_host, msty_port)
            return {
                "status": "healthy" if reachable else "unavailable",
                "host": f"{msty_host}:{msty_port}",
                "reachable": reachable,
            }

        def _msty_rate_check() -> None:
            if not _ext_app_limiter.try_consume():
                raise NativeBridgeError(
                    "MSTY rate limit exceeded (30/min)",
                    subsystem="msty_studio",
                    recoverable=True,
                )

        # ── native.msty.health ───────────────────────────────────────────
        def _handle_msty_health(params: dict) -> dict:
            return {"status": "ok", "data": _msty_health()}

        registry.register(
            name="native.msty.health",
            handler=_handle_msty_health,
            schema={
                "description": "Check MSTY Studio connectivity and health",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            gas_cost=0,
            permissions=["hearth", "forge", "sovereign"],
            tags=["native", "msty", "health", "external-app"],
        )
        registered += 1

        # ── native.msty.list_models ──────────────────────────────────────
        def _handle_msty_list(params: dict) -> dict:
            from agents.core.host_function_dispatcher import dispatch
            _msty_rate_check()
            result = dispatch("MSTY_LIST_MODELS", [], tier="hearth")
            return {"status": "ok", "data": json.loads(result) if isinstance(result, str) else result}

        registry.register(
            name="native.msty.list_models",
            handler=_handle_msty_list,
            schema={
                "description": "List all models available via MSTY Studio (local + cloud proxied)",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            gas_cost=1,
            permissions=["hearth", "forge", "sovereign"],
            tags=["native", "msty", "models", "external-app"],
        )
        registered += 1

        # ── native.msty.vibe_catalog ─────────────────────────────────────
        def _handle_msty_catalog(params: dict) -> dict:
            from agents.core.host_function_dispatcher import dispatch
            _msty_rate_check()
            force = params.get("force_refresh", False)
            result = dispatch("MSTY_VIBE_CATALOG", [force], tier="hearth")
            return {"status": "ok", "data": json.loads(result) if isinstance(result, str) else result}

        registry.register(
            name="native.msty.vibe_catalog",
            handler=_handle_msty_catalog,
            schema={
                "description": "Get dynamic model catalog with provider fingerprinting (local/cloud/image classification)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "force_refresh": {"type": "boolean", "description": "Bypass TTL cache (default: false)"},
                    },
                    "required": [],
                },
            },
            gas_cost=2,
            permissions=["hearth", "forge", "sovereign"],
            tags=["native", "msty", "catalog", "vibe-cli", "external-app"],
        )
        registered += 1

        # ── native.msty.knowledge_query ──────────────────────────────────
        def _handle_msty_knowledge(params: dict) -> dict:
            from agents.core.host_function_dispatcher import dispatch
            _msty_rate_check()
            stack = params.get("stack", "")
            question = params.get("question", "")
            result = dispatch("MSTY_KNOWLEDGE_QUERY", [stack, question], tier="forge")
            return {"status": "ok", "data": {"response": result}}

        registry.register(
            name="native.msty.knowledge_query",
            handler=_handle_msty_knowledge,
            schema={
                "description": "Query an MSTY Knowledge Stack",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "stack": {"type": "string", "description": "Knowledge Stack name"},
                        "question": {"type": "string", "description": "Question to ask"},
                    },
                    "required": ["stack", "question"],
                },
            },
            gas_cost=5,
            permissions=["forge", "sovereign"],
            tags=["native", "msty", "knowledge", "rag", "external-app"],
        )
        registered += 1

        # ── native.msty.persona_run ──────────────────────────────────────
        def _handle_msty_persona(params: dict) -> dict:
            from agents.core.host_function_dispatcher import dispatch
            _msty_rate_check()
            persona = params.get("persona", "")
            prompt = params.get("prompt", "")
            result = dispatch("MSTY_PERSONA_RUN", [persona, prompt], tier="forge")
            return {"status": "ok", "data": {"response": result}}

        registry.register(
            name="native.msty.persona_run",
            handler=_handle_msty_persona,
            schema={
                "description": "Run a prompt through an MSTY Persona (character-based system prompt)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "persona": {"type": "string", "description": "Persona name"},
                        "prompt": {"type": "string", "description": "Prompt to send"},
                    },
                    "required": ["persona", "prompt"],
                },
            },
            gas_cost=5,
            permissions=["forge", "sovereign"],
            tags=["native", "msty", "persona", "external-app"],
        )
        registered += 1

        # ── native.msty.split_chat ───────────────────────────────────────
        def _handle_msty_split(params: dict) -> dict:
            from agents.core.host_function_dispatcher import dispatch
            _msty_rate_check()
            models = params.get("models", [])
            prompt = params.get("prompt", "")
            result = dispatch("MSTY_SPLIT_CHAT", [models, prompt], tier="sovereign")
            return {"status": "ok", "data": json.loads(result) if isinstance(result, str) else result}

        registry.register(
            name="native.msty.split_chat",
            handler=_handle_msty_split,
            schema={
                "description": "Fan out a prompt to multiple models simultaneously (Split Chat)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "models": {"type": "array", "items": {"type": "string"},
                                   "description": "List of model names to query"},
                        "prompt": {"type": "string", "description": "Prompt to send to all models"},
                    },
                    "required": ["models", "prompt"],
                },
            },
            gas_cost=8,
            permissions=["sovereign"],
            tags=["native", "msty", "split-chat", "multi-model", "external-app"],
        )
        registered += 1

        _logger.log("MSTY_TOOLS_REGISTERED", {
            "count": registered,
            "host": f"{msty_host}:{msty_port}",
            "reachable": _check_port(msty_host, msty_port, timeout=0.5),
        })

    # ── AnythingLLM Tools ────────────────────────────────────────────────

    allm_registered = 0
    if features.get("anythingllm", True):
        allm_host, allm_port = _parse_host_port("ANYTHINGLLM_HOST", "http://localhost:3001")

        def _allm_health() -> dict:
            """Check AnythingLLM connectivity."""
            reachable = _check_port(allm_host, allm_port)
            has_key = bool(os.environ.get("ANYTHINGLLM_API_KEY"))
            return {
                "status": "healthy" if (reachable and has_key) else "degraded" if has_key else "unconfigured",
                "host": f"{allm_host}:{allm_port}",
                "reachable": reachable,
                "api_key_set": has_key,
            }

        def _allm_rate_check() -> None:
            if not _ext_app_limiter.try_consume():
                raise NativeBridgeError(
                    "AnythingLLM rate limit exceeded (30/min)",
                    subsystem="anythingllm",
                    recoverable=True,
                )

        # ── native.allm.health ───────────────────────────────────────────
        def _handle_allm_health(params: dict) -> dict:
            return {"status": "ok", "data": _allm_health()}

        registry.register(
            name="native.allm.health",
            handler=_handle_allm_health,
            schema={
                "description": "Check AnythingLLM connectivity, API key status, and health",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            gas_cost=0,
            permissions=["hearth", "forge", "sovereign"],
            tags=["native", "anythingllm", "health", "external-app"],
        )
        allm_registered += 1

        # ── native.allm.list_workspaces ──────────────────────────────────
        def _handle_allm_list_ws(params: dict) -> dict:
            from agents.core.host_function_dispatcher import dispatch
            _allm_rate_check()
            result = dispatch("ALLM_LIST_WORKSPACES", [], tier="hearth")
            return {"status": "ok", "data": json.loads(result) if isinstance(result, str) else result}

        registry.register(
            name="native.allm.list_workspaces",
            handler=_handle_allm_list_ws,
            schema={
                "description": "List all AnythingLLM workspaces",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            gas_cost=1,
            permissions=["hearth", "forge", "sovereign"],
            tags=["native", "anythingllm", "workspaces", "external-app"],
        )
        allm_registered += 1

        # ── native.allm.workspace_chat ───────────────────────────────────
        def _handle_allm_chat(params: dict) -> dict:
            from agents.core.host_function_dispatcher import dispatch
            _allm_rate_check()
            workspace = params.get("workspace", "")
            message = params.get("message", "")
            result = dispatch("ALLM_WORKSPACE_CHAT", [workspace, message], tier="forge")
            return {"status": "ok", "data": {"response": result}}

        registry.register(
            name="native.allm.workspace_chat",
            handler=_handle_allm_chat,
            schema={
                "description": "Send a message to an AnythingLLM workspace and get a response",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "workspace": {"type": "string", "description": "Workspace slug name"},
                        "message": {"type": "string", "description": "Message to send"},
                    },
                    "required": ["workspace", "message"],
                },
            },
            gas_cost=5,
            permissions=["forge", "sovereign"],
            tags=["native", "anythingllm", "chat", "rag", "external-app"],
        )
        allm_registered += 1

        # ── native.allm.add_document ─────────────────────────────────────
        def _handle_allm_add_doc(params: dict) -> dict:
            from agents.core.host_function_dispatcher import dispatch
            _allm_rate_check()
            workspace = params.get("workspace", "")
            title = params.get("title", "")
            content = params.get("content", "")
            result = dispatch("ALLM_ADD_DOCUMENT", [workspace, title, content], tier="forge")
            return {"status": "ok", "data": {"success": result}}

        registry.register(
            name="native.allm.add_document",
            handler=_handle_allm_add_doc,
            schema={
                "description": "Add a document to an AnythingLLM workspace for RAG indexing",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "workspace": {"type": "string", "description": "Workspace slug name"},
                        "title": {"type": "string", "description": "Document title"},
                        "content": {"type": "string", "description": "Document content"},
                    },
                    "required": ["workspace", "title", "content"],
                },
            },
            gas_cost=3,
            permissions=["forge", "sovereign"],
            tags=["native", "anythingllm", "documents", "rag", "external-app"],
        )
        allm_registered += 1

        _logger.log("ALLM_TOOLS_REGISTERED", {
            "count": allm_registered,
            "host": f"{allm_host}:{allm_port}",
            "reachable": _check_port(allm_host, allm_port, timeout=0.5),
            "api_key_set": bool(os.environ.get("ANYTHINGLLM_API_KEY")),
        })

    total = registered + allm_registered
    _logger.log("EXTERNAL_APP_TOOLS_TOTAL", {
        "total": total,
        "msty_count": registered,
        "allm_count": allm_registered,
        "features": {
            "msty_studio": features.get("msty_studio", True),
            "anythingllm": features.get("anythingllm", True),
        },
    })
