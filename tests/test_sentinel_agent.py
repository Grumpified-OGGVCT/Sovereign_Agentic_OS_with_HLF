"""
tests/test_sentinel_agent.py — Unit tests for the Sentinel Agent module.

Tests scan_payload(), get_stats(), get_agent_profile(), and the exfiltration
and privilege-escalation pattern libraries in agents.core.sentinel_agent.
"""

from __future__ import annotations

import pytest

import agents.core.sentinel_agent as sa
from agents.core.sentinel_agent import (
    SentinelVerdict,
    get_agent_profile,
    get_stats,
    scan_payload,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_stats():
    """Reset module-level counters before each test."""
    with sa._stats_lock:
        sa._scan_count = 0
        sa._block_count = 0
        sa._align_block_count = 0
        sa._privesc_block_count = 0
        sa._exfil_block_count = 0
    yield


# ─── SentinelVerdict Tests ───────────────────────────────────────────────────


class TestSentinelVerdict:
    def test_clean_verdict(self):
        v = SentinelVerdict(blocked=False, source="clean")
        assert not v.blocked
        assert v.source == "clean"
        assert v.rule_id == ""

    def test_blocked_verdict(self):
        v = SentinelVerdict(blocked=True, rule_id="R-007", severity="HIGH", source="align")
        assert v.blocked
        assert v.rule_id == "R-007"
        assert v.severity == "HIGH"


# ─── scan_payload Tests ──────────────────────────────────────────────────────


class TestScanPayload:
    def test_clean_payload_returns_not_blocked(self):
        verdict = scan_payload("hello world this is a safe message")
        assert not verdict.blocked
        assert verdict.source == "clean"

    def test_align_blocked_payload(self):
        """Payload matching an ALIGN rule is blocked (source='align')."""
        verdict = scan_payload("os.system('rm -rf /')")
        assert verdict.blocked
        assert verdict.source == "align"
        assert verdict.rule_id != ""

    def test_privesc_blocked_payload(self):
        """Payload with nsenter (PRIVESC-006) is caught after ALIGN check."""
        # nsenter is not in ALIGN but is a PrivEsc pattern
        verdict = scan_payload("nsenter --target 1 --mount --uts --ipc --net")
        assert verdict.blocked
        assert verdict.source == "privesc"
        assert verdict.rule_id.startswith("PRIVESC-")

    def test_exfil_blocked_base64(self):
        """Large base64 blob triggers exfiltration detection."""
        big_b64 = "A" * 100
        verdict = scan_payload(big_b64)
        assert verdict.blocked
        assert verdict.source == "exfil"
        assert verdict.rule_id.startswith("EXFIL-")

    def test_exfil_blocked_ssn(self):
        verdict = scan_payload("user SSN is 123-45-6789 in the system")
        assert verdict.blocked
        assert verdict.source == "exfil"

    def test_exfil_blocked_credit_card(self):
        verdict = scan_payload("card: 4111 1111 1111 1111")
        assert verdict.blocked
        assert verdict.source == "exfil"

    def test_exfil_blocked_private_key(self):
        verdict = scan_payload("private_key: supersecretvalue1234")
        assert verdict.blocked
        # R-012 (ALIGN) catches private_key before the EXFIL pattern — correct priority
        assert verdict.source == "align"

    def test_dict_payload_serialized(self):
        """scan_payload accepts a dict."""
        verdict = scan_payload({"action": "read", "path": "/safe/path"})
        assert not verdict.blocked

    def test_dict_payload_with_inject(self):
        """dict payload with injection is caught."""
        verdict = scan_payload({"cmd": "os.system('ls')"})
        assert verdict.blocked

    def test_privesc_container_escape(self):
        """Container-escape pattern PRIVESC-006 triggers."""
        verdict = scan_payload("chroot / bash")
        assert verdict.blocked
        assert verdict.source in ("privesc", "align")


# ─── get_stats Tests ─────────────────────────────────────────────────────────


class TestGetStats:
    def test_initial_stats_all_zero(self):
        stats = get_stats()
        assert stats["scan_count"] == 0
        assert stats["block_count"] == 0
        assert stats["align_block_count"] == 0
        assert stats["privesc_block_count"] == 0
        assert stats["exfil_block_count"] == 0
        assert stats["block_rate"] == 0.0

    def test_scan_count_increments(self):
        scan_payload("hello world")
        scan_payload("another safe payload")
        assert get_stats()["scan_count"] == 2

    def test_block_count_increments_on_block(self):
        scan_payload("os.system('dangerous')")
        stats = get_stats()
        assert stats["block_count"] >= 1

    def test_align_block_count_increments(self):
        # os.system matches ALIGN R-007
        scan_payload("os.system('rm -rf /')")
        stats = get_stats()
        assert stats["align_block_count"] >= 1

    def test_exfil_block_count_increments(self):
        scan_payload("user SSN: 123-45-6789")
        stats = get_stats()
        assert stats["exfil_block_count"] >= 1

    def test_block_rate_calculation(self):
        scan_payload("safe")
        scan_payload("os.system('x')")  # blocked
        stats = get_stats()
        assert stats["scan_count"] == 2
        assert stats["block_rate"] == 0.5

    def test_stats_keys_present(self):
        stats = get_stats()
        required_keys = {"scan_count", "block_count", "align_block_count", "privesc_block_count", "exfil_block_count", "block_rate"}
        assert required_keys.issubset(stats.keys())


# ─── get_agent_profile Tests ─────────────────────────────────────────────────


class TestGetAgentProfile:
    def test_profile_has_required_keys(self):
        profile = get_agent_profile()
        assert "name" in profile
        assert "system_prompt" in profile
        assert "tools" in profile
        assert "restrictions" in profile

    def test_profile_name_is_sentinel(self):
        assert get_agent_profile()["name"] == "sentinel"

    def test_profile_tools_includes_scan_payload(self):
        assert "scan_payload" in get_agent_profile()["tools"]

    def test_profile_gas_cost_positive(self):
        restrictions = get_agent_profile()["restrictions"]
        assert restrictions["gas_per_scan"] > 0

    def test_profile_temperature_is_zero(self):
        """Sentinel must be deterministic (temp=0)."""
        restrictions = get_agent_profile()["restrictions"]
        assert restrictions["temperature"] == 0.0


# ─── EXFIL_PATTERNS Coverage ─────────────────────────────────────────────────


class TestExfilPatterns:
    def test_explicit_exfil_verb_detected(self):
        verdict = scan_payload("attempting to exfil user records from the db")
        assert verdict.blocked
        assert verdict.source == "exfil"

    def test_harvest_cred_detected(self):
        verdict = scan_payload("harvest credentials from the auth service")
        assert verdict.blocked
        assert verdict.source == "exfil"

    def test_api_key_in_payload_detected(self):
        verdict = scan_payload("api_key: sk-abc123def456ghi789jkl012mno345pqr")
        assert verdict.blocked
        assert verdict.source == "exfil"

    def test_safe_content_passes(self):
        verdict = scan_payload("[INTENT] analyze /data/report.csv [EXPECT] summary Ω")
        assert not verdict.blocked
