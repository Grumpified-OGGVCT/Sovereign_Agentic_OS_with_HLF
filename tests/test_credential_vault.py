"""
Tests for CredentialVault — encrypted API key storage + auto-discovery.

Tests cover:
  - Provider detection from key prefixes
  - Credential add / remove / get
  - Capability queries (find by capability, provider)
  - Capability matrix generation
  - Encryption round-trip
  - Persistence (save/load)
  - Auto-discovery (mocked)
  - Best-for-capability selection
  - Listing and stats
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from agents.core.credential_vault import (
    CredentialVault,
    CredentialEntry,
    Capability,
    ProviderType,
    detect_provider,
    _encrypt,
    _decrypt,
    _infer_capabilities_from_models,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def vault(tmp_path: Path) -> CredentialVault:
    return CredentialVault(
        vault_dir=tmp_path / "vault",
        passphrase="test_passphrase",
        auto_discover=False,  # no live network calls
    )


@pytest.fixture
def vault_with_keys(vault: CredentialVault) -> CredentialVault:
    vault.add_key("AIzaSyFakeGoogleKey123", label="google_test")
    vault.add_key("sk-FakeOpenAIKey456", label="openai_test")
    vault.add_key("sk-ant-FakeAnthropicKey789", label="anthropic_test")
    return vault


# ─── Provider Detection ─────────────────────────────────────────────────────

class TestProviderDetection:
    def test_google_key(self) -> None:
        assert detect_provider("AIzaSyTest123") == ProviderType.GOOGLE

    def test_openai_key(self) -> None:
        assert detect_provider("sk-test123") == ProviderType.OPENAI

    def test_anthropic_key(self) -> None:
        # Must detect sk-ant before generic sk-
        assert detect_provider("sk-ant-test123") == ProviderType.ANTHROPIC

    def test_replicate_key(self) -> None:
        assert detect_provider("r8_test123") == ProviderType.REPLICATE

    def test_groq_key(self) -> None:
        assert detect_provider("gsk_test123") == ProviderType.GROQ

    def test_unknown_key(self) -> None:
        assert detect_provider("unknown_prefix_key") == ProviderType.CUSTOM


# ─── Encryption ──────────────────────────────────────────────────────────────

class TestEncryption:
    def test_roundtrip(self) -> None:
        plaintext = "sk-my-secret-api-key-12345"
        passphrase = "test_pass"
        encrypted = _encrypt(plaintext, passphrase)
        decrypted = _decrypt(encrypted, passphrase)
        assert decrypted == plaintext

    def test_different_passphrases(self) -> None:
        plaintext = "secret"
        encrypted = _encrypt(plaintext, "pass1")
        decrypted = _decrypt(encrypted, "pass1")
        assert decrypted == plaintext
        # Wrong passphrase gives empty string (decode failure)
        wrong = _decrypt(encrypted, "pass2")
        assert wrong != plaintext

    def test_encrypted_differs_from_plaintext(self) -> None:
        encrypted = _encrypt("hello", "key")
        assert encrypted != "hello"


# ─── Vault Operations ───────────────────────────────────────────────────────

class TestVaultOperations:
    def test_add_key(self, vault: CredentialVault) -> None:
        entry = vault.add_key("AIzaSyTest123", label="google")
        assert entry.provider == ProviderType.GOOGLE
        assert entry.label == "google"
        assert Capability.CHAT in entry.capabilities
        assert vault.count == 1

    def test_add_key_auto_label(self, vault: CredentialVault) -> None:
        entry = vault.add_key("sk-test123")
        assert entry.label.startswith("openai_")

    def test_remove_key(self, vault: CredentialVault) -> None:
        entry = vault.add_key("sk-test123")
        assert vault.count == 1
        vault.remove_key(entry.key_hash)
        assert vault.count == 0

    def test_get_key(self, vault: CredentialVault) -> None:
        entry = vault.add_key("sk-my-secret-key")
        raw = vault.get_key(entry.key_hash)
        assert raw == "sk-my-secret-key"

    def test_get_nonexistent_key(self, vault: CredentialVault) -> None:
        assert vault.get_key("nonexistent") is None

    def test_get_entry(self, vault: CredentialVault) -> None:
        entry = vault.add_key("AIzaSyTest")
        fetched = vault.get_entry(entry.key_hash)
        assert fetched is not None
        assert fetched.provider == ProviderType.GOOGLE

    def test_explicit_provider(self, vault: CredentialVault) -> None:
        entry = vault.add_key("custom_key_123", provider=ProviderType.OLLAMA)
        assert entry.provider == ProviderType.OLLAMA

    def test_explicit_base_url(self, vault: CredentialVault) -> None:
        entry = vault.add_key("key", base_url="http://localhost:4000/v1")
        assert entry.base_url == "http://localhost:4000/v1"


# ─── Capability Queries ─────────────────────────────────────────────────────

class TestCapabilityQueries:
    def test_find_by_capability(self, vault_with_keys: CredentialVault) -> None:
        chat_providers = vault_with_keys.find_by_capability(Capability.CHAT)
        assert len(chat_providers) == 3  # All providers have chat

    def test_find_by_capability_image(self, vault_with_keys: CredentialVault) -> None:
        image_providers = vault_with_keys.find_by_capability(Capability.IMAGE_GEN)
        labels = [e.label for e in image_providers]
        assert "google_test" in labels
        assert "openai_test" in labels

    def test_find_by_provider(self, vault_with_keys: CredentialVault) -> None:
        google = vault_with_keys.find_by_provider(ProviderType.GOOGLE)
        assert len(google) == 1
        assert google[0].label == "google_test"

    def test_capability_matrix(self, vault_with_keys: CredentialVault) -> None:
        matrix = vault_with_keys.capability_matrix()
        assert "chat" in matrix
        assert len(matrix["chat"]) == 3

    def test_best_for_capability(self, vault_with_keys: CredentialVault) -> None:
        key, entry = vault_with_keys.get_best_for_capability(Capability.CHAT)
        assert key is not None
        assert entry is not None
        assert Capability.CHAT in entry.capabilities

    def test_best_for_missing_capability(self, vault: CredentialVault) -> None:
        key, entry = vault.get_best_for_capability(Capability.FINE_TUNE)
        assert key is None
        assert entry is None


# ─── Listing ─────────────────────────────────────────────────────────────────

class TestListing:
    def test_list_entries(self, vault_with_keys: CredentialVault) -> None:
        entries = vault_with_keys.list_entries()
        assert len(entries) == 3
        # Should not contain raw keys
        for e in entries:
            assert "key" not in e or "key_hash" in e

    def test_providers(self, vault_with_keys: CredentialVault) -> None:
        providers = vault_with_keys.providers
        assert "google" in providers
        assert "openai" in providers
        assert "anthropic" in providers

    def test_entry_to_dict(self) -> None:
        entry = CredentialEntry(
            provider=ProviderType.GOOGLE,
            key_hash="abc123",
            label="test",
            capabilities={Capability.CHAT, Capability.VISION},
        )
        d = entry.to_dict()
        assert d["provider"] == "google"
        assert "chat" in d["capabilities"]


# ─── Persistence ─────────────────────────────────────────────────────────────

class TestPersistence:
    def test_save_and_reload(self, tmp_path: Path) -> None:
        vault1 = CredentialVault(
            vault_dir=tmp_path / "vault",
            passphrase="test",
            auto_discover=False,
        )
        vault1.add_key("sk-persist-test-key", label="persist_test")
        assert vault1.count == 1

        # Create new vault instance pointing to same dir
        vault2 = CredentialVault(
            vault_dir=tmp_path / "vault",
            passphrase="test",
            auto_discover=False,
        )
        assert vault2.count == 1
        entries = vault2.list_entries()
        assert entries[0]["label"] == "persist_test"

    def test_wrong_passphrase_fails_load(self, tmp_path: Path) -> None:
        vault1 = CredentialVault(
            vault_dir=tmp_path / "vault",
            passphrase="correct",
            auto_discover=False,
        )
        vault1.add_key("sk-test", label="test")

        # Wrong passphrase — should not crash, just empty
        vault2 = CredentialVault(
            vault_dir=tmp_path / "vault",
            passphrase="wrong",
            auto_discover=False,
        )
        # Will fail to parse, so count should be 0
        assert vault2.count == 0


# ─── Auto-Discovery ─────────────────────────────────────────────────────────

class TestAutoDiscovery:
    def test_infer_capabilities_from_models(self) -> None:
        models = [
            "gemini-3-pro",
            "text-embedding-004",
            "nano-banana-pro",
            "veo-3.1",
        ]
        caps = _infer_capabilities_from_models(models)
        assert Capability.CHAT in caps
        assert Capability.EMBEDDINGS in caps
        assert Capability.IMAGE_GEN in caps
        assert Capability.VIDEO_GEN in caps

    def test_infer_chat_models(self) -> None:
        caps = _infer_capabilities_from_models(["gpt-4o", "claude-3-opus"])
        assert Capability.CHAT in caps
        assert Capability.VISION in caps  # 4o and claude-3 have vision

    def test_infer_tts(self) -> None:
        caps = _infer_capabilities_from_models(["tts-1", "whisper-1"])
        assert Capability.TTS in caps
        assert Capability.STT in caps

    def test_auto_discover_on_add(self, tmp_path: Path) -> None:
        """Test that auto_discover=True triggers probe."""
        with patch("agents.core.credential_vault._probe_models") as mock_probe:
            mock_probe.return_value = ["gemini-3-pro", "text-embedding-004"]
            vault = CredentialVault(
                vault_dir=tmp_path / "vault",
                passphrase="test",
                auto_discover=True,
            )
            entry = vault.add_key("AIzaSyTest123", label="google_discover")
            mock_probe.assert_called_once()
            assert "gemini-3-pro" in entry.models
            assert Capability.EMBEDDINGS in entry.capabilities


# ─── Verification ────────────────────────────────────────────────────────────

class TestVerification:
    def test_verify_all(self, vault_with_keys: CredentialVault) -> None:
        with patch("agents.core.credential_vault._probe_models") as mock_probe:
            mock_probe.return_value = ["gpt-4o"]
            results = vault_with_keys.verify_all()
            assert len(results) == 3
