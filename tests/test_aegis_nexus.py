"""
Tests for the Tri-Perspective Aegis-Nexus Engine.

Covers:
  - Sentinel Agent: scan_payload, agent profile, ALIGN + PrivEsc detection
  - Scribe Agent:   audit_budget, agent profile, 80% gate logic
  - Arbiter Agent:  adjudicate, agent profile, all verdict paths
  - DB seeding:     seed_aegis_templates registers all 3 templates
  - Gas constants:  each agent exposes the expected gas-cost constant
  - Stream names:   each agent declares the expected Redis stream constants
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_memory_db(path: Path) -> sqlite3.Connection:
    """Create a minimal memory.db with the rolling_context table."""
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rolling_context (
            session_id  TEXT NOT NULL,
            timestamp   REAL NOT NULL,
            fifo_blob   TEXT NOT NULL DEFAULT '',
            token_count INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    return conn


# ══════════════════════════════════════════════════════════════════════════════
# Sentinel Agent Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestSentinelAgentProfile:
    """Verify Sentinel AgentProfile spec."""

    def test_profile_has_required_keys(self) -> None:
        from agents.core.sentinel_agent import get_agent_profile
        profile = get_agent_profile()
        assert profile["name"] == "sentinel"
        assert "required_tier" in profile
        assert "system_prompt" in profile
        assert len(profile["system_prompt"]) > 30
        assert "tools" in profile
        assert "restrictions" in profile

    def test_profile_gas_cost(self) -> None:
        from agents.core.sentinel_agent import SCAN_GAS_COST, get_agent_profile
        profile = get_agent_profile()
        assert profile["restrictions"]["gas_per_scan"] == SCAN_GAS_COST
        assert SCAN_GAS_COST > 0

    def test_stream_names_defined(self) -> None:
        from agents.core.sentinel_agent import ARBITER_EVENTS_STREAM, SENTINEL_EVENTS_STREAM
        assert SENTINEL_EVENTS_STREAM == "sentinel_events"
        assert ARBITER_EVENTS_STREAM == "arbiter_events"


class TestSentinelScanPayload:
    """Test scan_payload logic for various payload types."""

    def test_clean_payload_not_blocked(self) -> None:
        from agents.core.sentinel_agent import scan_payload
        verdict = scan_payload("hello world, just a harmless message")
        assert verdict.blocked is False
        assert verdict.source == "clean"
        assert verdict.rule_id == ""

    def test_clean_dict_not_blocked(self) -> None:
        from agents.core.sentinel_agent import scan_payload
        verdict = scan_payload({"intent": "analyze logs", "tier": "hearth"})
        assert verdict.blocked is False

    def test_align_rule_r001_blocked(self) -> None:
        """ALIGN R-001: shell injection via /bin/sh."""
        from agents.core.sentinel_agent import scan_payload
        verdict = scan_payload("run /bin/sh -c whoami")
        assert verdict.blocked is True
        assert verdict.source == "align"
        assert verdict.rule_id == "R-001"
        assert verdict.severity == "HIGH"

    def test_align_rule_r006_blocked(self) -> None:
        """ALIGN R-006: chmod 777 privilege escalation."""
        from agents.core.sentinel_agent import scan_payload
        verdict = scan_payload("chmod 777 /app/config")
        assert verdict.blocked is True
        assert verdict.source == "align"
        assert verdict.rule_id == "R-006"

    def test_align_rule_r007_blocked(self) -> None:
        """ALIGN R-007: process injection via os.system."""
        from agents.core.sentinel_agent import scan_payload
        verdict = scan_payload("execute using os.system('ls')")
        assert verdict.blocked is True
        assert verdict.source == "align"

    def test_privesc_etc_shadow(self) -> None:
        """Extended pattern PRIVESC-002: /etc/shadow read attempt."""
        from agents.core.sentinel_agent import scan_payload
        verdict = scan_payload("cat /etc/shadow | grep root")
        assert verdict.blocked is True
        assert verdict.source == "privesc"
        assert verdict.rule_id == "PRIVESC-002"
        assert verdict.severity == "CRITICAL"

    def test_privesc_setuid(self) -> None:
        """Extended pattern PRIVESC-003: setuid abuse."""
        from agents.core.sentinel_agent import scan_payload
        verdict = scan_payload("compile with setuid bit enabled")
        assert verdict.blocked is True
        assert verdict.source == "privesc"
        assert verdict.rule_id == "PRIVESC-003"

    def test_privesc_ptrace(self) -> None:
        """Extended pattern PRIVESC-004: ptrace debug attach."""
        from agents.core.sentinel_agent import scan_payload
        verdict = scan_payload("attach ptrace to process 1234")
        assert verdict.blocked is True
        assert verdict.source == "privesc"
        assert verdict.rule_id == "PRIVESC-004"

    def test_align_takes_precedence_over_privesc(self) -> None:
        """If ALIGN fires, source should be 'align', not 'privesc'."""
        from agents.core.sentinel_agent import scan_payload
        # /bin/sh triggers R-001; also contains /etc/shadow-like content
        verdict = scan_payload("/bin/sh -c 'cat /etc/shadow'")
        assert verdict.blocked is True
        assert verdict.source == "align"  # ALIGN checked first

    def test_dict_payload_serialised_for_scanning(self) -> None:
        """dict payload is JSON-serialised before pattern matching."""
        from agents.core.sentinel_agent import scan_payload
        verdict = scan_payload({"cmd": "/bin/sh", "args": ["-c", "id"]})
        assert verdict.blocked is True
        assert verdict.source == "align"


class TestSentinelPublishAlert:
    """Verify _publish_alert calls xadd with correct structure."""

    def test_publish_alert_calls_xadd(self) -> None:
        from agents.core.sentinel_agent import SentinelVerdict, _publish_alert

        mock_r = MagicMock()
        verdict = SentinelVerdict(blocked=True, rule_id="R-001", severity="HIGH", source="align")
        _publish_alert(mock_r, verdict, "test payload")

        mock_r.xadd.assert_called_once()
        call_args = mock_r.xadd.call_args
        stream_name = call_args[0][0]
        assert stream_name == "arbiter_events"
        payload = json.loads(call_args[0][1]["data"])
        assert payload["event_type"] == "SECURITY_ALERT"
        assert payload["source_agent"] == "sentinel"
        assert payload["rule_id"] == "R-001"
        assert payload["severity"] == "HIGH"

    def test_publish_alert_handles_xadd_error(self) -> None:
        from agents.core.sentinel_agent import SentinelVerdict, _publish_alert

        mock_r = MagicMock()
        mock_r.xadd.side_effect = ConnectionError("Redis unavailable")
        verdict = SentinelVerdict(blocked=True, rule_id="R-001", severity="HIGH", source="align")
        # Should not raise
        _publish_alert(mock_r, verdict, "payload")


class TestSentinelThreadLifecycle:
    """Verify start/stop lifecycle without a real Redis connection."""

    def test_start_stop_no_redis(self) -> None:
        import agents.core.sentinel_agent as sa

        # Reset module state so the test is isolated
        sa._stop_event.clear()
        sa._thread = None

        with patch("agents.core.sentinel_agent._consume_loop"):
            sa.start()
            assert sa._thread is not None
            sa.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Scribe Agent Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestScribeAgentProfile:
    """Verify Scribe AgentProfile spec."""

    def test_profile_has_required_keys(self) -> None:
        from agents.core.scribe_agent import get_agent_profile
        profile = get_agent_profile()
        assert profile["name"] == "scribe"
        assert "required_tier" in profile
        assert "system_prompt" in profile
        assert len(profile["system_prompt"]) > 30
        assert "restrictions" in profile
        assert "budget_gate_pct" in profile["restrictions"]

    def test_profile_gas_cost(self) -> None:
        from agents.core.scribe_agent import AUDIT_GAS_COST, get_agent_profile
        profile = get_agent_profile()
        assert profile["restrictions"]["gas_per_audit"] == AUDIT_GAS_COST
        assert AUDIT_GAS_COST > 0

    def test_budget_gate_pct_value(self) -> None:
        from agents.core.scribe_agent import BUDGET_GATE_PCT
        assert pytest.approx(0.80) == BUDGET_GATE_PCT

    def test_stream_names_defined(self) -> None:
        from agents.core.scribe_agent import ARBITER_EVENTS_STREAM, SCRIBE_EVENTS_STREAM
        assert SCRIBE_EVENTS_STREAM == "scribe_events"
        assert ARBITER_EVENTS_STREAM == "arbiter_events"


class TestScribeAuditBudget:
    """Test audit_budget logic."""

    def test_audit_below_threshold(self, tmp_path: Path) -> None:
        from agents.core.scribe_agent import audit_budget

        conn = _make_memory_db(tmp_path / "mem.db")
        conn.execute(
            "INSERT INTO rolling_context (session_id, timestamp, fifo_blob, token_count) "
            "VALUES ('s1', ?, '', 3000)",
            (time.time(),),
        )
        conn.commit()

        status = audit_budget(conn=conn, max_context_tokens=8192)
        assert status.tokens_used == 3000
        assert status.budget == 8192
        assert status.pct == pytest.approx(3000 / 8192)
        assert status.gate_blocked is False
        conn.close()

    def test_audit_at_80_percent(self, tmp_path: Path) -> None:
        from agents.core.scribe_agent import audit_budget

        conn = _make_memory_db(tmp_path / "mem.db")
        budget = 10000
        conn.execute(
            "INSERT INTO rolling_context (session_id, timestamp, fifo_blob, token_count) "
            "VALUES ('s1', ?, '', 8000)",
            (time.time(),),
        )
        conn.commit()

        status = audit_budget(conn=conn, max_context_tokens=budget)
        assert status.tokens_used == 8000
        assert status.gate_blocked is True  # 80% == threshold

    def test_audit_above_80_percent(self, tmp_path: Path) -> None:
        from agents.core.scribe_agent import audit_budget

        conn = _make_memory_db(tmp_path / "mem.db")
        conn.execute(
            "INSERT INTO rolling_context (session_id, timestamp, fifo_blob, token_count) "
            "VALUES ('s1', ?, '', 9000)",
            (time.time(),),
        )
        conn.commit()

        status = audit_budget(conn=conn, max_context_tokens=10000)
        assert status.gate_blocked is True
        assert status.pct == pytest.approx(0.9)

    def test_audit_multiple_rows_sum(self, tmp_path: Path) -> None:
        from agents.core.scribe_agent import audit_budget

        conn = _make_memory_db(tmp_path / "mem.db")
        now = time.time()
        conn.executemany(
            "INSERT INTO rolling_context (session_id, timestamp, fifo_blob, token_count) "
            "VALUES (?, ?, '', ?)",
            [("s1", now, 2000), ("s2", now, 2000), ("s3", now, 2000)],
        )
        conn.commit()

        status = audit_budget(conn=conn, max_context_tokens=8192)
        assert status.tokens_used == 6000

    def test_audit_missing_db_file(self, tmp_path: Path) -> None:
        """Missing DB file should return graceful non-blocked status."""
        from agents.core.scribe_agent import audit_budget

        missing = tmp_path / "nonexistent.db"
        status = audit_budget(db_path=missing, max_context_tokens=8192)
        assert status.tokens_used == 0
        assert status.gate_blocked is False

    def test_audit_empty_db(self, tmp_path: Path) -> None:
        """Empty rolling_context → 0 tokens used, not blocked."""
        from agents.core.scribe_agent import audit_budget

        conn = _make_memory_db(tmp_path / "empty.db")
        status = audit_budget(conn=conn, max_context_tokens=8192)
        assert status.tokens_used == 0
        assert status.gate_blocked is False
        conn.close()


class TestScribePublishBudgetAlert:
    """Verify _publish_budget_alert calls xadd with correct structure."""

    def test_publish_budget_alert_calls_xadd(self) -> None:
        from agents.core.scribe_agent import BudgetStatus, _publish_budget_alert

        mock_r = MagicMock()
        status = BudgetStatus(tokens_used=8000, budget=10000, pct=0.80, gate_blocked=True)
        _publish_budget_alert(mock_r, status)

        mock_r.xadd.assert_called_once()
        call_args = mock_r.xadd.call_args
        assert call_args[0][0] == "arbiter_events"
        payload = json.loads(call_args[0][1]["data"])
        assert payload["event_type"] == "BUDGET_GATE"
        assert payload["source_agent"] == "scribe"
        assert payload["tokens_used"] == 8000
        assert payload["budget"] == 10000

    def test_publish_budget_alert_handles_error(self) -> None:
        from agents.core.scribe_agent import BudgetStatus, _publish_budget_alert

        mock_r = MagicMock()
        mock_r.xadd.side_effect = ConnectionError("Redis down")
        status = BudgetStatus(tokens_used=9000, budget=10000, pct=0.9, gate_blocked=True)
        # Should not raise
        _publish_budget_alert(mock_r, status)


class TestScribeThreadLifecycle:
    """Verify start/stop lifecycle without a real Redis connection."""

    def test_start_stop_no_redis(self) -> None:
        import agents.core.scribe_agent as sa

        sa._stop_event.clear()
        sa._thread = None

        with patch("agents.core.scribe_agent._consume_loop"):
            sa.start()
            assert sa._thread is not None
            sa.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Arbiter Agent Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestArbiterAgentProfile:
    """Verify Arbiter AgentProfile spec."""

    def test_profile_has_required_keys(self) -> None:
        from agents.core.arbiter_agent import get_agent_profile
        profile = get_agent_profile()
        assert profile["name"] == "arbiter"
        assert "required_tier" in profile
        assert "system_prompt" in profile
        assert len(profile["system_prompt"]) > 30
        assert "restrictions" in profile

    def test_profile_gas_cost(self) -> None:
        from agents.core.arbiter_agent import ADJUDICATE_GAS_COST, get_agent_profile
        profile = get_agent_profile()
        assert profile["restrictions"]["gas_per_adjudication"] == ADJUDICATE_GAS_COST
        assert ADJUDICATE_GAS_COST >= 2  # Adjudication is more expensive

    def test_stream_name_defined(self) -> None:
        from agents.core.arbiter_agent import ARBITER_EVENTS_STREAM
        assert ARBITER_EVENTS_STREAM == "arbiter_events"

    def test_verdict_constants(self) -> None:
        from agents.core.arbiter_agent import VERDICT_ALLOW, VERDICT_ESCALATE, VERDICT_QUARANTINE
        assert VERDICT_ALLOW == "ALLOW"
        assert VERDICT_ESCALATE == "ESCALATE"
        assert VERDICT_QUARANTINE == "QUARANTINE"


class TestArbiterAdjudicate:
    """Test adjudicate() decision logic for all paths."""

    # ── SECURITY_ALERT paths ────────────────────────────────────────────────

    def test_security_alert_critical_quarantine(self) -> None:
        from agents.core.arbiter_agent import VERDICT_QUARANTINE, adjudicate

        payload = {"event_type": "SECURITY_ALERT", "severity": "CRITICAL", "rule_id": "PRIVESC-002"}
        verdict = adjudicate("SECURITY_ALERT", payload)
        assert verdict.verdict == VERDICT_QUARANTINE
        assert verdict.event_type == "SECURITY_ALERT"
        assert "PRIVESC-002" in verdict.justification

    def test_security_alert_high_escalate(self) -> None:
        from agents.core.arbiter_agent import VERDICT_ESCALATE, adjudicate

        payload = {"event_type": "SECURITY_ALERT", "severity": "HIGH", "rule_id": "R-001"}
        verdict = adjudicate("SECURITY_ALERT", payload)
        assert verdict.verdict == VERDICT_ESCALATE

    def test_security_alert_medium_escalate(self) -> None:
        from agents.core.arbiter_agent import VERDICT_ESCALATE, adjudicate

        payload = {"event_type": "SECURITY_ALERT", "severity": "MEDIUM", "rule_id": "R-005"}
        verdict = adjudicate("SECURITY_ALERT", payload)
        assert verdict.verdict == VERDICT_ESCALATE

    # ── BUDGET_GATE paths ────────────────────────────────────────────────────

    def test_budget_gate_below_90_allow(self) -> None:
        from agents.core.arbiter_agent import VERDICT_ALLOW, adjudicate

        payload = {"event_type": "BUDGET_GATE", "pct": 0.75}
        verdict = adjudicate("BUDGET_GATE", payload)
        assert verdict.verdict == VERDICT_ALLOW

    def test_budget_gate_at_90_escalate(self) -> None:
        from agents.core.arbiter_agent import VERDICT_ESCALATE, adjudicate

        payload = {"event_type": "BUDGET_GATE", "pct": 0.90}
        verdict = adjudicate("BUDGET_GATE", payload)
        assert verdict.verdict == VERDICT_ESCALATE

    def test_budget_gate_above_90_escalate(self) -> None:
        from agents.core.arbiter_agent import VERDICT_ESCALATE, adjudicate

        payload = {"event_type": "BUDGET_GATE", "pct": 0.95}
        verdict = adjudicate("BUDGET_GATE", payload)
        assert verdict.verdict == VERDICT_ESCALATE

    def test_budget_gate_at_98_quarantine(self) -> None:
        from agents.core.arbiter_agent import VERDICT_QUARANTINE, adjudicate

        payload = {"event_type": "BUDGET_GATE", "pct": 0.98}
        verdict = adjudicate("BUDGET_GATE", payload)
        assert verdict.verdict == VERDICT_QUARANTINE

    def test_budget_gate_above_98_quarantine(self) -> None:
        from agents.core.arbiter_agent import VERDICT_QUARANTINE, adjudicate

        payload = {"event_type": "BUDGET_GATE", "pct": 1.0}
        verdict = adjudicate("BUDGET_GATE", payload)
        assert verdict.verdict == VERDICT_QUARANTINE

    # ── ALIGN override paths ─────────────────────────────────────────────────

    def test_align_blocked_payload_quarantine(self) -> None:
        """ALIGN Ledger is the authoritative override for any event type."""
        from agents.core.arbiter_agent import VERDICT_QUARANTINE, adjudicate

        # ALIGN R-001 pattern embedded in a BUDGET_GATE payload
        payload = {"event_type": "BUDGET_GATE", "pct": 0.5, "note": "/bin/sh exploit"}
        verdict = adjudicate("BUDGET_GATE", payload)
        assert verdict.verdict == VERDICT_QUARANTINE
        assert verdict.rule_id == "R-001"

    def test_align_blocked_security_alert_quarantine(self) -> None:
        """ALIGN fires even on SECURITY_ALERT events."""
        from agents.core.arbiter_agent import VERDICT_QUARANTINE, adjudicate

        payload = {
            "event_type": "SECURITY_ALERT",
            "severity": "HIGH",
            "preview": "using os.system('rm -rf /')",
        }
        verdict = adjudicate("SECURITY_ALERT", payload)
        assert verdict.verdict == VERDICT_QUARANTINE

    # ── Unknown event paths ──────────────────────────────────────────────────

    def test_unknown_event_escalate(self) -> None:
        from agents.core.arbiter_agent import VERDICT_ESCALATE, adjudicate

        verdict = adjudicate("TOTALLY_UNKNOWN", {"some": "data"})
        assert verdict.verdict == VERDICT_ESCALATE
        assert "TOTALLY_UNKNOWN" in verdict.justification

    def test_string_payload(self) -> None:
        """adjudicate() accepts raw string payloads."""
        from agents.core.arbiter_agent import VERDICT_ESCALATE, adjudicate

        verdict = adjudicate("UNKNOWN_TYPE", "just some string payload")
        assert verdict.verdict == VERDICT_ESCALATE

    def test_verdict_dataclass_fields(self) -> None:
        from agents.core.arbiter_agent import adjudicate

        verdict = adjudicate("BUDGET_GATE", {"pct": 0.5})
        assert hasattr(verdict, "verdict")
        assert hasattr(verdict, "rule_id")
        assert hasattr(verdict, "justification")
        assert hasattr(verdict, "event_type")
        assert verdict.event_type == "BUDGET_GATE"


class TestArbiterThreadLifecycle:
    """Verify start/stop lifecycle without a real Redis connection."""

    def test_start_stop_no_redis(self) -> None:
        import agents.core.arbiter_agent as aa

        aa._stop_event.clear()
        aa._thread = None

        with patch("agents.core.arbiter_agent._consume_loop"):
            aa.start()
            assert aa._thread is not None
            aa.stop()


# ══════════════════════════════════════════════════════════════════════════════
# DB Template Seeding
# ══════════════════════════════════════════════════════════════════════════════

class TestSeedAegisTemplates:
    """Verify seed_aegis_templates() registers all three agent templates."""

    def test_seeds_all_three_templates(self, tmp_path: Path) -> None:
        from agents.core.db import get_agent_template, get_db, init_db, seed_aegis_templates

        db_file = tmp_path / "registry.db"
        init_db(db_file)

        with get_db(db_file) as conn:
            seed_aegis_templates(conn)

            sentinel_tmpl = get_agent_template(conn, "sentinel")
            scribe_tmpl = get_agent_template(conn, "scribe")
            arbiter_tmpl = get_agent_template(conn, "arbiter")

        assert sentinel_tmpl is not None, "sentinel template missing"
        assert scribe_tmpl is not None, "scribe template missing"
        assert arbiter_tmpl is not None, "arbiter template missing"

    def test_sentinel_template_fields(self, tmp_path: Path) -> None:
        from agents.core.db import get_agent_template, get_db, init_db, seed_aegis_templates

        db_file = tmp_path / "registry.db"
        init_db(db_file)

        with get_db(db_file) as conn:
            seed_aegis_templates(conn)
            tmpl = get_agent_template(conn, "sentinel")

        assert tmpl is not None
        assert len(tmpl["system_prompt"]) > 20
        restrictions = json.loads(tmpl["restrictions_json"])
        assert "gas_per_scan" in restrictions

    def test_scribe_template_fields(self, tmp_path: Path) -> None:
        from agents.core.db import get_agent_template, get_db, init_db, seed_aegis_templates

        db_file = tmp_path / "registry.db"
        init_db(db_file)

        with get_db(db_file) as conn:
            seed_aegis_templates(conn)
            tmpl = get_agent_template(conn, "scribe")

        assert tmpl is not None
        restrictions = json.loads(tmpl["restrictions_json"])
        assert "budget_gate_pct" in restrictions
        assert restrictions["budget_gate_pct"] == pytest.approx(0.80)

    def test_arbiter_template_fields(self, tmp_path: Path) -> None:
        from agents.core.db import get_agent_template, get_db, init_db, seed_aegis_templates

        db_file = tmp_path / "registry.db"
        init_db(db_file)

        with get_db(db_file) as conn:
            seed_aegis_templates(conn)
            tmpl = get_agent_template(conn, "arbiter")

        assert tmpl is not None
        restrictions = json.loads(tmpl["restrictions_json"])
        assert "gas_per_adjudication" in restrictions
        assert restrictions["gas_per_adjudication"] >= 2

    def test_seed_is_idempotent(self, tmp_path: Path) -> None:
        """Calling seed_aegis_templates twice should not raise or duplicate rows."""
        from agents.core.db import get_db, init_db, seed_aegis_templates

        db_file = tmp_path / "registry.db"
        init_db(db_file)

        with get_db(db_file) as conn:
            seed_aegis_templates(conn)
            seed_aegis_templates(conn)  # second call — idempotent

            count = conn.execute(
                "SELECT COUNT(*) FROM agent_templates WHERE name IN ('sentinel','scribe','arbiter')"
            ).fetchone()[0]

        assert count == 3  # exactly 3 rows, no duplicates


# ══════════════════════════════════════════════════════════════════════════════
# Gas Constants Consistency
# ══════════════════════════════════════════════════════════════════════════════

class TestGasConstants:
    """Verify gas constants are consistent across agents and profiles."""

    def test_sentinel_gas_constant_matches_profile(self) -> None:
        from agents.core.sentinel_agent import SCAN_GAS_COST, get_agent_profile
        profile = get_agent_profile()
        assert profile["restrictions"]["gas_per_scan"] == SCAN_GAS_COST

    def test_scribe_gas_constant_matches_profile(self) -> None:
        from agents.core.scribe_agent import AUDIT_GAS_COST, get_agent_profile
        profile = get_agent_profile()
        assert profile["restrictions"]["gas_per_audit"] == AUDIT_GAS_COST

    def test_arbiter_gas_constant_matches_profile(self) -> None:
        from agents.core.arbiter_agent import ADJUDICATE_GAS_COST, get_agent_profile
        profile = get_agent_profile()
        assert profile["restrictions"]["gas_per_adjudication"] == ADJUDICATE_GAS_COST

    def test_arbiter_gas_more_expensive_than_sentinel(self) -> None:
        """Adjudication is heavier than a simple scan."""
        from agents.core.arbiter_agent import ADJUDICATE_GAS_COST
        from agents.core.sentinel_agent import SCAN_GAS_COST
        assert ADJUDICATE_GAS_COST > SCAN_GAS_COST


# ══════════════════════════════════════════════════════════════════════════════
# Integration: Sentinel → Arbiter pipeline
# ══════════════════════════════════════════════════════════════════════════════

class TestSentinelToArbiterPipeline:
    """Simulate the Sentinel detecting a threat and Arbiter adjudicating it."""

    def test_privesc_flows_to_quarantine(self) -> None:
        """PrivEsc alert from Sentinel should result in QUARANTINE from Arbiter."""
        from agents.core.arbiter_agent import VERDICT_QUARANTINE, adjudicate
        from agents.core.sentinel_agent import scan_payload

        # Sentinel scans a payload with a PrivEsc pattern
        verdict = scan_payload("cat /etc/shadow")
        assert verdict.blocked is True
        assert verdict.severity == "CRITICAL"

        # Arbiter receives the SECURITY_ALERT
        arbiter_payload = {
            "event_type": "SECURITY_ALERT",
            "source_agent": "sentinel",
            "rule_id": verdict.rule_id,
            "severity": verdict.severity,
        }
        arb_verdict = adjudicate("SECURITY_ALERT", arbiter_payload)
        assert arb_verdict.verdict == VERDICT_QUARANTINE

    def test_budget_breach_flows_to_escalate(self, tmp_path: Path) -> None:
        """Budget breach from Scribe should result in ESCALATE from Arbiter."""
        from agents.core.arbiter_agent import VERDICT_ESCALATE, adjudicate
        from agents.core.scribe_agent import audit_budget

        conn = _make_memory_db(tmp_path / "mem.db")
        conn.execute(
            "INSERT INTO rolling_context (session_id, timestamp, fifo_blob, token_count) "
            "VALUES ('s1', ?, '', 9200)",
            (time.time(),),
        )
        conn.commit()

        status = audit_budget(conn=conn, max_context_tokens=10000)
        assert status.gate_blocked is True

        # Arbiter receives the BUDGET_GATE event
        arbiter_payload = {
            "event_type": "BUDGET_GATE",
            "source_agent": "scribe",
            "pct": status.pct,
        }
        arb_verdict = adjudicate("BUDGET_GATE", arbiter_payload)
        assert arb_verdict.verdict == VERDICT_ESCALATE
        conn.close()
