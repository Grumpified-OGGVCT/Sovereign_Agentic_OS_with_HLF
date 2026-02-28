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

import json
import os
import signal
import time
from pathlib import Path
from typing import Any

import httpx

from agents.core.logger import ALSLogger

_logger = ALSLogger(agent_role="agent-executor", goal_id="boot")
_SYSTEM_PROMPT_PATH = (
    Path(__file__).parent.parent.parent
    / "governance"
    / "templates"
    / "system_prompt.txt"
)


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
    signal.signal(getattr(signal, "SIGUSR1"), _handle_sigusr1)


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
    is_cloud = effective_model.endswith(":cloud") or openrouter_api or ollama_api_key

    if is_cloud:
        # Use OpenRouter as primary cloud bridge if available
        if openrouter_api:
            _logger.log("CLOUD_INFERENCE_START", {"provider": "openrouter", "model": effective_model})
            try:
                resp = httpx.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {openrouter_api}",
                        "X-Title": "Sovereign OS Autonomous Runner",
                    },
                    json={
                        "model": effective_model.replace(":cloud", ""),
                        "messages": [
                            {"role": "system", "content": _SYSTEM_PROMPT_PATH.read_text().strip() if _SYSTEM_PROMPT_PATH.exists() else ""},
                            {"role": "user", "content": text}
                        ],
                        "temperature": 0.0
                    },
                    timeout=60.0
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"].strip()
            except Exception as e:
                _logger.log("CLOUD_INFERENCE_ERROR", {"error": str(e)}, anomaly_score=0.5)
                # Fall through to local if cloud fails? No, if we are in cloud mode, we likely don't have local.

    # 2. Local Ollama Path
    ollama_host = os.environ.get("OLLAMA_HOST", "http://ollama-matrix:11434")
    system_prompt = ""
    if _SYSTEM_PROMPT_PATH.exists():
        system_prompt = _SYSTEM_PROMPT_PATH.read_text().strip()

    payload = {
        "model": effective_model.replace(":cloud", ""),
        "system": system_prompt,
        "prompt": text,
        "stream": False,
        "options": {"temperature": 0.0, "num_ctx": 2048},
    }
    try:
        resp = httpx.post(
            f"{ollama_host}/api/generate",
            json=payload,
            timeout=60.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", "").strip()
    except (httpx.RequestError, httpx.HTTPStatusError, PermissionError, OSError) as exc:
        if is_cloud:
            raise RuntimeError(f"Cloud inference failed and local Ollama unreachable: {exc}")
        raise RuntimeError(f"Ollama unavailable: {exc}") from exc


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
    from hlf.hlfrun import run as hlfrun
    from hlf.hlfc import compile as hlfc_compile, HlfSyntaxError

    tier = os.environ.get("DEPLOYMENT_TIER", "hearth")
    max_gas = int(os.environ.get("MAX_GAS_LIMIT", "10"))
    request_id = payload.get("request_id", "unknown")

    ast = payload.get("ast")

    if ast is None:
        # Text mode — call Ollama to generate HLF, then compile
        text = payload.get("text", "")
        if not text:
            return {"code": 1, "message": "no text or ast in payload", "gas_used": 0}
        try:
            hlf_response = _ollama_generate(text)
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
        try:
            r.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception:
            pass  # consumer group already exists

        _logger.log("CONSUMER_GROUP_READY", {"stream": stream, "group": group})

        while True:
            messages = r.xreadgroup(
                group, "executor-1", {stream: ">"}, count=1, block=5000
            )
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
        _logger.log(
            "AGENT_EXECUTOR_FATAL", {"error": str(exc)}, anomaly_score=1.0
        )


if __name__ == "__main__":
    run()
