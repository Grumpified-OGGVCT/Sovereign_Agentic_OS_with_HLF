"""Tests for SPIFFE Workload Identity System."""

from __future__ import annotations

import time

import pytest

from agents.core.spiffe_identity import (
    SVID,
    IdentityStatus,
    SpiffeRegistry,
    TrustBundle,
    TrustDomain,
    WorkloadIdentity,
)

# ─── TrustDomain Tests ──────────────────────────────────────────────────────


class TestTrustDomain:
    def test_default_domain(self):
        td = TrustDomain()
        assert td.name == "sovereign.os"

    def test_custom_domain(self):
        td = TrustDomain("example.com")
        assert td.name == "example.com"

    def test_invalid_domain_empty(self):
        with pytest.raises(ValueError):
            TrustDomain("")

    def test_invalid_domain_slash(self):
        with pytest.raises(ValueError):
            TrustDomain("bad/domain")

    def test_spiffe_id_format(self):
        td = TrustDomain("sovereign.os")
        sid = td.spiffe_id("core", "sentinel")
        assert sid == "spiffe://sovereign.os/ns/core/sa/sentinel"

    def test_spiffe_id_normalizes_case(self):
        td = TrustDomain("sovereign.os")
        sid = td.spiffe_id("Core", "SENTINEL")
        assert sid == "spiffe://sovereign.os/ns/core/sa/sentinel"

    def test_spiffe_id_empty_namespace_raises(self):
        td = TrustDomain()
        with pytest.raises(ValueError):
            td.spiffe_id("", "agent")

    def test_spiffe_id_empty_sa_raises(self):
        td = TrustDomain()
        with pytest.raises(ValueError):
            td.spiffe_id("core", "")

    def test_equality(self):
        assert TrustDomain("a") == TrustDomain("a")
        assert TrustDomain("a") != TrustDomain("b")

    def test_hash(self):
        s = {TrustDomain("a"), TrustDomain("a")}
        assert len(s) == 1


# ─── WorkloadIdentity Tests ─────────────────────────────────────────────────


class TestWorkloadIdentity:
    def test_auto_hash(self):
        identity = WorkloadIdentity(
            spiffe_id="spiffe://sovereign.os/ns/core/sa/test",
            agent_id="test",
            namespace="core",
            trust_domain="sovereign.os",
        )
        assert identity.integrity_hash
        assert len(identity.integrity_hash) == 16

    def test_is_active(self):
        identity = WorkloadIdentity(
            spiffe_id="x", agent_id="a", namespace="n", trust_domain="d",
        )
        assert identity.is_active()
        identity.status = IdentityStatus.REVOKED
        assert not identity.is_active()

    def test_to_dict(self):
        identity = WorkloadIdentity(
            spiffe_id="spiffe://test/ns/c/sa/a",
            agent_id="a", namespace="c", trust_domain="test",
        )
        d = identity.to_dict()
        assert d["spiffe_id"] == "spiffe://test/ns/c/sa/a"
        assert d["status"] == "active"
        assert "integrity_hash" in d


# ─── SVID Tests ──────────────────────────────────────────────────────────────


class TestSVID:
    def test_default_ttl(self):
        svid = SVID(spiffe_id="spiffe://test/ns/c/sa/a")
        assert svid.expires_at > svid.issued_at
        assert (svid.expires_at - svid.issued_at) == pytest.approx(3600, abs=5)

    def test_custom_ttl(self):
        now = time.time()
        svid = SVID(spiffe_id="x", issued_at=now, expires_at=now + 60)
        assert (svid.expires_at - svid.issued_at) == pytest.approx(60, abs=1)

    def test_is_valid(self):
        svid = SVID(spiffe_id="x")
        assert svid.is_valid()

    def test_expired(self):
        now = time.time()
        svid = SVID(spiffe_id="x", issued_at=now - 7200, expires_at=now - 3600)
        assert svid.is_expired()
        assert not svid.is_valid()

    def test_tampered_signature(self):
        svid = SVID(spiffe_id="x")
        svid.signature = "tampered"
        assert not svid.is_valid()

    def test_to_dict(self):
        svid = SVID(spiffe_id="x")
        d = svid.to_dict()
        assert d["spiffe_id"] == "x"
        assert d["is_valid"] is True
        assert "remaining_seconds" in d

    def test_remaining_seconds(self):
        svid = SVID(spiffe_id="x")
        assert svid.remaining_seconds() > 0


# ─── TrustBundle Tests ──────────────────────────────────────────────────────


class TestTrustBundle:
    def test_revoke(self):
        bundle = TrustBundle(domain="test")
        bundle.revoke("serial-1")
        assert bundle.is_revoked("serial-1")
        assert not bundle.is_revoked("serial-2")

    def test_to_dict(self):
        bundle = TrustBundle(domain="test")
        bundle.revoke("s1")
        d = bundle.to_dict()
        assert d["revoked_count"] == 1


# ─── SpiffeRegistry Tests ───────────────────────────────────────────────────


class TestSpiffeRegistry:
    def test_register_agent(self):
        reg = SpiffeRegistry()
        identity = reg.register_agent("sentinel", "core")
        assert identity.spiffe_id == "spiffe://sovereign.os/ns/core/sa/sentinel"
        assert identity.is_active()

    def test_register_duplicate_raises(self):
        reg = SpiffeRegistry()
        reg.register_agent("sentinel")
        with pytest.raises(ValueError, match="already registered"):
            reg.register_agent("sentinel")

    def test_get_identity(self):
        reg = SpiffeRegistry()
        identity = reg.register_agent("scribe")
        result = reg.get_identity(identity.spiffe_id)
        assert result is identity

    def test_get_agent_identity(self):
        reg = SpiffeRegistry()
        reg.register_agent("arbiter", "sys")
        result = reg.get_agent_identity("arbiter", "sys")
        assert result is not None
        assert result.agent_id == "arbiter"

    def test_list_identities_filter_namespace(self):
        reg = SpiffeRegistry()
        reg.register_agent("a", "core")
        reg.register_agent("b", "ext")
        core = reg.list_identities(namespace="core")
        assert len(core) == 1
        assert core[0].agent_id == "a"

    def test_list_identities_filter_status(self):
        reg = SpiffeRegistry()
        id1 = reg.register_agent("a")
        reg.register_agent("b")
        reg.revoke_identity(id1.spiffe_id)
        active = reg.list_identities(status=IdentityStatus.ACTIVE)
        assert len(active) == 1

    def test_issue_svid(self):
        reg = SpiffeRegistry()
        identity = reg.register_agent("sentinel")
        svid = reg.issue_svid(identity.spiffe_id)
        assert svid.is_valid()
        assert svid.spiffe_id == identity.spiffe_id

    def test_issue_svid_unknown_raises(self):
        reg = SpiffeRegistry()
        with pytest.raises(ValueError, match="not found"):
            reg.issue_svid("spiffe://unknown/ns/x/sa/y")

    def test_issue_svid_revoked_raises(self):
        reg = SpiffeRegistry()
        identity = reg.register_agent("test")
        reg.revoke_identity(identity.spiffe_id)
        with pytest.raises(ValueError, match="not active"):
            reg.issue_svid(identity.spiffe_id)

    def test_verify_svid(self):
        reg = SpiffeRegistry()
        identity = reg.register_agent("test")
        svid = reg.issue_svid(identity.spiffe_id)
        assert reg.verify_svid(svid) is True

    def test_verify_expired_svid(self):
        reg = SpiffeRegistry()
        identity = reg.register_agent("test")
        svid = reg.issue_svid(identity.spiffe_id, ttl=0)
        # Force expiration
        svid.expires_at = time.time() - 1
        svid.signature = svid._sign()  # re-sign with new expiry
        assert reg.verify_svid(svid) is False

    def test_verify_revoked_svid(self):
        reg = SpiffeRegistry()
        identity = reg.register_agent("test")
        svid = reg.issue_svid(identity.spiffe_id)
        reg.revoke_svid(svid)
        assert reg.verify_svid(svid) is False

    def test_verify_wrong_domain(self):
        reg1 = SpiffeRegistry("domain-a")
        reg2 = SpiffeRegistry("domain-b")
        id1 = reg1.register_agent("test")
        svid = reg1.issue_svid(id1.spiffe_id)
        assert reg2.verify_svid(svid) is False

    def test_revoke_identity(self):
        reg = SpiffeRegistry()
        identity = reg.register_agent("test")
        svid = reg.issue_svid(identity.spiffe_id)
        reg.revoke_identity(identity.spiffe_id)
        assert identity.status == IdentityStatus.REVOKED
        assert reg.verify_svid(svid) is False

    def test_suspend_and_reactivate(self):
        reg = SpiffeRegistry()
        identity = reg.register_agent("test")
        reg.suspend_identity(identity.spiffe_id)
        assert identity.status == IdentityStatus.SUSPENDED
        reg.reactivate_identity(identity.spiffe_id)
        assert identity.status == IdentityStatus.ACTIVE

    def test_cannot_reactivate_revoked(self):
        reg = SpiffeRegistry()
        identity = reg.register_agent("test")
        reg.revoke_identity(identity.spiffe_id)
        with pytest.raises(ValueError, match="Cannot reactivate"):
            reg.reactivate_identity(identity.spiffe_id)

    def test_rotate_svid(self):
        reg = SpiffeRegistry()
        identity = reg.register_agent("test")
        svid_old = reg.issue_svid(identity.spiffe_id)
        svid_new = reg.rotate_svid(svid_old)
        assert svid_new.svid_id != svid_old.svid_id
        assert reg.verify_svid(svid_old) is False
        assert reg.verify_svid(svid_new) is True

    def test_get_report(self):
        reg = SpiffeRegistry()
        reg.register_agent("a")
        reg.register_agent("b")
        id_a = reg.get_agent_identity("a")
        reg.issue_svid(id_a.spiffe_id)
        report = reg.get_report()
        assert report["total_identities"] == 2
        assert report["active"] == 2
        assert report["valid_svids"] == 1

    def test_save_and_load(self, tmp_path):
        reg = SpiffeRegistry()
        reg.register_agent("sentinel", "core", metadata={"role": "security"})
        reg.register_agent("scribe", "core")

        path = tmp_path / "spiffe.json"
        reg.save(path)

        loaded = SpiffeRegistry.load(path)
        assert len(loaded.list_identities()) == 2
        sentinel = loaded.get_agent_identity("sentinel")
        assert sentinel is not None
        assert sentinel.metadata["role"] == "security"

    def test_custom_svid_ttl(self):
        reg = SpiffeRegistry(svid_ttl=120)
        identity = reg.register_agent("test")
        svid = reg.issue_svid(identity.spiffe_id)
        assert (svid.expires_at - svid.issued_at) == pytest.approx(120, abs=5)

    def test_list_svids(self):
        reg = SpiffeRegistry()
        id1 = reg.register_agent("a")
        id2 = reg.register_agent("b")
        reg.issue_svid(id1.spiffe_id)
        reg.issue_svid(id1.spiffe_id)
        reg.issue_svid(id2.spiffe_id)
        assert len(reg.list_svids()) == 3
        assert len(reg.list_svids(id1.spiffe_id)) == 2
