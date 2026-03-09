"""
HLF OCI Module Distribution Client.

Implements OCI Distribution Spec for pulling/pushing HLF modules from
container registries (e.g. ghcr.io, Docker Hub).

Part of Phase 5.1 — Module Distribution (Sovereign OS Master Build Plan).

Usage::

    from hlf.oci_client import OCIClient, OCIModuleRef

    ref = OCIModuleRef.parse("ghcr.io/org/math:v1.0.0")
    client = OCIClient(cache_dir=Path("hlf/.oci_cache"))
    local_path = client.pull(ref)
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

try:
    import urllib.request
    import urllib.error

    _HAS_URLLIB = True
except ImportError:  # pragma: no cover
    _HAS_URLLIB = False

_logger = logging.getLogger("hlf.oci")

# Default cache directory relative to project root
_DEFAULT_CACHE_DIR = Path(__file__).resolve().parent / ".oci_cache"

# OCI Distribution API media types
_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
_LAYER_MEDIA_TYPE = "application/vnd.hlf.module.v1.hlf"
_CONFIG_MEDIA_TYPE = "application/vnd.hlf.module.config.v1+json"


# ─── Exceptions ──────────────────────────────────────────────────────────────


class OCIError(Exception):
    """Base exception for OCI operations."""


class OCIRegistryError(OCIError):
    """Raised when a registry operation fails (network, auth, 404, etc.)."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class OCIChecksumError(OCIError):
    """Raised when a pulled module fails checksum validation."""

    def __init__(self, ref: str, expected: str, actual: str):
        super().__init__(
            f"Checksum mismatch for '{ref}': expected={expected[:16]}…, got={actual[:16]}…"
        )
        self.expected = expected
        self.actual = actual


# ─── OCI Module Reference ───────────────────────────────────────────────────


@dataclass(frozen=True)
class OCIModuleRef:
    """
    A parsed OCI module reference.

    Format: ``registry/namespace/module:tag``

    Examples::

        ghcr.io/Grumpified-OGGVCT/hlf-modules/math:v1.0.0
        ghcr.io/Grumpified-OGGVCT/hlf-modules/crypto:latest
        localhost:5000/myorg/mymodule:dev
    """

    registry: str
    namespace: str
    module: str
    tag: str = "latest"

    @classmethod
    def parse(cls, uri: str) -> OCIModuleRef:
        """
        Parse an OCI URI into components.

        Supported formats:
          - ``registry/namespace/module:tag``
          - ``registry/namespace/module`` (tag defaults to 'latest')
          - ``module`` (uses default registry/namespace from settings)
        """
        # Strip protocol prefix if present
        if uri.startswith("oci://"):
            uri = uri[6:]

        # Split tag
        if ":" in uri.rsplit("/", 1)[-1]:
            base, tag = uri.rsplit(":", 1)
        else:
            base, tag = uri, "latest"

        parts = base.split("/")

        if len(parts) >= 3:
            # Full URI: registry/namespace.../module
            registry = parts[0]
            module = parts[-1]
            namespace = "/".join(parts[1:-1])
        elif len(parts) == 2:
            # namespace/module — use default registry
            registry = "ghcr.io"
            namespace = parts[0]
            module = parts[1]
        elif len(parts) == 1:
            # Just module name — use defaults
            registry = "ghcr.io"
            namespace = "Grumpified-OGGVCT/hlf-modules"
            module = parts[0]
        else:
            raise OCIError(f"Invalid OCI reference: '{uri}'")

        return cls(registry=registry, namespace=namespace, module=module, tag=tag)

    @property
    def full_ref(self) -> str:
        """Full reference string: registry/namespace/module:tag."""
        return f"{self.registry}/{self.namespace}/{self.module}:{self.tag}"

    @property
    def api_path(self) -> str:
        """OCI Distribution API path: /v2/namespace/module/..."""
        return f"/v2/{self.namespace}/{self.module}"

    def __str__(self) -> str:
        return self.full_ref


# ─── OCI Pull Result ────────────────────────────────────────────────────────


@dataclass
class OCIPullResult:
    """Result of pulling a module from an OCI registry."""

    ref: str
    local_path: Path
    sha256: str
    size_bytes: int
    cached: bool = False
    pull_time_ms: float = 0.0


# ─── OCI Client ─────────────────────────────────────────────────────────────


class OCIClient:
    """
    OCI Distribution client for HLF module pull/push/list operations.

    Uses the OCI Distribution Spec HTTP API directly — no Docker
    or container runtime dependency required.

    Args:
        cache_dir: Local directory for caching pulled modules.
        registry: Default registry (overridable per-ref).
        namespace: Default namespace (overridable per-ref).
        auth_token: Optional Bearer token for authenticated registries.
        timeout_seconds: HTTP request timeout.
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        registry: str = "ghcr.io",
        namespace: str = "Grumpified-OGGVCT/hlf-modules",
        auth_token: str | None = None,
        timeout_seconds: float = 30.0,
    ):
        self.cache_dir = cache_dir or _DEFAULT_CACHE_DIR
        self.default_registry = registry
        self.default_namespace = namespace
        self._auth_token = auth_token
        self._timeout = timeout_seconds

        # Ensure cache dir exists
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ─── Public API ──────────────────────────────────────────────────────

    def pull(
        self,
        ref: OCIModuleRef | str,
        expected_sha256: str | None = None,
    ) -> OCIPullResult:
        """
        Pull a module from an OCI registry.

        1. Check local cache first
        2. Fetch manifest from registry
        3. Download module layer
        4. Validate checksum
        5. Store in cache

        Args:
            ref: OCI module reference (string or parsed).
            expected_sha256: Optional expected SHA-256 for extra validation.

        Returns:
            OCIPullResult with local path and metadata.

        Raises:
            OCIRegistryError: If registry request fails.
            OCIChecksumError: If checksum validation fails.
        """
        if isinstance(ref, str):
            ref = OCIModuleRef.parse(ref)

        start_ms = time.monotonic() * 1000

        # Check cache
        cached_path = self._cache_path(ref)
        if cached_path.exists():
            sha = self._sha256_file(cached_path)
            if expected_sha256 and sha != expected_sha256:
                _logger.warning(
                    "Cache checksum mismatch for %s — re-pulling", ref.full_ref
                )
                cached_path.unlink()
            else:
                _logger.debug("Cache hit for %s", ref.full_ref)
                elapsed = time.monotonic() * 1000 - start_ms
                return OCIPullResult(
                    ref=ref.full_ref,
                    local_path=cached_path,
                    sha256=sha,
                    size_bytes=cached_path.stat().st_size,
                    cached=True,
                    pull_time_ms=elapsed,
                )

        # Fetch manifest
        manifest = self._fetch_manifest(ref)

        # Find HLF layer
        layers = manifest.get("layers", [])
        hlf_layer = None
        for layer in layers:
            if layer.get("mediaType") == _LAYER_MEDIA_TYPE:
                hlf_layer = layer
                break

        if hlf_layer is None:
            # Fallback: use first layer
            if layers:
                hlf_layer = layers[0]
            else:
                raise OCIRegistryError(
                    f"No HLF module layer found in manifest for {ref.full_ref}"
                )

        # Download layer blob
        digest = hlf_layer.get("digest", "")
        content = self._fetch_blob(ref, digest)

        # Validate checksum
        actual_sha = hashlib.sha256(content).hexdigest()
        if expected_sha256 and actual_sha != expected_sha256:
            raise OCIChecksumError(ref.full_ref, expected_sha256, actual_sha)

        # Validate digest from manifest
        if digest.startswith("sha256:"):
            manifest_sha = digest[7:]
            if actual_sha != manifest_sha:
                raise OCIChecksumError(ref.full_ref, manifest_sha, actual_sha)

        # Write to cache
        cached_path.parent.mkdir(parents=True, exist_ok=True)
        cached_path.write_bytes(content)

        elapsed = time.monotonic() * 1000 - start_ms
        _logger.info(
            "Pulled %s (%d bytes, %.0fms)", ref.full_ref, len(content), elapsed
        )

        return OCIPullResult(
            ref=ref.full_ref,
            local_path=cached_path,
            sha256=actual_sha,
            size_bytes=len(content),
            cached=False,
            pull_time_ms=elapsed,
        )

    def push(
        self,
        module_path: Path,
        ref: OCIModuleRef | str,
    ) -> str:
        """
        Push a local HLF module to an OCI registry.

        1. Read module file
        2. Compute digest
        3. Upload blob layer
        4. Create and push manifest

        Args:
            module_path: Path to the .hlf module file.
            ref: Target OCI reference.

        Returns:
            SHA-256 digest of the pushed module.

        Raises:
            OCIRegistryError: If push fails.
            FileNotFoundError: If module_path doesn't exist.
        """
        if isinstance(ref, str):
            ref = OCIModuleRef.parse(ref)

        if not module_path.exists():
            raise FileNotFoundError(f"Module file not found: {module_path}")

        content = module_path.read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        digest = f"sha256:{sha}"

        # Upload blob
        self._push_blob(ref, content, digest)

        # Create config
        config_content = json.dumps(
            {
                "mediaType": _CONFIG_MEDIA_TYPE,
                "module": ref.module,
                "tag": ref.tag,
                "sha256": sha,
            }
        ).encode("utf-8")
        config_sha = hashlib.sha256(config_content).hexdigest()
        config_digest = f"sha256:{config_sha}"
        self._push_blob(ref, config_content, config_digest)

        # Create and push manifest
        manifest = {
            "schemaVersion": 2,
            "mediaType": _MANIFEST_MEDIA_TYPE,
            "config": {
                "mediaType": _CONFIG_MEDIA_TYPE,
                "digest": config_digest,
                "size": len(config_content),
            },
            "layers": [
                {
                    "mediaType": _LAYER_MEDIA_TYPE,
                    "digest": digest,
                    "size": len(content),
                }
            ],
        }

        self._push_manifest(ref, manifest)
        _logger.info("Pushed %s (%d bytes, sha256=%s)", ref.full_ref, len(content), sha[:16])
        return sha

    def list_tags(self, ref: OCIModuleRef | str) -> list[str]:
        """
        List available tags for a module in the registry.

        Args:
            ref: OCI module reference (tag portion is ignored).

        Returns:
            Sorted list of tag strings.
        """
        if isinstance(ref, str):
            ref = OCIModuleRef.parse(ref)

        url = f"https://{ref.registry}{ref.api_path}/tags/list"
        response = self._http_get(url)
        data = json.loads(response)
        tags = data.get("tags", [])
        return sorted(tags)

    def clear_cache(self) -> int:
        """Remove all cached modules. Returns number of files removed."""
        count = 0
        if self.cache_dir.exists():
            for f in self.cache_dir.rglob("*.hlf"):
                f.unlink()
                count += 1
        _logger.info("Cleared %d cached modules", count)
        return count

    # ─── Internal Helpers ────────────────────────────────────────────────

    def _cache_path(self, ref: OCIModuleRef) -> Path:
        """Compute local cache path for a ref."""
        safe_registry = ref.registry.replace(":", "_").replace("/", "_")
        safe_namespace = ref.namespace.replace("/", "_")
        return self.cache_dir / safe_registry / safe_namespace / f"{ref.module}_{ref.tag}.hlf"

    @staticmethod
    def _sha256_file(path: Path) -> str:
        """Compute SHA-256 of a file."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def _http_get(self, url: str, accept: str | None = None) -> bytes:
        """HTTP GET with auth and timeout."""
        headers: dict[str, str] = {}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        if accept:
            headers["Accept"] = accept

        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            raise OCIRegistryError(
                f"Registry request failed: {e.code} {e.reason} ({url})",
                status_code=e.code,
            ) from e
        except urllib.error.URLError as e:
            raise OCIRegistryError(f"Registry unreachable: {e.reason} ({url})") from e

    def _http_put(self, url: str, data: bytes, content_type: str) -> int:
        """HTTP PUT with auth."""
        headers: dict[str, str] = {"Content-Type": content_type}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"

        req = urllib.request.Request(url, data=data, headers=headers, method="PUT")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            raise OCIRegistryError(
                f"Registry push failed: {e.code} {e.reason} ({url})",
                status_code=e.code,
            ) from e

    def _fetch_manifest(self, ref: OCIModuleRef) -> dict[str, Any]:
        """Fetch OCI manifest for a given ref."""
        url = f"https://{ref.registry}{ref.api_path}/manifests/{quote(ref.tag, safe='')}"
        content = self._http_get(url, accept=_MANIFEST_MEDIA_TYPE)
        return json.loads(content)

    def _fetch_blob(self, ref: OCIModuleRef, digest: str) -> bytes:
        """Fetch a blob by digest from the registry."""
        url = f"https://{ref.registry}{ref.api_path}/blobs/{quote(digest, safe=':')}"
        return self._http_get(url)

    def _push_blob(self, ref: OCIModuleRef, data: bytes, digest: str) -> None:
        """Push a blob to the registry (monolithic upload)."""
        url = f"https://{ref.registry}{ref.api_path}/blobs/uploads/?digest={quote(digest, safe=':')}"
        self._http_put(url, data, "application/octet-stream")

    def _push_manifest(self, ref: OCIModuleRef, manifest: dict[str, Any]) -> None:
        """Push a manifest to the registry."""
        url = f"https://{ref.registry}{ref.api_path}/manifests/{quote(ref.tag, safe='')}"
        data = json.dumps(manifest).encode("utf-8")
        self._http_put(url, data, _MANIFEST_MEDIA_TYPE)
