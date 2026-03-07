"""
Gateway bus — FastAPI app on port 40404.
Middleware chain: rate limiter → HLF linter → ALIGN enforcer → ULID nonce replay.
"""

from __future__ import annotations

import base64
import json
import time
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pydantic_settings import BaseSettings

from agents.gateway.matrix_sync.scheduler import get_scheduler_status, start_scheduler, stop_scheduler
from agents.gateway.ollama_dispatch import (
    InferenceRequest,
    complexity_score,
    get_dispatcher,
)
from agents.gateway.router import (
    consume_gas_async,
    record_intent_activity,
    route_request,
    verify_gas_limit,
)
from agents.gateway.sentinel_gate import enforce_align
from hlf import validate_hlf_heuristic
from hlf.hlfc import HlfSyntaxError, format_correction
from hlf.hlfc import compile as hlfc_compile

try:
    import ulid as _ulid_module

    def _new_ulid() -> str:
        return str(_ulid_module.new())

except ImportError:
    import uuid

    def _new_ulid() -> str:  # type: ignore[misc]
        return str(uuid.uuid4())


class Settings(BaseSettings):
    redis_url: str = "redis://localhost:6379/0"
    max_gas_limit: int = 10
    deployment_tier: str = "hearth"
    dapr_host: str = "http://localhost:3500"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()


@asynccontextmanager
async def _lifespan(_app: FastAPI):  # type: ignore[type-arg]
    """Start the pipeline scheduler on startup; stop it on shutdown."""
    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()


app = FastAPI(
    lifespan=_lifespan,
    title="Sovereign Gateway Bus",
    version="2.0.0",
    description=(
        "Gateway Bus for the Sovereign Agentic OS with HLF.\n\n"
        "Middleware chain: **Rate Limiter → HLF Linter → ALIGN Enforcer → "
        "ULID Nonce Replay → Dapr Pub/Sub (Redis fallback)**.\n\n"
        "All intents pass through the Sentinel Gate for governance enforcement. "
        "Gas budgets are per-tier (hearth/forge/sovereign) and tracked atomically "
        "via Redis Lua scripts."
    ),
    contact={"name": "Sovereign OS Admin", "url": "https://github.com/sovereign-os"},
    license_info={"name": "MIT"},
    openapi_tags=[
        {"name": "Health", "description": "Service health and readiness probes"},
        {"name": "Intents", "description": "HLF and natural-language intent dispatch"},
        {"name": "System", "description": "System state and configuration queries"},
        {"name": "Registry", "description": "Model & Agent Registry operations"},
    ],
)

_redis: aioredis.Redis | None = None

# Circuit Breaker state
_cb_failures = 0
_CB_THRESHOLD = 5
_CB_RESET_TIME = 0.0
_CB_TIMEOUT = 30.0  # seconds


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


class IntentRequest(BaseModel):
    text: str | None = None
    hlf: str | None = None


class IntentResponse(BaseModel):
    request_id: str
    timestamp: float
    ast: dict
    stream_id: str
    model: str = ""
    provider: str = ""
    routing_trace: list = []
    complexity: float = 0.0


class GenerateRequest(BaseModel):
    prompt: str
    model: str = ""
    system: str = ""
    stream: bool = False
    temperature: float = 0.7
    max_tokens: int = 4096


@app.get(
    "/health",
    tags=["Health"],
    summary="Service Health Check",
    description="Returns the current health status of the Gateway Bus. "
    "Used by load balancers, MCP servers, and the Streamlit GUI "
    "to verify the gateway is reachable.",
    response_description="Health status object with 'ok' or error details.",
)
async def health() -> dict:
    """Check Gateway Bus health. Returns `{"status": "ok"}` when operational."""
    return {"status": "ok"}


@app.get(
    "/health/scheduler",
    tags=["Health"],
    summary="Pipeline Scheduler Health Check",
    description=(
        "Returns the current status of the APScheduler pipeline background job.\n\n"
        "Key fields:\n"
        "- **running** — whether the scheduler process is active\n"
        "- **interval_hours** — configured fire interval (from `pipeline_scheduler.interval_hours` "
        "in `config/settings.json`)\n"
        "- **last_run_status** — `ok`, `error`, or `never_run`\n"
        "- **next_run_time** — Unix timestamp of the next scheduled run"
    ),
    response_description="Scheduler status object.",
)
async def scheduler_health() -> dict:
    """Return pipeline scheduler status (interval, last run, next run, errors)."""
    return get_scheduler_status()


_HLF_BYTECODE_MAGIC = bytes([0x48, 0x4C, 0x46, 0x04])  # "HLF\x04"


def _is_bytecode_payload(payload: str) -> bool:
    """Detect if a payload is base64-encoded .hlb bytecode.

    Checks whether decoding the first 8 base64 characters yields the
    HLF v0.4 magic header.  This is intentionally conservative —
    regular HLF text will never start with ``SEZG`` (the Base64 prefix
    of ``HLF\\x04``).
    """
    try:
        head = base64.b64decode(payload[:8])
        return head[:4] == _HLF_BYTECODE_MAGIC
    except Exception:
        return False


@app.post(
    "/api/v1/intent",
    status_code=202,
    tags=["Intents"],
    summary="Dispatch Intent (HLF or Text)",
    description=(
        "Submit an intent for processing through the full middleware chain.\n\n"
        "**Two modes:**\n"
        "- **HLF mode**: Provide `hlf` field with an `[HLF-v3]` program. "
        "Gets compiled, ALIGN-checked, and gas-metered.\n"
        "- **Text mode**: Provide `text` field with natural language. "
        "Gets forwarded to Ollama for inference (1 gas unit).\n\n"
        "**Middleware chain:** rate limiter (50 rpm) → HLF heuristic → "
        "HLFC compile → ALIGN sentinel → gas budget → ULID nonce → "
        "Dapr pub/sub (Redis fallback)."
    ),
    response_description="Accepted intent with request_id, AST, and stream_id.",
)
async def post_intent(request: Request, body: IntentRequest) -> IntentResponse:
    global _cb_failures, _CB_RESET_TIME

    r = await get_redis()
    from agents.gateway.router import is_gateway_healthy

    # 0. Health check (Canary trip)
    if not await is_gateway_healthy(r):
        raise HTTPException(status_code=503, detail="Gateway in emergency degradation state (Canary trip)")

    # Check circuit breaker
    if _cb_failures >= _CB_THRESHOLD:
        if time.time() < _CB_RESET_TIME:
            raise HTTPException(status_code=503, detail="Dapr Circuit Breaker Open")
        else:
            _cb_failures = 0  # Half-open

    # 1. Token bucket rate limiter (50 rpm)
    minute_key = f"rate:{int(time.time()) // 60}"
    count = await r.incr(minute_key)
    if count == 1:
        await r.expire(minute_key, 120)
    if count > 50:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # Determine mode: hlf (compile-time validated) vs text (Ollama pipeline)
    is_text_mode = body.hlf is None and body.text is not None
    hlf_payload = body.hlf if body.hlf is not None else body.text
    if not hlf_payload:
        raise HTTPException(status_code=422, detail="Provide 'text' or 'hlf' field")

    # Fast-path bypass for static Canary probes
    # (Matches _PROBE_HLF in canary_agent.py exactly)
    if hlf_payload.strip().startswith("[HLF-v2]") and 'canary_probe "health"' in hlf_payload:
        return IntentResponse(
            request_id="canary-" + _new_ulid()[:8],
            timestamp=time.time(),
            ast={"tag": "INTENT", "args": ["canary_probe", "health"], "human_readable": "Static Canary Probe Bypass"},
            stream_id="fast-path-bypass",
        )

    if is_text_mode:
        # Text mode: skip HLF validation — package for Ollama inference pipeline
        ast: dict = {"version": "0.3.0", "program": [], "text_mode": True}
        # Text-mode intents charge 1 gas unit (LLM inference cost tracked separately)
        gas_charge = 1
    elif _is_bytecode_payload(hlf_payload):
        # Bytecode mode: .hlb binary submitted as base64 in the hlf field
        from hlf.bytecode import HlfBytecodeError, execute_bytecode

        try:
            hlb_data = base64.b64decode(hlf_payload)
            vm_result = execute_bytecode(hlb_data, tier=settings.deployment_tier)
        except HlfBytecodeError as exc:
            raise HTTPException(status_code=422, detail=f"Bytecode execution error: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Bytecode error: {exc}") from exc

        ast = {
            "version": "0.4.0",
            "program": [],
            "bytecode_mode": True,
            "vm_result": vm_result,
        }
        gas_charge = vm_result.get("gas_used", 1)
    else:
        # HLF mode: full compile + validate pipeline
        # 2. Fast HLF Heuristic rejection
        if not validate_hlf_heuristic(hlf_payload):
            raise HTTPException(status_code=422, detail="Invalid HLF syntax: Rejected by heuristic")

        # 3. Compile HLF → AST (Before ALIGN evaluation)
        try:
            from hlf.hlfc import HlfAlignViolation

            ast = hlfc_compile(hlf_payload)
        except HlfAlignViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except HlfSyntaxError as exc:
            correction = format_correction(hlf_payload, exc)
            raise HTTPException(status_code=422, detail=correction) from exc
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"HLF compile error: {exc}") from exc

        # 4. ALIGN enforcer on structured AST
        blocked, rule_id = enforce_align(ast)
        if blocked:
            raise HTTPException(status_code=403, detail=f"ALIGN rule violated: {rule_id}")

        # Gas charge = AST node count (each top-level statement = 1 unit)
        gas_charge = len(ast.get("program", []))

        # Per-intent gas limit enforcement (in addition to the global tier bucket)
        ok, node_count = verify_gas_limit(ast, settings.max_gas_limit)
        if not ok:
            raise HTTPException(
                status_code=429,
                detail=f"Intent gas limit exceeded: {node_count}/{settings.max_gas_limit} nodes",
            )

    # 4b. Global Per-Tier Gas Bucket (Lua atomic decrement)
    gas_ok = await consume_gas_async(settings.deployment_tier, gas_charge, r)
    if not gas_ok:
        raise HTTPException(status_code=429, detail="Global gas budget exhausted for this tier")

    # 5. ULID nonce replay protection
    request_id = _new_ulid()
    nonce_key = f"nonce:{request_id}"
    set_ok = await r.set(nonce_key, "1", nx=True, ex=600)
    if not set_ok:
        raise HTTPException(status_code=409, detail="Duplicate request nonce")

    timestamp = time.time()

    # 5b. Dynamic Model Routing via MoMA Router (RFC 9004)
    cx = complexity_score(hlf_payload, ast)
    profile = route_request(hlf_payload, ast, complexity=cx)

    # 6. Publish via Dapr pub/sub; fall back to direct Redis stream on Dapr failure
    pub_url = f"{settings.dapr_host}/v1.0/publish/pubsub/intents"
    stream_payload: dict = {
        "request_id": request_id,
        "timestamp": timestamp,
        "ast": ast,
        "model": profile.model,
        "provider": profile.provider,
        "system_prompt": profile.system_prompt,
        "complexity": cx,
    }
    if is_text_mode:
        stream_payload["text"] = hlf_payload  # forward raw text for Ollama inference
    stream_id = "dapr-pubsub"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.post(pub_url, json=stream_payload)
            resp.raise_for_status()
            _cb_failures = 0  # reset on success
    except (httpx.RequestError, httpx.HTTPStatusError, PermissionError, OSError):
        _cb_failures += 1
        _CB_RESET_TIME = time.time() + _CB_TIMEOUT
        # Fallback to direct Redis streams if Dapr is unavailable/returns non-2xx
        redis_id = await r.xadd("intents", {"data": json.dumps(stream_payload)})
        stream_id = f"redis-stream:{redis_id}"

    record_intent_activity()
    return IntentResponse(
        request_id=request_id,
        timestamp=timestamp,
        ast=ast,
        stream_id=stream_id,
        model=profile.model,
        provider=profile.provider,
        routing_trace=profile.routing_trace,
        complexity=round(cx, 3),
    )


# ── Dynamic Ollama Dispatch ──────────────────────────────────────────────


@app.post(
    "/api/v1/generate",
    tags=["Intents"],
    summary="Direct Model Inference (Generate)",
    description=(
        "Dispatch a prompt directly to the MoMA-selected model for inference.\n\n"
        "**Modes:**\n"
        "- `stream=false` (default): Returns complete JSON response\n"
        "- `stream=true`: Returns Server-Sent Events (SSE) stream\n\n"
        "If no `model` is specified, the MoMA Router selects automatically "
        "based on prompt complexity (RFC 9004 𝕔 scoring)."
    ),
)
async def generate(body: GenerateRequest):
    """Direct model inference with optional streaming."""
    dispatcher = get_dispatcher()

    # Route model selection if not explicitly specified
    if not body.model:
        profile = route_request(body.prompt, {})
        model = profile.model
        provider = profile.provider
    else:
        model = body.model
        provider = "openrouter" if "openrouter" in model.lower() else "ollama"

    req = InferenceRequest(
        prompt=body.prompt,
        model=model,
        system=body.system,
        stream=body.stream,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        metadata={"provider": provider},
    )

    if body.stream:
        # SSE streaming response
        async def event_stream():
            async for chunk in dispatcher.generate_stream(req):
                yield chunk.to_sse()
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        # Synchronous JSON response
        result = await dispatcher.generate(req)
        return result.to_dict()


# ── Registry: Local Inventory Sync ───────────────────────────────────────


class InventorySyncResponse(BaseModel):
    synced: int = 0
    models: list[str] = []
    error: str | None = None


@app.post(
    "/api/v1/inventory/sync",
    tags=["Registry"],
    summary="Sync local Ollama inventory to the SQL registry",
    response_model=InventorySyncResponse,
)
async def sync_inventory() -> InventorySyncResponse:
    """Heartbeat-sync the local Ollama models into the registry's
    `user_local_inventory` table.  This is a non-destructive upsert."""
    try:
        import os
        import sys

        # Import db module
        _here = os.path.dirname(os.path.abspath(__file__))
        _sovereign_root = os.path.abspath(os.path.join(_here, "..", ".."))
        if _sovereign_root not in sys.path:
            sys.path.insert(0, _sovereign_root)
        from agents.core.db import (
            db_path,
            get_db,
            init_db,
            upsert_local_inventory,
            upsert_local_metadata,
        )

        # Fetch local Ollama tags
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("http://localhost:11434/api/tags")
            resp.raise_for_status()
            tags_data = resp.json()

        models_list = tags_data.get("models", [])
        registry_path = db_path()
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        init_db(registry_path)

        synced_names: list[str] = []
        with get_db(registry_path) as conn:
            for m in models_list:
                name = m.get("name", "")
                if not name:
                    continue
                size_gb = round(m.get("size", 0) / 1e9, 2)
                upsert_local_inventory(conn, name, size_gb=size_gb)
                upsert_local_metadata(
                    conn,
                    name,
                    digest=m.get("digest", ""),
                    modified_at=m.get("modified_at", ""),
                    quantization_level=m.get("quantization_level", ""),
                )
                synced_names.append(name)

        return InventorySyncResponse(synced=len(synced_names), models=synced_names)

    except Exception as e:
        return InventorySyncResponse(error=str(e))
