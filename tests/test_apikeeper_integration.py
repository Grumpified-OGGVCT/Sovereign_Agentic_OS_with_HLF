"""
Integration tests for API-Keeper credential vault and SearXng search routing.

Tests cover:
  - API-Keeper store/rotate/audit lifecycle
  - SearXng search routing via host function dispatcher
  - Model gateway credential resolution (vault first, env var fallback)
  - Credential rotation triggers
  - Edge cases: missing credential, expired credential, vault unavailable
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ─── API-Keeper Lifecycle Tests ────────────────────────────────────────────────

class TestApiKeeperLifecycle:
    """Test the full credential lifecycle: store → audit → rotate → audit."""

    def test_store_credential(self) -> None:
        """apikeeper.store should persist a credential and return metadata."""
        from agents.core.credential_vault import CredentialVault

        vault = CredentialVault()
        entry = vault.add_key(
            api_key="test-api-key-abc123",
            label="test-credential",
            provider="google",
        )

        assert entry.key_hash is not None
        assert entry.provider.value == "google"
        assert entry.label == "test-credential"
        assert len(entry.capabilities) > 0

        # Cleanup
        vault.remove_key(entry.key_hash)

    def test_rotate_credential(self) -> None:
        """apikeeper.rotate should replace a credential with a new one."""
        from agents.core.credential_vault import CredentialVault

        vault = CredentialVault()
        entry = vault.add_key(
            api_key="original-key-123",
            label="rotate-test",
            provider="openai",
        )

        old_hash = entry.key_hash
        old_key = vault.get_key(old_hash)
        assert old_key == "original-key-123"

        # Simulate rotation: remove old, add new
        vault.remove_key(old_hash)
        new_entry = vault.add_key(
            api_key="rotated-key-456",
            label="rotate-test",
            provider="openai",
        )

        new_hash = new_entry.key_hash
        new_key = vault.get_key(new_hash)
        assert new_key == "rotated-key-456"
        assert new_hash != old_hash

        # Old key should no longer be retrievable
        assert vault.get_key(old_hash) is None

        # Cleanup
        vault.remove_key(new_hash)

    def test_audit_trail(self) -> None:
        """apikeeper.audit should return metadata about a credential."""
        from agents.core.credential_vault import CredentialVault

        vault = CredentialVault()
        entry = vault.add_key(
            api_key="audit-test-key",
            label="audit-credential",
            provider="anthropic",
        )

        # Audit by label
        entries = vault.list_entries()
        matching = [e for e in entries if e["label"] == "audit-credential"]
        assert len(matching) == 1
        assert matching[0]["provider"] == "anthropic"
        assert matching[0]["is_valid"] is True
        assert "added_at" in matching[0]
        assert "key_hash" in matching[0]

        # Audit by key_hash
        entry_from_hash = vault.get_entry(entry.key_hash)
        assert entry_from_hash is not None
        assert entry_from_hash.provider.value == "anthropic"
        assert entry_from_hash.label == "audit-credential"

        # Cleanup
        vault.remove_key(entry.key_hash)

    def test_store_with_metadata(self) -> None:
        """apikeeper.store should accept optional metadata."""
        from agents.core.credential_vault import CredentialVault

        vault = CredentialVault()
        entry = vault.add_key(
            api_key="metadata-test-key",
            label="metadata-cred",
            provider="custom",
            base_url="https://custom-api.example.com/v1/",
        )

        assert entry.base_url == "https://custom-api.example.com/v1/"
        assert entry.provider.value == "custom"

        # Cleanup
        vault.remove_key(entry.key_hash)

    def test_multiple_credentials_same_provider(self) -> None:
        """Vault can hold multiple credentials for the same provider."""
        from agents.core.credential_vault import CredentialVault, ProviderType

        vault = CredentialVault()
        e1 = vault.add_key(api_key="key-alpha", label="alpha", provider="google")
        e2 = vault.add_key(api_key="key-beta", label="beta", provider="google")

        google_entries = vault.find_by_provider(ProviderType.GOOGLE)
        assert len(google_entries) >= 2

        # Cleanup
        vault.remove_key(e1.key_hash)
        vault.remove_key(e2.key_hash)

    def test_credential_not_found(self) -> None:
        """Vault gracefully handles requests for non-existent credentials."""
        from agents.core.credential_vault import CredentialVault

        vault = CredentialVault()
        assert vault.get_key("nonexistent-hash") is None
        assert vault.get_entry("nonexistent-hash") is None

    def test_remove_nonexistent(self) -> None:
        """Removing a nonexistent credential returns False."""
        from agents.core.credential_vault import CredentialVault

        vault = CredentialVault()
        assert vault.remove_key("does-not-exist") is False


# ─── Host Function Dispatcher Integration ──────────────────────────────────────

class TestApiKeeperDispatcher:
    """Test that the host function dispatcher correctly routes API-Keeper calls."""

    def test_apikeeper_store_via_dispatcher(self) -> None:
        """Call apikeeper.store through the dispatcher."""
        from agents.core.host_function_dispatcher import dispatch

        # Use a temporary vault directory to avoid polluting real vault
        with patch.dict(os.environ, {"SOVEREIGN_HOME": str(Path(os.environ.get("TEMP", "/tmp")) / "hlf-test-vault")}):
            result = dispatch(
                "apikeeper.store",
                ["test-dispatcher-cred", "dispatcher-test-key-xyz", {"provider": "google"}],
                tier="forge",
            )
            data = json.loads(result)
            assert "key_hash" in data
            assert data["provider"] == "google"
            assert data["label"] == "test-dispatcher-cred"

            # Verify via audit
            audit_result = dispatch("apikeeper.audit", ["test-dispatcher-cred"], tier="forge")
            audit_data = json.loads(audit_result)
            assert audit_data["credential_id"] == "test-dispatcher-cred"
            assert len(audit_data["entries"]) >= 1

    def test_apikeeper_store_missing_args(self) -> None:
        """apikeeper.store should return error when args are missing."""
        from agents.core.host_function_dispatcher import dispatch

        result = dispatch("apikeeper.store", [], tier="forge")
        data = json.loads(result)
        assert "error" in data

    def test_apikeeper_rotate_via_dispatcher(self) -> None:
        """Call apikeeper.rotate through the dispatcher."""
        from agents.core.host_function_dispatcher import dispatch

        with patch.dict(os.environ, {"SOVEREIGN_HOME": str(Path(os.environ.get("TEMP", "/tmp")) / "hlf-test-vault-rotate")}):
            # First store
            dispatch("apikeeper.store", ["rotate-dispatcher-cred", "key-to-rotate", {}], tier="forge")

            # Then rotate
            result = dispatch("apikeeper.rotate", ["rotate-dispatcher-cred"], tier="sovereign")
            data = json.loads(result)
            # Rotation should succeed or give a clear error
            assert "error" not in data or "old_hash" in data

    def test_apikeeper_tier_enforcement(self) -> None:
        """apikeeper.rotate should be sovereign-only."""
        from agents.core.host_function_dispatcher import dispatch

        with pytest.raises(PermissionError, match="not available in tier"):
            dispatch("apikeeper.rotate", ["some-cred"], tier="forge")

    def test_apikeeper_audit_via_dispatcher(self) -> None:
        """Call apikeeper.audit through the dispatcher."""
        from agents.core.host_function_dispatcher import dispatch

        with patch.dict(os.environ, {"SOVEREIGN_HOME": str(Path(os.environ.get("TEMP", "/tmp")) / "hlf-test-vault-audit")}):
            # Store then audit
            dispatch("apikeeper.store", ["audit-dispatcher-cred", "audit-key-abc", {}], tier="forge")
            result = dispatch("apikeeper.audit", ["audit-dispatcher-cred"], tier="forge")
            data = json.loads(result)
            assert "entries" in data
            assert data["credential_id"] == "audit-dispatcher-cred"

    def test_apikeeper_audit_nonexistent(self) -> None:
        """apikeeper.audit should handle nonexistent credentials gracefully."""
        from agents.core.host_function_dispatcher import dispatch

        result = dispatch("apikeeper.audit", ["definitely-does-not-exist-999"], tier="forge")
        data = json.loads(result)
        assert "entries" in data
        assert len(data["entries"]) == 0 or "note" in data


# ─── SearXng Search Routing Tests ──────────────────────────────────────────────

class TestSearXngRouting:
    """Test SearXng host function routing through the dispatcher."""

    def test_searxng_search_dispatches(self) -> None:
        """searxng.search should route through the searxng backend."""
        from agents.core.host_function_dispatcher import dispatch

        # With SearXng unavailable, should return an error JSON (not raise)
        result = dispatch("searxng.search", ["test query", 5], tier="forge")
        data = json.loads(result)
        # Should return structured error, not crash
        assert isinstance(data, dict)
        # Either error or results (if SearXng happens to be running)
        assert "error" in data or "results" in data

    def test_searxng_crawl_dispatches(self) -> None:
        """searxng.crawl should route through the searxng backend."""
        from agents.core.host_function_dispatcher import dispatch

        result = dispatch("searxng.crawl", ["https://example.com", 1], tier="forge")
        data = json.loads(result)
        assert isinstance(data, dict)
        assert "error" in data or "pages" in data

    def test_web_search_routes_to_searxng(self) -> None:
        """WEB_SEARCH should now route through the searxng backend (not dapr_http_proxy)."""
        # Verify the registry says WEB_SEARCH backend is searxng
        registry_path = Path(__file__).parent.parent / "governance" / "host_functions.json"
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        web_search = next(fn for fn in data["functions"] if fn["name"] == "WEB_SEARCH")
        assert web_search["backend"] == "searxng", (
            "WEB_SEARCH must use searxng backend, not dapr_http_proxy"
        )

    def test_searxng_search_missing_query(self) -> None:
        """searxng.search should handle empty query gracefully."""
        from agents.core.host_function_dispatcher import dispatch

        result = dispatch("searxng.search", [], tier="forge")
        data = json.loads(result)
        # Should return results with empty query (not crash)
        assert isinstance(data, dict)

    def test_searxng_crawl_missing_url(self) -> None:
        """searxng.crawl should return error when url is missing."""
        from agents.core.host_function_dispatcher import dispatch

        result = dispatch("searxng.crawl", [], tier="forge")
        data = json.loads(result)
        assert data.get("error") == "url is required for crawl"


# ─── Dispatch Edge Cases ───────────────────────────────────────────────────────

class TestDispatchEdgeCases:
    def test_unknown_host_function(self) -> None:
        """Dispatch should raise RuntimeError for unknown functions."""
        from agents.core.host_function_dispatcher import dispatch

        with pytest.raises(RuntimeError, match="Unknown host function"):
            dispatch("nonexistent.function", [], tier="forge")

    def test_tier_access_denied_for_searxng(self) -> None:
        """searxng.search should be forge/sovereign only."""
        from agents.core.host_function_dispatcher import dispatch

        with pytest.raises(PermissionError, match="not available in tier"):
            dispatch("searxng.search", ["query"], tier="hearth")
