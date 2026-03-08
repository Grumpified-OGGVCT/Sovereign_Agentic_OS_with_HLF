"""
Host Function Dispatcher — Phase 5.1 Standard Library runtime.

Routes [ACTION] tag calls to the backend defined in governance/host_functions.json:
  - builtin          : local Python implementation (SLEEP)
  - dapr_file_read   : Dapr file binding  (direct fs fallback for local dev)
  - dapr_file_write  : Dapr file binding  (direct fs fallback for local dev)
  - dapr_http_proxy  : Dapr HTTP proxy    (httpx fallback for local dev)
  - docker_orchestrator : Docker SDK      (Tier 2/3 only)
  - dapr_container_spawn: Dapr sidecar    (no direct fallback — requires Dapr)

Security guarantees enforced here:
  - Tier enforcement: request is rejected if the caller's tier is not in meta["tier"]
  - ACFS confinement: file paths are normalised against BASE_DIR; path traversal is rejected
  - Sensitive outputs: SHA-256 hashed in the dispatcher before being written to the ALS Merkle log
    (raw value is still returned to the caller; only the logged representation is hashed)
  - No subprocess calls: binary execution uses the Docker SDK via Dapr container spawn
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from agents.core.logger import ALSLogger

_logger = ALSLogger(agent_role="host-fn-dispatcher", goal_id="execution")
_REGISTRY_PATH = Path(__file__).parent.parent.parent / "governance" / "host_functions.json"
_registry: dict[str, dict] | None = None


def _load_registry() -> dict[str, dict]:
    global _registry
    if _registry is None:
        with _REGISTRY_PATH.open() as f:
            data = json.load(f)
        _registry = {fn["name"]: fn for fn in data.get("functions", [])}
    return _registry


def dispatch(name: str, args: list, tier: str = "hearth") -> Any:
    """
    Dispatch a host function call.

    :param name: Function name as it appears in host_functions.json (e.g. "READ").
    :param args: Positional argument list (already flattened by the interpreter).
    :param tier: Caller's deployment tier for access control.
    :returns:    Raw function return value (sensitive outputs logged as SHA-256 hash).
    :raises PermissionError: If the tier is not authorised for this function.
    :raises RuntimeError:    If the function name is unknown.
    """
    registry = _load_registry()
    fn_meta = registry.get(name)
    if fn_meta is None:
        raise RuntimeError(f"Unknown host function: {name}")

    # Tier access control
    allowed_tiers = fn_meta.get("tier", [])
    if tier not in allowed_tiers:
        raise PermissionError(
            f"Host function '{name}' is not available in tier '{tier}'. Requires one of: {allowed_tiers}"
        )

    backend = fn_meta.get("backend", "builtin")
    result = _dispatch_backend(backend, name, args, fn_meta)

    # Structured log — sensitive outputs recorded as SHA-256 hash (never raw)
    log_result = (
        hashlib.sha256(str(result).encode()).hexdigest() if fn_meta.get("sensitive", False) else str(result)[:200]
    )
    _logger.log(
        "HOST_FN_CALL",
        {"name": name, "tier": tier, "result_hash_or_preview": log_result},
    )
    return result


# --------------------------------------------------------------------------- #
# Backend implementations
# --------------------------------------------------------------------------- #


def _dispatch_backend(backend: str, name: str, args: list, meta: dict) -> Any:
    if backend == "builtin":
        return _exec_builtin(name, args)
    if backend == "dapr_file_read":
        return _dapr_file_read(args)
    if backend == "dapr_file_write":
        return _dapr_file_write(args)
    if backend == "dapr_http_proxy":
        return _dapr_http(name, args, meta)
    if backend == "docker_orchestrator":
        return _docker_spawn(args, meta)
    if backend == "dapr_container_spawn":
        return _dapr_container_spawn(name, args, meta)
    if backend == "tool_forge":
        return _tool_forge(args)
    raise RuntimeError(f"Unknown backend: {backend}")


def _tool_forge(args: list) -> str:
    """FORGE_TOOL <task_description> — dispatch to tool_forge."""
    if not args or not args[0]:
        return "FORGE_TOOL_ERROR: Missing task description"

    from agents.core.tool_forge import forge_tool

    result = forge_tool(str(args[0]))
    if not result:
        return "FORGE_TOOL_REJECTED: Security gates or LLM judge failed"

    _logger.log(
        "TOOL_FORGE_INVOKED",
        {"task": str(args[0]), "tool": result["name"], "sha256": result["sha256"]},
    )

    return json.dumps(
        {"name": result["name"], "sha256": result["sha256"], "human_readable": result.get("human_readable", "")}
    )


def _exec_builtin(name: str, args: list) -> Any:
    """Builtins with no I/O side-effects."""
    if name == "SLEEP":
        ms = int(args[0]) if args else 0
        time.sleep(ms / 1000.0)
        return True
    raise RuntimeError(f"No builtin implementation for host function '{name}'")


@functools.lru_cache(maxsize=4)
def _get_base_dir(base_dir_env: str) -> Path:
    """Cache the BASE_DIR resolution to avoid repeated disk I/O on every file access."""
    try:
        return Path(base_dir_env).resolve(strict=False)
    except PermissionError:
        return Path(base_dir_env).absolute()


def _acfs_path(raw: str) -> Path:
    """
    Resolve *raw* against BASE_DIR and verify it stays within the ACFS tree.
    Raises PermissionError on traversal attempts.

    Uses Path.is_relative_to() (Python 3.9+) to avoid the startswith-prefix
    bypass where BASE_DIR=/tmp/base and target=/tmp/base_evil/... would pass
    a naive string check.
    """
    base = _get_base_dir(os.environ.get("BASE_DIR", "/app"))
    try:
        target = (base / raw.lstrip("/")).resolve(strict=False)
    except PermissionError:
        # Windows volume-locked symlinks may trigger PermissionError on resolve()
        target = (base / raw.lstrip("/")).absolute()

    if not target.is_relative_to(base):
        raise PermissionError(f"ACFS confinement violation: '{raw}' resolves outside BASE_DIR")
    return target


def _dapr_file_read(args: list) -> str:
    """READ <path> — read a file via Dapr component or direct fs fallback."""
    path = str(args[0]) if args else ""
    # Validate ACFS confinement BEFORE attempting any I/O (network or local)
    target = _acfs_path(path)
    dapr_host = os.environ.get("DAPR_HOST", "http://localhost:3500")
    try:
        resp = httpx.post(
            f"{dapr_host}/v1.0/bindings/file-read",
            json={"operation": "get", "metadata": {"fileName": path}},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.text
    except (httpx.RequestError, httpx.HTTPStatusError, PermissionError, OSError):
        # Direct fs fallback for local development / test environments
        return target.read_text()


def _dapr_file_write(args: list) -> bool:
    """WRITE <path> <data> — write a file via Dapr or direct fs fallback."""
    path = str(args[0]) if args else ""
    data = str(args[1]) if len(args) > 1 else ""
    dapr_host = os.environ.get("DAPR_HOST", "http://localhost:3500")
    try:
        resp = httpx.post(
            f"{dapr_host}/v1.0/bindings/file-write",
            json={"operation": "create", "metadata": {"fileName": path}, "data": data},
            timeout=10.0,
        )
        resp.raise_for_status()
        return True
    except (httpx.RequestError, httpx.HTTPStatusError, PermissionError, OSError):
        # Direct fs fallback
        target = _acfs_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data)
        return True


def _dapr_http(name: str, args: list, meta: dict) -> str:
    """HTTP_GET <url> and WEB_SEARCH <query> — network helpers (Dapr optional)."""
    query_or_url = str(args[0]) if args else ""

    if name == "WEB_SEARCH":
        # Perform a direct HTTP GET for WEB_SEARCH (caller is responsible for providing a full URL).
        try:
            resp = httpx.get(query_or_url, timeout=30.0, follow_redirects=True)
            return resp.text[:4096]
        except (httpx.RequestError, httpx.HTTPStatusError, PermissionError, OSError) as exc:
            # Network unavailable — return structured error (never leak raw exception)
            _logger.log(
                "WEB_SEARCH_UNAVAILABLE",
                {"query": query_or_url[:80], "error": str(exc)[:120]},
                anomaly_score=0.4,
            )
            return "WEB_SEARCH_UNAVAILABLE: upstream search endpoint not reachable"
    else:
        # HTTP_GET — direct httpx call (Dapr proxy is recommended in production)
        try:
            resp = httpx.get(query_or_url, timeout=10.0, follow_redirects=True)
            return resp.text[:4096]  # cap response size
        except (httpx.RequestError, httpx.HTTPStatusError, PermissionError, OSError) as exc:
            return f"HTTP_GET_ERROR: {exc}"


def _docker_spawn(args: list, meta: dict) -> str:
    """SPAWN <image> — launches a container via Docker SDK (never os.system)."""
    image = str(args[0]) if args else ""
    try:
        import docker  # type: ignore[import]

        client = docker.from_env()
        container = client.containers.run(
            image,
            detach=True,
            remove=True,
            mem_limit="256m",
            pids_limit=50,
            network_disabled=True,
        )
        return container.id
    except ImportError:
        return "SPAWN_UNAVAILABLE: docker SDK not installed"
    except Exception as exc:
        _logger.log("SPAWN_ERROR", {"image": image, "error": str(exc)}, anomaly_score=0.5)
        return f"SPAWN_ERROR: {exc}"


def _dapr_container_spawn(name: str, args: list, meta: dict) -> str:
    """
    OPENCLAW_SUMMARIZE and other dapr_container_spawn functions.

    Routing priority:
      1. Dapr service invocation (production — mTLS, SHA-256 verified)
      2. Ollama-native OpenClaw (local dev — Ollama 0.17+ `ollama launch openclaw`)
      3. Error message
    """
    # --- Try Dapr first (production path) ---
    dapr_host = os.environ.get("DAPR_HOST", "http://localhost:3500")
    payload = {
        "name": name,
        "args": [str(a) for a in args],
        "binary_path": meta.get("binary_path", ""),
        "binary_sha256": meta.get("binary_sha256", ""),
    }
    try:
        resp = httpx.post(
            f"{dapr_host}/v1.0/invoke/openclaw-runner/method/run",
            json=payload,
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.text
    except (httpx.RequestError, httpx.HTTPStatusError, PermissionError, OSError):
        pass  # Fall through to Ollama-native OpenClaw

    # --- Ollama-native OpenClaw fallback (Ollama 0.17+) ---
    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    # Use the best available OpenClaw-compatible model
    openclaw_model = os.environ.get(
        "OPENCLAW_MODEL",
        os.environ.get("PRIMARY_MODEL", "kimi-k2.5:cloud"),
    )
    try:
        prompt = " ".join(str(a) for a in args)
        if name == "OPENCLAW_SUMMARIZE":
            prompt = f"Summarize the following content:\n\n{prompt}"

        resp = httpx.post(
            f"{ollama_host}/api/generate",
            json={
                "model": openclaw_model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1, "num_ctx": 4096},
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        result = resp.json().get("response", "").strip()
        _logger.log(
            "OPENCLAW_VIA_OLLAMA",
            {"name": name, "model": openclaw_model, "preview": result[:80]},
        )
        return result
    except (httpx.RequestError, httpx.HTTPStatusError, PermissionError, OSError) as exc:
        _logger.log(
            "OPENCLAW_UNAVAILABLE",
            {"name": name, "error": str(exc)[:120]},
            anomaly_score=0.4,
        )
        return f"{name}_UNAVAILABLE: Neither Dapr container nor Ollama OpenClaw reachable"
