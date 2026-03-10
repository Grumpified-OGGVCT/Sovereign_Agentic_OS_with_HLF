"""
CLI AI Tools Integration — Detects and wires external AI CLI tools.

Discovers installed AI CLI tools (OpenAI Codex, Claude Code, Task Master AI),
checks their auth status, and provides a unified interface for the Sovereign OS.
Includes setup wizard guidance and tips for each tool.

Detected tools (from npm global):
  - @openai/codex   → Multi-provider CLI (Ollama + OpenAI + Azure)
  - @anthropic-ai/claude-code → Claude Code CLI
  - task-master-ai  → Task Master AI for project management

Usage:
    from agents.core.native.cli_tools import detect_cli_tools, register_cli_tools
    status = detect_cli_tools()
    register_cli_tools(tool_registry)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field, asdict
from typing import Any

from agents.core.logger import ALSLogger

_logger = ALSLogger(agent_role="cli-tools", goal_id="integration")


# ── Tool Definitions ─────────────────────────────────────────────────────────

@dataclass
class CLIToolInfo:
    """Metadata for a detected CLI AI tool."""
    name: str
    command: str
    version: str = ""
    installed: bool = False
    auth_status: str = "unknown"  # "authenticated", "needs_auth", "unknown"
    auth_command: str = ""
    description: str = ""
    tips: list[str] = field(default_factory=list)
    provider_keys: list[str] = field(default_factory=list)
    # Links to user's subscriptions
    subscription_info: str = ""


# ── Tool Detection ───────────────────────────────────────────────────────────

def _run_cmd(cmd: list[str], timeout: int = 10) -> tuple[int, str]:
    """Run a command and return (exit_code, stdout)."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            shell=(os.name == "nt"),
        )
        return result.returncode, result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return -1, ""


def _detect_codex() -> CLIToolInfo:
    """Detect OpenAI Codex CLI (@openai/codex)."""
    tool = CLIToolInfo(
        name="OpenAI Codex CLI",
        command="codex",
        description=(
            "Multi-provider AI coding assistant. Works with OpenAI, Ollama, "
            "and Azure backends. Supports IDE-like interactions from the terminal."
        ),
        auth_command="codex auth",
        provider_keys=["OPENAI_API_KEY", "AZURE_OPENAI_API_KEY"],
        subscription_info="GitHub Copilot Pro includes OpenAI API credits",
        tips=[
            "Use 'codex --provider ollama' to route through local Ollama (free, no quota)",
            "Use 'codex --provider openai' for GPT-4o/o1 via your OpenAI key",
            "Pair with GitHub Copilot Pro for unified billing and quota management",
            "In Sovereign OS: route complex tasks to Codex, simple tasks to local Ollama",
            "Use 'codex completion' for inline code completion in terminal workflows",
            "Set CODEX_DEFAULT_MODEL to match your preferred Ollama model",
        ],
    )

    code, output = _run_cmd(["codex", "--version"])
    if code == 0 and output:
        tool.installed = True
        tool.version = output.split("\n")[0]

        # Check auth status
        key = os.environ.get("OPENAI_API_KEY", "")
        if key:
            tool.auth_status = "authenticated"
        else:
            tool.auth_status = "needs_auth"
    return tool


def _detect_claude_code() -> CLIToolInfo:
    """Detect Anthropic Claude Code CLI (@anthropic-ai/claude-code)."""
    tool = CLIToolInfo(
        name="Claude Code CLI",
        command="claude",
        description=(
            "Anthropic's agentic coding tool. Excels at complex refactoring, "
            "codebase understanding, and multi-file changes."
        ),
        auth_command="claude auth",
        provider_keys=["ANTHROPIC_API_KEY"],
        subscription_info="Works with Anthropic API keys or Claude Pro subscription",
        tips=[
            "Use 'claude' for complex multi-file refactoring tasks",
            "Claude Code excels at understanding large codebases — ideal for architecture reviews",
            "In Sovereign OS: use for Hat Review escalation when local models lack depth",
            "Supports tool use and file operations — can modify code directly",
            "Use '--print' flag for non-interactive, scriptable output",
            "Pair with local Ollama for cost-effective routing: simple→Ollama, complex→Claude",
        ],
    )

    code, output = _run_cmd(["claude", "--version"])
    if code == 0 and output:
        tool.installed = True
        tool.version = output.split("\n")[0]

        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if key:
            tool.auth_status = "authenticated"
        else:
            # Claude Code may use browser-based auth
            tool.auth_status = "needs_auth"
    return tool


def _detect_task_master() -> CLIToolInfo:
    """Detect Task Master AI (task-master-ai)."""
    tool = CLIToolInfo(
        name="Task Master AI",
        command="task-master",
        description=(
            "AI-powered project management CLI. Breaks down complex projects "
            "into structured task lists with dependencies and priorities."
        ),
        auth_command="task-master init",
        provider_keys=["OPENAI_API_KEY", "ANTHROPIC_API_KEY"],
        subscription_info="Uses OpenAI or Anthropic APIs (shared keys with Codex/Claude)",
        tips=[
            "Use 'task-master parse' to break down a project description into tasks",
            "In Sovereign OS: use for Sprint Planning and DAG generation",
            "Integrates with the SpindleDAG for execution ordering",
            "Export tasks as JSON for import into the HLF pipeline runner",
            "Use '--complexity-report' to estimate effort for each task",
            "Pair with Hat Review for pre-implementation architecture analysis",
        ],
    )

    code, output = _run_cmd(["task-master", "--version"])
    if code == 0 and output:
        tool.installed = True
        tool.version = output.split("\n")[0]

        # Task Master uses the same keys as Codex/Claude
        has_key = any(os.environ.get(k, "") for k in tool.provider_keys)
        tool.auth_status = "authenticated" if has_key else "needs_auth"
    return tool


def _detect_ollama() -> CLIToolInfo:
    """Detect local Ollama installation."""
    tool = CLIToolInfo(
        name="Ollama",
        command="ollama",
        description=(
            "Local LLM runtime. Run open-source models (Llama, Mistral, etc.) "
            "on your own hardware. Zero-cost, zero-latency, full privacy."
        ),
        auth_command="(no auth needed — runs locally)",
        provider_keys=[],
        subscription_info="Free and open-source — no subscription needed",
        tips=[
            "Ollama is the backbone of Sovereign OS AI features — always keep it running",
            "Use 'ollama pull llama3.2' to download the recommended default model",
            "Set OLLAMA_HOST and OLLAMA_HOST_SECONDARY for automatic failover",
            "Use 'ollama list' to see all downloaded models",
            "The Sovereign OS GUI auto-detects all Ollama models for the chat interface",
            "For coding tasks: 'codellama' or 'deepseek-coder' are optimized choices",
        ],
    )

    code, output = _run_cmd(["ollama", "--version"])
    if code == 0 and output:
        tool.installed = True
        # Ollama version output: "ollama version is X.Y.Z"
        tool.version = output.replace("ollama version is ", "").strip()
        tool.auth_status = "authenticated"  # Always auth'd — it's local
    return tool


def detect_cli_tools() -> dict[str, CLIToolInfo]:
    """Detect all known CLI AI tools and return their status.

    Returns a dict mapping tool command names to their CLIToolInfo.
    """
    detectors = [_detect_ollama, _detect_codex, _detect_claude_code, _detect_task_master]
    tools = {}

    for detector in detectors:
        try:
            info = detector()
            tools[info.command] = info
        except Exception as exc:
            _logger.log("CLI_DETECT_ERROR", {"detector": detector.__name__, "error": str(exc)})

    installed = [t.name for t in tools.values() if t.installed]
    _logger.log("CLI_TOOLS_DETECTED", {
        "total": len(tools),
        "installed": installed,
        "count": len(installed),
    })
    return tools


# ── Setup Wizard Data ────────────────────────────────────────────────────────

def get_setup_wizard_steps(tools: dict[str, CLIToolInfo]) -> list[dict]:
    """Generate setup wizard steps based on detected tool status.

    Returns ordered steps for the GUI setup wizard to walk through.
    """
    steps = []

    # Step 1: Ollama (foundation)
    ollama = tools.get("ollama")
    if ollama and not ollama.installed:
        steps.append({
            "title": "Install Ollama",
            "description": "Ollama is required for local AI features.",
            "action": "Download from https://ollama.com and install",
            "command": None,
            "priority": "required",
        })
    elif ollama and ollama.installed:
        steps.append({
            "title": "✅ Ollama Ready",
            "description": f"Ollama {ollama.version} detected. Pull a default model if needed.",
            "action": "ollama pull llama3.2",
            "command": "ollama pull llama3.2",
            "priority": "optional",
        })

    # Step 2: Codex CLI auth
    codex = tools.get("codex")
    if codex and codex.installed and codex.auth_status == "needs_auth":
        steps.append({
            "title": "Authorize OpenAI Codex CLI",
            "description": (
                "Codex CLI needs an API key. If you have GitHub Copilot Pro, "
                "your OpenAI credits may cover this. Run the auth command or set "
                "OPENAI_API_KEY in your environment."
            ),
            "action": "codex auth",
            "command": "codex auth",
            "priority": "recommended",
        })
    elif codex and codex.installed:
        steps.append({
            "title": "✅ Codex CLI Ready",
            "description": f"OpenAI Codex {codex.version} — authenticated.",
            "action": None,
            "command": None,
            "priority": "done",
        })

    # Step 3: Claude Code auth
    claude = tools.get("claude")
    if claude and claude.installed and claude.auth_status == "needs_auth":
        steps.append({
            "title": "Authorize Claude Code CLI",
            "description": (
                "Claude Code needs an Anthropic API key or browser-based login. "
                "Run 'claude auth' to authenticate via browser."
            ),
            "action": "claude auth",
            "command": "claude auth",
            "priority": "recommended",
        })
    elif claude and claude.installed:
        steps.append({
            "title": "✅ Claude Code Ready",
            "description": f"Claude Code {claude.version} — authenticated.",
            "action": None,
            "command": None,
            "priority": "done",
        })

    # Step 4: Task Master
    tm = tools.get("task-master")
    if tm and tm.installed and tm.auth_status == "needs_auth":
        steps.append({
            "title": "Configure Task Master AI",
            "description": "Task Master shares API keys with Codex/Claude. Configure once for all.",
            "action": "task-master init",
            "command": "task-master init",
            "priority": "optional",
        })
    elif tm and tm.installed:
        steps.append({
            "title": "✅ Task Master Ready",
            "description": f"Task Master AI {tm.version} — configured.",
            "action": None,
            "command": None,
            "priority": "done",
        })

    # Step 5: Not-installed tools (install recommendations)
    for cmd, tool in tools.items():
        if not tool.installed and cmd != "ollama":
            steps.append({
                "title": f"Install {tool.name}",
                "description": tool.description,
                "action": f"npm install -g {cmd}",
                "command": f"npm install -g {cmd}",
                "priority": "optional",
            })

    # Step 6: BrowserOS (optional addon)
    try:
        from agents.core.native.browseros import get_browseros_wizard_step
        steps.append(get_browseros_wizard_step())
    except Exception:
        pass  # BrowserOS module not available on non-Windows

    return steps


def get_routing_tips() -> list[dict]:
    """Return tips for optimal model routing across providers.

    Helps users leverage their Copilot Pro + Google Ultimate + Ollama combo.
    """
    return [
        {
            "title": "🎯 Smart Routing Strategy",
            "tip": (
                "Route by complexity: Simple tasks → Ollama (free, instant). "
                "Medium tasks → Codex/Copilot (quota-managed). "
                "Complex tasks → Claude Code (best for refactoring)."
            ),
        },
        {
            "title": "💰 Quota Management",
            "tip": (
                "GitHub Copilot Pro gives you OpenAI API credits. "
                "Google Ultimate gives you Gemini access. "
                "Use Ollama for 80% of tasks to preserve cloud quotas for hard problems."
            ),
        },
        {
            "title": "🔀 Model Diversification",
            "tip": (
                "Use different models for different strengths: "
                "GPT-4o for breadth, Claude for depth, Gemini for multimodal, "
                "Llama/Mistral locally for speed and privacy."
            ),
        },
        {
            "title": "🏠 Privacy-First Default",
            "tip": (
                "Sovereign OS defaults to local Ollama for all AI tools. "
                "Cloud models are opt-in per-request, ensuring your code "
                "never leaves your machine unless you explicitly choose."
            ),
        },
        {
            "title": "⚡ IDE Integration",
            "tip": (
                "Codex CLI works in terminal like an IDE assistant. "
                "Use it alongside VS Code Copilot for dual-stream generation: "
                "Copilot for inline, Codex for multi-file operations."
            ),
        },
        {
            "title": "🔄 Hat Review Escalation",
            "tip": (
                "The 14-Hat review system runs on local Ollama by default. "
                "For critical PRs, escalate to Claude Code or Codex for "
                "a cloud-powered deep analysis without switching tools."
            ),
        },
    ]


# ── Tool Registration ────────────────────────────────────────────────────────

def register_cli_tools(registry: Any) -> None:
    """Register CLI tool management commands into the ToolRegistry."""
    from agents.core.native import _load_native_config

    config = _load_native_config()
    features = config.get("features", {})
    registered = 0

    if features.get("cli_tools", True):
        def _handle_cli_status(params: dict) -> dict:
            tools = detect_cli_tools()
            return {
                "status": "ok",
                "data": {
                    tool_cmd: asdict(info)
                    for tool_cmd, info in tools.items()
                },
            }

        registry.register(
            name="cli.status",
            handler=_handle_cli_status,
            schema={
                "description": "Detect and report status of installed AI CLI tools (Codex, Claude Code, Task Master, Ollama)",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            gas_cost=2,
            permissions=["hearth", "forge", "sovereign"],
            tags=["cli", "ai", "status"],
        )
        registered += 1

        def _handle_cli_tips(params: dict) -> dict:
            return {"status": "ok", "data": {"tips": get_routing_tips()}}

        registry.register(
            name="cli.tips",
            handler=_handle_cli_tips,
            schema={
                "description": "Get tips for optimal AI model routing across providers",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            gas_cost=1,
            permissions=["hearth", "forge", "sovereign"],
            tags=["cli", "ai", "tips"],
        )
        registered += 1

        def _handle_setup_wizard(params: dict) -> dict:
            tools = detect_cli_tools()
            steps = get_setup_wizard_steps(tools)
            return {
                "status": "ok",
                "data": {
                    "steps": steps,
                    "total": len(steps),
                    "completed": sum(1 for s in steps if s["priority"] == "done"),
                },
            }

        registry.register(
            name="cli.setup_wizard",
            handler=_handle_setup_wizard,
            schema={
                "description": "Run the CLI tools setup wizard — checks auth, recommends installations",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            gas_cost=2,
            permissions=["hearth", "forge", "sovereign"],
            tags=["cli", "ai", "setup", "wizard"],
        )
        registered += 1

    _logger.log("CLI_TOOLS_REGISTERED", {"count": registered})
