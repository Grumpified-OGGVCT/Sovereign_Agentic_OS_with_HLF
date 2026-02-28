"""
Sovereign OS MCP Server — Bridge between Antigravity and the Sovereign Agentic OS.

Exposes core OS capabilities as MCP tools that Antigravity can call directly:
  - Health checks (Gateway, Redis, Ollama)
  - Intent dispatch through the Gateway Bus
  - Dream Mode triggering and result retrieval
  - Hat findings viewer
  - ALIGN rule management
  - System state overview
  - Memory/fact store queries (RAG bridge)

Usage:
  uv run mcp/sovereign_mcp_server.py

Config (add to ~/.gemini/antigravity/mcp_config.json):
  {
    "sovereign-os": {
      "command": "uv",
      "args": ["--directory", "<project-path>", "run", "mcp/sovereign_mcp_server.py"],
      "env": {}
    }
  }
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.request
import urllib.error
from dataclasses import asdict
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Server init
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "sovereign-os",
    version="0.1.0",
    description="Sovereign Agentic OS — MCP bridge for health, intent dispatch, "
                "Dream Mode, Hat analysis, ALIGN governance, and memory queries.",
)

_BASE_DIR = Path(os.environ.get(
    "BASE_DIR",
    str(Path(__file__).resolve().parent.parent)
))
_GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:40404")
_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
if _OLLAMA_HOST and not _OLLAMA_HOST.startswith("http"):
    _OLLAMA_HOST = f"http://{_OLLAMA_HOST}"
if "0.0.0.0" in _OLLAMA_HOST:
    _OLLAMA_HOST = _OLLAMA_HOST.replace("0.0.0.0", "localhost")

_DB_PATH = _BASE_DIR / "data" / "sqlite" / "memory.db"


def _get_db() -> sqlite3.Connection | None:
    """Open the memory DB if it exists."""
    if _DB_PATH.exists():
        conn = sqlite3.connect(str(_DB_PATH))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn
    return None


def _http_get(url: str, timeout: float = 5.0) -> dict | None:
    """Quick HTTP GET returning JSON or None."""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _http_post(url: str, payload: dict, timeout: float = 10.0) -> dict | None:
    """Quick HTTP POST returning JSON or None."""
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


# ===========================================================================
# MCP Tools
# ===========================================================================

@mcp.tool()
def check_health() -> dict:
    """
    Check the health of all Sovereign OS services: Gateway Bus, Redis, and Ollama.
    Returns status (online/offline) for each service with response times.
    """
    results = {}

    # Gateway Bus
    t0 = time.time()
    gw = _http_get(f"{_GATEWAY_URL}/health")
    results["gateway_bus"] = {
        "status": "online" if gw and gw.get("status") == "ok" else "offline",
        "url": _GATEWAY_URL,
        "response_ms": round((time.time() - t0) * 1000),
    }

    # Ollama
    t0 = time.time()
    ollama = _http_get(f"{_OLLAMA_HOST}/api/tags")
    model_count = len(ollama.get("models", [])) if ollama else 0
    results["ollama"] = {
        "status": "online" if ollama else "offline",
        "url": _OLLAMA_HOST,
        "model_count": model_count,
        "response_ms": round((time.time() - t0) * 1000),
    }

    # Redis
    try:
        import redis
        pw = os.environ.get("REDIS_PASSWORD", "")
        r = redis.Redis(host="localhost", port=6379, password=pw or None, decode_responses=True)
        t0 = time.time()
        r.ping()
        results["redis"] = {
            "status": "online",
            "response_ms": round((time.time() - t0) * 1000),
        }
    except Exception as e:
        results["redis"] = {"status": "offline", "error": str(e)}

    # DB
    conn = _get_db()
    if conn:
        try:
            facts = conn.execute("SELECT COUNT(*) FROM fact_store").fetchone()[0]
            context = conn.execute("SELECT COUNT(*) FROM rolling_context").fetchone()[0]
            results["memory_db"] = {
                "status": "online",
                "fact_count": facts,
                "context_rows": context,
            }
        except Exception:
            results["memory_db"] = {"status": "schema_missing"}
        finally:
            conn.close()
    else:
        results["memory_db"] = {"status": "no_database", "path": str(_DB_PATH)}

    return results


@mcp.tool()
def dispatch_intent(text: str) -> dict:
    """
    Dispatch a text or HLF intent through the Gateway Bus.
    The intent passes through the full middleware chain:
    rate limiter → HLF linter → ALIGN enforcer → ULID nonce → Redis stream.

    Args:
        text: Plain English text OR an HLF program (starting with [HLF-v3]).
    """
    is_hlf = text.strip().startswith("[HLF")
    payload = {"hlf": text} if is_hlf else {"text": text}
    result = _http_post(f"{_GATEWAY_URL}/api/v1/intent", payload, timeout=15.0)
    return result if result else {"error": f"Gateway unreachable at {_GATEWAY_URL}"}


@mcp.tool()
def run_dream_cycle() -> dict:
    """
    Trigger a manual Dream Mode cycle. This runs the full 5-stage pipeline:
    1. Context compression (FractalSummarizer map-reduce)
    2. Trace archival (Parquet cold storage)
    3. HLF practice round (generate + validate HLF intents)
    4. Six Thinking Hats analysis (Red/Black/White/Yellow/Green/Blue)
    5. Results persistence to SQLite

    Returns the full DreamCycleReport with hat findings, HLF scores, and compression stats.
    """
    import sys
    sys.path.insert(0, str(_BASE_DIR))
    from agents.core.dream_state import run_dream_cycle as _run
    report = _run(manual=True)
    return asdict(report)


@mcp.tool()
def get_hat_findings(limit: int = 20) -> list[dict]:
    """
    Retrieve the most recent Hat analysis findings from the database.
    Each finding includes: hat color, severity, title, description, recommendation.

    Args:
        limit: Maximum number of findings to return (default 20).
    """
    conn = _get_db()
    if not conn:
        return [{"error": "Database not found"}]
    try:
        import sys
        sys.path.insert(0, str(_BASE_DIR))
        from agents.core.hat_engine import get_recent_findings
        return get_recent_findings(conn, limit=limit)
    except Exception as e:
        return [{"error": str(e)}]
    finally:
        conn.close()


@mcp.tool()
def list_align_rules() -> list[dict]:
    """
    List all current ALIGN governance rules from the ledger.
    Rules define blocked patterns and actions (DROP_AND_QUARANTINE, HUMAN_OK_REQUIRED, etc).
    """
    align_path = _BASE_DIR / "governance" / "align_ledger.yaml"
    if not align_path.exists():
        return [{"error": f"ALIGN ledger not found at {align_path}"}]
    try:
        import yaml
        rules = yaml.safe_load(align_path.read_text())
        if isinstance(rules, dict):
            return rules.get("rules", [rules])
        elif isinstance(rules, list):
            return rules
        return [{"raw": str(rules)}]
    except ImportError:
        # Fallback: parse as text
        return [{"raw": align_path.read_text()}]


@mcp.tool()
def get_system_state() -> dict:
    """
    Get a comprehensive overview of the Sovereign OS state:
    deployment tier, gas budget, agent counts, recent dream results, and feature flags.
    """
    state = {}

    # Settings
    settings_path = _BASE_DIR / "config" / "settings.json"
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
            state["deployment_tier"] = settings.get("deployment_tier", "unknown")
            state["gas_buckets"] = settings.get("gas_buckets", {})
            state["features"] = settings.get("features", {})
            state["dream_mode_config"] = settings.get("dream_mode", {})
            state["models"] = settings.get("models", {})
        except Exception:
            pass

    # Agent counts from Redis
    try:
        import redis
        pw = os.environ.get("REDIS_PASSWORD", "")
        r = redis.Redis(host="localhost", port=6379, password=pw or None, decode_responses=True)
        state["agents"] = {
            "working": int(r.get("agents:working") or 0),
            "input_required": int(r.get("agents:input_required") or 0),
            "exceptions": int(r.get("agents:exceptions") or 0),
        }
    except Exception:
        state["agents"] = {"error": "Redis unavailable"}

    # Dream history
    conn = _get_db()
    if conn:
        try:
            last = conn.execute(
                "SELECT timestamp, cycle_type, hlf_practiced, hlf_passed, summary "
                "FROM dream_results ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            if last:
                state["last_dream_cycle"] = {
                    "timestamp": last[0],
                    "type": last[1],
                    "hlf_practiced": last[2],
                    "hlf_passed": last[3],
                    "summary": last[4],
                }
        except Exception:
            pass
        finally:
            conn.close()

    return state


@mcp.tool()
def query_memory(search_term: str, limit: int = 10) -> list[dict]:
    """
    Search the fact_store for knowledge entries matching the search term.
    This is the RAG bridge — queries the system's accumulated knowledge.

    Args:
        search_term: Text to search for in entity_id and semantic_relationship fields.
        limit: Maximum results to return (default 10).
    """
    conn = _get_db()
    if not conn:
        return [{"error": "Database not found"}]
    try:
        rows = conn.execute(
            "SELECT entity_id, semantic_relationship, confidence_score "
            "FROM fact_store "
            "WHERE entity_id LIKE ? OR semantic_relationship LIKE ? "
            "ORDER BY confidence_score DESC LIMIT ?",
            (f"%{search_term}%", f"%{search_term}%", limit),
        ).fetchall()
        return [
            {
                "entity_id": r[0],
                "relationship": r[1],
                "confidence": r[2],
            }
            for r in rows
        ]
    except Exception as e:
        return [{"error": str(e)}]
    finally:
        conn.close()


@mcp.tool()
def get_dream_history(limit: int = 5) -> list[dict]:
    """
    Retrieve past Dream Mode cycle results.

    Args:
        limit: Number of recent cycles to return (default 5).
    """
    conn = _get_db()
    if not conn:
        return [{"error": "Database not found"}]
    try:
        rows = conn.execute(
            "SELECT id, timestamp, cycle_type, hlf_practiced, hlf_passed, "
            "context_compressed_chars, context_result_chars, duration_seconds, summary "
            "FROM dream_results ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "id": r[0],
                "timestamp": r[1],
                "cycle_type": r[2],
                "hlf_practiced": r[3],
                "hlf_passed": r[4],
                "compressed_chars": r[5],
                "result_chars": r[6],
                "duration_seconds": r[7],
                "summary": r[8],
            }
            for r in rows
        ]
    except Exception as e:
        return [{"error": str(e)}]
    finally:
        conn.close()


# ===========================================================================
# MCP Resources
# ===========================================================================

@mcp.resource("sovereign://settings")
def get_settings() -> str:
    """Current Sovereign OS configuration (settings.json)."""
    settings_path = _BASE_DIR / "config" / "settings.json"
    if settings_path.exists():
        return settings_path.read_text()
    return "{}"


@mcp.resource("sovereign://build-plan")
def get_build_plan() -> str:
    """Sovereign OS Master Build Plan summary."""
    plan_path = _BASE_DIR.parent / "Sovereign_OS_Master_Build_Plan.md"
    if plan_path.exists():
        # Return first 5000 chars (executive summary)
        content = plan_path.read_text()
        if len(content) > 5000:
            return content[:5000] + "\n\n... [truncated — use view_file for full plan]"
        return content
    return "Build plan not found."


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    mcp.run()
