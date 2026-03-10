"""
Tier 2A AI Tools — Gateway-routed utilities for the Sovereign OS tool registry.

These tools route through the Sovereign Model Gateway (cloud-first) to provide
AI-powered capabilities using the best available model.  Each tool:
  - Routes through the Gateway (Gemini → OpenRouter → Ollama fallback)
  - Falls back to direct Ollama only if OLLAMA_DEFAULT_MODEL is explicitly set
  - Respects gas metering (higher gas for LLM calls)
  - Is feature-flag gated via settings.json

Usage:
    from agents.core.native.ai_tools import register_ai_tools
    register_ai_tools(tool_registry)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from agents.core.logger import ALSLogger

_logger = ALSLogger(agent_role="ai-tools", goal_id="registration")

# ── Inference endpoints (cloud-first via Gateway) ────────────────────────────
# Default: route through the Sovereign Model Gateway (cloud-first)
# Override: set OLLAMA_DEFAULT_MODEL to force a specific local model
_GATEWAY_HOST = os.environ.get("SOVEREIGN_GATEWAY", "http://127.0.0.1:4000")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", _GATEWAY_HOST)
OLLAMA_HOST_SECONDARY = os.environ.get("OLLAMA_HOST_SECONDARY", "")
_DEFAULT_MODEL = os.environ.get("OLLAMA_DEFAULT_MODEL", "qwen3-vl:235b-cloud")


def _ollama_generate(prompt: str, system: str = "", model: str | None = None) -> str:
    """Call Ollama /api/generate with failover to secondary host."""
    model = model or _DEFAULT_MODEL
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": False,
    }).encode()

    for host in [OLLAMA_HOST, OLLAMA_HOST_SECONDARY]:
        if not host:
            continue
        try:
            req = urllib.request.Request(
                f"{host}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode())
                return data.get("response", "").strip()
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            _logger.log("OLLAMA_FAILOVER", {"host": host, "error": str(exc)})
            continue

    return "[ERROR] No Ollama backend available. Ensure Ollama is running."


def register_ai_tools(registry: Any) -> None:
    """Register all Tier 2A AI tools into the ToolRegistry.

    Requires Ollama running locally.  Each tool is feature-flag gated.
    """
    from agents.core.native import _load_native_config

    config = _load_native_config()
    features = config.get("features", {})
    registered = 0

    # ── ai.summarize ──────────────────────────────────────────────────────
    if features.get("ai_summarize", True):
        def _handle_summarize(params: dict) -> dict:
            text = params.get("text", "")
            if not text:
                return {"status": "error", "error": "No text provided"}
            length = params.get("length", "brief")
            system = (
                "You are a summarization assistant. Produce clear, accurate summaries. "
                f"Target length: {length}. Do not add information not in the original."
            )
            result = _ollama_generate(text, system=system)
            return {"status": "ok", "data": {"summary": result}}

        registry.register(
            name="ai.summarize",
            handler=_handle_summarize,
            schema={
                "description": "Summarize text using local AI (Ollama)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Text to summarize"},
                        "length": {
                            "type": "string",
                            "enum": ["brief", "detailed", "bullet-points"],
                            "description": "Summary length/format",
                        },
                    },
                    "required": ["text"],
                },
            },
            gas_cost=5,
            permissions=["hearth", "forge", "sovereign"],
            tags=["ai", "text", "ollama"],
        )
        registered += 1

    # ── ai.explain_code ───────────────────────────────────────────────────
    if features.get("ai_explain_code", True):
        def _handle_explain_code(params: dict) -> dict:
            code = params.get("code", "")
            lang = params.get("language", "auto-detect")
            if not code:
                return {"status": "error", "error": "No code provided"}
            system = (
                f"You are a code explainer. Language: {lang}. "
                "Explain what this code does in clear, concise terms. "
                "Cover: purpose, key logic, potential issues."
            )
            result = _ollama_generate(code, system=system)
            return {"status": "ok", "data": {"explanation": result}}

        registry.register(
            name="ai.explain_code",
            handler=_handle_explain_code,
            schema={
                "description": "Explain what a code snippet does using local AI",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "Code to explain"},
                        "language": {"type": "string", "description": "Programming language (or auto-detect)"},
                    },
                    "required": ["code"],
                },
            },
            gas_cost=5,
            permissions=["hearth", "forge", "sovereign"],
            tags=["ai", "code", "ollama"],
        )
        registered += 1

    # ── ai.commit_msg ─────────────────────────────────────────────────────
    if features.get("ai_commit_msg", True):
        def _handle_commit_msg(params: dict) -> dict:
            diff = params.get("diff", "")
            if not diff:
                return {"status": "error", "error": "No diff provided"}
            system = (
                "You are a git commit message generator. Given a unified diff, "
                "produce a commit message following Conventional Commits format "
                "(type(scope): description). Be concise but descriptive. "
                "Return ONLY the commit message, no explanation."
            )
            result = _ollama_generate(diff, system=system)
            return {"status": "ok", "data": {"message": result}}

        registry.register(
            name="ai.commit_msg",
            handler=_handle_commit_msg,
            schema={
                "description": "Generate a git commit message from a diff using local AI",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "diff": {"type": "string", "description": "Unified diff text"},
                    },
                    "required": ["diff"],
                },
            },
            gas_cost=3,
            permissions=["hearth", "forge", "sovereign"],
            tags=["ai", "git", "ollama"],
        )
        registered += 1

    # ── ai.translate ──────────────────────────────────────────────────────
    if features.get("ai_translate", True):
        def _handle_translate(params: dict) -> dict:
            text = params.get("text", "")
            target = params.get("target_language", "English")
            if not text:
                return {"status": "error", "error": "No text provided"}
            system = (
                f"You are a translator. Translate the following text to {target}. "
                "Return ONLY the translated text, no explanation."
            )
            result = _ollama_generate(text, system=system)
            return {"status": "ok", "data": {"translation": result, "target": target}}

        registry.register(
            name="ai.translate",
            handler=_handle_translate,
            schema={
                "description": "Translate text between languages using local AI",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Text to translate"},
                        "target_language": {"type": "string", "description": "Target language (e.g., Spanish, French, Japanese)"},
                    },
                    "required": ["text", "target_language"],
                },
            },
            gas_cost=5,
            permissions=["hearth", "forge", "sovereign"],
            tags=["ai", "text", "translation", "ollama"],
        )
        registered += 1

    # ── ai.regex_gen ──────────────────────────────────────────────────────
    if features.get("ai_regex_gen", True):
        def _handle_regex_gen(params: dict) -> dict:
            description = params.get("description", "")
            if not description:
                return {"status": "error", "error": "No description provided"}
            system = (
                "You are a regex expert. Given a natural language description, "
                "produce a valid Python regex pattern. Return ONLY the raw regex "
                "pattern string and a brief explanation. Format:\n"
                "Pattern: <regex>\n"
                "Explanation: <what it matches>"
            )
            result = _ollama_generate(description, system=system)
            return {"status": "ok", "data": {"result": result}}

        registry.register(
            name="ai.regex_gen",
            handler=_handle_regex_gen,
            schema={
                "description": "Generate regex patterns from natural language descriptions",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string", "description": "What the regex should match (natural language)"},
                    },
                    "required": ["description"],
                },
            },
            gas_cost=3,
            permissions=["hearth", "forge", "sovereign"],
            tags=["ai", "regex", "code", "ollama"],
        )
        registered += 1

    # ── ai.shell_gen ──────────────────────────────────────────────────────
    if features.get("ai_shell_gen", True):
        def _handle_shell_gen(params: dict) -> dict:
            task = params.get("task", "")
            shell = params.get("shell", "powershell")
            if not task:
                return {"status": "error", "error": "No task provided"}
            system = (
                f"You are a shell command expert for {shell} on Windows 11. "
                "Given a natural language task, produce the exact command(s) to run. "
                "Return ONLY the command(s), one per line. Add brief comments. "
                "NEVER produce destructive commands (rm -rf, format, etc.)."
            )
            result = _ollama_generate(task, system=system)
            return {"status": "ok", "data": {"commands": result, "shell": shell,
                    "warning": "Review commands before executing. AI-generated commands require human verification."}}

        registry.register(
            name="ai.shell_gen",
            handler=_handle_shell_gen,
            schema={
                "description": "Generate shell commands from natural language (with safety gate)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": "What you want the command to do (natural language)"},
                        "shell": {"type": "string", "enum": ["powershell", "bash", "cmd"], "description": "Target shell"},
                    },
                    "required": ["task"],
                },
            },
            gas_cost=5,
            permissions=["forge", "sovereign"],  # Not available in hearth tier
            tags=["ai", "shell", "code", "ollama"],
        )
        registered += 1

    # ── ai.code_review ────────────────────────────────────────────────────
    if features.get("ai_code_review", True):
        def _handle_code_review(params: dict) -> dict:
            code = params.get("code", "")
            if not code:
                return {"status": "error", "error": "No code provided"}
            focus = params.get("focus", "general")
            system = (
                f"You are an expert code reviewer. Focus: {focus}. "
                "Review the code for bugs, security issues, performance problems, "
                "and style improvements. Use severity levels: CRITICAL, HIGH, MEDIUM, LOW. "
                "Be concise but thorough."
            )
            result = _ollama_generate(code, system=system)
            return {"status": "ok", "data": {"review": result, "focus": focus}}

        registry.register(
            name="ai.code_review",
            handler=_handle_code_review,
            schema={
                "description": "AI-powered code review with severity-rated findings",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "Code to review"},
                        "focus": {
                            "type": "string",
                            "enum": ["general", "security", "performance", "style"],
                            "description": "Review focus area",
                        },
                    },
                    "required": ["code"],
                },
            },
            gas_cost=8,
            permissions=["hearth", "forge", "sovereign"],
            tags=["ai", "code", "review", "ollama"],
        )
        registered += 1

    # ── ai.json_schema ────────────────────────────────────────────────────
    if features.get("ai_json_schema", True):
        def _handle_json_schema(params: dict) -> dict:
            description = params.get("description", "")
            if not description:
                return {"status": "error", "error": "No description provided"}
            system = (
                "You are a JSON Schema expert. Convert the natural language description "
                "into a valid JSON Schema (draft-07). Return ONLY valid JSON."
            )
            result = _ollama_generate(description, system=system)
            return {"status": "ok", "data": {"schema": result}}

        registry.register(
            name="ai.json_schema",
            handler=_handle_json_schema,
            schema={
                "description": "Generate JSON Schema from natural language descriptions",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string", "description": "What the JSON should look like (natural language)"},
                    },
                    "required": ["description"],
                },
            },
            gas_cost=3,
            permissions=["hearth", "forge", "sovereign"],
            tags=["ai", "json", "schema", "ollama"],
        )
        registered += 1

    # ── ai.sentiment ──────────────────────────────────────────────────────
    if features.get("ai_sentiment", True):
        def _handle_sentiment(params: dict) -> dict:
            text = params.get("text", "")
            if not text:
                return {"status": "error", "error": "No text provided"}
            system = (
                "You are a sentiment analyst. Analyze the sentiment and tone of the text. "
                "Return a JSON object with: sentiment (positive/negative/neutral/mixed), "
                "confidence (0.0-1.0), tone (list of tones like formal, casual, urgent, friendly), "
                "and a brief rationale. Return ONLY valid JSON."
            )
            result = _ollama_generate(text, system=system)
            return {"status": "ok", "data": {"analysis": result}}

        registry.register(
            name="ai.sentiment",
            handler=_handle_sentiment,
            schema={
                "description": "Analyze sentiment and tone of text using local AI",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Text to analyze"},
                    },
                    "required": ["text"],
                },
            },
            gas_cost=3,
            permissions=["hearth", "forge", "sovereign"],
            tags=["ai", "text", "sentiment", "ollama"],
        )
        registered += 1

    # ── ai.readme_gen ─────────────────────────────────────────────────────
    if features.get("ai_readme_gen", True):
        def _handle_readme_gen(params: dict) -> dict:
            context = params.get("context", "")
            project_name = params.get("project_name", "Project")
            if not context:
                return {"status": "error", "error": "No context provided (file list, descriptions, etc.)"}
            system = (
                f"You are a README.md generator for '{project_name}'. "
                "Given file listings and descriptions, produce a professional GitHub README "
                "with: title, badges placeholder, description, features, installation, usage, "
                "contributing, and license sections. Use markdown formatting."
            )
            result = _ollama_generate(context, system=system)
            return {"status": "ok", "data": {"readme": result}}

        registry.register(
            name="ai.readme_gen",
            handler=_handle_readme_gen,
            schema={
                "description": "Generate a README.md from project context using local AI",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "context": {"type": "string", "description": "File list, descriptions, or code samples"},
                        "project_name": {"type": "string", "description": "Project name for the README header"},
                    },
                    "required": ["context"],
                },
            },
            gas_cost=8,
            permissions=["hearth", "forge", "sovereign"],
            tags=["ai", "documentation", "readme", "ollama"],
        )
        registered += 1

    _logger.log("AI_TOOLS_REGISTERED", {"count": registered, "features": features})
