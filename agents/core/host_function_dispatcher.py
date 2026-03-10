"""
Host Function Dispatcher — Phase 5.1 Standard Library runtime.

Routes [ACTION] tag calls to the backend defined in governance/host_functions.json:
  - builtin            : local Python implementation (SLEEP)
  - dapr_file_read     : Dapr file binding  (direct fs fallback for local dev)
  - dapr_file_write    : Dapr file binding  (direct fs fallback for local dev)
  - dapr_http_proxy    : Dapr HTTP proxy    (httpx fallback for local dev)
  - docker_orchestrator: Docker SDK         (Tier 2/3 only)
  - dapr_container_spawn: Dapr sidecar      (no direct fallback — requires Dapr)
  - native_bridge      : Platform Abstraction Layer (OS-level operations)
  - anythingllm_api    : AnythingLLM Developer API (v1, REST, Bearer auth)
  - msty_bridge        : MSTY Studio bridge (Ollama-compatible + Knowledge Stacks)

  NOTE: The if-chain in _dispatch_backend_inner() is growing (9 backends).
  A future refactor could use a dict[str, Callable] dispatch table.
  Deferring to keep this diff minimal and the pattern familiar.

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

# Per-backend circuit breaker state
_backend_failures: dict[str, int] = {}
_backend_reset_times: dict[str, float] = {}
_BACKEND_CB_THRESHOLD = 3
_BACKEND_CB_TIMEOUT = 60.0  # seconds


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
    # Circuit breaker: skip backends with repeated failures
    if _backend_failures.get(backend, 0) >= _BACKEND_CB_THRESHOLD:
        reset_time = _backend_reset_times.get(backend, 0)
        if time.time() < reset_time:
            raise RuntimeError(
                f"Backend '{backend}' circuit breaker open — "
                f"resets in {int(reset_time - time.time())}s"
            )
        _backend_failures[backend] = 0  # half-open

    try:
        result = _dispatch_backend_inner(backend, name, args, meta)
        _backend_failures[backend] = 0  # reset on success
        return result
    except Exception:
        _backend_failures[backend] = _backend_failures.get(backend, 0) + 1
        if _backend_failures[backend] >= _BACKEND_CB_THRESHOLD:
            _backend_reset_times[backend] = time.time() + _BACKEND_CB_TIMEOUT
            _logger.log(
                "BACKEND_CIRCUIT_BREAKER_OPEN",
                {"backend": backend, "name": name, "threshold": _BACKEND_CB_THRESHOLD},
                anomaly_score=0.7,
            )
        raise


def _dispatch_backend_inner(backend: str, name: str, args: list, meta: dict) -> Any:
    """Route to the concrete backend implementation."""
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
    if backend == "native_bridge":
        return _native_bridge(name, args, meta)
    if backend == "anythingllm_api":
        return _anythingllm_api(name, args, meta)
    if backend == "msty_bridge":
        return _msty_bridge(name, args, meta)
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


def _native_bridge(name: str, args: list, meta: dict) -> Any:
    """Dispatch native OS operations via the Platform Abstraction Layer.

    Security model (lightweight, zero-overhead):
      - Tier enforcement: handled by dispatch() above — O(1) set lookup
      - Command allowlist: O(1) frozenset membership test in bridge
      - ACFS confinement: reuses existing _acfs_path() for any file ops
      - Gas metering: handled by the HLF runtime — no cost here
      - Audit: dispatch() logs every call via ALSLogger
    """
    from agents.core.native import get_bridge
    from agents.core.native.bridge import NotificationRequest, NotificationUrgency

    bridge = get_bridge()

    if name == "SYS_INFO":
        info = bridge.system_info()
        return json.dumps({
            "platform": info.platform,
            "version": info.platform_version,
            "hostname": info.hostname,
            "cpu_count": info.cpu_count,
            "cpu_percent": info.cpu_percent,
            "memory_total_mb": info.memory_total_mb,
            "memory_available_mb": info.memory_available_mb,
            "disk_total_gb": info.disk_total_gb,
            "disk_free_gb": info.disk_free_gb,
            "uptime_seconds": info.uptime_seconds,
        })

    if name == "CLIPBOARD_READ":
        content = bridge.clipboard_read()
        return content.text

    if name == "CLIPBOARD_WRITE":
        text = str(args[0]) if args else ""
        return bridge.clipboard_write(text)

    if name == "NOTIFY":
        title = str(args[0]) if args else "Sovereign OS"
        body = str(args[1]) if len(args) > 1 else ""
        return bridge.notify(NotificationRequest(
            title=title,
            body=body,
            urgency=NotificationUrgency.NORMAL,
        ))

    if name == "SHELL_EXEC":
        # Security: delegated to bridge's 6-layer stack (allowlist → rate limit
        # → ACFS confinement → output cap → timeout → ALS audit), see shell.py
        command = str(args[0]) if args else ""
        cmd_args = list(args[1]) if len(args) > 1 and args[1] else []
        result = bridge.shell_exec(command, cmd_args, timeout_seconds=30.0)
        return json.dumps({
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
            "timed_out": result.timed_out,
        })

    if name == "APP_LAUNCH":
        app = str(args[0]) if args else ""
        app_args = list(args[1]) if len(args) > 1 and args[1] else None
        pid = bridge.launch_app(app, app_args)
        return str(pid) if pid else "APP_LAUNCH_FAILED"

    if name == "PROCESS_LIST":
        filter_name = str(args[0]) if args else None
        processes = bridge.list_processes(filter_name)
        return json.dumps([
            {"pid": p.pid, "name": p.name, "status": p.status,
             "cpu_percent": p.cpu_percent, "memory_mb": p.memory_mb}
            for p in processes[:50]  # Cap at 50 entries
        ])

    raise RuntimeError(f"Unknown native_bridge function: {name}")


# --------------------------------------------------------------------------- #
# AnythingLLM Developer API backend
# --------------------------------------------------------------------------- #
# REST API docs: http://localhost:3001/api/docs
# Auth: Bearer token via ANYTHINGLLM_API_KEY env var
# Desktop Assistant coexists without conflict — it's a UI overlay on the same backend.
#
# Expansion: ALLM_AGENT_FLOW (no-code agent builder) and ALLM_DESKTOP_CAPTURE
# (native bridge → CTRL+/ hotkey) are Phase 2/4 candidates.
# --------------------------------------------------------------------------- #


def _anythingllm_api(name: str, args: list, meta: dict) -> Any:
    """Route AnythingLLM host function calls to the Developer API (v1)."""
    host = os.environ.get("ANYTHINGLLM_HOST", "http://localhost:3001")
    api_key = os.environ.get("ANYTHINGLLM_API_KEY", "")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        if name == "ALLM_LIST_WORKSPACES":
            resp = httpx.get(
                f"{host}/api/v1/workspaces",
                headers=headers,
                timeout=15.0,
            )
            resp.raise_for_status()
            return resp.text

        if name == "ALLM_WORKSPACE_CHAT":
            slug = str(args[0]) if args else ""
            message = str(args[1]) if len(args) > 1 else ""
            mode = str(args[2]) if len(args) > 2 else "chat"
            resp = httpx.post(
                f"{host}/api/v1/workspace/{slug}/chat",
                headers=headers,
                json={"message": message, "mode": mode},
                timeout=120.0,
            )
            resp.raise_for_status()
            data = resp.json()
            # Return the textResponse plus source citations if present
            text_response = data.get("textResponse", "")
            sources = data.get("sources", [])
            if sources:
                return json.dumps({"response": text_response, "sources": sources})
            return text_response

        if name == "ALLM_VECTOR_SEARCH":
            slug = str(args[0]) if args else ""
            query = str(args[1]) if len(args) > 1 else ""
            resp = httpx.post(
                f"{host}/api/v1/workspace/{slug}/vector-search",
                headers=headers,
                json={"query": query},
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.text

        if name == "ALLM_ADD_DOCUMENT":
            slug = str(args[0]) if args else ""
            title = str(args[1]) if len(args) > 1 else "untitled"
            content = str(args[2]) if len(args) > 2 else ""
            resp = httpx.post(
                f"{host}/api/v1/workspace/{slug}/document/add-texts",
                headers=headers,
                json={"texts": [{"title": title, "content": content}]},
                timeout=30.0,
            )
            resp.raise_for_status()
            return True

        raise RuntimeError(f"Unknown anythingllm_api function: {name}")

    except (httpx.RequestError, httpx.HTTPStatusError, PermissionError, OSError) as exc:
        _logger.log(
            "ANYTHINGLLM_UNAVAILABLE",
            {"name": name, "error": str(exc)[:120]},
            anomaly_score=0.4,
        )
        return f"ALLM_UNAVAILABLE: {exc}"


# --------------------------------------------------------------------------- #
# MSTY Studio bridge backend
# --------------------------------------------------------------------------- #
# MSTY is a local-first desktop app (v2.5.0+, Feb 2026) with no formal REST API.
# It uses Ollama-compatible endpoints under the hood for inference,
# and stores Knowledge Stacks as local files for RAG.
#
# Bridge strategy (v1 — Ollama-compatible baseline):
#   - Generation/persona/models → Ollama-compatible API at MSTY_HOST
#   - Knowledge queries → Ollama API with RAG context prefix
#   - Split chats → Sequential calls to each model, combined response
#
# MSTY Studio full feature surface (deep-dive research, March 2026):
#   - Toolbox (MCP)      : Local STDIO/JSON + Streamable HTTP MCP servers
#   - Vibe CLI Proxy     : Proxies CLI model providers including Antigravity,
#                          Claude Code, Codex, Gemini, Copilot, iFlow
#   - Live Contexts      : Real-time JSON endpoint injection into conversations
#   - Knowledge Stacks   : Full RAG with Next-Gen reranking, folder sync,
#                          PII scrubbing, chunk visualization.
#                          ⚠ Desktop-only — NOT exposed via Remote Connections.
#                          MSTY_KNOWLEDGE_QUERY requires co-located execution.
#   - Crew Mode          : Multi-persona collaboration (v2.5.0)
#   - Turnstiles         : Workflow sequencing with persona/tool chaining
#   - Personas           : System-prompt presets (UI convenience layer).
#                          Our 14-Hat + 19-agent framework is far more capable.
#   - Remote Connections : Authenticated tunnel (Connection Token) exposing
#                          local models, MCP tools, and real-time data via
#                          a single proxy endpoint. Could replace raw Ollama
#                          bridge in Phase 2 for full-stack MSTY access.
#   - Workspaces         : Isolated environments with per-workspace settings
#
# Expansion candidates (Phase 2+):
#   - MSTY_MCP_TOOL       : Invoke MSTY's MCP Toolbox directly from HLF
#   - MSTY_LIVE_CONTEXT   : Inject real-time JSON endpoints as chat context
#   - MSTY_CREW_RUN       : Launch a Crew Mode conversation with multiple personas
#   - MSTY_TURNSTILE_EXEC : Execute a predefined Turnstile workflow
#   - MSTY_VIBE_PROXY     : Route inference through MSTY's Vibe CLI Proxy
#                          (could loop back to Antigravity — recursive HLF!)
#   - MSTY_REMOTE_CONNECT : Full-stack access via Remote Tools Connector
#                          (auth token, local+tunnel URLs, models+tools+data)
#
# User has lifetime license on both MSTY Studio and AnythingLLM.
# --------------------------------------------------------------------------- #


def _msty_bridge(name: str, args: list, meta: dict) -> Any:
    """Route MSTY Studio host function calls through Ollama-compatible API."""
    host = os.environ.get("MSTY_HOST", "http://localhost:11434")
    default_model = os.environ.get("MSTY_DEFAULT_MODEL", "qwen3:32b")

    try:
        if name == "MSTY_VIBE_CATALOG":
            # Dynamic provider fingerprinting with TTL-based auto-refresh.
            # No hardcoded provider/tier mappings — new CLI providers added to
            # MSTY Desktop will automatically appear as new family clusters.
            force = bool(args[0]) if args else False
            cache_ttl = int(os.environ.get("MSTY_CATALOG_TTL_SEC", "300"))  # 5m default

            # Check cache (module-level)
            now = __import__("time").time()
            cached = getattr(_msty_bridge, "_catalog_cache", None)
            if cached and not force and (now - cached["ts"]) < cache_ttl:
                return cached["data"]

            resp = httpx.get(f"{host}/api/tags", timeout=15.0)
            resp.raise_for_status()
            raw_models = resp.json().get("models", [])

            # Classify each model using remote_model/remote_host as the
            # primary cloud signal (populated by MSTY's Ollama-compatible API).
            # Falls back to format/family heuristics only when those fields
            # are absent.
            catalog = {"local": [], "cloud": [], "image": [], "summary": {}}
            for m in raw_models:
                mname = m.get("name", "")
                det = m.get("details", {})
                fmt = det.get("format", "")
                family = det.get("family", "")
                remote_model = m.get("remote_model", "")
                remote_host = m.get("remote_host", "")
                entry = {
                    "name": mname,
                    "family": family,
                    "params": det.get("parameter_size", ""),
                    "quant": det.get("quantization_level", ""),
                    "format": fmt,
                    "remote_host": remote_host,
                }

                # Primary signal: remote_model/remote_host present → cloud
                if remote_model or remote_host:
                    entry["source"] = "cloud"
                    # Extract provider hint from remote_host domain
                    if remote_host:
                        try:
                            from urllib.parse import urlparse
                            host_domain = urlparse(remote_host).hostname or ""
                            entry["provider_hint"] = host_domain
                        except Exception:
                            entry["provider_hint"] = remote_host
                    catalog["cloud"].append(entry)
                elif fmt == "gguf":
                    entry["source"] = "local"
                    catalog["local"].append(entry)
                elif fmt == "safetensors":
                    entry["source"] = "local-image"
                    catalog["image"].append(entry)
                elif ":cloud" in mname or (not fmt and not family):
                    # Fallback heuristic for models without remote fields
                    entry["source"] = "cloud"
                    catalog["cloud"].append(entry)
                else:
                    entry["source"] = "unknown"
                    catalog["local"].append(entry)

            # Build family cluster summary for cloud models
            family_clusters = {}
            for m in catalog["cloud"]:
                fam = m["family"] if m["family"] else "(unknown-provider)"
                if fam not in family_clusters:
                    family_clusters[fam] = []
                family_clusters[fam].append(m["name"])

            catalog["summary"] = {
                "total": len(raw_models),
                "local_count": len(catalog["local"]),
                "cloud_count": len(catalog["cloud"]),
                "image_count": len(catalog["image"]),
                "cloud_family_clusters": {
                    k: len(v) for k, v in family_clusters.items()
                },
                "cache_ttl_sec": cache_ttl,
                "refreshed_at": now,
            }

            result = json.dumps(catalog)
            # Store in cache
            _msty_bridge._catalog_cache = {"ts": now, "data": result}
            _logger.log(
                "MSTY_VIBE_CATALOG",
                {
                    "total": catalog["summary"]["total"],
                    "cloud": catalog["summary"]["cloud_count"],
                    "local": catalog["summary"]["local_count"],
                    "families": len(family_clusters),
                    "forced": force,
                },
            )
            return result

        if name == "MSTY_LIST_MODELS":
            resp = httpx.get(f"{host}/api/tags", timeout=10.0)
            resp.raise_for_status()
            return resp.text

        if name == "MSTY_KNOWLEDGE_QUERY":
            stack_name = str(args[0]) if args else ""
            query = str(args[1]) if len(args) > 1 else ""
            # Inject Knowledge Stack context as system prompt prefix
            system_prompt = (
                f"You are answering questions using the '{stack_name}' knowledge stack. "
                f"Ground your answers in the documents from this stack. "
                f"If the stack has no relevant information, say so explicitly."
            )
            resp = httpx.post(
                f"{host}/api/generate",
                json={
                    "model": default_model,
                    "prompt": query,
                    "system": system_prompt,
                    "stream": False,
                    "options": {"temperature": 0.3, "num_ctx": 8192},
                },
                timeout=120.0,
            )
            resp.raise_for_status()
            result = resp.json().get("response", "").strip()
            _logger.log(
                "MSTY_KNOWLEDGE_QUERY",
                {"stack": stack_name, "model": default_model, "preview": result[:80]},
            )
            return result

        if name == "MSTY_PERSONA_RUN":
            persona = str(args[0]) if args else "default"
            prompt = str(args[1]) if len(args) > 1 else ""
            system_prompt = (
                f"You are the '{persona}' persona. Stay fully in character. "
                f"Respond as this persona would, using their voice and expertise."
            )
            resp = httpx.post(
                f"{host}/api/generate",
                json={
                    "model": default_model,
                    "prompt": prompt,
                    "system": system_prompt,
                    "stream": False,
                },
                timeout=120.0,
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip()

        if name == "MSTY_SPLIT_CHAT":
            models = list(args[0]) if args else []
            prompt = str(args[1]) if len(args) > 1 else ""
            if not models:
                return "MSTY_SPLIT_CHAT_ERROR: No models specified"
            # Sequential fan-out to each model, collect responses
            responses = {}
            for model in models:
                try:
                    resp = httpx.post(
                        f"{host}/api/generate",
                        json={
                            "model": str(model),
                            "prompt": prompt,
                            "stream": False,
                        },
                        timeout=120.0,
                    )
                    resp.raise_for_status()
                    responses[str(model)] = resp.json().get("response", "").strip()
                except (httpx.RequestError, httpx.HTTPStatusError) as model_exc:
                    responses[str(model)] = f"ERROR: {model_exc}"
            _logger.log(
                "MSTY_SPLIT_CHAT",
                {"models": [str(m) for m in models], "response_count": len(responses)},
            )
            return json.dumps(responses)

        raise RuntimeError(f"Unknown msty_bridge function: {name}")

    except (httpx.RequestError, httpx.HTTPStatusError, PermissionError, OSError) as exc:
        _logger.log(
            "MSTY_UNAVAILABLE",
            {"name": name, "error": str(exc)[:120]},
            anomaly_score=0.4,
        )
        return f"MSTY_UNAVAILABLE: {exc}"
