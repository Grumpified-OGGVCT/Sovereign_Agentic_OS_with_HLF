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

from hlf import validate_hlf
from hlf.hlfc import compile as hlfc_compile
from agents.gateway.sentinel_gate import enforce_align

try:
    from ulid import ULID
except ImportError:
    import uuid

    class ULID:  # type: ignore[no-redef]
        @staticmethod
        def new() -> str:
            return str(uuid.uuid4())


class Settings(BaseSettings):
    redis_url: str = "redis://localhost:6379/0"
    max_gas_limit: int = 10
    deployment_tier: str = "hearth"

    model_config = {"env_file": ".env"}


settings = Settings()
app = FastAPI(title="Sovereign Gateway", version="0.1.0")

_redis: Optional[aioredis.Redis] = None


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
    r = await get_redis()

    # 1. Token bucket rate limiter (50 rpm)
    minute_key = f"rate:{int(time.time()) // 60}"
    count = await r.incr(minute_key)
    if count == 1:
        await r.expire(minute_key, 120)
    if count > 50:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # Determine payload
    hlf_payload = body.hlf if body.hlf is not None else body.text
    if not hlf_payload:
        raise HTTPException(status_code=422, detail="Provide 'text' or 'hlf' field")

    # 2. HLF linter
    for line in hlf_payload.splitlines():
        if line.strip() and not validate_hlf(line):
            raise HTTPException(status_code=422, detail=f"Invalid HLF syntax: {line!r}")

    # 3. ALIGN enforcer
    blocked, rule_id = enforce_align(hlf_payload)
    if blocked:
        raise HTTPException(status_code=403, detail=f"ALIGN rule violated: {rule_id}")

    # 4. ULID nonce replay protection
    request_id = str(ULID.new())
    nonce_key = f"nonce:{request_id}"
    set_ok = await r.set(nonce_key, "1", nx=True, ex=600)
    if not set_ok:
        raise HTTPException(status_code=409, detail="Duplicate request nonce")

    # Compile HLF → AST
    try:
        ast = hlfc_compile(hlf_payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"HLF compile error: {exc}") from exc

    timestamp = time.time()
    message = json.dumps({"request_id": request_id, "timestamp": timestamp, "ast": ast})
    stream_id = await r.xadd("intents", {"data": message})

    return IntentResponse(request_id=request_id, timestamp=timestamp, ast=ast, stream_id=stream_id)
