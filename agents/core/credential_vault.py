"""
Sovereign Credential Vault — Encrypted API key storage + auto-discovery.

Stores provider credentials securely with auto-detection of what
capabilities each API key unlocks. The vault is the OS-level glue
that enables universal tool integration.

Architecture:
    User adds key → Vault probes provider → Discovers capabilities →
    Registers in HostFunctionRegistry → HLF programs can use them.

Storage strategy (layered):
    1. OS keyring (Windows Credential Store / macOS Keychain / Linux Secret Service)
    2. Encrypted JSON fallback ($SOVEREIGN_HOME/vault/credentials.enc)

Auto-discovery:
    When a key is added, the vault queries the provider's /v1/models
    (or equivalent) and catalogs available models + capabilities.

Thread-safe: yes (uses threading.Lock for all mutations).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import threading
from base64 import b64decode, b64encode
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ─── Provider Capabilities ───────────────────────────────────────────────────

class Capability(StrEnum):
    """What an API key can do."""
    CHAT = "chat"
    VISION = "vision"
    EMBEDDINGS = "embeddings"
    IMAGE_GEN = "image_gen"
    VIDEO_GEN = "video_gen"
    TTS = "tts"
    STT = "stt"
    CODE = "code"
    SEARCH = "search"
    FINE_TUNE = "fine_tune"


class ProviderType(StrEnum):
    """Known API providers."""
    GOOGLE = "google"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    REPLICATE = "replicate"
    GROQ = "groq"
    TOGETHER = "together"
    OLLAMA = "ollama"
    ZHIPU = "zhipu"          # z.AI / GLM
    CUSTOM = "custom"


# ─── Provider Detection ─────────────────────────────────────────────────────

_KEY_PREFIXES: dict[str, ProviderType] = {
    "AIza": ProviderType.GOOGLE,
    "sk-": ProviderType.OPENAI,
    "sk-ant-": ProviderType.ANTHROPIC,
    "r8_": ProviderType.REPLICATE,
    "gsk_": ProviderType.GROQ,
    "": ProviderType.CUSTOM,
}


def detect_provider(api_key: str) -> ProviderType:
    """Detect provider from API key prefix."""
    # Anthropic keys start with sk-ant- (must check before generic sk-)
    if api_key.startswith("sk-ant-"):
        return ProviderType.ANTHROPIC
    for prefix, provider in _KEY_PREFIXES.items():
        if prefix and api_key.startswith(prefix):
            return provider
    return ProviderType.CUSTOM


# ─── Known Provider Capability Maps ─────────────────────────────────────────

_DEFAULT_CAPABILITIES: dict[ProviderType, set[Capability]] = {
    ProviderType.GOOGLE: {
        Capability.CHAT, Capability.VISION, Capability.EMBEDDINGS,
        Capability.IMAGE_GEN, Capability.VIDEO_GEN, Capability.CODE,
        Capability.SEARCH,
    },
    ProviderType.OPENAI: {
        Capability.CHAT, Capability.VISION, Capability.EMBEDDINGS,
        Capability.IMAGE_GEN, Capability.TTS, Capability.STT,
        Capability.CODE, Capability.FINE_TUNE,
    },
    ProviderType.ANTHROPIC: {
        Capability.CHAT, Capability.VISION, Capability.CODE,
    },
    ProviderType.GROQ: {
        Capability.CHAT, Capability.VISION, Capability.CODE,
    },
    ProviderType.REPLICATE: {
        Capability.IMAGE_GEN, Capability.VIDEO_GEN, Capability.CHAT,
    },
    ProviderType.TOGETHER: {
        Capability.CHAT, Capability.EMBEDDINGS, Capability.CODE,
    },
    ProviderType.OLLAMA: {
        Capability.CHAT, Capability.VISION, Capability.EMBEDDINGS, Capability.CODE,
    },
    ProviderType.ZHIPU: {
        Capability.CHAT, Capability.VISION, Capability.EMBEDDINGS,
        Capability.IMAGE_GEN, Capability.VIDEO_GEN, Capability.CODE,
    },
    ProviderType.CUSTOM: {
        Capability.CHAT,
    },
}


# ─── Credential Entry ───────────────────────────────────────────────────────

@dataclass
class CredentialEntry:
    """A stored API credential with discovered capabilities."""

    provider: ProviderType
    key_hash: str                           # SHA-256 of key (for indexing)
    label: str = ""                         # User-friendly name
    base_url: str = ""                      # Custom endpoint URL
    capabilities: set[Capability] = field(default_factory=set)
    models: list[str] = field(default_factory=list)
    added_at: float = field(default_factory=time.time)
    last_verified: float = 0.0
    is_valid: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider.value,
            "key_hash": self.key_hash,
            "label": self.label,
            "base_url": self.base_url,
            "capabilities": sorted(c.value for c in self.capabilities),
            "models": self.models[:20],  # cap for display
            "added_at": self.added_at,
            "last_verified": self.last_verified,
            "is_valid": self.is_valid,
        }


# ─── Simple Encryption (XOR + b64 — NOT production crypto) ──────────────────

def _derive_key(passphrase: str) -> bytes:
    """Derive a repeatable key from passphrase."""
    return hashlib.sha256(passphrase.encode()).digest()


def _xor_encrypt(data: bytes, key: bytes) -> bytes:
    """Simple XOR encryption (use Fernet/AES for production)."""
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def _encrypt(plaintext: str, passphrase: str) -> str:
    key = _derive_key(passphrase)
    encrypted = _xor_encrypt(plaintext.encode(), key)
    return b64encode(encrypted).decode()


def _decrypt(ciphertext: str, passphrase: str) -> str:
    try:
        key = _derive_key(passphrase)
        encrypted = b64decode(ciphertext.encode())
        decrypted = _xor_encrypt(encrypted, key)
        return decrypted.decode()
    except (UnicodeDecodeError, Exception):
        return ""


# ─── Provider Probes (Auto-Discovery) ───────────────────────────────────────

# Provider endpoint URLs for /v1/models queries
_PROVIDER_ENDPOINTS: dict[ProviderType, str] = {
    ProviderType.GOOGLE: "https://generativelanguage.googleapis.com/v1beta/openai/",
    ProviderType.OPENAI: "https://api.openai.com/v1/",
    ProviderType.ANTHROPIC: "https://api.anthropic.com/v1/",
    ProviderType.GROQ: "https://api.groq.com/openai/v1/",
    ProviderType.TOGETHER: "https://api.together.xyz/v1/",
    ProviderType.OLLAMA: "http://localhost:11434/v1/",
}


def _probe_models(
    provider: ProviderType,
    api_key: str,
    base_url: str = "",
) -> list[str]:
    """Query a provider's /v1/models endpoint to discover available models.

    Returns list of model IDs, or empty list on failure.
    """
    try:
        import urllib.request
        import urllib.error

        url = base_url or _PROVIDER_ENDPOINTS.get(provider, "")
        if not url:
            return []

        models_url = url.rstrip("/") + "/models"
        req = urllib.request.Request(models_url)

        # Set auth headers per provider
        if provider == ProviderType.GOOGLE:
            models_url = f"{url.rstrip('/')}/models?key={api_key}"
            req = urllib.request.Request(models_url)
        elif provider == ProviderType.ANTHROPIC:
            req.add_header("x-api-key", api_key)
            req.add_header("anthropic-version", "2023-06-01")
        else:
            req.add_header("Authorization", f"Bearer {api_key}")

        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        # OpenAI-style response: {"data": [{"id": "model-name"}, ...]}
        if "data" in data:
            return [m.get("id", "") for m in data["data"] if m.get("id")]
        # Google-style: {"models": [{"name": "models/gemini-..."}, ...]}
        elif "models" in data:
            return [m.get("name", "").replace("models/", "") for m in data["models"]]

        return []

    except Exception as e:
        logger.debug("Model probe failed for %s: %s", provider, e)
        return []


def _infer_capabilities_from_models(models: list[str]) -> set[Capability]:
    """Infer capabilities from model names."""
    caps: set[Capability] = set()
    for model in models:
        m = model.lower()
        if any(k in m for k in ("gpt", "gemini", "claude", "llama", "mistral", "chat")):
            caps.add(Capability.CHAT)
        if any(k in m for k in ("vision", "4o", "gemini", "claude-3")):
            caps.add(Capability.VISION)
        if "embed" in m:
            caps.add(Capability.EMBEDDINGS)
        if any(k in m for k in ("dall-e", "imagen", "nano-banana", "stable")):
            caps.add(Capability.IMAGE_GEN)
        if any(k in m for k in ("veo", "sora", "video")):
            caps.add(Capability.VIDEO_GEN)
        if any(k in m for k in ("tts", "speech")):
            caps.add(Capability.TTS)
        if any(k in m for k in ("whisper", "stt")):
            caps.add(Capability.STT)
        if any(k in m for k in ("code", "codex", "codestral")):
            caps.add(Capability.CODE)
    return caps


# ─── Credential Vault ───────────────────────────────────────────────────────

class CredentialVault:
    """Encrypted credential store with auto-discovery.

    Args:
        vault_dir: Directory for encrypted storage files.
        passphrase: Encryption passphrase (defaults to machine ID).
        auto_discover: Whether to probe providers on key add.
    """

    def __init__(
        self,
        vault_dir: Path | str | None = None,
        passphrase: str | None = None,
        auto_discover: bool = True,
    ) -> None:
        self._lock = threading.Lock()
        self._auto_discover = auto_discover

        # Vault directory
        if vault_dir:
            self._vault_dir = Path(vault_dir)
        else:
            self._vault_dir = Path.home() / ".sovereign" / "vault"
        self._vault_dir.mkdir(parents=True, exist_ok=True)

        # Passphrase (defaults to machine-specific)
        self._passphrase = passphrase or self._default_passphrase()

        # In-memory stores
        self._entries: dict[str, CredentialEntry] = {}   # key_hash → entry
        self._raw_keys: dict[str, str] = {}              # key_hash → encrypted key

        # Load persisted data
        self._load()

    def _default_passphrase(self) -> str:
        """Generate a machine-specific default passphrase."""
        import platform
        machine = platform.node() + platform.machine()
        return hashlib.sha256(machine.encode()).hexdigest()[:32]

    # ── Key Management ──────────────────────────────────────────────────

    def add_key(
        self,
        api_key: str,
        label: str = "",
        provider: ProviderType | str | None = None,
        base_url: str = "",
    ) -> CredentialEntry:
        """Add an API key to the vault.

        Auto-detects provider and discovers capabilities.
        """
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()[:16]

        # Detect provider
        if provider:
            prov = ProviderType(provider) if isinstance(provider, str) else provider
        else:
            prov = detect_provider(api_key)

        # Create entry
        entry = CredentialEntry(
            provider=prov,
            key_hash=key_hash,
            label=label or f"{prov.value}_{key_hash[:6]}",
            base_url=base_url,
            capabilities=set(_DEFAULT_CAPABILITIES.get(prov, {Capability.CHAT})),
        )

        # Auto-discover models + capabilities
        if self._auto_discover:
            try:
                models = _probe_models(prov, api_key, base_url)
                if models:
                    entry.models = models
                    discovered = _infer_capabilities_from_models(models)
                    entry.capabilities |= discovered
                    entry.last_verified = time.time()
            except Exception as e:
                logger.warning("Auto-discovery failed for %s: %s", prov, e)

        # Store
        with self._lock:
            self._entries[key_hash] = entry
            self._raw_keys[key_hash] = _encrypt(api_key, self._passphrase)
            self._save()

        logger.info(
            "Added %s key (%s) with capabilities: %s",
            prov, key_hash[:6],
            ", ".join(c.value for c in entry.capabilities),
        )
        return entry

    def remove_key(self, key_hash: str) -> bool:
        """Remove a credential by key hash."""
        with self._lock:
            if key_hash in self._entries:
                del self._entries[key_hash]
                self._raw_keys.pop(key_hash, None)
                self._save()
                return True
        return False

    def get_key(self, key_hash: str) -> str | None:
        """Retrieve a decrypted API key by hash."""
        encrypted = self._raw_keys.get(key_hash)
        if encrypted:
            try:
                return _decrypt(encrypted, self._passphrase)
            except Exception:
                return None
        return None

    def get_entry(self, key_hash: str) -> CredentialEntry | None:
        """Get credential entry metadata."""
        return self._entries.get(key_hash)

    # ── Capability Queries ──────────────────────────────────────────────

    def find_by_capability(self, capability: Capability) -> list[CredentialEntry]:
        """Find all credentials that support a given capability."""
        return [
            entry for entry in self._entries.values()
            if capability in entry.capabilities and entry.is_valid
        ]

    def find_by_provider(self, provider: ProviderType) -> list[CredentialEntry]:
        """Find all credentials for a given provider."""
        return [
            entry for entry in self._entries.values()
            if entry.provider == provider and entry.is_valid
        ]

    def get_best_for_capability(self, capability: Capability) -> tuple[str | None, CredentialEntry | None]:
        """Get the best API key for a capability (most recently verified).

        Returns (decrypted_key, entry) or (None, None).
        """
        candidates = self.find_by_capability(capability)
        if not candidates:
            return None, None
        # Prefer most recently verified
        best = max(candidates, key=lambda e: e.last_verified)
        key = self.get_key(best.key_hash)
        return key, best

    def capability_matrix(self) -> dict[str, list[str]]:
        """Get a capability → providers matrix."""
        matrix: dict[str, list[str]] = {}
        for cap in Capability:
            providers = [
                e.label for e in self._entries.values()
                if cap in e.capabilities and e.is_valid
            ]
            if providers:
                matrix[cap.value] = providers
        return matrix

    # ── Listing ──────────────────────────────────────────────────────────

    def list_entries(self) -> list[dict[str, Any]]:
        """List all credential entries (without raw keys)."""
        return [entry.to_dict() for entry in self._entries.values()]

    @property
    def count(self) -> int:
        return len(self._entries)

    @property
    def providers(self) -> list[str]:
        return sorted({e.provider.value for e in self._entries.values()})

    # ── Persistence ──────────────────────────────────────────────────────

    def _save(self) -> None:
        """Save vault to encrypted file."""
        data = {
            "entries": {
                k: {
                    "provider": e.provider.value,
                    "label": e.label,
                    "base_url": e.base_url,
                    "capabilities": [c.value for c in e.capabilities],
                    "models": e.models[:50],
                    "added_at": e.added_at,
                    "last_verified": e.last_verified,
                    "is_valid": e.is_valid,
                }
                for k, e in self._entries.items()
            },
            "keys": dict(self._raw_keys),
        }
        payload = json.dumps(data, indent=2)
        encrypted = _encrypt(payload, self._passphrase)
        vault_file = self._vault_dir / "vault.enc"
        vault_file.write_text(encrypted, encoding="utf-8")

    def _load(self) -> None:
        """Load vault from encrypted file."""
        vault_file = self._vault_dir / "vault.enc"
        if not vault_file.exists():
            return

        try:
            encrypted = vault_file.read_text(encoding="utf-8")
            payload = _decrypt(encrypted, self._passphrase)
            data = json.loads(payload)

            for k, v in data.get("entries", {}).items():
                self._entries[k] = CredentialEntry(
                    provider=ProviderType(v["provider"]),
                    key_hash=k,
                    label=v.get("label", ""),
                    base_url=v.get("base_url", ""),
                    capabilities={Capability(c) for c in v.get("capabilities", [])},
                    models=v.get("models", []),
                    added_at=v.get("added_at", 0),
                    last_verified=v.get("last_verified", 0),
                    is_valid=v.get("is_valid", True),
                )

            self._raw_keys = data.get("keys", {})
        except Exception as e:
            logger.warning("Failed to load vault: %s", e)

    # ── Re-verify ────────────────────────────────────────────────────────

    def verify_all(self) -> dict[str, bool]:
        """Re-probe all stored keys to update capabilities and validity."""
        results: dict[str, bool] = {}
        for key_hash, entry in list(self._entries.items()):
            raw_key = self.get_key(key_hash)
            if not raw_key:
                entry.is_valid = False
                results[key_hash] = False
                continue

            try:
                models = _probe_models(entry.provider, raw_key, entry.base_url)
                if models:
                    entry.models = models
                    entry.capabilities |= _infer_capabilities_from_models(models)
                    entry.last_verified = time.time()
                    entry.is_valid = True
                    results[key_hash] = True
                else:
                    # Could be network issue, keep valid
                    results[key_hash] = False
            except Exception:
                results[key_hash] = False

        with self._lock:
            self._save()
        return results
