"""
Agent executor entrypoint.
Initialises OpenLLMetry TracerProvider, registers SIGUSR1 handler,
connects to Dapr pub/sub, and processes Redis Stream intents.

For each received intent the executor:
  1. If the payload already contains a compiled AST (from the ASB HLF path),
     execute it directly via hlf.hlfrun.
  2. If the payload is text-mode (raw human language), call the local Ollama
     instance to generate an HLF response, compile it, and then execute.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import time
from pathlib import Path
from typing import Any

import httpx

from agents.core.logger import ALSLogger

_logger = ALSLogger(agent_role="agent-executor", goal_id="boot")


def _try_route_request():
    """Lazy import of route_request to avoid circular imports."""
    try:
        from agents.gateway.router import AgentProfile, route_request

        return route_request, AgentProfile
    except ImportError:
        return None, None


_SYSTEM_PROMPT_PATH = Path(__file__).parent.parent.parent / "governance" / "templates" / "system_prompt.txt"


def quarantine_dump() -> None:
    """Dump active memory snapshot to quarantine_dumps on SIGUSR1."""
    dump_dir = Path(os.environ.get("BASE_DIR", "/app")) / "data" / "quarantine_dumps"
    dump_dir.mkdir(parents=True, exist_ok=True)
    dump_file = dump_dir / f"quarantine_{int(time.time())}.json"
    dump_file.write_text(json.dumps({"event": "SIGUSR1_DUMP", "ts": time.time()}))
    _logger.log("QUARANTINE_DUMP", {"path": str(dump_file)})


def _handle_sigusr1(signum: int, frame: object) -> None:
    quarantine_dump()


if hasattr(signal, "SIGUSR1"):
    signal.signal(signal.SIGUSR1, _handle_sigusr1)


# --------------------------------------------------------------------------- #
# Ollama dual-endpoint helpers
# --------------------------------------------------------------------------- #

_OLLAMA_PRIMARY = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
_OLLAMA_SECONDARY = os.environ.get("OLLAMA_HOST_SECONDARY", "")
_OLLAMA_SECONDARY_KEY = os.environ.get("OLLAMA_API_KEY_SECONDARY", "")
_OLLAMA_STRATEGY = os.environ.get("OLLAMA_LOAD_STRATEGY", "failover")
_ollama_rr_counter = 0  # round-robin counter


def _get_ollama_endpoints() -> list[tuple[str, dict[str, str]]]:
    """Return ordered list of (host, headers) tuples based on load strategy."""
    global _ollama_rr_counter
    primary = (_OLLAMA_PRIMARY, {})
    if not _OLLAMA_SECONDARY:
        return [primary]

    sec_headers = {}
    if _OLLAMA_SECONDARY_KEY:
        sec_headers["Authorization"] = f"Bearer {_OLLAMA_SECONDARY_KEY}"
    secondary = (_OLLAMA_SECONDARY, sec_headers)

    if _OLLAMA_STRATEGY == "round_robin":
        _ollama_rr_counter += 1
        if _ollama_rr_counter % 2 == 0:
            return [primary, secondary]
        return [secondary, primary]
    elif _OLLAMA_STRATEGY == "primary_only":
        return [primary]
    # default: failover (primary first, secondary if primary fails)
    return [primary, secondary]


# --------------------------------------------------------------------------- #
# Ollama inference (text → HLF)
# --------------------------------------------------------------------------- #


def _ollama_generate(text: str, model: str | None = None) -> str:
    """
    Call the local Ollama instance or cloud provider to convert human-language
    text into an HLF program.
    """
    effective_model = model or os.environ.get("PRIMARY_MODEL") or os.environ.get("SUMMARIZATION_MODEL", "qwen:7b")

    # 1. Cloud Provider Fallback (OpenRouter/Ollama Cloud)
    openrouter_api = os.environ.get("OPENROUTER_API")
    ollama_api_key = os.environ.get("OLLAMA_API_KEY")
    is_cloud = (
        effective_model.endswith(":cloud") or effective_model.endswith("-cloud") or openrouter_api or ollama_api_key
    )

    if is_cloud and openrouter_api:
        # Use OpenRouter as primary cloud bridge if available
        _logger.log("CLOUD_INFERENCE_START", {"provider": "openrouter", "model": effective_model})
        try:
            resp = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {openrouter_api}",
                    "X-Title": "Sovereign OS Autonomous Runner",
                },
                json={
                    "model": effective_model.removesuffix(":cloud").removesuffix("-cloud"),
                    "messages": [
                        {
                            "role": "system",
                            "content": _SYSTEM_PROMPT_PATH.read_text().strip() if _SYSTEM_PROMPT_PATH.exists() else "",
                        },
                        {"role": "user", "content": text},
                    ],
                    "temperature": 0.0,
                },
                timeout=60.0,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            _logger.log("CLOUD_INFERENCE_ERROR", {"error": str(e)}, anomaly_score=0.5)
            # Fall through to local if cloud fails? No, if we are in cloud mode, we likely don't have local.

    # 2. Local / Docker Ollama Path (dual-endpoint failover)
    system_prompt = ""
    if _SYSTEM_PROMPT_PATH.exists():
        system_prompt = _SYSTEM_PROMPT_PATH.read_text().strip()

    payload = {
        "model": effective_model.removesuffix(":cloud").removesuffix("-cloud"),
        "system": system_prompt,
        "prompt": text,
        "stream": False,
        "options": {"temperature": 0.0, "num_ctx": 2048},
    }
    last_exc = None
    for host, headers in _get_ollama_endpoints():
        try:
            # Enforce strict 12s timeout for Ollama endpoint calls
            resp = httpx.post(
                f"{host}/api/generate",
                json=payload,
                headers={"Content-Type": "application/json", **headers},
                timeout=12.0,
            )
            resp.raise_for_status()
            data = resp.json()
            _logger.log("OLLAMA_ENDPOINT_USED", {"host": host, "model": effective_model})
            return data.get("response", "").strip()
        except (httpx.RequestError, httpx.HTTPStatusError, PermissionError, OSError) as exc:
            _logger.log("OLLAMA_ENDPOINT_FAILED", {"host": host, "error": str(exc)}, anomaly_score=0.4)
            last_exc = exc
    if is_cloud:
        raise RuntimeError(f"Cloud inference failed and all Ollama endpoints unreachable: {last_exc}")
    raise RuntimeError(f"All Ollama endpoints unavailable: {last_exc}") from last_exc


# --------------------------------------------------------------------------- #
# AgentProfile-aware inference (Phase 4 v2)
# --------------------------------------------------------------------------- #


def _ollama_generate_v2(text: str, profile: Any) -> str:
    """
    Like _ollama_generate but applies AgentProfile fields:
      - profile.model       → model ID
      - profile.provider    → "ollama" | "openrouter" | "cloud"
      - profile.system_prompt → system message override
      - profile.restrictions → max_tokens, temperature, etc.
    Falls back to _ollama_generate() if profile is minimal.
    """
    model = profile.model
    system_prompt = profile.system_prompt or ""
    if not system_prompt and _SYSTEM_PROMPT_PATH.exists():
        system_prompt = _SYSTEM_PROMPT_PATH.read_text().strip()

    restrictions = profile.restrictions or {}
    temperature = restrictions.get("temperature", 0.0)
    max_tokens = restrictions.get("max_tokens", 2048)

    # --- OpenRouter provider path ---
    if profile.provider == "openrouter":
        api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_API")
        if not api_key:
            _logger.log("OPENROUTER_NO_KEY", {"model": model}, anomaly_score=0.5)
            return _ollama_generate(text, model=model)
        _logger.log("OPENROUTER_INFERENCE_START", {"model": model})
        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Title": "Sovereign OS Autonomous Runner",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()

    # --- Cloud provider path ---
    if profile.provider == "cloud":
        return _ollama_generate(text, model=model)

    # --- Local Ollama path (default) — dual-endpoint failover ---
    payload = {
        "model": model.removesuffix(":cloud").removesuffix("-cloud"),
        "system": system_prompt,
        "prompt": text,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_ctx": max_tokens,
        },
    }
    last_exc = None
    for host, headers in _get_ollama_endpoints():
        try:
            # Enforce strict 12s timeout for Ollama V2 endpoint calls
            resp = httpx.post(
                f"{host}/api/generate",
                json=payload,
                headers={"Content-Type": "application/json", **headers},
                timeout=12.0,
            )
            resp.raise_for_status()
            _logger.log("OLLAMA_V2_ENDPOINT_USED", {"host": host, "model": model})
            return resp.json().get("response", "").strip()
        except (httpx.RequestError, httpx.HTTPStatusError, PermissionError, OSError) as exc:
            _logger.log("OLLAMA_V2_ENDPOINT_FAILED", {"host": host, "error": str(exc)}, anomaly_score=0.4)
            last_exc = exc
    raise RuntimeError(f"All Ollama endpoints unavailable: {last_exc}") from last_exc


# --------------------------------------------------------------------------- #
# Intent execution pipeline
# --------------------------------------------------------------------------- #


def execute_intent(payload: dict) -> dict:
    """
    Execute a single intent payload received from the Redis stream.

    The payload may be in two forms:
      - AST mode:  {"request_id": "...", "ast": {...}, ...}
      - Text mode: {"request_id": "...", "text": "...", ...}

    Returns the HLF execution result dict.
    """
    from hlf.hlfc import HlfSyntaxError
    from hlf.hlfc import compile as hlfc_compile
    from hlf.hlfrun import run as hlfrun

    tier = os.environ.get("DEPLOYMENT_TIER", "hearth")
    max_gas = int(os.environ.get("MAX_GAS_LIMIT", "10"))
    request_id = payload.get("request_id", "unknown")

    # --- Phase 4: Registry-aware model routing ---
    route_request_fn, AgentProfileCls = _try_route_request()
    profile = None
    if route_request_fn is not None:
        text_for_routing = payload.get("text", "")
        if text_for_routing:
            try:
                profile = route_request_fn(text_for_routing, payload)
                _logger.log(
                    "ROUTE_DECISION",
                    {
                        "request_id": request_id,
                        "model": profile.model,
                        "provider": profile.provider,
                        "tier": profile.tier,
                        "confidence": profile.confidence,
                        "gas_remaining": profile.gas_remaining,
                        "trace": profile.routing_trace,
                    },
                    confidence_score=profile.confidence,
                )
            except Exception as exc:
                _logger.log(
                    "ROUTE_DECISION_ERROR",
                    {"request_id": request_id, "error": str(exc)},
                    anomaly_score=0.4,
                )
                # Fall through to legacy path

    ast = payload.get("ast")

    if ast is None:
        # Text mode — call Ollama to generate HLF, then compile
        text = payload.get("text", "")
        if not text:
            return {"code": 1, "message": "no text or ast in payload", "gas_used": 0}
        try:
            # Use AgentProfile-aware inference if available, else legacy
            hlf_response = _ollama_generate_v2(text, profile) if profile is not None else _ollama_generate(text)
            _logger.log(
                "OLLAMA_RESPONSE",
                {"request_id": request_id, "preview": hlf_response[:120]},
            )
            ast = hlfc_compile(hlf_response)
        except (RuntimeError, HlfSyntaxError) as exc:
            _logger.log(
                "OLLAMA_OR_COMPILE_ERROR",
                {"request_id": request_id, "error": str(exc)},
                anomaly_score=0.6,
            )
            return {"code": 1, "message": str(exc), "gas_used": 0}

    try:
        result = hlfrun(ast, tier=tier, max_gas=max_gas)
        _logger.log(
            "INTENT_EXECUTED",
            {
                "request_id": request_id,
                "code": result["code"],
                "gas_used": result["gas_used"],
                "routed_model": profile.model if profile else "env_default",
            },
        )
        return result
    except Exception as exc:
        _logger.log(
            "EXECUTION_ERROR",
            {"request_id": request_id, "error": str(exc)},
            anomaly_score=0.8,
        )
        return {"code": 1, "message": str(exc), "gas_used": 0}


# --------------------------------------------------------------------------- #
# Redis stream consumer loop
# --------------------------------------------------------------------------- #


def run() -> None:
    _logger.log("AGENT_EXECUTOR_START", {"tier": os.environ.get("DEPLOYMENT_TIER", "hearth")})
    try:
        import redis

        r = redis.from_url(
            os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        )
        group = "executor-group"
        stream = "intents"
        with contextlib.suppress(Exception):
            r.xgroup_create(stream, group, id="0", mkstream=True)

        _logger.log("CONSUMER_GROUP_READY", {"stream": stream, "group": group})

        while True:
            messages = r.xreadgroup(group, "executor-1", {stream: ">"}, count=1, block=5000)
            if not messages:
                continue
            for _stream, entries in messages:
                for entry_id, data in entries:
                    try:
                        payload: dict[str, Any] = json.loads(data.get("data", "{}"))
                        _logger.log(
                            "INTENT_RECEIVED",
                            {"request_id": payload.get("request_id")},
                        )
                        result = execute_intent(payload)
                        _logger.log(
                            "INTENT_RESULT",
                            {
                                "request_id": payload.get("request_id"),
                                "code": result.get("code"),
                                "gas_used": result.get("gas_used"),
                            },
                        )
                        r.xack(stream, group, entry_id)
                    except Exception as exc:
                        _logger.log(
                            "INTENT_ERROR",
                            {"error": str(exc)},
                            anomaly_score=0.9,
                        )
    except Exception as exc:
        _logger.log("AGENT_EXECUTOR_FATAL", {"error": str(exc)}, anomaly_score=1.0)


if __name__ == "__main__":
    run()
