"""
User Tool Extensibility — Let users register their own MCP tools.

Provides a zero-friction way for users to extend the Sovereign OS
tool surface with their own MCP servers and custom tools.

Design:
  - Users define tools in config/user_tools.json
  - Each tool specifies: name, description, transport, command/URL, schema
  - Tools are auto-loaded at startup via load_user_tools()
  - Built-in validation prevents malformed or duplicate registrations
  - ALSLogger tracks all user tool registrations
  - User tools are sandboxed — they run in subprocess or via HTTP,
    never as in-process Python code (security boundary)

Supported transports:
  - stdio: MCP server via stdin/stdout subprocess
  - sse:   MCP server via Server-Sent Events HTTP
  - http:  Simple HTTP endpoint (REST API)
  - shell: Shell command wrapper (runs through governed shell_exec)

Usage:
  1. Create config/user_tools.json (see user_tools.example.json)
  2. On startup: from agents.core.native.user_tools import load_user_tools
  3. load_user_tools(registry) — auto-registers all valid tools
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from agents.core.logger import ALSLogger

_logger = ALSLogger(agent_role="user-tools", goal_id="extensibility")

_USER_TOOLS_PATH = Path(__file__).parent.parent.parent.parent / "config" / "user_tools.json"
_USER_TOOLS_EXAMPLE = Path(__file__).parent.parent.parent.parent / "config" / "user_tools.example.json"

# Reserved prefixes that user tools cannot claim
_RESERVED_PREFIXES = frozenset({"native.", "hlf.", "zai.", "os.", "system."})


def _validate_tool_entry(entry: dict, index: int) -> list[str]:
    """Validate a single user tool entry. Returns list of errors."""
    errors: list[str] = []

    name = entry.get("name")
    if not name or not isinstance(name, str):
        errors.append(f"Tool #{index}: 'name' is required and must be a string")
        return errors

    # Check reserved prefixes
    for prefix in _RESERVED_PREFIXES:
        if name.lower().startswith(prefix):
            errors.append(f"Tool '{name}': prefix '{prefix}' is reserved for built-in tools")

    if not entry.get("description"):
        errors.append(f"Tool '{name}': 'description' is required")

    transport = entry.get("transport")
    if transport not in ("stdio", "sse", "http", "shell"):
        errors.append(f"Tool '{name}': transport must be one of: stdio, sse, http, shell (got: {transport})")

    if transport in ("stdio", "shell") and not entry.get("command"):
        errors.append(f"Tool '{name}': 'command' is required for {transport} transport")

    if transport in ("sse", "http") and not entry.get("url"):
        errors.append(f"Tool '{name}': 'url' is required for {transport} transport")

    return errors


def load_user_tools(registry: Any) -> dict[str, Any]:
    """Load and register user-defined tools from config/user_tools.json.

    Returns a report dict with registration results.
    """
    report: dict[str, Any] = {
        "loaded": 0,
        "skipped": 0,
        "errors": [],
        "tools": [],
    }

    if not _USER_TOOLS_PATH.exists():
        _ensure_example_exists()
        _logger.log("USER_TOOLS_NONE", {"path": str(_USER_TOOLS_PATH)})
        return report

    try:
        data = json.loads(_USER_TOOLS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        error = f"Invalid JSON in user_tools.json: {exc}"
        report["errors"].append(error)
        _logger.log("USER_TOOLS_PARSE_ERROR", {"error": error}, anomaly_score=0.5)
        return report

    tools = data.get("tools", [])
    if not isinstance(tools, list):
        report["errors"].append("'tools' must be an array")
        return report

    for i, entry in enumerate(tools):
        # Validate
        validation_errors = _validate_tool_entry(entry, i)
        if validation_errors:
            report["errors"].extend(validation_errors)
            report["skipped"] += 1
            continue

        if not entry.get("enabled", True):
            report["skipped"] += 1
            continue

        name = entry["name"]
        transport = entry["transport"]

        # Create handler based on transport
        handler = _create_handler(name, transport, entry)
        if handler is None:
            report["errors"].append(f"Tool '{name}': failed to create handler")
            report["skipped"] += 1
            continue

        # Register into ToolRegistry
        try:
            registry.register(
                name=f"user.{name}",
                handler=handler,
                schema={
                    "description": entry.get("description", ""),
                    "parameters": entry.get("schema", {"type": "object", "properties": {}, "required": []}),
                },
                gas_cost=entry.get("gas_cost", 5),
                permissions=entry.get("permissions", ["forge", "sovereign"]),
                tags=["user", "custom"] + entry.get("tags", []),
            )
            report["loaded"] += 1
            report["tools"].append(name)
            _logger.log("USER_TOOL_REGISTERED", {
                "name": name, "transport": transport,
            })
        except Exception as exc:
            report["errors"].append(f"Tool '{name}': registration failed — {exc}")
            report["skipped"] += 1

    _logger.log("USER_TOOLS_LOADED", {
        "loaded": report["loaded"],
        "skipped": report["skipped"],
        "error_count": len(report["errors"]),
    })

    return report


def _create_handler(name: str, transport: str, entry: dict) -> Any:
    """Create a tool handler function for the given transport type."""

    if transport == "http":
        return _create_http_handler(name, entry)
    elif transport == "shell":
        return _create_shell_handler(name, entry)
    elif transport == "stdio":
        return _create_stdio_handler(name, entry)
    elif transport == "sse":
        return _create_sse_handler(name, entry)
    return None


def _create_http_handler(name: str, entry: dict) -> Any:
    """Create a handler that calls an HTTP endpoint."""
    url = entry["url"]
    method = entry.get("method", "POST").upper()
    headers = entry.get("headers", {})
    timeout = entry.get("timeout_seconds", 30)

    def handler(params: dict) -> dict:
        import httpx
        try:
            with httpx.Client(timeout=timeout) as client:
                if method == "GET":
                    resp = client.get(url, params=params, headers=headers)
                else:
                    resp = client.post(url, json=params, headers=headers)
                resp.raise_for_status()
                return {"status": "ok", "data": resp.json()}
        except Exception as exc:
            _logger.log("USER_TOOL_HTTP_ERROR", {"name": name, "error": str(exc)[:120]})
            return {"status": "error", "error": str(exc)[:200]}

    return handler


def _create_shell_handler(name: str, entry: dict) -> Any:
    """Create a handler that wraps a shell command (governed)."""
    command = entry["command"]
    timeout = entry.get("timeout_seconds", 30)

    def handler(params: dict) -> dict:
        from agents.core.native import get_bridge
        bridge = get_bridge()
        # Build args from params
        args = []
        for key, val in params.items():
            args.extend([f"--{key}", str(val)])

        result = bridge.shell_exec(command, args, timeout_seconds=timeout)
        return {
            "status": "ok" if result.exit_code == 0 else "error",
            "data": {
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
        }

    return handler


def _create_stdio_handler(name: str, entry: dict) -> Any:
    """Create a handler for stdio-based MCP servers.

    Spawns the MCP server subprocess and communicates via stdin/stdout
    using the MCP JSON-RPC protocol.
    """
    command = entry["command"]
    cmd_args = entry.get("args", [])
    timeout = entry.get("timeout_seconds", 30)

    def handler(params: dict) -> dict:
        try:
            request = json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": name,
                    "arguments": params,
                },
            })

            result = subprocess.run(
                [command] + cmd_args,
                input=request,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if result.returncode == 0 and result.stdout.strip():
                try:
                    response = json.loads(result.stdout)
                    return {"status": "ok", "data": response.get("result", result.stdout)}
                except json.JSONDecodeError:
                    return {"status": "ok", "data": result.stdout[:5000]}

            return {
                "status": "error",
                "error": result.stderr[:500] if result.stderr else f"Exit code: {result.returncode}",
            }
        except subprocess.TimeoutExpired:
            return {"status": "error", "error": f"MCP server timed out after {timeout}s"}
        except Exception as exc:
            _logger.log("USER_TOOL_STDIO_ERROR", {"name": name, "error": str(exc)[:120]})
            return {"status": "error", "error": str(exc)[:200]}

    return handler


def _create_sse_handler(name: str, entry: dict) -> Any:
    """Create a handler for SSE-based MCP servers."""
    url = entry["url"]
    timeout = entry.get("timeout_seconds", 30)

    def handler(params: dict) -> dict:
        import httpx
        try:
            with httpx.Client(timeout=timeout) as client:
                # SSE-based MCP: POST to the endpoint with the tool call
                resp = client.post(url, json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": params},
                })
                resp.raise_for_status()
                return {"status": "ok", "data": resp.json()}
        except Exception as exc:
            _logger.log("USER_TOOL_SSE_ERROR", {"name": name, "error": str(exc)[:120]})
            return {"status": "error", "error": str(exc)[:200]}

    return handler


def _ensure_example_exists() -> None:
    """Create the example user_tools.json if it doesn't exist."""
    if _USER_TOOLS_EXAMPLE.exists():
        return

    example = {
        "_comment": "Sovereign OS — User Tool Registry. Copy to user_tools.json and customize.",
        "tools": [
            {
                "name": "my_api",
                "description": "Example HTTP tool — calls a local API endpoint",
                "transport": "http",
                "url": "http://localhost:8080/api/my-tool",
                "method": "POST",
                "headers": {"Authorization": "Bearer ${MY_API_KEY}"},
                "timeout_seconds": 15,
                "gas_cost": 3,
                "permissions": ["forge", "sovereign"],
                "tags": ["api", "custom"],
                "enabled": false,
                "schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The query to send"}
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "my_mcp_server",
                "description": "Example stdio MCP server",
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "my-mcp-server@latest"],
                "timeout_seconds": 30,
                "gas_cost": 5,
                "permissions": ["sovereign"],
                "tags": ["mcp"],
                "enabled": false,
                "schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "my_script",
                "description": "Example shell tool — wraps a local script (governed by allowlist)",
                "transport": "shell",
                "command": "python",
                "timeout_seconds": 10,
                "gas_cost": 3,
                "permissions": ["forge", "sovereign"],
                "tags": ["script", "custom"],
                "enabled": false,
                "schema": {
                    "type": "object",
                    "properties": {
                        "input": {"type": "string"}
                    },
                    "required": [],
                },
            },
        ],
    }

    try:
        _USER_TOOLS_EXAMPLE.parent.mkdir(parents=True, exist_ok=True)
        _USER_TOOLS_EXAMPLE.write_text(
            json.dumps(example, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        _logger.log("USER_TOOLS_EXAMPLE_CREATED", {"path": str(_USER_TOOLS_EXAMPLE)})
    except Exception:
        pass


def list_user_tools() -> list[dict[str, Any]]:
    """List all user-defined tools from config/user_tools.json without registering.

    Returns parsed tool entries for display/inspection.
    """
    if not _USER_TOOLS_PATH.exists():
        return []

    try:
        data = json.loads(_USER_TOOLS_PATH.read_text(encoding="utf-8"))
        return data.get("tools", [])
    except Exception:
        return []
