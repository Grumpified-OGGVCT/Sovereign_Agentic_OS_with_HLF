"""
tests/test_oci_client.py — Unit tests for the HLF OCI Distribution Client.

Tests module reference parsing, pull/push operations (with mocked HTTP),
checksum validation, cache behavior, and error handling.
"""

from __future__ import annotations

import hashlib
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

from hlf.oci_client import (
    OCIChecksumError,
    OCIClient,
    OCIError,
    OCIModuleRef,
    OCIPullResult,
    OCIRegistryError,
)


# ─── Module Reference Parsing Tests ─────────────────────────────────────────


class TestOCIModuleRef:
    """Tests for OCIModuleRef.parse() and properties."""

    def test_parse_full_uri(self):
        ref = OCIModuleRef.parse("ghcr.io/org/hlf-modules/math:v1.0.0")
        assert ref.registry == "ghcr.io"
        assert ref.namespace == "org/hlf-modules"
        assert ref.module == "math"
        assert ref.tag == "v1.0.0"

    def test_parse_without_tag_defaults_latest(self):
        ref = OCIModuleRef.parse("ghcr.io/org/hlf-modules/crypto")
        assert ref.tag == "latest"

    def test_parse_short_namespace_module(self):
        ref = OCIModuleRef.parse("myorg/mymodule:v2")
        assert ref.registry == "ghcr.io"
        assert ref.namespace == "myorg"
        assert ref.module == "mymodule"
        assert ref.tag == "v2"

    def test_parse_bare_module_name(self):
        ref = OCIModuleRef.parse("math")
        assert ref.registry == "ghcr.io"
        assert ref.namespace == "Grumpified-OGGVCT/hlf-modules"
        assert ref.module == "math"
        assert ref.tag == "latest"

    def test_parse_oci_protocol_prefix(self):
        ref = OCIModuleRef.parse("oci://ghcr.io/org/mod/crypto:v1")
        assert ref.registry == "ghcr.io"
        assert ref.module == "crypto"
        assert ref.tag == "v1"

    def test_full_ref_property(self):
        ref = OCIModuleRef(registry="ghcr.io", namespace="org/mods", module="math", tag="v1")
        assert ref.full_ref == "ghcr.io/org/mods/math:v1"

    def test_api_path_property(self):
        ref = OCIModuleRef(registry="ghcr.io", namespace="org/mods", module="math", tag="v1")
        assert ref.api_path == "/v2/org/mods/math"

    def test_str_equals_full_ref(self):
        ref = OCIModuleRef.parse("ghcr.io/org/mods/io:v2")
        assert str(ref) == ref.full_ref

    def test_localhost_registry(self):
        ref = OCIModuleRef.parse("localhost:5000/myns/mymod:dev")
        assert ref.registry == "localhost:5000"
        assert ref.namespace == "myns"
        assert ref.module == "mymod"

    def test_frozen_dataclass(self):
        ref = OCIModuleRef.parse("math")
        with pytest.raises(AttributeError):
            ref.module = "other"


# ─── OCI Client Initialization ──────────────────────────────────────────────


class TestOCIClientInit:

    def test_creates_cache_dir(self, tmp_path):
        cache = tmp_path / "oci_cache"
        client = OCIClient(cache_dir=cache)
        assert cache.exists()

    def test_default_registry(self, tmp_path):
        client = OCIClient(cache_dir=tmp_path)
        assert client.default_registry == "ghcr.io"

    def test_custom_auth_token(self, tmp_path):
        client = OCIClient(cache_dir=tmp_path, auth_token="my-token")
        assert client._auth_token == "my-token"


# ─── Pull Tests (Mocked HTTP) ───────────────────────────────────────────────


class TestPull:
    """Tests for pull operations with mocked HTTP."""

    def test_pull_cache_hit(self, tmp_path):
        """Cached module should return without HTTP call."""
        client = OCIClient(cache_dir=tmp_path)
        ref = OCIModuleRef.parse("math")

        # Pre-populate cache
        cache_path = client._cache_path(ref)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        content = b"[HLF-v2]\n[INTENT] greet\nOmega"
        cache_path.write_bytes(content)

        result = client.pull(ref)
        assert result.cached is True
        assert result.local_path == cache_path
        assert result.size_bytes == len(content)

    def test_pull_string_ref(self, tmp_path):
        """Pull should accept string refs."""
        client = OCIClient(cache_dir=tmp_path)

        # Pre-populate cache for string ref
        ref = OCIModuleRef.parse("math")
        cache_path = client._cache_path(ref)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(b"content")

        result = client.pull("math")
        assert result.cached is True

    def test_pull_checksum_mismatch_repulls(self, tmp_path):
        """Cache with wrong checksum should be deleted and re-pulled."""
        client = OCIClient(cache_dir=tmp_path)
        ref = OCIModuleRef.parse("math")

        # Pre-populate cache with content
        cache_path = client._cache_path(ref)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(b"old content")

        # Mock HTTP for re-pull
        new_content = b"new correct content"
        new_sha = hashlib.sha256(new_content).hexdigest()
        manifest = {
            "layers": [{
                "mediaType": "application/vnd.hlf.module.v1.hlf",
                "digest": f"sha256:{new_sha}",
            }]
        }

        with patch.object(client, "_fetch_manifest", return_value=manifest), \
             patch.object(client, "_fetch_blob", return_value=new_content):
            result = client.pull(ref, expected_sha256=new_sha)
            assert result.cached is False
            assert result.sha256 == new_sha

    def test_pull_validates_manifest_digest(self, tmp_path):
        """Pull should validate content against manifest digest."""
        client = OCIClient(cache_dir=tmp_path)
        ref = OCIModuleRef.parse("math")

        content = b"module content"
        sha = hashlib.sha256(content).hexdigest()
        manifest = {
            "layers": [{
                "mediaType": "application/vnd.hlf.module.v1.hlf",
                "digest": f"sha256:{sha}",
            }]
        }

        with patch.object(client, "_fetch_manifest", return_value=manifest), \
             patch.object(client, "_fetch_blob", return_value=content):
            result = client.pull(ref)
            assert result.sha256 == sha

    def test_pull_no_hlf_layer_uses_first_layer(self, tmp_path):
        """If no HLF layer found, should use first available layer."""
        client = OCIClient(cache_dir=tmp_path)
        ref = OCIModuleRef.parse("math")

        content = b"generic layer"
        sha = hashlib.sha256(content).hexdigest()
        manifest = {
            "layers": [{
                "mediaType": "application/octet-stream",
                "digest": f"sha256:{sha}",
            }]
        }

        with patch.object(client, "_fetch_manifest", return_value=manifest), \
             patch.object(client, "_fetch_blob", return_value=content):
            result = client.pull(ref)
            assert result.sha256 == sha

    def test_pull_empty_manifest_raises(self, tmp_path):
        """Manifest with no layers should raise OCIRegistryError."""
        client = OCIClient(cache_dir=tmp_path)
        ref = OCIModuleRef.parse("math")

        manifest = {"layers": []}
        with patch.object(client, "_fetch_manifest", return_value=manifest):
            with pytest.raises(OCIRegistryError, match="No HLF module layer"):
                client.pull(ref)

    def test_pull_checksum_validation_failure(self, tmp_path):
        """Wrong expected checksum should raise OCIChecksumError."""
        client = OCIClient(cache_dir=tmp_path)
        ref = OCIModuleRef.parse("math")

        content = b"module content"
        manifest = {"layers": [{"mediaType": "x", "digest": ""}]}

        with patch.object(client, "_fetch_manifest", return_value=manifest), \
             patch.object(client, "_fetch_blob", return_value=content):
            with pytest.raises(OCIChecksumError):
                client.pull(ref, expected_sha256="wrong_sha256_value")


# ─── Push Tests (Mocked HTTP) ───────────────────────────────────────────────


class TestPush:
    """Tests for push operations."""

    def test_push_returns_sha256(self, tmp_path):
        client = OCIClient(cache_dir=tmp_path)
        module_file = tmp_path / "test.hlf"
        module_file.write_bytes(b"[HLF-v2]\n[INTENT] test\nOmega")

        expected_sha = hashlib.sha256(module_file.read_bytes()).hexdigest()

        with patch.object(client, "_push_blob"), \
             patch.object(client, "_push_manifest"):
            result = client.push(module_file, "math:v1.0.0")
            assert result == expected_sha

    def test_push_missing_file_raises(self, tmp_path):
        client = OCIClient(cache_dir=tmp_path)
        with pytest.raises(FileNotFoundError):
            client.push(tmp_path / "nonexistent.hlf", "math:v1")

    def test_push_string_ref(self, tmp_path):
        client = OCIClient(cache_dir=tmp_path)
        module_file = tmp_path / "test.hlf"
        module_file.write_bytes(b"content")

        with patch.object(client, "_push_blob"), \
             patch.object(client, "_push_manifest"):
            sha = client.push(module_file, "org/mods/math:v2")
            assert isinstance(sha, str)
            assert len(sha) == 64  # SHA-256 hex


# ─── List Tags Tests ────────────────────────────────────────────────────────


class TestListTags:

    def test_list_tags_returns_sorted(self, tmp_path):
        client = OCIClient(cache_dir=tmp_path)

        mock_response = json.dumps({"tags": ["v1.0.0", "v2.0.0", "latest"]}).encode()
        with patch.object(client, "_http_get", return_value=mock_response):
            tags = client.list_tags("math")
            assert tags == ["latest", "v1.0.0", "v2.0.0"]

    def test_list_tags_empty_registry(self, tmp_path):
        client = OCIClient(cache_dir=tmp_path)

        mock_response = json.dumps({"tags": []}).encode()
        with patch.object(client, "_http_get", return_value=mock_response):
            tags = client.list_tags("math")
            assert tags == []


# ─── Cache Tests ────────────────────────────────────────────────────────────


class TestCache:

    def test_clear_cache(self, tmp_path):
        client = OCIClient(cache_dir=tmp_path)

        # Create some cached files
        (tmp_path / "mod1.hlf").write_bytes(b"a")
        (tmp_path / "mod2.hlf").write_bytes(b"b")

        count = client.clear_cache()
        assert count == 2

    def test_clear_cache_empty(self, tmp_path):
        client = OCIClient(cache_dir=tmp_path)
        count = client.clear_cache()
        assert count == 0

    def test_cache_path_deterministic(self, tmp_path):
        client = OCIClient(cache_dir=tmp_path)
        ref = OCIModuleRef.parse("ghcr.io/org/mods/math:v1")
        path1 = client._cache_path(ref)
        path2 = client._cache_path(ref)
        assert path1 == path2


# ─── Error Tests ─────────────────────────────────────────────────────────────


class TestErrors:

    def test_oci_registry_error_stores_status_code(self):
        err = OCIRegistryError("not found", status_code=404)
        assert err.status_code == 404
        assert "not found" in str(err)

    def test_oci_checksum_error_stores_hashes(self):
        err = OCIChecksumError("ref", "expected_hash", "actual_hash")
        assert err.expected == "expected_hash"
        assert err.actual == "actual_hash"

    def test_sha256_file_helper(self, tmp_path):
        f = tmp_path / "test.txt"
        content = b"hello world"
        f.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        assert OCIClient._sha256_file(f) == expected
