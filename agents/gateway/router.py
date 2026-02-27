"""
MoMA Router — routes intents to the appropriate model based on complexity,
VRAM availability, gas budget, and dynamic model downshifting.
"""
from __future__ import annotations

import json
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

# Gas Lua atomic decrement script
_GAS_DECREMENT_LUA = """
local key = KEYS[1]
local cost = tonumber(ARGV[1])
local cap  = tonumber(ARGV[2])
local curr = redis.call('GET', key)
if curr == false then
    redis.call('SET', key, cap - cost, 'EX', 3600)
    return cap - cost
end
curr = tonumber(curr)
if curr < cost then
    return -1
end
return redis.call('DECRBY', key, cost)
"""


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
        max_vram = max(m.get("size_vram", 0) for m in models) * 5  # rough heuristic
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
