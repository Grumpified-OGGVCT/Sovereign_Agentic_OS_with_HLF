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

    _logger.log("NATIVE_TOOLS_REGISTERED", {"count": registered, "features": features})
