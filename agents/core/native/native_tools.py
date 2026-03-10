"""
Native Tool Registration — Registers native OS tools into the ToolRegistry.

Makes native capabilities discoverable and invocable by agents through
the standard ToolRegistry interface. Each tool has:
  - Input schema for validation
  - Gas cost for metering
  - Tier restriction for access control
  - Feature-flag gating for per-deployment customization

Usage:
    from agents.core.native.native_tools import register_native_tools
    register_native_tools(tool_registry)
"""

from __future__ import annotations

import json
from typing import Any

from agents.core.logger import ALSLogger

_logger = ALSLogger(agent_role="native-tools", goal_id="registration")


def register_native_tools(registry: Any) -> None:
    """Register all native OS tools into the ToolRegistry.

    Respects feature flags from settings.json — disabled features
    are not registered, keeping the tool surface minimal.
    """
    from agents.core.native import get_bridge, _load_native_config

    config = _load_native_config()
    features = config.get("features", {})
    registered = 0

    # ── SYS_INFO (always available) ──────────────────────────────────────

    if features.get("sysinfo", True):
        def _handle_sysinfo(params: dict) -> dict:
            from agents.core.native import get_bridge
            from dataclasses import asdict
            bridge = get_bridge()
            info = bridge.system_info()
            return {"status": "ok", "data": asdict(info)}

        registry.register(
            name="native.sysinfo",
            handler=_handle_sysinfo,
            schema={
                "description": "Get system information (CPU, RAM, disk, uptime, platform)",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            gas_cost=1,
            permissions=["hearth", "forge", "sovereign"],
            tags=["native", "system", "info"],
        )
        registered += 1

    # ── HEALTH CHECK ─────────────────────────────────────────────────────

    def _handle_health(params: dict) -> dict:
        from agents.core.native import get_bridge
        bridge = get_bridge()
        report = bridge.health()
        return {
            "status": "ok",
            "data": {
                "overall": report.overall.value,
                "platform": report.platform,
                "clipboard": report.clipboard.value,
                "notifications": report.notifications.value,
                "shell": report.shell.value,
                "tray": report.tray.value,
                "sysinfo": report.sysinfo.value,
                "details": report.details,
            },
        }

    registry.register(
        name="native.health",
        handler=_handle_health,
        schema={
            "description": "Check health of all native subsystems",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        gas_cost=1,
        permissions=["hearth", "forge", "sovereign"],
        tags=["native", "health", "diagnostics"],
    )
    registered += 1

    # ── CLIPBOARD ────────────────────────────────────────────────────────

    if features.get("clipboard", True):
        def _handle_clipboard_read(params: dict) -> dict:
            from agents.core.native import get_bridge
            bridge = get_bridge()
            content = bridge.clipboard_read()
            return {"status": "ok", "data": {"text": content.text, "format": content.format}}

        registry.register(
            name="native.clipboard.read",
            handler=_handle_clipboard_read,
            schema={
                "description": "Read current clipboard contents",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            gas_cost=1,
            permissions=["forge", "sovereign"],
            tags=["native", "clipboard"],
        )
        registered += 1

        def _handle_clipboard_write(params: dict) -> dict:
            from agents.core.native import get_bridge
            bridge = get_bridge()
            success = bridge.clipboard_write(params.get("text", ""))
            return {"status": "ok", "data": {"success": success}}

        registry.register(
            name="native.clipboard.write",
            handler=_handle_clipboard_write,
            schema={
                "description": "Write text to clipboard",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string", "description": "Text to write"}},
                    "required": ["text"],
                },
            },
            gas_cost=2,
            permissions=["forge", "sovereign"],
            tags=["native", "clipboard"],
        )
        registered += 1

    # ── NOTIFICATIONS ────────────────────────────────────────────────────

    if features.get("notifications", True):
        def _handle_notify(params: dict) -> dict:
            from agents.core.native import get_bridge
            from agents.core.native.bridge import NotificationRequest, NotificationUrgency
            bridge = get_bridge()
            urgency_map = {
                "low": NotificationUrgency.LOW,
                "normal": NotificationUrgency.NORMAL,
                "critical": NotificationUrgency.CRITICAL,
            }
            req = NotificationRequest(
                title=params.get("title", "Sovereign OS"),
                body=params.get("body", ""),
                urgency=urgency_map.get(params.get("urgency", "normal"), NotificationUrgency.NORMAL),
            )
            success = bridge.notify(req)
            return {"status": "ok", "data": {"success": success}}

        registry.register(
            name="native.notify",
            handler=_handle_notify,
            schema={
                "description": "Send a desktop notification",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Notification title"},
                        "body": {"type": "string", "description": "Notification body"},
                        "urgency": {"type": "string", "enum": ["low", "normal", "critical"]},
                    },
                    "required": ["title", "body"],
                },
            },
            gas_cost=1,
            permissions=["hearth", "forge", "sovereign"],
            tags=["native", "notifications"],
        )
        registered += 1

    # ── SHELL ────────────────────────────────────────────────────────────

    if features.get("shell", True):
        def _handle_shell(params: dict) -> dict:
            from agents.core.native import get_bridge
            bridge = get_bridge()
            result = bridge.shell_exec(
                command=params.get("command", ""),
                args=params.get("args", []),
                timeout_seconds=min(params.get("timeout", 30), 60),
            )
            return {
                "status": "ok",
                "data": {
                    "exit_code": result.exit_code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "duration_ms": result.duration_ms,
                    "timed_out": result.timed_out,
                },
            }

        registry.register(
            name="native.shell",
            handler=_handle_shell,
            schema={
                "description": "Execute a governed shell command (allowlisted, rate-limited, ACFS-confined)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Base command (must be allowlisted)"},
                        "args": {"type": "array", "items": {"type": "string"}, "description": "Command arguments"},
                        "timeout": {"type": "number", "description": "Max seconds (capped at 60)"},
                    },
                    "required": ["command"],
                },
            },
            gas_cost=8,
            permissions=["forge", "sovereign"],
            tags=["native", "shell", "execution"],
        )
        registered += 1

    # ── PROCESS LIST ─────────────────────────────────────────────────────

    if features.get("process_list", True):
        def _handle_process_list(params: dict) -> dict:
            from agents.core.native import get_bridge
            bridge = get_bridge()
            processes = bridge.list_processes(params.get("filter"))
            return {
                "status": "ok",
                "data": [
                    {"pid": p.pid, "name": p.name, "status": p.status,
                     "cpu_percent": p.cpu_percent, "memory_mb": p.memory_mb}
                    for p in processes[:50]
                ],
            }

        registry.register(
            name="native.processes",
            handler=_handle_process_list,
            schema={
                "description": "List running processes with optional name filter",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filter": {"type": "string", "description": "Filter by process name (case-insensitive)"},
                    },
                    "required": [],
                },
            },
            gas_cost=2,
            permissions=["forge", "sovereign"],
            tags=["native", "processes"],
        )
        registered += 1

    # ── APP LAUNCH ───────────────────────────────────────────────────────

    if features.get("app_launch", True):
        def _handle_app_launch(params: dict) -> dict:
            from agents.core.native import get_bridge
            bridge = get_bridge()
            pid = bridge.launch_app(params.get("app", ""), params.get("args"))
            return {
                "status": "ok" if pid else "error",
                "data": {"pid": pid} if pid else {"error": "Launch failed"},
            }

        registry.register(
            name="native.app.launch",
            handler=_handle_app_launch,
            schema={
                "description": "Launch a native application",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "app": {"type": "string", "description": "Application name or path"},
                        "args": {"type": "array", "items": {"type": "string"}, "description": "Launch arguments"},
                    },
                    "required": ["app"],
                },
            },
            gas_cost=5,
            permissions=["forge", "sovereign"],
            tags=["native", "apps"],
        )
        registered += 1

    # ── DEPENDENCIES CHECK ───────────────────────────────────────────────

    def _handle_deps(params: dict) -> dict:
        from agents.core.native import check_dependencies, install_instructions
        deps = check_dependencies()
        return {
            "status": "ok",
            "data": {
                "dependencies": deps,
                "all_installed": all(deps.values()),
                "instructions": install_instructions() if not all(deps.values()) else "All installed.",
            },
        }

    registry.register(
        name="native.deps",
        handler=_handle_deps,
        schema={
            "description": "Check native dependency installation status",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        gas_cost=0,
        permissions=["hearth", "forge", "sovereign"],
        tags=["native", "setup", "diagnostics"],
    )
    registered += 1

    # ── FILE HASHER ──────────────────────────────────────────────────────

    if features.get("hash_file", True):
        def _handle_hash_file(params: dict) -> dict:
            import hashlib
            from pathlib import Path
            target = Path(params.get("path", ""))
            if not target.exists():
                return {"status": "error", "data": {"error": f"File not found: {target}"}}
            algo = params.get("algorithm", "sha256")
            if algo not in ("md5", "sha1", "sha256", "sha512"):
                return {"status": "error", "data": {"error": f"Unsupported algorithm: {algo}"}}
            h = hashlib.new(algo)
            with open(target, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            return {"status": "ok", "data": {"path": str(target), "algorithm": algo, "hash": h.hexdigest()}}

        registry.register(
            name="native.hash_file",
            handler=_handle_hash_file,
            schema={
                "description": "Compute cryptographic hash of a file (SHA-256, MD5, SHA-1, SHA-512)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to the file"},
                        "algorithm": {"type": "string", "enum": ["md5", "sha1", "sha256", "sha512"],
                                      "description": "Hash algorithm (default: sha256)"},
                    },
                    "required": ["path"],
                },
            },
            gas_cost=1,
            permissions=["hearth", "forge", "sovereign"],
            tags=["native", "crypto", "utility"],
        )
        registered += 1

    # ── PASSWORD / TOKEN GENERATOR ───────────────────────────────────────

    if features.get("gen_password", True):
        def _handle_gen_password(params: dict) -> dict:
            import secrets
            import string
            length = min(max(params.get("length", 24), 8), 128)
            mode = params.get("mode", "mixed")
            if mode == "hex":
                token = secrets.token_hex(length // 2)
            elif mode == "url_safe":
                token = secrets.token_urlsafe(length)[:length]
            elif mode == "alphanumeric":
                alphabet = string.ascii_letters + string.digits
                token = "".join(secrets.choice(alphabet) for _ in range(length))
            else:  # mixed
                alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
                token = "".join(secrets.choice(alphabet) for _ in range(length))
            return {"status": "ok", "data": {"token": token, "length": len(token), "mode": mode}}

        registry.register(
            name="native.gen_password",
            handler=_handle_gen_password,
            schema={
                "description": "Generate a cryptographically secure password or API token",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "length": {"type": "integer", "description": "Token length (8-128, default: 24)"},
                        "mode": {"type": "string", "enum": ["mixed", "hex", "url_safe", "alphanumeric"],
                                 "description": "Generation mode (default: mixed)"},
                    },
                    "required": [],
                },
            },
            gas_cost=1,
            permissions=["hearth", "forge", "sovereign"],
            tags=["native", "crypto", "utility"],
        )
        registered += 1

    # ── DIFF FILES ───────────────────────────────────────────────────────

    if features.get("diff_files", True):
        def _handle_diff_files(params: dict) -> dict:
            import difflib
            from pathlib import Path
            file_a = Path(params.get("file_a", ""))
            file_b = Path(params.get("file_b", ""))
            if not file_a.exists() or not file_b.exists():
                return {"status": "error", "data": {"error": "One or both files not found"}}
            try:
                lines_a = file_a.read_text(encoding="utf-8").splitlines(keepends=True)
                lines_b = file_b.read_text(encoding="utf-8").splitlines(keepends=True)
            except UnicodeDecodeError:
                return {"status": "error", "data": {"error": "Files must be text (not binary)"}}
            diff_type = params.get("format", "unified")
            if diff_type == "context":
                diff = list(difflib.context_diff(lines_a, lines_b, str(file_a), str(file_b)))
            else:
                diff = list(difflib.unified_diff(lines_a, lines_b, str(file_a), str(file_b)))
            return {
                "status": "ok",
                "data": {
                    "diff": "".join(diff[:500]),  # cap output
                    "lines_a": len(lines_a),
                    "lines_b": len(lines_b),
                    "changes": len([l for l in diff if l.startswith("+") or l.startswith("-")]),
                },
            }

        registry.register(
            name="native.diff_files",
            handler=_handle_diff_files,
            schema={
                "description": "Compute a unified or context diff between two text files",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_a": {"type": "string", "description": "Path to first file"},
                        "file_b": {"type": "string", "description": "Path to second file"},
                        "format": {"type": "string", "enum": ["unified", "context"],
                                   "description": "Diff format (default: unified)"},
                    },
                    "required": ["file_a", "file_b"],
                },
            },
            gas_cost=1,
            permissions=["hearth", "forge", "sovereign"],
            tags=["native", "diff", "utility"],
        )
        registered += 1

    # ── REGEX TESTER ─────────────────────────────────────────────────────

    if features.get("regex_test", True):
        def _handle_regex_test(params: dict) -> dict:
            import re as _re
            pattern = params.get("pattern", "")
            test_string = params.get("text", "")
            try:
                compiled = _re.compile(pattern)
            except _re.error as e:
                return {"status": "error", "data": {"error": f"Invalid regex: {e}"}}
            matches = [
                {"match": m.group(), "start": m.start(), "end": m.end(), "groups": list(m.groups())}
                for m in compiled.finditer(test_string)
            ]
            return {
                "status": "ok",
                "data": {
                    "pattern": pattern,
                    "match_count": len(matches),
                    "matches": matches[:20],  # cap at 20
                    "full_match": bool(compiled.fullmatch(test_string)),
                },
            }

        registry.register(
            name="native.regex_test",
            handler=_handle_regex_test,
            schema={
                "description": "Test a regex pattern against text — useful for ALIGN rule debugging",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Regular expression pattern"},
                        "text": {"type": "string", "description": "Text to match against"},
                    },
                    "required": ["pattern", "text"],
                },
            },
            gas_cost=1,
            permissions=["hearth", "forge", "sovereign"],
            tags=["native", "regex", "utility", "debug"],
        )
        registered += 1

    # ── PORT CHECKER ─────────────────────────────────────────────────────

    if features.get("port_check", True):
        def _handle_port_check(params: dict) -> dict:
            import socket
            host = params.get("host", "localhost")
            ports = params.get("ports", [8501, 11434, 6379, 40404, 8080])
            results = {}
            for port in ports[:20]:  # cap at 20 ports
                try:
                    with socket.create_connection((host, port), timeout=1):
                        results[port] = "open"
                except (ConnectionRefusedError, TimeoutError, OSError):
                    results[port] = "closed"
            return {"status": "ok", "data": {"host": host, "ports": results}}

        registry.register(
            name="native.port_check",
            handler=_handle_port_check,
            schema={
                "description": "Check if network ports are open (service discovery, diagnostics)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "host": {"type": "string", "description": "Hostname (default: localhost)"},
                        "ports": {"type": "array", "items": {"type": "integer"},
                                  "description": "Ports to check (default: Streamlit, Ollama, Redis, Gateway, MCP)"},
                    },
                    "required": [],
                },
            },
            gas_cost=2,
            permissions=["hearth", "forge", "sovereign"],
            tags=["native", "network", "diagnostics"],
        )
        registered += 1

    # ── ENVIRONMENT INFO ─────────────────────────────────────────────────

    if features.get("env_info", True):
        def _handle_env_info(params: dict) -> dict:
            import os
            import sys
            filter_prefix = params.get("filter", "").upper()
            env_vars = {
                k: v for k, v in sorted(os.environ.items())
                if not filter_prefix or k.upper().startswith(filter_prefix)
            }
            # Redact sensitive keys
            sensitive = {"PASSWORD", "SECRET", "TOKEN", "KEY", "CREDENTIALS", "API_KEY"}
            for k in env_vars:
                if any(s in k.upper() for s in sensitive):
                    env_vars[k] = "***REDACTED***"
            return {
                "status": "ok",
                "data": {
                    "python_version": sys.version,
                    "platform": sys.platform,
                    "cwd": str(os.getcwd()),
                    "env_count": len(env_vars),
                    "env_vars": dict(list(env_vars.items())[:50]),  # cap at 50
                },
            }

        registry.register(
            name="native.env_info",
            handler=_handle_env_info,
            schema={
                "description": "Show Python version, platform, and environment variables (with sensitive redaction)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filter": {"type": "string", "description": "Filter env vars by prefix (e.g. 'OLLAMA')"},
                    },
                    "required": [],
                },
            },
            gas_cost=1,
            permissions=["hearth", "forge", "sovereign"],
            tags=["native", "env", "diagnostics"],
        )
        registered += 1

    # ── TIMESTAMP CONVERTER ──────────────────────────────────────────────

    if features.get("timestamp_convert", True):
        def _handle_timestamp(params: dict) -> dict:
            import time
            from datetime import datetime, timezone
            value = params.get("value", "")
            if not value or value == "now":
                ts = time.time()
            else:
                try:
                    ts = float(value)
                except ValueError:
                    # Try ISO format parse
                    try:
                        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                        ts = dt.timestamp()
                    except (ValueError, TypeError):
                        return {"status": "error", "data": {"error": f"Cannot parse: {value}"}}
            dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
            dt_local = datetime.fromtimestamp(ts)
            return {
                "status": "ok",
                "data": {
                    "unix": ts,
                    "unix_ms": int(ts * 1000),
                    "iso_utc": dt_utc.isoformat(),
                    "iso_local": dt_local.isoformat(),
                    "human": dt_local.strftime("%B %d, %Y %I:%M:%S %p"),
                },
            }

        registry.register(
            name="native.timestamp",
            handler=_handle_timestamp,
            schema={
                "description": "Convert between Unix timestamps, ISO 8601, and human-readable dates",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "string",
                                  "description": "Unix timestamp, ISO date, or 'now' (default: now)"},
                    },
                    "required": [],
                },
            },
            gas_cost=1,
            permissions=["hearth", "forge", "sovereign"],
            tags=["native", "time", "utility"],
        )
        registered += 1

    _logger.log("NATIVE_TOOLS_REGISTERED", {"count": registered, "features": features})

    # ── Tier 2A: AI-powered tools (Ollama backend) ───────────────────────
    try:
        from agents.core.native.ai_tools import register_ai_tools
        register_ai_tools(registry)
    except Exception as exc:
        _logger.log("AI_TOOLS_LOAD_ERROR", {"error": str(exc)})

    # ── CLI AI Tools (Codex, Claude Code, Task Master) ───────────────────
    try:
        from agents.core.native.cli_tools import register_cli_tools
        register_cli_tools(registry)
    except Exception as exc:
        _logger.log("CLI_TOOLS_LOAD_ERROR", {"error": str(exc)})

    # ── External App Tools (MSTY Studio, AnythingLLM) ────────────────────
    try:
        from agents.core.native.external_app_tools import register_external_app_tools
        register_external_app_tools(registry)
    except Exception as exc:
        _logger.log("EXTERNAL_APP_TOOLS_LOAD_ERROR", {"error": str(exc)})

