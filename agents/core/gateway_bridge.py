"""
Gateway-to-HFR Bridge — Wires vault capabilities into HostFunctionRegistry.

When the credential vault discovers that a provider supports "chat", "image_gen",
"embeddings", etc., this bridge auto-registers corresponding host functions
in host_functions.json so HLF programs can call them via τ().

Architecture:
    Vault discovers capabilities → Bridge creates HostFunction entries →
    Registered in host_functions.json → HLF programs use τ("GENERATE_TEXT", ...)

Host functions created:
    GENERATE_TEXT   → routes chat/completion through gateway
    GENERATE_IMAGE  → routes image generation through gateway
    EMBED_TEXT      → routes embedding requests through gateway
    GENERATE_VIDEO  → routes video generation through gateway
    SPEECH_TO_TEXT  → routes STT through gateway
    TEXT_TO_SPEECH  → routes TTS through gateway
    WEB_SEARCH_AI   → routes search-grounded queries through gateway
    LAUNCH_PWA      → launches Google Suite / web apps as PWAs
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_HOST_FUNCTIONS_PATH = _PROJECT_ROOT / "governance" / "host_functions.json"


# ─── Capability → Host Function Mapping ─────────────────────────────────────

@dataclass
class GatewayHostFunction:
    """A host function definition backed by the model gateway."""

    name: str
    args: list[dict[str, str]]
    returns: str
    tier: list[str]
    gas: int
    capability: str        # Credential vault capability this requires
    description: str = ""

    def to_registry_entry(self) -> dict[str, Any]:
        """Convert to host_functions.json entry format."""
        return {
            "name": self.name,
            "args": self.args,
            "returns": self.returns,
            "tier": self.tier,
            "gas": self.gas,
            "backend": "model_gateway",
            "sensitive": True,
        }


# Default gateway-backed host functions
GATEWAY_HOST_FUNCTIONS: list[GatewayHostFunction] = [
    GatewayHostFunction(
        name="GENERATE_TEXT",
        args=[
            {"name": "prompt", "type": "string"},
            {"name": "model", "type": "string"},
            {"name": "temperature", "type": "float"},
        ],
        returns="string",
        tier=["forge", "sovereign"],
        gas=5,
        capability="chat",
        description="Generate text via any LLM provider (gateway-routed)",
    ),
    GatewayHostFunction(
        name="GENERATE_IMAGE",
        args=[
            {"name": "prompt", "type": "string"},
            {"name": "size", "type": "string"},
        ],
        returns="path",
        tier=["sovereign"],
        gas=10,
        capability="image_gen",
        description="Generate images via DALL-E, Imagen, Nano Banana",
    ),
    GatewayHostFunction(
        name="EMBED_TEXT",
        args=[
            {"name": "text", "type": "string"},
            {"name": "model", "type": "string"},
        ],
        returns="list",
        tier=["forge", "sovereign"],
        gas=3,
        capability="embeddings",
        description="Generate text embeddings for semantic search",
    ),
    GatewayHostFunction(
        name="GENERATE_VIDEO",
        args=[
            {"name": "prompt", "type": "string"},
            {"name": "duration_sec", "type": "int"},
        ],
        returns="path",
        tier=["sovereign"],
        gas=20,
        capability="video_gen",
        description="Generate video via Veo, Sora, or Replicate",
    ),
    GatewayHostFunction(
        name="SPEECH_TO_TEXT",
        args=[
            {"name": "audio_path", "type": "path"},
            {"name": "language", "type": "string"},
        ],
        returns="string",
        tier=["forge", "sovereign"],
        gas=5,
        capability="stt",
        description="Transcribe audio to text via Whisper or equivalent",
    ),
    GatewayHostFunction(
        name="TEXT_TO_SPEECH",
        args=[
            {"name": "text", "type": "string"},
            {"name": "voice", "type": "string"},
        ],
        returns="path",
        tier=["forge", "sovereign"],
        gas=5,
        capability="tts",
        description="Convert text to speech audio",
    ),
    GatewayHostFunction(
        name="SEARCH_GROUNDED",
        args=[
            {"name": "query", "type": "string"},
            {"name": "model", "type": "string"},
        ],
        returns="string",
        tier=["forge", "sovereign"],
        gas=8,
        capability="search",
        description="Search-grounded AI query (Google, Perplexity, etc.)",
    ),
]


# ─── PWA Launcher (Google Suite as Local Apps) ──────────────────────────────

@dataclass
class PWAApp:
    """A Progressive Web App launchable as a local window."""

    name: str
    url: str
    icon: str = ""
    category: str = "productivity"

    def launch_command(self) -> list[str]:
        """Generate Chrome --app command to launch as PWA."""
        # Try common Chrome paths
        chrome_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            "/usr/bin/google-chrome",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]

        chrome = "chrome"
        for p in chrome_paths:
            if Path(p).exists():
                chrome = p
                break

        return [chrome, f"--app={self.url}", "--new-window"]


# Google Suite PWAs
GOOGLE_SUITE_APPS: list[PWAApp] = [
    PWAApp("Google Docs", "https://docs.google.com", category="productivity"),
    PWAApp("Google Sheets", "https://sheets.google.com", category="productivity"),
    PWAApp("Google Slides", "https://slides.google.com", category="productivity"),
    PWAApp("Google Drive", "https://drive.google.com", category="storage"),
    PWAApp("Gmail", "https://mail.google.com", category="communication"),
    PWAApp("Google Calendar", "https://calendar.google.com", category="productivity"),
    PWAApp("Google Meet", "https://meet.google.com", category="communication"),
    PWAApp("AI Studio", "https://aistudio.google.com", category="development"),
    PWAApp("NotebookLM", "https://notebooklm.google.com", category="research"),
    PWAApp("Google Colab", "https://colab.research.google.com", category="development"),
    PWAApp("Gemini", "https://gemini.google.com", category="ai"),
]


# ─── Bridge ──────────────────────────────────────────────────────────────────

class GatewayHFRBridge:
    """Bridges the credential vault + model gateway to the HostFunctionRegistry.

    Reads vault capabilities and registers corresponding host functions
    in host_functions.json, making AI capabilities available to HLF programs.
    """

    def __init__(
        self,
        vault: Any = None,
        gateway: Any = None,
        host_functions_path: Path | None = None,
    ) -> None:
        self._vault = vault
        self._gateway = gateway
        self._hf_path = host_functions_path or _HOST_FUNCTIONS_PATH
        self._registered: list[str] = []

    def sync_capabilities(self) -> dict[str, Any]:
        """Sync vault capabilities to host function registry.

        Returns:
            Dict with 'added', 'skipped', 'total' counts.
        """
        added: list[str] = []
        skipped: list[str] = []

        # Load existing host functions
        existing_names: set[str] = set()
        if self._hf_path.exists():
            try:
                data = json.loads(self._hf_path.read_text(encoding="utf-8"))
                existing_names = {f["name"] for f in data.get("functions", [])}
            except (json.JSONDecodeError, KeyError):
                pass

        # Check each gateway host function
        for ghf in GATEWAY_HOST_FUNCTIONS:
            if ghf.name in existing_names:
                skipped.append(ghf.name)
                continue

            # If vault is available, check if capability is supported
            if self._vault:
                from agents.core.credential_vault import Capability
                try:
                    cap = Capability(ghf.capability)
                    providers = self._vault.find_by_capability(cap)
                    if not providers:
                        skipped.append(ghf.name)
                        continue
                except ValueError:
                    skipped.append(ghf.name)
                    continue

            # Register the function
            self._register_function(ghf)
            added.append(ghf.name)

        self._registered = added

        return {
            "added": added,
            "skipped": skipped,
            "total": len(existing_names) + len(added),
        }

    def _register_function(self, ghf: GatewayHostFunction) -> None:
        """Add a gateway host function to host_functions.json."""
        try:
            if self._hf_path.exists():
                data = json.loads(self._hf_path.read_text(encoding="utf-8"))
            else:
                data = {"version": "1.2.0", "functions": []}

            data["functions"].append(ghf.to_registry_entry())
            self._hf_path.write_text(
                json.dumps(data, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Failed to register %s: %s", ghf.name, e)

    def register_pwa_launcher(self) -> None:
        """Register LAUNCH_PWA host function for Google Suite apps."""
        existing_names: set[str] = set()
        if self._hf_path.exists():
            try:
                data = json.loads(self._hf_path.read_text(encoding="utf-8"))
                existing_names = {f["name"] for f in data.get("functions", [])}
            except (json.JSONDecodeError, KeyError):
                pass

        if "LAUNCH_PWA" not in existing_names:
            pwa_func = GatewayHostFunction(
                name="LAUNCH_PWA",
                args=[
                    {"name": "app_name", "type": "string"},
                    {"name": "url", "type": "string"},
                ],
                returns="bool",
                tier=["hearth", "forge", "sovereign"],
                gas=2,
                capability="",  # No API capability needed
                description="Launch web apps as native-like PWA windows",
            )
            entry = pwa_func.to_registry_entry()
            entry["backend"] = "native_bridge"  # PWAs go through native bridge
            self._register_function_raw(entry)

    def _register_function_raw(self, entry: dict[str, Any]) -> None:
        """Add a raw function entry to host_functions.json."""
        try:
            if self._hf_path.exists():
                data = json.loads(self._hf_path.read_text(encoding="utf-8"))
            else:
                data = {"version": "1.2.0", "functions": []}

            data["functions"].append(entry)
            self._hf_path.write_text(
                json.dumps(data, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Failed to register function: %s", e)

    def list_available_apps(self) -> list[dict[str, str]]:
        """List Google Suite PWA apps available for launch."""
        return [
            {"name": app.name, "url": app.url, "category": app.category}
            for app in GOOGLE_SUITE_APPS
        ]

    def launch_pwa(self, app_name: str) -> dict[str, Any]:
        """Launch a Google Suite app as a PWA window."""
        for app in GOOGLE_SUITE_APPS:
            if app.name.lower() == app_name.lower():
                cmd = app.launch_command()
                try:
                    subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    return {"success": True, "app": app.name, "url": app.url}
                except FileNotFoundError:
                    return {"success": False, "error": "Chrome not found"}
                except Exception as e:
                    return {"success": False, "error": str(e)}

        available = [app.name for app in GOOGLE_SUITE_APPS]
        return {"success": False, "error": f"Unknown app: {app_name}", "available": available}

    @property
    def registered_functions(self) -> list[str]:
        return list(self._registered)

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "registered": len(self._registered),
            "available_gateway_functions": len(GATEWAY_HOST_FUNCTIONS),
            "available_pwa_apps": len(GOOGLE_SUITE_APPS),
            "vault_connected": self._vault is not None,
            "gateway_connected": self._gateway is not None,
        }
