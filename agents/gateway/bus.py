"""
Gateway bus — FastAPI app on port 40404.
Middleware chain: rate limiter → HLF linter → ALIGN enforcer → ULID nonce replay.
"""
from __future__ import annotations

import json
import time
from typing import Optional

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel
from pydantic_settings import BaseSettings

from hlf import validate_hlf_heuristic
from hlf.hlfc import compile as hlfc_compile
from agents.gateway.sentinel_gate import enforce_align
from agents.gateway.router import consume_gas_async, verify_gas_limit, record_intent_activity
import os
import httpx

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

    model_config = {"env_file": ".env"}


settings = Settings()
app = FastAPI(title="Sovereign Gateway", version="0.1.0")

_redis: Optional[aioredis.Redis] = None

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
    text: Optional[str] = None
    hlf: Optional[str] = None


class IntentResponse(BaseModel):
    request_id: str
    timestamp: float
    ast: dict
    stream_id: str


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/api/v1/intent", status_code=202)
async def post_intent(request: Request, body: IntentRequest) -> IntentResponse:
    global _cb_failures, _CB_RESET_TIME
    
    # Check circuit breaker
    if _cb_failures >= _CB_THRESHOLD:
        if time.time() < _CB_RESET_TIME:
            raise HTTPException(status_code=503, detail="Dapr Circuit Breaker Open")
        else:
            _cb_failures = 0  # Half-open
            
    r = await get_redis()

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

    if is_text_mode:
        # Text mode: skip HLF validation — package for Ollama inference pipeline
        ast: dict = {"version": "0.2.0", "program": [], "text_mode": True}
        # Text-mode intents charge 1 gas unit (LLM inference cost tracked separately)
        gas_charge = 1
    else:
        # HLF mode: full compile + validate pipeline
        # 2. Fast HLF Heuristic rejection
        if not validate_hlf_heuristic(hlf_payload):
            raise HTTPException(status_code=422, detail="Invalid HLF syntax: Rejected by heuristic")

        # 3. Compile HLF → AST (Before ALIGN evaluation)
        try:
            ast = hlfc_compile(hlf_payload)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"HLF compile error: {exc}") from exc

        # 4. ALIGN enforcer on structured AST
        blocked, rule_id = enforce_align(ast)
        if blocked:
            raise HTTPException(status_code=403, detail=f"ALIGN rule violated: {rule_id}")

        # Gas charge = AST node count (each top-level statement = 1 unit)
        gas_charge = len(ast.get("program", []))

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
    
    # 6. Publish via Dapr pub/sub
    pub_url = f"{settings.dapr_host}/v1.0/publish/pubsub/intents"
    stream_payload: dict = {"request_id": request_id, "timestamp": timestamp, "ast": ast}
    if is_text_mode:
        stream_payload["text"] = hlf_payload  # forward raw text for Ollama inference
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.post(pub_url, json=stream_payload)
            resp.raise_for_status()
            _cb_failures = 0  # reset on success
    except httpx.RequestError as exc:
        _cb_failures += 1
        _CB_RESET_TIME = time.time() + _CB_TIMEOUT
        # Fallback to direct Redis streams if Dapr fails
        await r.xadd("intents", {"data": json.dumps(stream_payload)})

    record_intent_activity()
    return IntentResponse(request_id=request_id, timestamp=timestamp, ast=ast, stream_id="dapr-pubsub")
