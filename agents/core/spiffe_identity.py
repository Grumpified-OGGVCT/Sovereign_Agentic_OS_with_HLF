"""
SPIFFE Workload Identity — Agent Cryptographic Identity System.

Implements SPIFFE (Secure Production Identity Framework for Everyone)
workload identities for agents in the Sovereign OS. Replaces the
placeholder KYA self-signed cert references with proper:

  1. Trust Domain management (spiffe://sovereign.os/...)
  2. SVID (SPIFFE Verifiable Identity Document) generation
  3. X.509 SVID creation with configurable lifetimes
  4. Agent identity registration, lookup, and revocation
  5. mTLS trust bundle for inter-agent communication

Architecture:
  TrustDomain → WorkloadIdentity → SVID (X.509 cert)
                                 → Agent verification
                                 → Rotation/renewal

Note: This is a local implementation. For production, integrate with
a real SPIRE server. This module provides the identity abstractions
and local CA for development/testing.

Usage:
    registry = SpiffeRegistry(domain="sovereign.os")
    identity = registry.register_agent("sentinel", namespace="core")
    svid = registry.issue_svid(identity.spiffe_id)
    is_valid = registry.verify_svid(svid)
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─── Trust Domain ───────────────────────────────────────────────────────────

class TrustDomain:
    """SPIFFE Trust Domain — the root of trust for all identities.

    Format: spiffe://<trust-domain>

    Sovereign OS uses: spiffe://sovereign.os
    """

    def __init__(self, name: str = "sovereign.os") -> None:
        if not name or "/" in name:
            raise ValueError(f"Invalid trust domain: '{name}'")
        self.name = name

    def spiffe_id(self, namespace: str, service_account: str) -> str:
        """Generate a SPIFFE ID for a workload.

        Format: spiffe://<domain>/ns/<namespace>/sa/<service-account>
        """
        if not namespace or not service_account:
            raise ValueError("Namespace and service account are required")
        # Sanitize
        ns = namespace.strip("/").lower()
        sa = service_account.strip("/").lower()
        return f"spiffe://{self.name}/ns/{ns}/sa/{sa}"

    def __repr__(self) -> str:
        return f"TrustDomain({self.name!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TrustDomain):
            return NotImplemented
        return self.name == other.name

    def __hash__(self) -> int:
        return hash(self.name)


# ─── Identity Status ────────────────────────────────────────────────────────

class IdentityStatus(Enum):
    """Lifecycle status of a workload identity."""
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"
    SUSPENDED = "suspended"
    PENDING = "pending"


# ─── Workload Identity ──────────────────────────────────────────────────────

@dataclass
class WorkloadIdentity:
    """A SPIFFE workload identity for an agent.

    Each agent in the Sovereign OS gets a unique SPIFFE ID
    that serves as its cryptographic identity.
    """

    spiffe_id: str
    agent_id: str
    namespace: str
    trust_domain: str
    status: IdentityStatus = IdentityStatus.ACTIVE
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
    integrity_hash: str = ""

    def __post_init__(self) -> None:
        if not self.integrity_hash:
            self.integrity_hash = self._compute_hash()

    def _compute_hash(self) -> str:
        """Compute SHA-256 integrity hash of the identity."""
        data = f"{self.spiffe_id}:{self.agent_id}:{self.namespace}:{self.trust_domain}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    def is_active(self) -> bool:
        return self.status == IdentityStatus.ACTIVE

    def to_dict(self) -> dict[str, Any]:
        return {
            "spiffe_id": self.spiffe_id,
            "agent_id": self.agent_id,
            "namespace": self.namespace,
            "trust_domain": self.trust_domain,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "integrity_hash": self.integrity_hash,
            "metadata": self.metadata,
        }


# ─── SVID (SPIFFE Verifiable Identity Document) ────────────────────────────

@dataclass
class SVID:
    """X.509 SVID — a short-lived certificate binding a SPIFFE ID.

    In a real SPIRE deployment, this would be an actual X.509 certificate.
    This implementation uses a signed token with expiration for local dev.
    """

    svid_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    spiffe_id: str = ""
    issued_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    serial_number: str = field(default_factory=lambda: secrets.token_hex(8))
    signature: str = ""
    trust_domain: str = ""
    is_ca: bool = False

    def __post_init__(self) -> None:
        if self.expires_at == 0.0:
            # Default: 1 hour lifetime
            self.expires_at = self.issued_at + 3600
        if not self.signature:
            self.signature = self._sign()

    def _sign(self) -> str:
        """Create a signature for the SVID (local CA simulation)."""
        data = f"{self.svid_id}:{self.spiffe_id}:{self.serial_number}:{self.expires_at}"
        return hashlib.sha256(data.encode()).hexdigest()

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def is_valid(self) -> bool:
        """Check if the SVID is currently valid (not expired, signature matches)."""
        if self.is_expired():
            return False
        expected = self._sign()
        return self.signature == expected

    def remaining_seconds(self) -> float:
        return max(0, self.expires_at - time.time())

    def to_dict(self) -> dict[str, Any]:
        return {
            "svid_id": self.svid_id,
            "spiffe_id": self.spiffe_id,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "serial_number": self.serial_number,
            "signature": self.signature,
            "trust_domain": self.trust_domain,
            "is_valid": self.is_valid(),
            "remaining_seconds": round(self.remaining_seconds(), 1),
        }


# ─── Trust Bundle ───────────────────────────────────────────────────────────

@dataclass
class TrustBundle:
    """Trust bundle for a domain — contains CA certificates for verification.

    In real SPIRE, this contains the CA public keys. Here we track
    the signing keys and revocation lists.
    """

    domain: str
    ca_fingerprint: str = field(default_factory=lambda: secrets.token_hex(16))
    created_at: float = field(default_factory=time.time)
    revoked_serials: set[str] = field(default_factory=set)

    def revoke(self, serial_number: str) -> None:
        """Add a serial number to the revocation list."""
        self.revoked_serials.add(serial_number)

    def is_revoked(self, serial_number: str) -> bool:
        return serial_number in self.revoked_serials

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "ca_fingerprint": self.ca_fingerprint,
            "created_at": self.created_at,
            "revoked_count": len(self.revoked_serials),
        }


# ─── SPIFFE Registry ────────────────────────────────────────────────────────

class SpiffeRegistry:
    """Central registry for SPIFFE workload identities.

    Manages the identity lifecycle: register → issue SVID → verify → revoke.

    Acts as a local SPIRE server for development and testing.
    For production, this would delegate to a real SPIRE agent.

    Args:
        domain: Trust domain name (default: "sovereign.os").
        svid_ttl: Default SVID lifetime in seconds (default: 3600 = 1 hour).
    """

    def __init__(
        self,
        domain: str = "sovereign.os",
        *,
        svid_ttl: int = 3600,
    ) -> None:
        self._domain = TrustDomain(domain)
        self._svid_ttl = svid_ttl
        self._identities: dict[str, WorkloadIdentity] = {}
        self._svids: dict[str, SVID] = {}
        self._bundle = TrustBundle(domain=domain)

    @property
    def domain(self) -> TrustDomain:
        return self._domain

    @property
    def bundle(self) -> TrustBundle:
        return self._bundle

    # ── Registration ─────────────────────────────────────────────────────

    def register_agent(
        self,
        agent_id: str,
        namespace: str = "core",
        *,
        metadata: dict[str, Any] | None = None,
    ) -> WorkloadIdentity:
        """Register an agent and create its SPIFFE workload identity.

        Args:
            agent_id: Unique agent identifier (e.g., "sentinel").
            namespace: SPIFFE namespace (e.g., "core", "ext", "sys").
            metadata: Optional metadata to attach.

        Returns:
            WorkloadIdentity for the agent.

        Raises:
            ValueError: If agent is already registered.
        """
        spiffe_id = self._domain.spiffe_id(namespace, agent_id)

        if spiffe_id in self._identities:
            raise ValueError(f"Agent already registered: {spiffe_id}")

        identity = WorkloadIdentity(
            spiffe_id=spiffe_id,
            agent_id=agent_id,
            namespace=namespace,
            trust_domain=self._domain.name,
            metadata=metadata or {},
        )

        self._identities[spiffe_id] = identity
        logger.info("Registered SPIFFE identity: %s", spiffe_id)
        return identity

    def get_identity(self, spiffe_id: str) -> WorkloadIdentity | None:
        """Look up an identity by SPIFFE ID."""
        return self._identities.get(spiffe_id)

    def get_agent_identity(self, agent_id: str, namespace: str = "core") -> WorkloadIdentity | None:
        """Look up an identity by agent ID and namespace."""
        spiffe_id = self._domain.spiffe_id(namespace, agent_id)
        return self._identities.get(spiffe_id)

    def list_identities(
        self,
        *,
        namespace: str | None = None,
        status: IdentityStatus | None = None,
    ) -> list[WorkloadIdentity]:
        """List all registered identities, optionally filtered."""
        identities = list(self._identities.values())

        if namespace is not None:
            identities = [i for i in identities if i.namespace == namespace]
        if status is not None:
            identities = [i for i in identities if i.status == status]

        return identities

    # ── SVID Management ──────────────────────────────────────────────────

    def issue_svid(
        self,
        spiffe_id: str,
        *,
        ttl: int | None = None,
    ) -> SVID:
        """Issue an SVID (short-lived certificate) for an identity.

        Args:
            spiffe_id: The SPIFFE ID to issue for.
            ttl: Override the default TTL (seconds).

        Returns:
            SVID with signature and expiration.

        Raises:
            ValueError: If identity not found or not active.
        """
        identity = self._identities.get(spiffe_id)
        if identity is None:
            raise ValueError(f"Identity not found: {spiffe_id}")
        if not identity.is_active():
            raise ValueError(f"Identity not active: {spiffe_id} ({identity.status.value})")

        lifetime = ttl or self._svid_ttl
        now = time.time()

        svid = SVID(
            spiffe_id=spiffe_id,
            issued_at=now,
            expires_at=now + lifetime,
            trust_domain=self._domain.name,
        )

        self._svids[svid.svid_id] = svid
        logger.info("Issued SVID %s for %s (TTL: %ds)", svid.svid_id[:8], spiffe_id, lifetime)
        return svid

    def verify_svid(self, svid: SVID) -> bool:
        """Verify an SVID is valid.

        Checks:
          1. Not expired
          2. Signature is valid
          3. Not revoked
          4. Trust domain matches
          5. Identity is active
        """
        if svid.is_expired():
            return False

        if not svid.is_valid():
            return False

        if self._bundle.is_revoked(svid.serial_number):
            return False

        if svid.trust_domain != self._domain.name:
            return False

        identity = self._identities.get(svid.spiffe_id)
        if identity is None or not identity.is_active():
            return False

        return True

    def revoke_svid(self, svid: SVID) -> None:
        """Revoke an SVID by adding its serial to the revocation list."""
        self._bundle.revoke(svid.serial_number)
        self._svids.pop(svid.svid_id, None)
        logger.info("Revoked SVID %s (serial: %s)", svid.svid_id[:8], svid.serial_number)

    def get_svid(self, svid_id: str) -> SVID | None:
        """Look up an SVID by ID."""
        return self._svids.get(svid_id)

    def list_svids(self, spiffe_id: str | None = None) -> list[SVID]:
        """List all issued SVIDs, optionally filtered by SPIFFE ID."""
        svids = list(self._svids.values())
        if spiffe_id is not None:
            svids = [s for s in svids if s.spiffe_id == spiffe_id]
        return svids

    # ── Identity Lifecycle ───────────────────────────────────────────────

    def revoke_identity(self, spiffe_id: str) -> None:
        """Revoke an identity and all its SVIDs."""
        identity = self._identities.get(spiffe_id)
        if identity is None:
            raise ValueError(f"Identity not found: {spiffe_id}")

        identity.status = IdentityStatus.REVOKED
        identity.updated_at = time.time()

        # Revoke all SVIDs for this identity
        for svid in list(self._svids.values()):
            if svid.spiffe_id == spiffe_id:
                self.revoke_svid(svid)

        logger.info("Revoked identity: %s", spiffe_id)

    def suspend_identity(self, spiffe_id: str) -> None:
        """Temporarily suspend an identity."""
        identity = self._identities.get(spiffe_id)
        if identity is None:
            raise ValueError(f"Identity not found: {spiffe_id}")

        identity.status = IdentityStatus.SUSPENDED
        identity.updated_at = time.time()

    def reactivate_identity(self, spiffe_id: str) -> None:
        """Reactivate a suspended identity."""
        identity = self._identities.get(spiffe_id)
        if identity is None:
            raise ValueError(f"Identity not found: {spiffe_id}")
        if identity.status == IdentityStatus.REVOKED:
            raise ValueError("Cannot reactivate a revoked identity")

        identity.status = IdentityStatus.ACTIVE
        identity.updated_at = time.time()

    # ── Rotation ─────────────────────────────────────────────────────────

    def rotate_svid(self, svid: SVID, *, ttl: int | None = None) -> SVID:
        """Rotate an SVID — revoke old, issue new.

        This is the standard SPIRE rotation pattern: the old SVID is
        revoked and a fresh one is issued with a new serial number.
        """
        self.revoke_svid(svid)
        return self.issue_svid(svid.spiffe_id, ttl=ttl)

    # ── Reports ──────────────────────────────────────────────────────────

    def get_report(self) -> dict[str, Any]:
        """Generate a registry status report."""
        identities = list(self._identities.values())
        active = sum(1 for i in identities if i.is_active())
        revoked = sum(1 for i in identities if i.status == IdentityStatus.REVOKED)
        suspended = sum(1 for i in identities if i.status == IdentityStatus.SUSPENDED)

        valid_svids = sum(1 for s in self._svids.values() if s.is_valid())
        expired_svids = sum(1 for s in self._svids.values() if s.is_expired())

        return {
            "trust_domain": self._domain.name,
            "total_identities": len(identities),
            "active": active,
            "revoked": revoked,
            "suspended": suspended,
            "total_svids": len(self._svids),
            "valid_svids": valid_svids,
            "expired_svids": expired_svids,
            "revoked_serials": len(self._bundle.revoked_serials),
            "ca_fingerprint": self._bundle.ca_fingerprint,
        }

    # ── Persistence ──────────────────────────────────────────────────────

    def save(self, path: Path | str) -> None:
        """Save registry state to JSON."""
        data = {
            "domain": self._domain.name,
            "svid_ttl": self._svid_ttl,
            "identities": {k: v.to_dict() for k, v in self._identities.items()},
            "bundle": self._bundle.to_dict(),
        }
        Path(path).write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    @classmethod
    def load(cls, path: Path | str) -> SpiffeRegistry:
        """Load registry state from JSON."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        registry = cls(domain=data["domain"], svid_ttl=data.get("svid_ttl", 3600))

        for spiffe_id, identity_data in data.get("identities", {}).items():
            identity = WorkloadIdentity(
                spiffe_id=identity_data["spiffe_id"],
                agent_id=identity_data["agent_id"],
                namespace=identity_data["namespace"],
                trust_domain=identity_data["trust_domain"],
                status=IdentityStatus(identity_data.get("status", "active")),
                created_at=identity_data.get("created_at", 0),
                updated_at=identity_data.get("updated_at", 0),
                metadata=identity_data.get("metadata", {}),
                integrity_hash=identity_data.get("integrity_hash", ""),
            )
            registry._identities[spiffe_id] = identity

        return registry
