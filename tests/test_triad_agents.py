import json
import sqlite3
import time

import pytest

from agents.core.arbiter_agent import ArbiterVerdict, adjudicate
from agents.core.scribe_agent import BudgetStatus, audit_budget
from agents.core.sentinel_agent import SentinelVerdict, scan_payload


class TestSentinelAgent:
    def test_scan_payload_clean(self):
        verdict = scan_payload("Just a normal string with no malicious content.")
        assert isinstance(verdict, SentinelVerdict)
        assert not verdict.blocked
        assert verdict.source == "clean"

    def test_scan_payload_privesc(self):
        verdict = scan_payload("I want to dump all the secrets")
        assert isinstance(verdict, SentinelVerdict)
        assert verdict.blocked
        assert verdict.source == "privesc"
        assert verdict.rule_id == "PRIVESC-005"
        assert verdict.severity == "CRITICAL"

    def test_scan_payload_dict_input(self):
        # Ensure dict payloads are serialised via json.dumps internally
        payload = json.loads('{"action": "read", "target": "/tmp/safe"}')
        verdict = scan_payload(payload)
        assert isinstance(verdict, SentinelVerdict)
        assert not verdict.blocked


class TestScribeAgent:
    @pytest.fixture
    def mock_db(self):
        conn = sqlite3.connect(":memory:")
        # Provide the expected schema for memory.db as requested by audit_budget
        conn.execute(
            "CREATE TABLE rolling_context (id INTEGER PRIMARY KEY, role TEXT, content TEXT, token_count INTEGER)"
        )
        yield conn
        conn.close()

    def test_audit_budget_under_limit(self, mock_db):
        mock_db.execute("INSERT INTO rolling_context (content, token_count) VALUES (?, ?)", ("some text", 100))
        mock_db.commit()
        status = audit_budget(conn=mock_db, max_context_tokens=1000)
        assert isinstance(status, BudgetStatus)
        assert not status.gate_blocked
        assert status.pct == 0.1

    def test_audit_budget_over_limit(self, mock_db):
        mock_db.execute("INSERT INTO rolling_context (content, token_count) VALUES (?, ?)", ("lots of text", 900))
        mock_db.commit()
        status = audit_budget(conn=mock_db, max_context_tokens=1000)
        assert isinstance(status, BudgetStatus)
        assert status.gate_blocked
        assert status.pct >= 0.80


class TestArbiterAgent:
    def test_adjudicate_security_alert_quarantine(self):
        # Arbiter quarantines SECURITY_ALERT events from Sentinel if blocked by ALIGN
        payload = {
            "source_agent": "sentinel",
            "rule_id": "PRIVESC-001",
            "severity": "CRITICAL",
            "preview": "chmod 777",
            "ts": time.time(),
        }
        verdict = adjudicate("SECURITY_ALERT", payload)
        assert isinstance(verdict, ArbiterVerdict)
        assert verdict.verdict == "QUARANTINE"
        assert verdict.event_type == "SECURITY_ALERT"

    def test_adjudicate_budget_gate_escalate(self):
        payload = {
            "source_agent": "scribe",
            "pct": 0.95,
            "tokens_used": 950,
            "ts": time.time(),
        }
        verdict = adjudicate("BUDGET_GATE", payload)
        assert isinstance(verdict, ArbiterVerdict)
        assert verdict.verdict == "ESCALATE"
        assert verdict.event_type == "BUDGET_GATE"

    def test_adjudicate_budget_gate_quarantine(self):
        payload = {
            "source_agent": "scribe",
            "pct": 0.99,
            "tokens_used": 990,
            "ts": time.time(),
        }
        verdict = adjudicate("BUDGET_GATE", payload)
        assert isinstance(verdict, ArbiterVerdict)
        assert verdict.verdict == "QUARANTINE"
        assert verdict.event_type == "BUDGET_GATE"

    def test_adjudicate_json_string_payload(self):
        # Arbiter must handle a pre-serialised JSON string (Redis stream path).
        # When given a string, severity cannot be extracted → defaults to HIGH → ESCALATE.
        payload_str = json.dumps(
            {
                "source_agent": "sentinel",
                "rule_id": "PRIVESC-003",
                "severity": "CRITICAL",
                "preview": "setuid detected",
                "ts": time.time(),
            }
        )
        verdict = adjudicate("SECURITY_ALERT", payload_str)
        assert isinstance(verdict, ArbiterVerdict)
        assert verdict.verdict == "ESCALATE"
        assert verdict.event_type == "SECURITY_ALERT"
