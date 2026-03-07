"""
MoMA Router — routes intents to the appropriate model based on complexity,
VRAM availability, gas budget, and dynamic model downshifting.
"""

from __future__ import annotations

import inspect
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from pydantic_settings import BaseSettings

# ALS audit logging
try:
    from agents.core.logger import ALSLogger

    _routing_logger = ALSLogger(agent_role="moma-router", goal_id="routing")
except ImportError:
    _routing_logger = None


class Settings(BaseSettings):
    ollama_host: str = "http://localhost:11434"
    ollama_host_secondary: str = ""
    ollama_api_key_secondary: str = ""
    ollama_load_strategy: str = "failover"  # failover | round_robin | primary_only
    redis_url: str = "redis://localhost:6379/0"
    max_gas_limit: int = 10
    deployment_tier: str = "hearth"
    primary_model: str = "qwen3-vl:32b-cloud"
    reasoning_model: str = "qwen-max"
    summarization_model: str = "qwen:7b"
    openrouter_api_key: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}


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
    # Ollama cloud models use either '{root}:cloud' (bare tag) or '{root}:{size}-cloud' (size-qualified tag)
    return model.endswith(":cloud") or model.endswith("-cloud")


async def is_gateway_healthy(r: Any) -> bool:
    """Return False if the Canary agent has tripped the health signal."""
    try:
        res = r.exists("health:gateway:failed")
        if inspect.isawaitable(res):
            return not await res
        return not res
    except Exception:
        return True  # Fail-open


def route_intent(intent_text: str, ast: dict) -> str:
    """Return the model name appropriate for this intent."""
    text_lower = intent_text.lower()
    if any(kw in text_lower for kw in ("image", "ocr", "visual", "screenshot")):
        return settings.primary_model
    if any(kw in text_lower for kw in ("code", "debug", "symbol", "compile", "ast")):
        return settings.reasoning_model
    return settings.summarization_model


# ─── Model Allowlist Enforcement ──────────────────────────────────────────

_SETTINGS_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "settings.json"


def _load_allowed_models(tier: str) -> set[str]:
    """Load the ollama_allowed_models list for the given tier from settings.json."""
    try:
        data = json.loads(_SETTINGS_PATH.read_text())
        allowed = data.get("ollama_allowed_models", {})
        models = allowed.get(tier, [])
        return {m.lower() for m in models}
    except Exception:
        return set()  # fail-open if settings unreadable


def is_model_allowed(model: str, tier: str) -> bool:
    """Check if a model is in the allowlist for the given deployment tier.

    Normalizes model names before checking:
      - Strips ':cloud' / '-cloud' suffix
      - Strips ':latest' tag suffix
      - Replaces ':' with '-' for consistent comparison
      - Uses EXACT match on normalized canonical form (no substring matching)
    Returns True (fail-open) if the allowlist is empty or unreadable.
    """
    allowed = _load_allowed_models(tier)
    if not allowed:
        return True  # fail-open

    def _normalize(name: str) -> str:
        """Normalize a model name to canonical form for exact comparison."""
        name = name.lower()
        # Strip cloud and version suffixes
        for suffix in (":cloud", "-cloud", ":latest"):
            name = name.removesuffix(suffix)
        return name.replace(":", "-")

    norm_model = _normalize(model)
    for allowed_model in allowed:
        norm_allowed = _normalize(allowed_model)
        if norm_model == norm_allowed:
            return True
    return False


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
    # Try primary then secondary Ollama for VRAM check
    hosts = [settings.ollama_host]
    if settings.ollama_host_secondary:
        hosts.append(settings.ollama_host_secondary)
    for host in hosts:
        try:
            # Enforce strict 12s timeout for Ollama checks
            c = client or httpx.Client(timeout=12.0)
            resp = c.get(f"{host}/api/ps")
            if resp.status_code != 200:
                continue
            data = resp.json()
            models = data.get("models", [])
            if not models:
                return True
            total_vram = sum(m.get("size_vram", 0) for m in models)
            multiplier = int(os.environ.get("VRAM_CAPACITY_MULTIPLIER", "5"))
            max_vram = max(m.get("size_vram", 0) for m in models) * multiplier
            if max_vram == 0:
                return True
            return (total_vram / max_vram) < 0.80
        except Exception:
            continue
    return True  # fail-open for VRAM check


def mediate_web_search(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip web_search:true from Ollama API calls, reroute via WEB_SEARCH host function."""
    if payload.get("web_search"):
        del payload["web_search"]
        payload["_reroute_web_search"] = True
    return payload


# ─────────────────────────────────────────────────────────────────────────
# Phase 3 — Registry-Aware Routing Engine
# ─────────────────────────────────────────────────────────────────────────


def _log_routing_decision(
    profile: AgentProfile,
    intent_text: str,
    phase: str,
) -> None:
    """Emit an ALS ROUTING_DECISION event for governance audit."""
    if _routing_logger is None:
        return
    _routing_logger.log(
        event="ROUTING_DECISION",
        data={
            "model": profile.model,
            "provider": profile.provider,
            "tier": profile.tier,
            "phase": phase,
            "specialization": next(
                (s["match"] for s in profile.routing_trace if s.get("step") == "specialization"),
                None,
            ),
            "trace_steps": len(profile.routing_trace),
            "intent_preview": intent_text[:80],
        },
        confidence_score=profile.confidence,
    )


@dataclass
class AgentProfile:
    """Rich routing result — replaces the bare model-name string."""

    model: str  # Selected model ID
    provider: str = "ollama"  # "ollama" | "openrouter" | "cloud"
    tier: str = "D"  # Tier of selected model
    system_prompt: str = ""  # From agent_templates or default
    tools: list[str] = field(default_factory=list)
    restrictions: dict[str, Any] = field(default_factory=dict)
    routing_trace: list[dict[str, Any]] = field(default_factory=list)
    gas_remaining: int = -1  # Post-consumption gas balance
    confidence: float = 0.5  # Router confidence in selection


# Tier walk order — Cloud-First isolation invariant
_TIER_WALK_ORDER = ["S", "A+", "A", "A-", "B+", "B", "C", "D"]

# Specialized intent overrides (checked BEFORE tier walk)
_SPECIALIZATION_PATTERNS: dict[str, list[str]] = {
    "coding": ["code", "debug", "refactor", "compile", "ast", "symbol", "lint"],
    "visual": ["image", "ocr", "visual", "screenshot", "photo", "diagram"],
    "uncensored": ["uncensored", "unfiltered", "unrestricted"],
}


def _try_import_db():
    """Lazy-import db module from agents.core to avoid circular imports."""
    try:
        from agents.core.db import (
            db_path,
            get_agent_template,
            get_db,
            get_equivalents,
            get_local_inventory,
            get_models_by_tier,
            init_db,
        )

        return get_db, db_path, init_db, get_models_by_tier, get_local_inventory, get_agent_template, get_equivalents
    except ImportError:
        return None


def route_request(
    intent_text: str,
    ast: dict,
    metadata: dict[str, Any] | None = None,
    complexity: float = -1.0,
) -> AgentProfile:
    """
    Registry-aware 3-Phase Tier Walk router.  Returns a full AgentProfile.

    Phase 0: Complexity Short-Circuit — 𝕔 < 0.3 → SLM, 𝕔 > 0.7 → frontier
    Phase 1: Cloud Tier Walk — query registry for best model per-tier
    Phase 2: Local Inventory Fallback — if cloud walk exhausted
    Phase 3: OpenRouter Handoff — if both cloud and local fail

    Falls back to legacy route_intent() if the registry is unavailable.
    """
    trace: list[dict[str, Any]] = []
    text_lower = intent_text.lower()
    tier = settings.deployment_tier

    # ── Specialization Pre-Routing Hooks ──────────────────────────────
    specialization = None
    for spec_name, keywords in _SPECIALIZATION_PATTERNS.items():
        if any(kw in text_lower for kw in keywords):
            specialization = spec_name
            trace.append({"step": "specialization", "match": spec_name, "keywords": keywords})
            break

    # ── Phase 0: Complexity Short-Circuit ──────────────────────────────
    if complexity >= 0 and specialization is None:
        if complexity < 0.3:
            trace.append({"step": "complexity_shortcircuit", "score": round(complexity, 3), "target": "slm"})
            model = settings.summarization_model
            if is_model_allowed(model, tier):
                profile = AgentProfile(
                    model=model,
                    provider="cloud" if _is_cloud(model) else "ollama",
                    tier="slm",
                    routing_trace=trace,
                    confidence=0.85,
                )
                _log_routing_decision(profile, intent_text, phase="complexity_slm")
                return profile
        elif complexity > 0.7:
            trace.append({"step": "complexity_shortcircuit", "score": round(complexity, 3), "target": "frontier"})
            model = settings.primary_model
            if is_model_allowed(model, tier):
                profile = AgentProfile(
                    model=model,
                    provider="cloud" if _is_cloud(model) else "ollama",
                    tier="S",
                    routing_trace=trace,
                    confidence=0.9,
                )
                _log_routing_decision(profile, intent_text, phase="complexity_frontier")
                return profile
        else:
            trace.append({"step": "complexity_midrange", "score": round(complexity, 3)})

    # ── Try registry-backed routing ───────────────────────────────────
    db_imports = _try_import_db()
    if db_imports is None:
        # Registry not available — graceful fallback to legacy router
        legacy_model = route_intent(intent_text, ast)
        trace.append({"step": "fallback", "reason": "db_import_failed", "model": legacy_model})
        return AgentProfile(
            model=legacy_model,
            provider="cloud" if _is_cloud(legacy_model) else "ollama",
            routing_trace=trace,
            confidence=0.3,
        )
        # NOTE: _log_routing_decision deferred to after return — use wrapper below

    get_db, db_path, init_db, get_models_by_tier, get_local_inventory, get_agent_template, get_equivalents = db_imports

    registry = db_path()
    if not registry.exists():
        legacy_model = route_intent(intent_text, ast)
        trace.append({"step": "fallback", "reason": "registry_not_found", "model": legacy_model})
        return AgentProfile(
            model=legacy_model,
            provider="cloud" if _is_cloud(legacy_model) else "ollama",
            routing_trace=trace,
            confidence=0.3,
        )

    init_db(registry)

    try:
        with get_db(registry) as conn:
            # ── Phase 1: Cloud Tier Walk ──────────────────────────────
            selected_model = None
            selected_tier = None

            for tier in _TIER_WALK_ORDER:
                candidates = get_models_by_tier(conn, tier)
                trace.append(
                    {
                        "step": "tier_walk",
                        "tier": tier,
                        "candidates": len(candidates),
                    }
                )

                for candidate in candidates:
                    model_id = candidate["model_id"]
                    # Cloud-First isolation: skip local-only models in cloud walk
                    if check_vram_threshold(model_id):
                        selected_model = model_id
                        selected_tier = tier
                        trace.append({"step": "selected", "phase": "cloud", "model": model_id, "tier": tier})
                        break

                if selected_model:
                    break

            # ── Phase 2: Local Inventory Fallback ─────────────────────
            if selected_model is None:
                local_models = get_local_inventory(conn)
                trace.append({"step": "local_fallback", "available": len(local_models)})

                if local_models:
                    # Pick the first (most recently seen) local model
                    selected_model = local_models[0]["model_id"]
                    selected_tier = "local"
                    trace.append({"step": "selected", "phase": "local", "model": selected_model})

            # ── Phase 3: OpenRouter Handoff ────────────────────────────
            if selected_model is None:
                # Check for OpenRouter equivalents of the primary model
                equivs = get_equivalents(conn, settings.primary_model)
                or_hit = next((e for e in equivs if e["provider"] == "openrouter"), None)
                if or_hit:
                    selected_model = or_hit["provider_model_id"]
                    selected_tier = "openrouter"
                    trace.append({"step": "selected", "phase": "openrouter", "model": selected_model})

            # ── Ultimate fallback ─────────────────────────────────────
            if selected_model is None:
                selected_model = route_intent(intent_text, ast)
                selected_tier = "fallback"
                trace.append({"step": "fallback", "reason": "all_phases_exhausted", "model": selected_model})

            # ── Specialization override (if match found earlier) ──────
            if specialization == "coding":
                # Prefer devstral-small-2 or reasoning_model for coding
                coding_candidates = [
                    m["model_id"] for m in get_local_inventory(conn) if "devstral" in m["model_id"].lower()
                ]
                if coding_candidates:
                    selected_model = coding_candidates[0]
                    trace.append({"step": "override", "specialization": "coding", "model": selected_model})
                else:
                    selected_model = settings.reasoning_model
                    trace.append({"step": "override", "specialization": "coding_fallback", "model": selected_model})

            elif specialization == "visual":
                selected_model = settings.primary_model
                trace.append({"step": "override", "specialization": "visual", "model": selected_model})

            # ── Load agent template if available ──────────────────────
            system_prompt = ""
            tools: list[str] = []
            restrictions: dict[str, Any] = {}

            if specialization:
                template = get_agent_template(conn, specialization)
                if template:
                    system_prompt = template["system_prompt"]
                    tools = json.loads(template["tools_json"]) if template["tools_json"] else []
                    restrictions = json.loads(template["restrictions_json"]) if template["restrictions_json"] else {}
                    trace.append({"step": "template_loaded", "name": specialization})

            # Determine provider
            provider = "ollama"
            if _is_cloud(selected_model):
                provider = "cloud"
            elif selected_tier == "openrouter":
                provider = "openrouter"

            # ── Model Allowlist Gate ─────────────────────────────────
            if not is_model_allowed(selected_model, tier):
                trace.append({"step": "allowlist_blocked", "model": selected_model, "tier": tier})
                # Downshift to summarization model — re-check it too
                fallback_model = settings.summarization_model
                if is_model_allowed(fallback_model, tier):
                    selected_model = fallback_model
                    selected_tier = "allowlist_fallback"
                    trace.append({"step": "allowlist_fallback", "model": selected_model})
                else:
                    # Fallback also blocked — fail closed with clear error trace
                    trace.append({"step": "allowlist_fallback_also_blocked", "model": fallback_model})
                    # Pick the first model from the allowlist deterministically
                    allowed_set = _load_allowed_models(tier)
                    if allowed_set:
                        selected_model = sorted(allowed_set)[0]
                        selected_tier = "allowlist_deterministic"
                        trace.append({"step": "allowlist_deterministic", "model": selected_model})
                    else:
                        # Empty allowlist = fail-open (keep current model)
                        trace.append({"step": "allowlist_empty_failopen"})

            profile = AgentProfile(
                model=selected_model,
                provider=provider,
                tier=selected_tier or "D",
                system_prompt=system_prompt,
                tools=tools,
                restrictions=restrictions,
                routing_trace=trace,
                confidence=0.9 if selected_tier in ("S", "A+", "A") else 0.7 if selected_tier in ("A-", "B+") else 0.5,
            )
            _log_routing_decision(profile, intent_text, phase=selected_tier or "fallback")
            return profile

    except Exception as exc:
        # Registry query failed — graceful fallback
        legacy_model = route_intent(intent_text, ast)
        trace.append({"step": "fallback", "reason": f"registry_error: {exc}", "model": legacy_model})
        profile = AgentProfile(
            model=legacy_model,
            provider="cloud" if _is_cloud(legacy_model) else "ollama",
            routing_trace=trace,
            confidence=0.2,
        )
        _log_routing_decision(profile, intent_text, phase="error_fallback")
        return profile
