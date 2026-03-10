"""
action_menu.py — OS Action Menu System for the Sovereign Tray.

Builds the full hierarchical tray menu and wires callbacks to the
NativeBridge, DaemonManager, and service lifecycle methods.

Phase 6 deliverable (6A-6I): system tray menus, HLF submenu,
agent management, security/daemons, quick tools, OpenClaw, services.
"""

from __future__ import annotations

import hashlib
import secrets
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any

from agents.core.native.bridge import TrayMenuItem

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Menu Builder
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def build_os_action_menu() -> list[TrayMenuItem]:
    """Construct the full Sovereign OS hierarchical tray menu.

    Returns:
        List of TrayMenuItems with nested submenus.
    """
    return [
        TrayMenuItem(label="🛡️ Sovereign OS", action="header", enabled=False),
        TrayMenuItem(label="Status: Active", action="status", enabled=False, separator_before=True),

        # ── 6A: System Overview ──────────────────────────────────────
        TrayMenuItem(label="System Info", action="sysinfo", separator_before=True),
        TrayMenuItem(label="Clipboard History", action="clipboard"),
        TrayMenuItem(label="Notifications", action="notifications"),

        # ── 6B: HLF Programs ────────────────────────────────────────
        TrayMenuItem(
            label="📜 HLF Programs",
            action="hlf",
            separator_before=True,
            children=[
                TrayMenuItem(label="Run Gallery", action="hlf_run_gallery"),
                TrayMenuItem(label="Compile File…", action="hlf_compile"),
                TrayMenuItem(label="Start LSP Server", action="hlf_lsp_start"),
                TrayMenuItem(label="Run Tests (hlftest)", action="hlf_test"),
                TrayMenuItem(label="Open stdlib/", action="hlf_open_stdlib"),
            ],
        ),

        # ── 6C: Agent Management ─────────────────────────────────────
        TrayMenuItem(
            label="🤖 Agents",
            action="agents",
            children=[
                TrayMenuItem(label="List Running", action="agents_list"),
                TrayMenuItem(label="Pause All", action="agents_pause"),
                TrayMenuItem(label="Resume All", action="agents_resume"),
                TrayMenuItem(label="Daemon Health", action="daemon_health"),
                TrayMenuItem(label="Gas Report", action="gas_report"),
            ],
        ),

        # ── 6D: Security / Daemons ──────────────────────────────────
        TrayMenuItem(
            label="🔒 Security",
            action="security",
            children=[
                TrayMenuItem(label="Sentinel Status", action="sentinel_status"),
                TrayMenuItem(label="Scribe Prose Log", action="scribe_log"),
                TrayMenuItem(label="Arbiter Disputes", action="arbiter_disputes"),
                TrayMenuItem(label="View ALIGN Rules", action="view_align_rules"),
                TrayMenuItem(label="Run Security Scan", action="security_scan"),
            ],
        ),

        # ── 6E: Quick Tools ─────────────────────────────────────────
        TrayMenuItem(
            label="⚡ Quick Tools",
            action="quick_tools",
            children=[
                TrayMenuItem(label="Hash File…", action="tool_hash_file"),
                TrayMenuItem(label="Generate Password", action="tool_gen_password"),
                TrayMenuItem(label="Diff Files…", action="tool_diff_files"),
                TrayMenuItem(label="Format Code", action="tool_format_code"),
                TrayMenuItem(label="Screenshot", action="tool_screenshot"),
            ],
        ),

        # ── 6F: OpenClaw Sandbox ─────────────────────────────────────
        TrayMenuItem(
            label="🐾 OpenClaw",
            action="openclaw",
            children=[
                TrayMenuItem(label="Sandbox Status", action="openclaw_status"),
                TrayMenuItem(label="Run in Sandbox…", action="openclaw_run"),
                TrayMenuItem(label="View Audit Trail", action="openclaw_audit"),
            ],
        ),

        # ── 6G: Services ────────────────────────────────────────────
        TrayMenuItem(
            label="🔧 Services",
            action="services",
            separator_before=True,
            children=[
                TrayMenuItem(label="▶ Start Backend", action="svc_start_backend"),
                TrayMenuItem(label="⏹ Stop Backend", action="svc_stop_backend"),
                TrayMenuItem(label="▶ Start GUI", action="svc_start_gui"),
                TrayMenuItem(label="⏹ Stop GUI", action="svc_stop_gui"),
                TrayMenuItem(label="▶ Start MCP Server", action="svc_start_mcp"),
                TrayMenuItem(label="⏹ Stop MCP Server", action="svc_stop_mcp"),
                TrayMenuItem(label="Start All", action="svc_start_all"),
                TrayMenuItem(label="Stop All", action="svc_stop_all"),
            ],
        ),

        # ── 6H: Gateway ─────────────────────────────────────────────
        TrayMenuItem(
            label="🌐 Gateway",
            action="gateway",
            children=[
                TrayMenuItem(label="Start Gateway", action="gateway_start"),
                TrayMenuItem(label="Stop Gateway", action="gateway_stop"),
                TrayMenuItem(label="Restart Gateway", action="gateway_restart"),
                TrayMenuItem(label="Health Check", action="gateway_health"),
            ],
        ),

        # ── 6I: Settings / Quit ──────────────────────────────────────
        TrayMenuItem(label="Open Workspace", action="open_workspace", separator_before=True),
        TrayMenuItem(label="View Logs", action="view_logs"),
        TrayMenuItem(label="Run Preflight", action="run_preflight"),
        TrayMenuItem(label="Settings", action="settings", separator_before=True),
        TrayMenuItem(label="About", action="about"),
        TrayMenuItem(label="Quit", action="quit", separator_before=True),
    ]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Callback Library
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _get_bridge():
    """Lazy import to avoid circular deps."""
    from agents.core.native import get_bridge
    return get_bridge()


def _notify(title: str, body: str) -> None:
    """Show a tray notification via the NativeBridge."""
    from agents.core.native.bridge import NotificationRequest, NotificationUrgency
    bridge = _get_bridge()
    bridge.notify(NotificationRequest(
        title=title,
        body=body,
        urgency=NotificationUrgency.NORMAL,
    ))


# ── 6A: System Overview ─────────────────────────────────────────────────

def on_sysinfo() -> None:
    """Show system info as notification."""
    bridge = _get_bridge()
    info = bridge.system_info()
    _notify("Sovereign OS — System Info", (
        f"CPU: {info.cpu_count} cores @ {info.cpu_percent}%\n"
        f"RAM: {info.memory_available_mb}MB / {info.memory_total_mb}MB\n"
        f"Disk: {info.disk_free_gb}GB free\n"
        f"Uptime: {info.uptime_seconds / 3600:.1f}h"
    ))


def on_clipboard() -> None:
    """Show clipboard contents as notification."""
    bridge = _get_bridge()
    content = bridge.clipboard_read()
    preview = content.text[:200] + "…" if len(content.text) > 200 else content.text
    _notify("Clipboard", preview or "(empty)")


# ── 6B: HLF Programs ────────────────────────────────────────────────────

def on_hlf_run_gallery() -> None:
    """Run the HLF program gallery."""
    script = _PROJECT_ROOT / "scripts" / "run_hlf_gallery.py"
    if script.exists():
        subprocess.Popen([sys.executable, str(script)], cwd=str(_PROJECT_ROOT))
        _notify("HLF Gallery", "Running HLF gallery… Check terminal.")
    else:
        _notify("HLF Gallery", "run_hlf_gallery.py not found.")


def on_hlf_compile() -> None:
    """Launch HLF compiler prompt."""
    _notify("HLF Compile", "Use CLI: python -m hlf.compiler <file.hlf>")


def on_hlf_lsp_start() -> None:
    """Start the HLF Language Server."""
    lsp = _PROJECT_ROOT / "hlf" / "hlflsp.py"
    if lsp.exists():
        subprocess.Popen([sys.executable, str(lsp)], cwd=str(_PROJECT_ROOT))
        _notify("HLF LSP", "Language Server started.")
    else:
        _notify("HLF LSP", "hlflsp.py not found.")


def on_hlf_test() -> None:
    """Run hlftest suite."""
    subprocess.Popen(
        [sys.executable, "-m", "pytest", "tests/test_stdlib.py", "-v"],
        cwd=str(_PROJECT_ROOT),
    )
    _notify("HLF Tests", "Running stdlib tests… Check terminal.")


def on_hlf_open_stdlib() -> None:
    """Open stdlib directory."""
    stdlib = _PROJECT_ROOT / "hlf" / "stdlib"
    if stdlib.exists():
        _get_bridge().open_file(str(stdlib))


# ── 6C: Agent Management ────────────────────────────────────────────────

def on_agents_list() -> None:
    """Show agent status notification."""
    _notify("Agents", "Agent orchestration panel available in the Command Center GUI.")


def on_agents_pause() -> None:
    """Pause all agents."""
    _notify("Agents", "Agent pause: send SIGPAUSE to orchestrator (not yet wired).")


def on_agents_resume() -> None:
    """Resume all agents."""
    _notify("Agents", "Agent resume: send SIGRESUME to orchestrator (not yet wired).")


def on_daemon_health() -> None:
    """Show daemon health check."""
    try:
        from agents.core.daemons import DaemonManager
        mgr = DaemonManager()
        mgr.start_all()
        health = mgr.health_check()
        mgr.stop_all()
        body = "\n".join(f"  {k}: {v}" for k, v in health["daemons"].items())
        _notify("Daemon Health", f"All healthy: {health['all_healthy']}\n{body}")
    except Exception as e:
        _notify("Daemon Health", f"Error: {e}")


def on_gas_report() -> None:
    """Show gas utilization summary."""
    _notify("Gas Report", "Gas dashboard available at http://localhost:8501 (GUI).")


# ── 6D: Security / Daemons ──────────────────────────────────────────────

def on_sentinel_status() -> None:
    """Show Sentinel daemon stats."""
    try:
        from agents.core.daemons.sentinel import SentinelDaemon
        s = SentinelDaemon()
        s.start()
        stats = s.get_stats()
        s.stop()
        _notify("Sentinel", f"Status: {stats['status']}\nChecks: {stats['check_count']}\nAlerts: {stats['total_alerts']}")
    except Exception as e:
        _notify("Sentinel", f"Error: {e}")


def on_scribe_log() -> None:
    """Open Scribe prose log."""
    log_path = _PROJECT_ROOT / "data" / "scribe_log.jsonl"
    if log_path.exists():
        _get_bridge().open_file(str(log_path))
    else:
        _notify("Scribe", "No scribe log found yet. Start daemons first.")


def on_arbiter_disputes() -> None:
    """Open Arbiter rulings log."""
    log_path = _PROJECT_ROOT / "data" / "arbiter_rulings.jsonl"
    if log_path.exists():
        _get_bridge().open_file(str(log_path))
    else:
        _notify("Arbiter", "No dispute rulings found yet.")


def on_view_align_rules() -> None:
    """Open ALIGN rules file."""
    align = _PROJECT_ROOT / "governance" / "ALIGN_LEDGER.md"
    if align.exists():
        _get_bridge().open_file(str(align))
    else:
        _notify("ALIGN", "ALIGN_LEDGER.md not found.")


def on_security_scan() -> None:
    """Run a quick security scan via Sentinel."""
    _notify("Security Scan", "Run: python -m pytest tests/test_sentinel.py -v")


# ── 6E: Quick Tools ─────────────────────────────────────────────────────

def on_tool_hash_file() -> None:
    """Generate SHA-256 hash of clipboard path."""
    bridge = _get_bridge()
    content = bridge.clipboard_read()
    path = Path(content.text.strip())
    if path.is_file():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        _notify("File Hash", f"{path.name}\nSHA-256: {digest[:32]}…")
    else:
        _notify("File Hash", "Copy a file path to clipboard first.")


def on_tool_gen_password() -> None:
    """Generate a secure password and copy to clipboard."""
    bridge = _get_bridge()
    password = secrets.token_urlsafe(24)
    bridge.clipboard_write(password)
    _notify("Password Generator", f"Secure password copied to clipboard.\n{password[:8]}…")


def on_tool_diff_files() -> None:
    """Show diff instructions."""
    _notify("Diff Tool", "Use CLI: python -c \"import difflib; ...\" or the GUI diff panel.")


def on_tool_format_code() -> None:
    """Format code with ruff."""
    subprocess.Popen(
        [sys.executable, "-m", "ruff", "format", "."],
        cwd=str(_PROJECT_ROOT),
    )
    _notify("Code Formatter", "Ruff format running on project root…")


def on_tool_screenshot() -> None:
    """Take a screenshot."""
    _notify("Screenshot", "Use: python -c \"from mss import mss; ...\" (requires mss package).")


# ── 6F: OpenClaw Sandbox ────────────────────────────────────────────────

def on_openclaw_status() -> None:
    """Show OpenClaw sandbox status."""
    _notify("OpenClaw", "OpenClaw sandbox manager available via agent dispatch.")


def on_openclaw_run() -> None:
    """Run command in OpenClaw sandbox."""
    _notify("OpenClaw", "Run in sandbox: use the Intent Dispatch panel in the GUI.")


def on_openclaw_audit() -> None:
    """View OpenClaw audit trail."""
    audit = _PROJECT_ROOT / "data" / "openclaw_audit.jsonl"
    if audit.exists():
        _get_bridge().open_file(str(audit))
    else:
        _notify("OpenClaw", "No audit trail found yet.")


# ── 6G: Services ────────────────────────────────────────────────────────
# These are wired directly to SovereignTray service methods via the
# register_action_menu_callbacks function below.


# ── 6I: Misc ────────────────────────────────────────────────────────────

def on_open_workspace() -> None:
    """Open project workspace."""
    _get_bridge().open_file(str(_PROJECT_ROOT))


def on_view_logs() -> None:
    """Open logs directory."""
    logs = _PROJECT_ROOT / "logs"
    if logs.exists():
        _get_bridge().open_file(str(logs))
    else:
        _notify("Logs", "No logs directory found.")


def on_settings() -> None:
    """Open settings.json."""
    settings = _PROJECT_ROOT / "config" / "settings.json"
    if settings.exists():
        _get_bridge().open_file(str(settings))


def on_run_preflight() -> None:
    """Run preflight checks."""
    _get_bridge().shell_exec("npm", ["run", "preflight"], timeout_seconds=120.0, cwd=str(_PROJECT_ROOT))
    _notify("Preflight", "Running npm run preflight… Check terminal.")


def on_about() -> None:
    """Show about dialog."""
    _notify("About Sovereign OS", (
        "Sovereign Agentic OS with HLF v0.4\n"
        "14-Hat CoVE Framework | 19 Agents\n"
        "Military-grade zero-trust execution\n"
        "github.com/Grumpified-OGGVCT"
    ))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Registration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def register_action_menu_callbacks(tray: Any) -> None:
    """Wire all action menu callbacks into a SovereignTray instance.

    This registers every menu action callback. Service actions
    (svc_start_*, svc_stop_*) use the tray's own service lifecycle methods.

    Args:
        tray: A SovereignTray instance.
    """
    # 6A: System Overview
    tray.register_callback("sysinfo", on_sysinfo)
    tray.register_callback("clipboard", on_clipboard)

    # 6B: HLF Programs
    tray.register_callback("hlf_run_gallery", on_hlf_run_gallery)
    tray.register_callback("hlf_compile", on_hlf_compile)
    tray.register_callback("hlf_lsp_start", on_hlf_lsp_start)
    tray.register_callback("hlf_test", on_hlf_test)
    tray.register_callback("hlf_open_stdlib", on_hlf_open_stdlib)

    # 6C: Agent Management
    tray.register_callback("agents_list", on_agents_list)
    tray.register_callback("agents_pause", on_agents_pause)
    tray.register_callback("agents_resume", on_agents_resume)
    tray.register_callback("daemon_health", on_daemon_health)
    tray.register_callback("gas_report", on_gas_report)

    # 6D: Security / Daemons
    tray.register_callback("sentinel_status", on_sentinel_status)
    tray.register_callback("scribe_log", on_scribe_log)
    tray.register_callback("arbiter_disputes", on_arbiter_disputes)
    tray.register_callback("view_align_rules", on_view_align_rules)
    tray.register_callback("security_scan", on_security_scan)

    # 6E: Quick Tools
    tray.register_callback("tool_hash_file", on_tool_hash_file)
    tray.register_callback("tool_gen_password", on_tool_gen_password)
    tray.register_callback("tool_diff_files", on_tool_diff_files)
    tray.register_callback("tool_format_code", on_tool_format_code)
    tray.register_callback("tool_screenshot", on_tool_screenshot)

    # 6F: OpenClaw
    tray.register_callback("openclaw_status", on_openclaw_status)
    tray.register_callback("openclaw_run", on_openclaw_run)
    tray.register_callback("openclaw_audit", on_openclaw_audit)

    # 6G: Services (delegate to tray's service lifecycle)
    tray.register_callback("svc_start_backend", tray.start_backend)
    tray.register_callback("svc_stop_backend", tray.stop_backend)
    tray.register_callback("svc_start_gui", tray.start_gui)
    tray.register_callback("svc_stop_gui", tray.stop_gui)
    tray.register_callback("svc_start_mcp", tray.start_mcp)
    tray.register_callback("svc_stop_mcp", tray.stop_mcp)
    tray.register_callback("svc_start_all", tray.auto_launch_all)
    tray.register_callback("svc_stop_all", tray.stop_all_services)

    # 6I: Settings / Misc
    tray.register_callback("open_workspace", on_open_workspace)
    tray.register_callback("view_logs", on_view_logs)
    tray.register_callback("run_preflight", on_run_preflight)
    tray.register_callback("settings", on_settings)
    tray.register_callback("about", on_about)
    tray.register_callback("quit", tray.stop_all_services)
