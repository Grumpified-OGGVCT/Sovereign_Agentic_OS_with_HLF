"""
MoMA Router — routes intents to the appropriate model based on complexity,
VRAM availability, gas budget, and dynamic model downshifting.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ollama_host: str = "http://ollama-matrix:11434"
    redis_url: str = "redis://localhost:6379/0"
    max_gas_limit: int = 10
    deployment_tier: str = "hearth"
    primary_model: str = "qwen3-vl:32b-cloud"
    reasoning_model: str = "qwen-max"
    summarization_model: str = "qwen:7b"

    model_config = {"env_file": ".env"}


settings = Settings()

# Per-tier gas bucket capacities (from config/settings.json)
_TIER_GAS_CAPS: dict[str, int] = {
    "hearth": 1000,
    "forge": 10000,
    "sovereign": 100000,
}

# Gas Lua atomic check-and-decrement script.
# Initialises the bucket on first use with the tier cap, then atomically decrements.
# Sets a 25-hour TTL so the bucket self-resets if the nightly replenish_gas() cron fails.
# Returns remaining gas, or -1 if the bucket is exhausted.
_GAS_DECREMENT_LUA = """
local key = KEYS[1]
local cost = tonumber(ARGV[1])
local cap  = tonumber(ARGV[2])
local ttl  = 90000
-- Guard: reject requests that exceed tier capacity to prevent negative balance
-- (e.g., misconfiguration or a malformed intent with abnormally large AST)
if cost > cap then
    return -1
end
local curr = redis.call('GET', key)
if curr == false then
    -- Bucket not yet initialised — set to cap-cost and return remaining
    redis.call('SET', key, cap - cost, 'EX', ttl)
    return cap - cost
end
curr = tonumber(curr)
if curr < cost then
    return -1
end
local remaining = redis.call('DECRBY', key, cost)
redis.call('EXPIRE', key, ttl)
return remaining
"""

# Last-known intent timestamps for Idle Curiosity Protocol
_last_intent_ts: float = time.time()


def record_intent_activity() -> None:
    """Record that an intent was just processed (for Idle Curiosity tracking)."""
    global _last_intent_ts
    _last_intent_ts = time.time()


def get_last_intent_timestamp() -> float:
    """Return the timestamp of the last recorded intent (for observability/logging)."""
    return _last_intent_ts


def is_system_idle(idle_threshold_sec: int = 3600) -> bool:
    """Return True if no intent has been received for *idle_threshold_sec* seconds."""
    return (time.time() - _last_intent_ts) >= idle_threshold_sec


def consume_gas(tier: str, cost: int, r: Any) -> bool:
    """
    Atomically consume *cost* gas units from the per-tier Redis bucket.

    Returns True if gas was available and consumed, False (HTTP 429 signal) if
    the bucket is exhausted.  Uses a Lua script for atomic check-and-decrement.
    Synchronous version — use ``consume_gas_async`` in async contexts.
    """
    cap = _TIER_GAS_CAPS.get(tier, _TIER_GAS_CAPS["hearth"])
    key = f"gas:{tier}"
    result = r.eval(_GAS_DECREMENT_LUA, 1, key, str(cost), str(cap))
    return int(result) >= 0


async def consume_gas_async(tier: str, cost: int, r: Any) -> bool:
    """Async variant of :func:`consume_gas` for use with redis.asyncio clients."""
    cap = _TIER_GAS_CAPS.get(tier, _TIER_GAS_CAPS["hearth"])
    key = f"gas:{tier}"
    result = await r.eval(_GAS_DECREMENT_LUA, 1, key, str(cost), str(cap))
    return int(result) >= 0


def replenish_gas(tier: str, r: Any) -> None:
    """Restore the gas bucket for *tier* to its full capacity (nightly cron use)."""
    cap = _TIER_GAS_CAPS.get(tier, _TIER_GAS_CAPS["hearth"])
    r.set(f"gas:{tier}", cap)


def _is_cloud(model: str) -> bool:
    return model.endswith(":cloud")


def route_intent(intent_text: str, ast: dict) -> str:
    """Return the model name appropriate for this intent."""
    text_lower = intent_text.lower()
    if any(kw in text_lower for kw in ("image", "ocr", "visual", "screenshot")):
        return settings.primary_model
    if any(kw in text_lower for kw in ("code", "debug", "symbol", "compile", "ast")):
        return settings.reasoning_model
    return settings.summarization_model


def verify_gas_limit(ast: dict, max_gas: int = 10) -> tuple[bool, int]:
    """Count AST nodes and check against gas limit. Returns (ok, node_count)."""
    program = ast.get("program", [])
    node_count = len(program)
    return node_count <= max_gas, node_count


def check_vram_threshold(model: str, client: httpx.Client | None = None) -> bool:
    """
    Returns True if the model can be loaded (VRAM < 80% or model is cloud).
    Cloud models skip VRAM check entirely.
    """
    if _is_cloud(model):
        return True
    try:
        c = client or httpx.Client(timeout=5)
        resp = c.get(f"{settings.ollama_host}/api/ps")
        if resp.status_code != 200:
            return True  # Can't determine — allow
        data = resp.json()
        models = data.get("models", [])
        if not models:
            return True
        total_vram = sum(m.get("size_vram", 0) for m in models)
        # Rough heuristic: estimate max capacity as 5x the largest single model's VRAM.
        # This accounts for typical GPU headroom. Configurable via VRAM_CAPACITY_MULTIPLIER env var.
        multiplier = int(os.environ.get("VRAM_CAPACITY_MULTIPLIER", "5"))
        max_vram = max(m.get("size_vram", 0) for m in models) * multiplier
        if max_vram == 0:
            return True
        return (total_vram / max_vram) < 0.80
    except Exception:
        return True  # fail-open for VRAM check


def mediate_web_search(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip web_search:true from Ollama API calls, reroute via WEB_SEARCH host function."""
    if payload.get("web_search"):
        del payload["web_search"]
        payload["_reroute_web_search"] = True
    return payload
