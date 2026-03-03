"""
Sentinel Agent — Aegis-Nexus Engine (Red Hat / Security).

Responsibilities:
1. Privilege escalation detection via extended pattern library.
2. Security scan of incoming payloads (HLF + raw text).
3. Publish ALERT events to the ``arbiter_events`` Redis Stream.
4. Consume ``sentinel_events`` stream for reactive scanning.
5. Gas-accounted: each scan costs 1 unit from the per-tier bucket.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from agents.core.logger import ALSLogger
from agents.gateway.sentinel_gate import enforce_align

_logger = ALSLogger(agent_role="sentinel-agent", goal_id="security")

# Extended privilege-escalation + injection patterns beyond ALIGN_LEDGER.yaml
_PRIVESC_PATTERNS: list[tuple[str, str]] = [
    (r"(?i)(chmod\s+[0-7]*7[0-7]*|chown\s+root|sudo\s+\S)", "PRIVESC-001"),
    (r"(?i)(\/etc\/shadow|\/etc\/passwd|\/proc\/self)", "PRIVESC-002"),
    (r"(?i)(setuid|setgid|linux_capabilities|cap_sys_admin)", "PRIVESC-003"),
    (r"(?i)(ptrace|SYS_ADMIN|CAP_SYS_PTRACE)", "PRIVESC-004"),
    (r"(?i)(token.*exfil|steal.*credential|dump.*secret)", "PRIVESC-005"),
]
_COMPILED_PRIVESC: list[tuple[re.Pattern[str], str]] = [(re.compile(pat), rid) for pat, rid in _PRIVESC_PATTERNS]

# Redis stream names
SENTINEL_EVENTS_STREAM = "sentinel_events"
ARBITER_EVENTS_STREAM = "arbiter_events"
_CONSUMER_GROUP = "sentinel-group"

# Gas cost per scan
SCAN_GAS_COST = 1


@dataclass
class SentinelVerdict:
    """Result of a Sentinel security scan."""

    blocked: bool
    rule_id: str = ""
    severity: str = "INFO"
    source: str = ""  # "align" | "privesc" | "clean"


def get_agent_profile() -> dict[str, Any]:
    """Return the Sentinel AgentProfile spec for DB template registration."""
    return {
        "name": "sentinel",
        "required_tier": "D",
        "system_prompt": (
            "You are the SENTINEL agent — Sovereign OS security guardian. "
            "Detect privilege escalation, injection attacks, data exfiltration, "
            "and ALIGN Ledger violations. Return structured JSON findings."
        ),
        "tools": ["enforce_align", "scan_payload"],
        "restrictions": {
            "max_tokens": 2048,
            "temperature": 0.0,
            "gas_per_scan": SCAN_GAS_COST,
        },
    }


def scan_payload(payload: str | dict[str, Any]) -> SentinelVerdict:
    """
    Scan a payload for security violations.

    Evaluation order:
      1. ALIGN Ledger patterns (authoritative).
      2. Extended PrivEsc patterns.

    Returns a :class:`SentinelVerdict`.
    """
    text = json.dumps(payload) if isinstance(payload, dict) else str(payload)

    # 1. ALIGN Ledger check
    blocked, rule_id = enforce_align(text)
    if blocked:
        _logger.log(
            "SENTINEL_BLOCKED_ALIGN",
            {"rule_id": rule_id, "preview": text[:120]},
            anomaly_score=0.9,
        )
        return SentinelVerdict(blocked=True, rule_id=rule_id, severity="HIGH", source="align")

    # 2. Extended PrivEsc patterns
    for pattern, rid in _COMPILED_PRIVESC:
        if pattern.search(text):
            _logger.log(
                "SENTINEL_BLOCKED_PRIVESC",
                {"rule_id": rid, "preview": text[:120]},
                anomaly_score=0.85,
            )
            return SentinelVerdict(blocked=True, rule_id=rid, severity="CRITICAL", source="privesc")

    return SentinelVerdict(blocked=False, source="clean")


def _publish_alert(r: Any, verdict: SentinelVerdict, original_payload: str) -> None:
    """Publish a security alert to the ``arbiter_events`` Redis Stream."""
    try:
        r.xadd(
            ARBITER_EVENTS_STREAM,
            {
                "data": json.dumps(
                    {
                        "event_type": "SECURITY_ALERT",
                        "source_agent": "sentinel",
                        "rule_id": verdict.rule_id,
                        "severity": verdict.severity,
                        "preview": original_payload[:120],
                        "ts": time.time(),
                    }
                ),
            },
        )
    except Exception as exc:
        _logger.log("SENTINEL_PUBLISH_ERROR", {"error": str(exc)}, anomaly_score=0.5)


# --------------------------------------------------------------------------- #
# Background consumer
# --------------------------------------------------------------------------- #


def _consume_loop(stop_event: threading.Event) -> None:
    """Redis XREADGROUP consumer for ``sentinel_events``."""
    try:
        import redis  # noqa: PLC0415

        r = redis.from_url(
            os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        )
        with contextlib.suppress(Exception):
            r.xgroup_create(SENTINEL_EVENTS_STREAM, _CONSUMER_GROUP, id="0", mkstream=True)

        tier = os.environ.get("DEPLOYMENT_TIER", "hearth")
        _logger.log("SENTINEL_AGENT_CONSUMER_READY", {"stream": SENTINEL_EVENTS_STREAM})

        from agents.gateway.router import consume_gas  # noqa: PLC0415

        while not stop_event.is_set():
            try:
                messages = r.xreadgroup(
                    _CONSUMER_GROUP,
                    "sentinel-1",
                    {SENTINEL_EVENTS_STREAM: ">"},
                    count=1,
                    block=2000,
                )
                if not messages:
                    continue

                for _stream, entries in messages:
                    for entry_id, data in entries:
                        raw = data.get("data", "{}")
                        try:
                            if not consume_gas(tier, SCAN_GAS_COST, r):
                                _logger.log("SENTINEL_GAS_EXHAUSTED", {}, anomaly_score=0.3)
                                r.xack(SENTINEL_EVENTS_STREAM, _CONSUMER_GROUP, entry_id)
                                continue

                            verdict = scan_payload(raw)
                            if verdict.blocked:
                                _publish_alert(r, verdict, raw)
                            _logger.log(
                                "SENTINEL_SCAN_COMPLETE",
                                {
                                    "blocked": verdict.blocked,
                                    "rule_id": verdict.rule_id,
                                    "source": verdict.source,
                                },
                            )
                        except Exception as exc:
                            _logger.log("SENTINEL_CONSUME_ERROR", {"error": str(exc)}, anomaly_score=0.6)
                        finally:
                            r.xack(SENTINEL_EVENTS_STREAM, _CONSUMER_GROUP, entry_id)
            except Exception as exc:
                _logger.log("SENTINEL_LOOP_ERROR", {"error": str(exc)}, anomaly_score=0.5)
                time.sleep(2)
    except Exception as exc:
        _logger.log("SENTINEL_FATAL", {"error": str(exc)}, anomaly_score=1.0)


_stop_event: threading.Event = threading.Event()
_thread: threading.Thread | None = None


def start() -> None:
    """Start the Sentinel Agent daemon thread (idempotent)."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(
        target=_consume_loop,
        args=(_stop_event,),
        daemon=True,
        name="sentinel-agent",
    )
    _thread.start()
    _logger.log("SENTINEL_AGENT_STARTED", {})


def stop() -> None:
    """Signal the Sentinel Agent thread to exit cleanly."""
    _stop_event.set()
    if _thread:
        _thread.join(timeout=10)
    _logger.log("SENTINEL_AGENT_STOPPED", {})
