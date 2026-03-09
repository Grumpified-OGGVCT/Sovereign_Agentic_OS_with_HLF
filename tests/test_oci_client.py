"""
Tests for HLF OCI Module Distribution Client.

Covers:
  - OCIModuleRef URI parsing (valid, invalid, defaults)
  - OCIClient pull (cache hit, cache miss, checksum validation)
  - OCIClient push (success, file not found)
  - OCIClient list_tags
  - ModuleLoader OCI fallback integration
"""

from __future__ import annotations

import hashlib
import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hlf.oci_client import (
    OCIChecksumError,
    OCIClient,
    OCIError,
    OCIModuleRef,
    OCIPullResult,
    OCIRegistryError,
)


# ─── OCIModuleRef Parsing ────────────────────────────────────────────────────


class TestOCIModuleRefParsing:
    """Test OCI URI parsing."""

    def test_full_uri_with_tag(self):
        ref = OCIModuleRef.parse("ghcr.io/myorg/hlf-modules/math:v1.0.0")
        assert ref.registry == "ghcr.io"
        assert ref.namespace == "myorg/hlf-modules"
        assert ref.module == "math"
        assert ref.tag == "v1.0.0"

    def test_full_uri_without_tag(self):
        ref = OCIModuleRef.parse("ghcr.io/myorg/hlf-modules/crypto")
        assert ref.registry == "ghcr.io"
        assert ref.namespace == "myorg/hlf-modules"
        assert ref.module == "crypto"
        assert ref.tag == "latest"

    def test_namespace_module_only(self):
        ref = OCIModuleRef.parse("myorg/mymodule:v2")
        assert ref.registry == "ghcr.io"
        assert ref.namespace == "myorg"
        assert ref.module == "mymodule"
        assert ref.tag == "v2"

    def test_module_name_only(self):
        ref = OCIModuleRef.parse("math")
        assert ref.registry == "ghcr.io"
        assert ref.namespace == "Grumpified-OGGVCT/hlf-modules"
        assert ref.module == "math"
        assert ref.tag == "latest"

    def test_oci_scheme_stripped(self):
        ref = OCIModuleRef.parse("oci://ghcr.io/org/mod/pkg:1.0")
        assert ref.registry == "ghcr.io"
        assert ref.module == "pkg"
        assert ref.tag == "1.0"

    def test_localhost_registry(self):
        ref = OCIModuleRef.parse("localhost:5000/myorg/mymod:dev")
        assert ref.registry == "localhost:5000"
        assert ref.namespace == "myorg"
        assert ref.module == "mymod"
        assert ref.tag == "dev"

    def test_full_ref_property(self):
        ref = OCIModuleRef(registry="ghcr.io", namespace="org", module="math", tag="v1")
        assert ref.full_ref == "ghcr.io/org/math:v1"

    def test_api_path_property(self):
        ref = OCIModuleRef(registry="ghcr.io", namespace="org/sub", module="math", tag="v1")
        assert ref.api_path == "/v2/org/sub/math"

    def test_str_representation(self):
        ref = OCIModuleRef.parse("ghcr.io/org/math:v1")
        assert str(ref) == "ghcr.io/org/math:v1"

    def test_frozen_immutability(self):
        ref = OCIModuleRef.parse("math")
        with pytest.raises(AttributeError):
            ref.tag = "v2"  # type: ignore


# ─── OCIClient Pull ─────────────────────────────────────────────────────────


class TestOCIClientPull:
    """Test OCIClient.pull() with mocked HTTP."""

    @pytest.fixture
    def client(self, tmp_path):
        return OCIClient(cache_dir=tmp_path / "cache")

    @pytest.fixture
    def sample_hlf_content(self):
        return b'[INTENT] name="test_module" [/INTENT]\n'

    @pytest.fixture
    def sample_manifest(self, sample_hlf_content):
        sha = hashlib.sha256(sample_hlf_content).hexdigest()
        return {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.hlf.module.config.v1+json",
                "digest": "sha256:configdigest",
                "size": 10,
            },
            "layers": [
                {
                    "mediaType": "application/vnd.hlf.module.v1.hlf",
                    "digest": f"sha256:{sha}",
                    "size": len(sample_hlf_content),
                }
            ],
        }

    def test_pull_success(self, client, sample_hlf_content, sample_manifest):
        ref = OCIModuleRef.parse("math")
        sha = hashlib.sha256(sample_hlf_content).hexdigest()

        with patch.object(client, "_fetch_manifest", return_value=sample_manifest), \
             patch.object(client, "_fetch_blob", return_value=sample_hlf_content):
            result = client.pull(ref)

        assert result.local_path.exists()
        assert result.sha256 == sha
        assert result.size_bytes == len(sample_hlf_content)
        assert result.cached is False
        assert result.pull_time_ms >= 0

    def test_pull_cache_hit(self, client, sample_hlf_content, sample_manifest):
        ref = OCIModuleRef.parse("math")

        # First pull
        with patch.object(client, "_fetch_manifest", return_value=sample_manifest), \
             patch.object(client, "_fetch_blob", return_value=sample_hlf_content):
            result1 = client.pull(ref)

        # Second pull should be cached
        result2 = client.pull(ref)
        assert result2.cached is True
        assert result2.local_path == result1.local_path

    def test_pull_checksum_mismatch_raises(self, client, sample_hlf_content, sample_manifest):
        ref = OCIModuleRef.parse("math")

        with patch.object(client, "_fetch_manifest", return_value=sample_manifest), \
             patch.object(client, "_fetch_blob", return_value=sample_hlf_content):
            with pytest.raises(OCIChecksumError):
                client.pull(ref, expected_sha256="wrong_sha_value")

    def test_pull_no_layers_raises(self, client):
        ref = OCIModuleRef.parse("math")
        manifest = {"schemaVersion": 2, "layers": []}

        with patch.object(client, "_fetch_manifest", return_value=manifest):
            with pytest.raises(OCIRegistryError, match="No HLF module layer"):
                client.pull(ref)

    def test_pull_registry_error(self, client):
        ref = OCIModuleRef.parse("math")

        with patch.object(client, "_fetch_manifest", side_effect=OCIRegistryError("Not found", 404)):
            with pytest.raises(OCIRegistryError):
                client.pull(ref)

    def test_pull_cache_invalidation_on_checksum_change(self, client, tmp_path):
        ref = OCIModuleRef.parse("math")
        cache_path = client._cache_path(ref)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(b"old content")

        new_content = b"new content"
        sha = hashlib.sha256(new_content).hexdigest()
        manifest = {
            "schemaVersion": 2,
            "layers": [{"mediaType": "application/vnd.hlf.module.v1.hlf", "digest": f"sha256:{sha}", "size": len(new_content)}],
        }

        with patch.object(client, "_fetch_manifest", return_value=manifest), \
             patch.object(client, "_fetch_blob", return_value=new_content):
            result = client.pull(ref, expected_sha256=sha)

        assert result.cached is False
        assert result.sha256 == sha


# ─── OCIClient Push ──────────────────────────────────────────────────────────


class TestOCIClientPush:
    """Test OCIClient.push() with mocked HTTP."""

    @pytest.fixture
    def client(self, tmp_path):
        return OCIClient(cache_dir=tmp_path / "cache")

    def test_push_success(self, client, tmp_path):
        module_file = tmp_path / "test.hlf"
        module_file.write_text('[INTENT] name="test" [/INTENT]\n')
        ref = OCIModuleRef.parse("ghcr.io/org/hlf/test:v1")

        with patch.object(client, "_push_blob"), \
             patch.object(client, "_push_manifest"):
            sha = client.push(module_file, ref)

        expected_sha = hashlib.sha256(module_file.read_bytes()).hexdigest()
        assert sha == expected_sha

    def test_push_file_not_found(self, client):
        ref = OCIModuleRef.parse("test")
        with pytest.raises(FileNotFoundError):
            client.push(Path("/nonexistent.hlf"), ref)

    def test_push_string_ref(self, client, tmp_path):
        module_file = tmp_path / "mod.hlf"
        module_file.write_text('[INTENT] name="mod" [/INTENT]\n')

        with patch.object(client, "_push_blob"), \
             patch.object(client, "_push_manifest"):
            sha = client.push(module_file, "ghcr.io/org/hlf/mod:v1")
        assert len(sha) == 64


# ─── OCIClient List Tags ────────────────────────────────────────────────────


class TestOCIClientListTags:
    """Test OCIClient.list_tags()."""

    @pytest.fixture
    def client(self, tmp_path):
        return OCIClient(cache_dir=tmp_path / "cache")

    def test_list_tags_success(self, client):
        ref = OCIModuleRef.parse("math")
        response = json.dumps({"name": "math", "tags": ["v1.0.0", "latest", "v0.9.0"]}).encode()

        with patch.object(client, "_http_get", return_value=response):
            tags = client.list_tags(ref)

        assert tags == ["latest", "v0.9.0", "v1.0.0"]  # sorted

    def test_list_tags_empty(self, client):
        ref = OCIModuleRef.parse("math")
        response = json.dumps({"name": "math", "tags": []}).encode()

        with patch.object(client, "_http_get", return_value=response):
            tags = client.list_tags(ref)

        assert tags == []

    def test_list_tags_string_ref(self, client):
        response = json.dumps({"name": "crypto", "tags": ["v1"]}).encode()

        with patch.object(client, "_http_get", return_value=response):
            tags = client.list_tags("crypto")

        assert tags == ["v1"]


# ─── OCIClient Cache ─────────────────────────────────────────────────────────


class TestOCIClientCache:
    """Test OCIClient cache management."""

    @pytest.fixture
    def client(self, tmp_path):
        return OCIClient(cache_dir=tmp_path / "cache")

    def test_clear_cache_empty(self, client):
        count = client.clear_cache()
        assert count == 0

    def test_clear_cache_with_files(self, client, tmp_path):
        # Create mock cached files
        sub = client.cache_dir / "ghcr.io" / "org"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "math_v1.hlf").write_text("content")
        (sub / "crypto_latest.hlf").write_text("content")

        count = client.clear_cache()
        assert count == 2

    def test_cache_path_deterministic(self, client):
        ref = OCIModuleRef.parse("ghcr.io/org/math:v1")
        path1 = client._cache_path(ref)
        path2 = client._cache_path(ref)
        assert path1 == path2

    def test_cache_path_unique_per_ref(self, client):
        ref1 = OCIModuleRef.parse("ghcr.io/org/math:v1")
        ref2 = OCIModuleRef.parse("ghcr.io/org/math:v2")
        assert client._cache_path(ref1) != client._cache_path(ref2)


# ─── ModuleLoader OCI Integration ────────────────────────────────────────────


class TestModuleLoaderOCIFallback:
    """Test ModuleLoader with OCI fallback enabled."""

    def test_local_resolution_preferred_over_oci(self, tmp_path):
        """Local modules should resolve before OCI is tried."""
        from hlf.runtime import ModuleLoader

        stdlib = tmp_path / "stdlib"
        stdlib.mkdir()
        (stdlib / "math.hlf").write_text('[INTENT] name="math" [/INTENT]')

        loader = ModuleLoader(
            search_paths=[stdlib],
            oci_enabled=True,
        )
        path = loader.resolve_path("math")
        assert path is not None
        assert path == stdlib / "math.hlf"

    def test_oci_fallback_when_local_not_found(self, tmp_path):
        """OCI should be tried when local resolution fails."""
        from hlf.runtime import ModuleLoader

        loader = ModuleLoader(
            search_paths=[tmp_path / "empty"],
            oci_enabled=True,
            oci_cache_dir=tmp_path / "cache",
        )

        # Mock the OCI client pull to return a cached file
        cache_file = tmp_path / "cache" / "test.hlf"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text('[INTENT] name="remote_mod" [/INTENT]')

        mock_result = OCIPullResult(
            ref="ghcr.io/org/remote_mod:latest",
            local_path=cache_file,
            sha256="abc123",
            size_bytes=100,
        )

        mock_client = MagicMock()
        mock_client.pull.return_value = mock_result
        loader._oci_client = mock_client

        path = loader.resolve_path("remote_mod")
        assert path == cache_file
        mock_client.pull.assert_called_once()

    def test_oci_disabled_skips_fallback(self, tmp_path):
        """When OCI is disabled, no fallback should occur."""
        from hlf.runtime import ModuleLoader

        loader = ModuleLoader(
            search_paths=[tmp_path / "empty"],
            oci_enabled=False,
        )
        path = loader.resolve_path("nonexistent")
        assert path is None

    def test_oci_failure_returns_none(self, tmp_path):
        """OCI errors should be caught and return None."""
        from hlf.runtime import ModuleLoader

        loader = ModuleLoader(
            search_paths=[tmp_path / "empty"],
            oci_enabled=True,
            oci_cache_dir=tmp_path / "cache",
        )

        mock_client = MagicMock()
        mock_client.pull.side_effect = OCIRegistryError("Network error")
        loader._oci_client = mock_client

        path = loader.resolve_path("broken_mod")
        assert path is None


# ─── OCIModuleRef Edge Cases ─────────────────────────────────────────────────


class TestOCIModuleRefEdgeCases:
    """Edge cases for URI parsing."""

    def test_deeply_nested_namespace(self):
        ref = OCIModuleRef.parse("ghcr.io/org/sub1/sub2/module:v1")
        assert ref.namespace == "org/sub1/sub2"
        assert ref.module == "module"

    def test_tag_with_dots(self):
        ref = OCIModuleRef.parse("ghcr.io/org/mod:1.2.3-beta.1")
        assert ref.tag == "1.2.3-beta.1"

    def test_default_tag_is_latest(self):
        ref = OCIModuleRef.parse("math")
        assert ref.tag == "latest"

    def test_sha256_file_utility(self, tmp_path):
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"hello world")
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert OCIClient._sha256_file(test_file) == expected
