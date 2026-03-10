"""
Client Connector — Auto-configures local AI tools to use the Sovereign gateway.

Generates ready-to-use configs for:
    - MSTY Studio       — OpenAI-compatible endpoint override
    - AnythingLLM       — custom model provider config
    - LoLLMS            — API binding configuration
    - StudioLLM         — model endpoint configuration
    - VS Code (Continue) — config.json with gateway endpoint
    - Open WebUI        — connection profile

Each client gets a configuration snippet that points it at:
    http://127.0.0.1:4000/v1/

This makes ALL credential-vault-managed models (Google Ultimate, OpenAI,
Anthropic, Ollama, Groq, etc.) seamlessly available through any client.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─── Client Definitions ─────────────────────────────────────────────────────

@dataclass
class ClientConfig:
    """Configuration snippet for a local AI client."""

    name: str
    display_name: str
    config_format: str              # "json", "env", "yaml", "toml"
    config_path_hint: str           # Where the user's config usually lives
    config_template: dict[str, Any] | str
    description: str = ""
    install_check: str = ""         # Command to check if installed
    docs_url: str = ""


SUPPORTED_CLIENTS: list[ClientConfig] = [
    ClientConfig(
        name="msty",
        display_name="MSTY Studio",
        config_format="json",
        config_path_hint="MSTY Settings > Models > Custom Provider",
        config_template={
            "provider": "custom",
            "name": "Sovereign Gateway",
            "base_url": "http://127.0.0.1:4000/v1/",
            "api_key": "sovereign-local",
            "models": ["gemini/gemini-3-pro", "qwen3-vl:235b-cloud", "openai/gpt-4o"],
        },
        description="Add as Custom OpenAI-compatible provider in MSTY settings",
        install_check="msty",
        docs_url="https://docs.msty.app",
    ),
    ClientConfig(
        name="anythingllm",
        display_name="AnythingLLM",
        config_format="json",
        config_path_hint="Settings > LLM Preference > Generic OpenAI",
        config_template={
            "provider": "generic-openai",
            "base_url": "http://127.0.0.1:4000/v1/",
            "api_key": "sovereign-local",
            "model_name": "gemini/gemini-3-pro",
            "max_tokens": 4096,
            "temperature": 0.7,
        },
        description="Select 'Generic OpenAI' as LLM provider, enter gateway URL",
        docs_url="https://docs.anythingllm.com",
    ),
    ClientConfig(
        name="lollms",
        display_name="LoLLMS WebUI",
        config_format="yaml",
        config_path_hint="Settings > Bindings > OpenAI API",
        config_template={
            "binding": "open_ai",
            "host": "http://127.0.0.1:4000",
            "api_key": "sovereign-local",
            "model": "gemini/gemini-3-pro",
            "ctx_size": 4096,
        },
        description="Select OpenAI API binding, configure gateway endpoint",
        docs_url="https://github.com/ParisNeo/lollms-webui",
    ),
    ClientConfig(
        name="studiollm",
        display_name="StudioLLM",
        config_format="json",
        config_path_hint="Settings > Model Providers > OpenAI Compatible",
        config_template={
            "provider_type": "openai_compatible",
            "endpoint_url": "http://127.0.0.1:4000/v1/",
            "api_key": "sovereign-local",
            "default_model": "gemini/gemini-3-pro",
        },
        description="Add OpenAI-compatible provider pointing to gateway",
    ),
    ClientConfig(
        name="vscode_continue",
        display_name="VS Code (Continue)",
        config_format="json",
        config_path_hint="~/.continue/config.json",
        config_template={
            "models": [
                {
                    "title": "Sovereign Gateway",
                    "provider": "openai",
                    "model": "gemini/gemini-3-pro",
                    "apiBase": "http://127.0.0.1:4000/v1/",
                    "apiKey": "sovereign-local",
                }
            ],
            "tabAutocompleteModel": {
                "title": "Sovereign Autocomplete",
                "provider": "openai",
                "model": "gemini/gemini-3-pro",
                "apiBase": "http://127.0.0.1:4000/v1/",
                "apiKey": "sovereign-local",
            },
        },
        description="Merge into ~/.continue/config.json for VS Code AI",
        docs_url="https://continue.dev/docs",
    ),
    ClientConfig(
        name="open_webui",
        display_name="Open WebUI",
        config_format="env",
        config_path_hint=".env or Docker env vars",
        config_template="OPENAI_API_BASE_URL=http://127.0.0.1:4000/v1/\nOPENAI_API_KEY=sovereign-local\n",
        description="Set environment variables for OpenAI connection",
        docs_url="https://github.com/open-webui/open-webui",
    ),
]


# ─── Connector ───────────────────────────────────────────────────────────────

class ClientConnector:
    """Generates and applies gateway configurations for local AI clients.

    Args:
        gateway_host: Gateway host (default 127.0.0.1).
        gateway_port: Gateway port (default 4000).
        available_models: Models to expose in configs.
    """

    def __init__(
        self,
        gateway_host: str = "127.0.0.1",
        gateway_port: int = 4000,
        available_models: list[str] | None = None,
    ) -> None:
        self._host = gateway_host
        self._port = gateway_port
        self._base_url = f"http://{gateway_host}:{gateway_port}/v1/"
        self._models = available_models or ["gemini/gemini-3-pro"]

    def list_clients(self) -> list[dict[str, str]]:
        """List all supported client tools."""
        return [
            {
                "name": c.name,
                "display_name": c.display_name,
                "config_format": c.config_format,
                "description": c.description,
            }
            for c in SUPPORTED_CLIENTS
        ]

    def get_config(self, client_name: str) -> dict[str, Any] | None:
        """Get configuration snippet for a specific client.

        Returns:
            Dict with 'client', 'config', 'instructions', 'config_path'.
        """
        client = self._find_client(client_name)
        if not client:
            return None

        config = self._render_config(client)

        return {
            "client": client.display_name,
            "config_format": client.config_format,
            "config": config,
            "config_path": client.config_path_hint,
            "instructions": client.description,
            "docs_url": client.docs_url,
            "gateway_url": self._base_url,
        }

    def get_all_configs(self) -> list[dict[str, Any]]:
        """Get configuration snippets for all supported clients."""
        return [
            self.get_config(c.name)
            for c in SUPPORTED_CLIENTS
        ]

    def export_config(self, client_name: str, output_path: Path) -> bool:
        """Export a client configuration to a file.

        Args:
            client_name: Client identifier.
            output_path: Where to write the config file.

        Returns:
            True if written successfully.
        """
        result = self.get_config(client_name)
        if not result:
            return False

        config = result["config"]
        fmt = result["config_format"]

        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)

            if fmt == "json":
                output_path.write_text(
                    json.dumps(config, indent=2), encoding="utf-8"
                )
            elif fmt == "env":
                output_path.write_text(str(config), encoding="utf-8")
            elif fmt == "yaml":
                # Simple YAML serialization (no PyYAML dependency)
                lines = []
                for k, v in config.items():
                    lines.append(f"{k}: {v}")
                output_path.write_text("\n".join(lines), encoding="utf-8")
            else:
                output_path.write_text(
                    json.dumps(config, indent=2), encoding="utf-8"
                )

            return True

        except Exception as e:
            logger.warning("Failed to export config for %s: %s", client_name, e)
            return False

    @property
    def gateway_url(self) -> str:
        return self._base_url

    @property
    def client_count(self) -> int:
        return len(SUPPORTED_CLIENTS)

    # ── Internal ────────────────────────────────────────────────────────

    def _find_client(self, name: str) -> ClientConfig | None:
        for c in SUPPORTED_CLIENTS:
            if c.name == name:
                return c
        return None

    def _render_config(self, client: ClientConfig) -> dict[str, Any] | str:
        """Render a config template with current gateway settings."""
        tmpl = client.config_template

        if isinstance(tmpl, str):
            # String templates (e.g., .env files)
            return tmpl.replace(
                f"http://127.0.0.1:4000/v1/", self._base_url
            )

        # Dict templates — deep replace base_url and models
        rendered = json.loads(json.dumps(tmpl))  # deep copy
        self._replace_in_dict(rendered)
        return rendered

    def _replace_in_dict(self, d: dict | list) -> None:
        """Recursively replace gateway URL and model references."""
        if isinstance(d, dict):
            for key, val in d.items():
                if isinstance(val, str) and "127.0.0.1:4000" in val:
                    d[key] = val.replace(
                        "http://127.0.0.1:4000/v1/", self._base_url
                    ).replace(
                        "http://127.0.0.1:4000", f"http://{self._host}:{self._port}"
                    )
                elif isinstance(val, (dict, list)):
                    self._replace_in_dict(val)
        elif isinstance(d, list):
            for item in d:
                if isinstance(item, (dict, list)):
                    self._replace_in_dict(item)
