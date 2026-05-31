"""
Ollama dual-endpoint configuration — single source of truth.

Centralises the environment-variable reads and endpoint-ordering logic that
was previously duplicated across ``agents/core/main.py`` and
``agents/core/hat_engine.py``.

Usage::

    from agents.core.ollama_config import get_ollama_endpoints

    for host, headers in get_ollama_endpoints():
        ...  # try the request
"""

from __future__ import annotations

import os
import threading

# ---------------------------------------------------------------------------
# Module-level constants (read once at import time, overrideable via env)
# ---------------------------------------------------------------------------

_OLLAMA_PRIMARY: str = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
_OLLAMA_SECONDARY: str = os.environ.get("OLLAMA_HOST_SECONDARY", "")
_OLLAMA_SECONDARY_KEY: str = os.environ.get("OLLAMA_API_KEY_SECONDARY", "")
_OLLAMA_STRATEGY: str = os.environ.get("OLLAMA_LOAD_STRATEGY", "failover")

# Normalise primary host: add scheme when missing.
# Replace "0.0.0.0" only when it is the entire host component to avoid
# incorrectly transforming subdomains such as "api.0.0.0.0.example.com".
if _OLLAMA_PRIMARY and not _OLLAMA_PRIMARY.startswith("http"):
    _OLLAMA_PRIMARY = f"http://{_OLLAMA_PRIMARY}"

_parsed_host = _OLLAMA_PRIMARY.removeprefix("http://").removeprefix("https://").split("/")[0].split(":")[0]
if _parsed_host == "0.0.0.0":
    _OLLAMA_PRIMARY = _OLLAMA_PRIMARY.replace("0.0.0.0", "localhost", 1)

# Round-robin state — intentionally module-global to persist across calls.
# Protected by a lock for thread-safe increment.
_rr_counter: int = 0
_rr_lock: threading.Lock = threading.Lock()


def get_ollama_endpoints() -> list[tuple[str, dict[str, str]]]:
    """Return an ordered list of ``(host, extra_headers)`` tuples.

    The order reflects the configured load-balancing strategy:

    * ``failover`` (default) — primary first, secondary appended as fallback.
    * ``round_robin`` — alternates which endpoint leads on successive calls.
    * ``primary_only`` — only the primary endpoint is returned.

    Callers should iterate over the returned list and stop at the first
    successful response, so that failover is handled uniformly.
    """
    global _rr_counter

    primary: tuple[str, dict[str, str]] = (_OLLAMA_PRIMARY, {})

    if not _OLLAMA_SECONDARY:
        return [primary]

    sec_headers: dict[str, str] = {}
    if _OLLAMA_SECONDARY_KEY:
        sec_headers["Authorization"] = f"Bearer {_OLLAMA_SECONDARY_KEY}"
    secondary: tuple[str, dict[str, str]] = (_OLLAMA_SECONDARY, sec_headers)

    if _OLLAMA_STRATEGY == "round_robin":
        with _rr_lock:
            _rr_counter += 1
            current = _rr_counter
        if current % 2 == 0:
            return [primary, secondary]
        return [secondary, primary]

    if _OLLAMA_STRATEGY == "primary_only":
        return [primary]

    # Default: failover — primary first, secondary as fallback
    return [primary, secondary]
