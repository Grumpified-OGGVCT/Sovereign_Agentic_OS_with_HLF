"""
OllamaDispatcher — Multi-provider inference engine for the Sovereign Gateway.

Routes inference requests to Ollama (local), OpenRouter (cloud relay), or
direct cloud endpoints based on the AgentProfile returned by the MoMA Router.

Features:
  - Async generate / chat / stream endpoints
  - Provider-aware URL routing (ollama vs openrouter vs cloud)
  - VRAM-aware model preloading (keep_alive)
  - Complexity scoring per RFC 9004 (𝕔)
  - Automatic downshift on model failure
  - ALS audit logging for every inference call
  - Web search mediation (strip web_search from Ollama, reroute to host fn)

References:
  - RFC 9004 § Application Synthesis / MoMA Router
  - RFC 9008 § Agentic Log Standard
  - Sovereign OS Master Build Plan § Infra Phase 4
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

try:
    from agents.core.logger import ALSLogger

    _logger = ALSLogger(agent_role="ollama-dispatcher", goal_id="inference")
except ImportError:
    _logger = None


# ─── Configuration ───────────────────────────────────────────────────────────

_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
_OLLAMA_SECONDARY = os.environ.get("OLLAMA_HOST_SECONDARY", "")
_OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_DEFAULT_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "120"))
_STREAM_TIMEOUT = float(os.environ.get("OLLAMA_STREAM_TIMEOUT", "300"))


# ─── Data Structures ────────────────────────────────────────────────────────


@dataclass
class InferenceRequest:
    """Unified inference request across all providers."""

    prompt: str
    model: str = ""
    system: str = ""
    stream: bool = False
    temperature: float = 0.7
    max_tokens: int = 4096
    context: list[int] | None = None
    format: str = ""  # "json" for structured output
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class InferenceResult:
    """Result of an inference call with full telemetry."""

    text: str
    model: str
    provider: str
    latency_ms: float
    tokens_eval: int = 0
    tokens_prompt: int = 0
    done: bool = True
    context: list[int] | None = None
    complexity_score: float = 0.0
    routing_trace: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "model": self.model,
            "provider": self.provider,
            "latency_ms": round(self.latency_ms, 2),
            "tokens": {
                "eval": self.tokens_eval,
                "prompt": self.tokens_prompt,
                "total": self.tokens_eval + self.tokens_prompt,
            },
            "done": self.done,
            "complexity_score": round(self.complexity_score, 3),
        }

    @property
    def log_hash(self) -> str:
        """SHA-256 hash of the response for Merkle log entry."""
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass
class StreamChunk:
    """Single chunk in a streaming inference response."""

    text: str
    done: bool = False
    model: str = ""
    tokens_eval: int = 0

    def to_sse(self) -> str:
        """Format as a Server-Sent Event line."""
        payload = json.dumps({"text": self.text, "done": self.done})
        return f"data: {payload}\n\n"


# ─── Complexity Scoring (RFC 9004 𝕔) ─────────────────────────────────────────


def complexity_score(intent_text: str, ast: dict | None = None) -> float:
    """
    Compute a 0.0-1.0 complexity score for an intent (RFC 9004).

    The score determines model tier selection:
      0.0 - 0.3 → SLM (summarization_model, 7B)
      0.3 - 0.6 → Medium (reasoning_model, 32B)
      0.6 - 1.0 → Frontier (primary_model, cloud)

    Factors:
      - Token count (word estimate)
      - AST node count and depth
      - Presence of concurrency/conditional/host-fn tags
      - Epistemic modifier density
    """
    score = 0.0

    # Factor 1: Token length (rough word count)
    words = len(intent_text.split())
    if words > 200:
        score += 0.3
    elif words > 50:
        score += 0.15
    else:
        score += 0.05

    if ast is None:
        return min(score, 1.0)

    program = ast.get("program", [])
    node_count = len(program)

    # Factor 2: AST node count
    if node_count > 10:
        score += 0.25
    elif node_count > 5:
        score += 0.15
    elif node_count > 0:
        score += 0.05

    # Factor 3: Complex tags (concurrency, conditionals, host functions)
    complex_tags = {"IF", "PARALLEL", "SPAWN", "WEB_SEARCH", "OPENCLAW_SUMMARIZE", "FUNCTION", "IMPORT"}
    found_complex = sum(1 for n in program if n.get("tag") in complex_tags)
    if found_complex >= 3:
        score += 0.25
    elif found_complex >= 1:
        score += 0.1

    # Factor 4: Epistemic modifiers (uncertainty requires reasoning)
    epistemic_count = sum(1 for n in program if any(k.startswith("epistemic") or k == "confidence" for k in n))
    if epistemic_count > 0:
        score += 0.1

    # Factor 5: Nested depth (struct operators, sub-programs)
    max_depth = _ast_depth(program)
    if max_depth > 3:
        score += 0.15
    elif max_depth > 1:
        score += 0.05

    return min(score, 1.0)


def _ast_depth(nodes: list, depth: int = 0) -> int:
    """Recursively compute max nesting depth of AST nodes."""
    if not nodes:
        return depth
    max_d = depth
    for node in nodes:
        if isinstance(node, dict):
            # Check for nested programs (FUNCTION bodies, IF blocks)
            for key in ("body", "then", "else", "program"):
                sub = node.get(key)
                if isinstance(sub, list):
                    max_d = max(max_d, _ast_depth(sub, depth + 1))
    return max_d


# ─── Provider Dispatch ───────────────────────────────────────────────────────


class OllamaDispatcher:
    """
    Multi-provider async inference dispatcher.

    Supports:
      - Ollama (local or remote): /api/generate, /api/chat
      - OpenRouter: /api/v1/chat/completions
      - Cloud (direct): configured via OLLAMA_HOST with :cloud suffix

    Automatic downshift: if the primary model fails, falls back to the
    summarization model.  ALS audit logging for every call.
    """

    def __init__(
        self,
        ollama_host: str = _OLLAMA_HOST,
        ollama_secondary: str = _OLLAMA_SECONDARY,
        openrouter_key: str = _OPENROUTER_API_KEY,
        timeout: float = _DEFAULT_TIMEOUT,
        stream_timeout: float = _STREAM_TIMEOUT,
        fallback_model: str = "qwen:7b",
        strategy: str = "",
    ):
        self.ollama_host = ollama_host.rstrip("/")
        self.ollama_secondary = ollama_secondary.rstrip("/") if ollama_secondary else ""
        self.openrouter_key = openrouter_key
        self.timeout = timeout
        self.stream_timeout = stream_timeout
        self.fallback_model = fallback_model
        self.strategy = strategy or os.environ.get("OLLAMA_LOAD_STRATEGY", "failover")
        self._rr_counter = 0

    def _get_ordered_hosts(self) -> list[str]:
        """Return host list ordered by strategy: failover, round_robin, or primary_only."""
        primary = self.ollama_host
        if not self.ollama_secondary:
            return [primary]

        secondary = self.ollama_secondary
        if self.strategy == "round_robin":
            self._rr_counter += 1
            if self._rr_counter % 2 == 0:
                return [primary, secondary]
            return [secondary, primary]
        elif self.strategy == "primary_only":
            return [primary]
        # default: failover (primary first, secondary if primary fails)
        return [primary, secondary]

    # ── Core Generate ─────────────────────────────────────────────────

    async def generate(self, req: InferenceRequest) -> InferenceResult:
        """
        Non-streaming inference call.  Routes by provider convention:
          - model ending in ':cloud' → cloud endpoint
          - provider == 'openrouter' → OpenRouter API
          - else → local Ollama /api/generate
        """
        t0 = time.time()
        provider = req.metadata.get("provider", "ollama")

        try:
            if provider == "openrouter":
                result = await self._openrouter_generate(req)
            else:
                result = await self._ollama_generate(req)

            result.latency_ms = (time.time() - t0) * 1000
            result.complexity_score = complexity_score(req.prompt, req.metadata.get("ast"))
            self._log_inference(req, result)
            return result

        except Exception as exc:
            # Automatic downshift on failure
            if req.model != self.fallback_model:
                self._log_downshift(req.model, self.fallback_model, str(exc))
                fallback_req = InferenceRequest(
                    prompt=req.prompt,
                    model=self.fallback_model,
                    system=req.system,
                    temperature=req.temperature,
                    max_tokens=req.max_tokens,
                    metadata={**req.metadata, "downshifted_from": req.model},
                )
                return await self.generate(fallback_req)
            raise

    async def _ollama_generate(self, req: InferenceRequest) -> InferenceResult:
        """Call Ollama /api/generate endpoint."""
        payload: dict[str, Any] = {
            "model": req.model,
            "prompt": req.prompt,
            "stream": False,
        }
        if req.system:
            payload["system"] = req.system
        if req.context:
            payload["context"] = req.context
        if req.format:
            payload["format"] = req.format
        if req.temperature != 0.7:
            payload["options"] = {"temperature": req.temperature}
        if req.max_tokens != 4096:
            payload.setdefault("options", {})["num_predict"] = req.max_tokens

        # Try hosts in strategy-determined order
        hosts = self._get_ordered_hosts()

        last_exc: Exception | None = None
        for host in hosts:
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(
                        f"{host}/api/generate",
                        json=payload,
                    )
                    resp.raise_for_status()
                    data = resp.json()

                return InferenceResult(
                    text=data.get("response", ""),
                    model=data.get("model", req.model),
                    provider="ollama",
                    latency_ms=0,  # filled by caller
                    tokens_eval=data.get("eval_count", 0),
                    tokens_prompt=data.get("prompt_eval_count", 0),
                    context=data.get("context"),
                )
            except Exception as exc:
                last_exc = exc
                continue

        raise RuntimeError(f"All Ollama hosts failed for model '{req.model}': {last_exc}")

    async def _openrouter_generate(self, req: InferenceRequest) -> InferenceResult:
        """Call OpenRouter chat completions API."""
        if not self.openrouter_key:
            raise RuntimeError("OpenRouter API key not configured")

        messages = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.append({"role": "user", "content": req.prompt})

        payload = {
            "model": req.model,
            "messages": messages,
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
        }

        headers = {
            "Authorization": f"Bearer {self.openrouter_key}",
            "HTTP-Referer": "https://sovereign-os.local",
            "X-Title": "Sovereign Agentic OS",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(_OPENROUTER_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        choice = data.get("choices", [{}])[0]
        usage = data.get("usage", {})

        return InferenceResult(
            text=choice.get("message", {}).get("content", ""),
            model=data.get("model", req.model),
            provider="openrouter",
            latency_ms=0,
            tokens_eval=usage.get("completion_tokens", 0),
            tokens_prompt=usage.get("prompt_tokens", 0),
        )

    # ── Streaming ─────────────────────────────────────────────────────

    async def generate_stream(self, req: InferenceRequest) -> AsyncIterator[StreamChunk]:
        """
        Stream inference response as chunks (for SSE endpoints).

        Yields StreamChunk objects that can be formatted as SSE events.
        Only supports Ollama provider (OpenRouter streaming uses different format).
        """
        provider = req.metadata.get("provider", "ollama")

        if provider == "openrouter":
            async for chunk in self._openrouter_stream(req):
                yield chunk
        else:
            async for chunk in self._ollama_stream(req):
                yield chunk

    async def _ollama_stream(self, req: InferenceRequest) -> AsyncIterator[StreamChunk]:
        """Stream from Ollama /api/generate with stream=true."""
        payload: dict[str, Any] = {
            "model": req.model,
            "prompt": req.prompt,
            "stream": True,
        }
        if req.system:
            payload["system"] = req.system
        if req.context:
            payload["context"] = req.context
        if req.temperature != 0.7:
            payload["options"] = {"temperature": req.temperature}

        async with (
            httpx.AsyncClient(timeout=self.stream_timeout) as client,
            client.stream(
                "POST",
                f"{self.ollama_host}/api/generate",
                json=payload,
            ) as response,
        ):
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                yield StreamChunk(
                    text=data.get("response", ""),
                    done=data.get("done", False),
                    model=data.get("model", req.model),
                    tokens_eval=data.get("eval_count", 0),
                )

                if data.get("done"):
                    break

    async def _openrouter_stream(self, req: InferenceRequest) -> AsyncIterator[StreamChunk]:
        """Stream from OpenRouter using SSE format."""
        if not self.openrouter_key:
            raise RuntimeError("OpenRouter API key not configured")

        messages = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.append({"role": "user", "content": req.prompt})

        payload = {
            "model": req.model,
            "messages": messages,
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
            "stream": True,
        }

        headers = {
            "Authorization": f"Bearer {self.openrouter_key}",
            "HTTP-Referer": "https://sovereign-os.local",
            "X-Title": "Sovereign Agentic OS",
        }

        async with (
            httpx.AsyncClient(timeout=self.stream_timeout) as client,
            client.stream("POST", _OPENROUTER_URL, json=payload, headers=headers) as response,
        ):
            response.raise_for_status()
            async for line in response.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    yield StreamChunk(text="", done=True, model=req.model)
                    break
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                delta = data.get("choices", [{}])[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    yield StreamChunk(
                        text=content,
                        done=False,
                        model=data.get("model", req.model),
                    )

    # ── Chat Interface ────────────────────────────────────────────────

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str = "",
        provider: str = "ollama",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> InferenceResult:
        """
        Multi-turn chat completion. Uses Ollama /api/chat or OpenRouter.
        """
        t0 = time.time()

        if provider == "openrouter":
            return await self._openrouter_chat(messages, model, temperature, max_tokens, t0)

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if temperature != 0.7:
            payload["options"] = {"temperature": temperature}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.ollama_host}/api/chat",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        return InferenceResult(
            text=data.get("message", {}).get("content", ""),
            model=data.get("model", model),
            provider="ollama",
            latency_ms=(time.time() - t0) * 1000,
            tokens_eval=data.get("eval_count", 0),
            tokens_prompt=data.get("prompt_eval_count", 0),
        )

    async def _openrouter_chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int,
        t0: float,
    ) -> InferenceResult:
        """OpenRouter chat completion."""
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.openrouter_key}",
            "HTTP-Referer": "https://sovereign-os.local",
            "X-Title": "Sovereign Agentic OS",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(_OPENROUTER_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        choice = data.get("choices", [{}])[0]
        usage = data.get("usage", {})

        return InferenceResult(
            text=choice.get("message", {}).get("content", ""),
            model=data.get("model", model),
            provider="openrouter",
            latency_ms=(time.time() - t0) * 1000,
            tokens_eval=usage.get("completion_tokens", 0),
            tokens_prompt=usage.get("prompt_tokens", 0),
        )

    # ── Model Management ──────────────────────────────────────────────

    async def preload_model(self, model: str) -> bool:
        """
        Preload a model into Ollama VRAM (keep_alive call).
        Returns True if successful, False on failure.
        """
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.ollama_host}/api/generate",
                    json={"model": model, "prompt": "", "keep_alive": "10m"},
                )
                return resp.status_code == 200
        except Exception:
            return False

    async def list_running(self) -> list[dict[str, Any]]:
        """List models currently loaded in Ollama VRAM."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.ollama_host}/api/ps")
                resp.raise_for_status()
                return resp.json().get("models", [])
        except Exception:
            return []

    # ── Audit Logging ─────────────────────────────────────────────────

    def _log_inference(self, req: InferenceRequest, result: InferenceResult) -> None:
        """Emit ALS INFERENCE event."""
        if _logger is None:
            return
        _logger.log(
            "INFERENCE",
            data={
                "model": result.model,
                "provider": result.provider,
                "latency_ms": round(result.latency_ms, 2),
                "tokens_eval": result.tokens_eval,
                "tokens_prompt": result.tokens_prompt,
                "complexity": round(result.complexity_score, 3),
                "response_hash": result.log_hash,
                "downshifted_from": req.metadata.get("downshifted_from", ""),
            },
        )

    def _log_downshift(self, from_model: str, to_model: str, reason: str) -> None:
        """Log when a model downshift occurs."""
        if _logger is None:
            return
        _logger.log(
            "MODEL_DOWNSHIFT",
            data={
                "from_model": from_model,
                "to_model": to_model,
                "reason": reason,
            },
            anomaly_score=0.5,
        )


# ─── Module-Level Convenience ────────────────────────────────────────────────

# Singleton dispatcher instance (initialized lazily)
_dispatcher: OllamaDispatcher | None = None


def get_dispatcher() -> OllamaDispatcher:
    """Get or create the singleton OllamaDispatcher instance."""
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = OllamaDispatcher()
    return _dispatcher
