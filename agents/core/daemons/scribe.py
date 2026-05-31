"""
Scribe Daemon — Continuous InsAIts V2 prose translation stream.

Transforms raw AST/decision events into human-readable InsAIts prose,
maintaining a structured log stream with token budget enforcement.

Output format: data/scribe_log.jsonl (append-only structured log).

Part of the Aegis-Nexus runtime daemon triad (Issue #17).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from agents.core.daemons import DaemonEventBus

_logger = logging.getLogger("aegis.scribe")

# Default token limits per tier
_DEFAULT_TOKEN_LIMITS = {
    "hearth": 8192,
    "forge": 16384,
    "sovereign": 32768,
}


# ─── Prose Entry ─────────────────────────────────────────────────────────────


@dataclass
class ProseEntry:
    """A human-readable InsAIts prose translation of a runtime event."""
    timestamp: str
    event_type: str
    prose: str
    token_count: int
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ─── Scribe Daemon ───────────────────────────────────────────────────────────


class ScribeDaemon:
    """
    Continuous InsAIts prose translation stream.

    Subscribes to runtime execution events and transforms raw AST node
    executions, host function calls, and security decisions into
    human-readable prose for the Transparency Panel.

    Token Budget: The Scribe enforces that prose output stays within
    a configurable fraction of the tier's max_context_tokens
    (default: 80%).

    Args:
        event_bus: The shared daemon event bus.
        token_budget_pct: Fraction of max tokens for prose (0.0-1.0).
        enabled: Whether the daemon should activate.
        tier: Deployment tier for token limit lookup.
        log_path: Path for the structured prose log.
    """

    name = "scribe"

    def __init__(
        self,
        event_bus: DaemonEventBus | None = None,
        token_budget_pct: float = 0.80,
        enabled: bool = True,
        tier: str = "hearth",
        log_path: Path | None = None,
    ):
        self._event_bus = event_bus
        self._token_budget_pct = min(max(token_budget_pct, 0.0), 1.0)
        self._enabled = enabled
        self._tier = tier
        self._log_path = log_path

        # State
        from agents.core.daemons import DaemonStatus
        self._status = DaemonStatus.STOPPED
        self._entries: list[ProseEntry] = []
        self._total_tokens: int = 0
        self._translate_count: int = 0

        # Token limit
        tier_limit = _DEFAULT_TOKEN_LIMITS.get(tier, 8192)
        self._token_budget = int(tier_limit * self._token_budget_pct)

    @property
    def status(self):
        from agents.core.daemons import DaemonStatus
        return self._status

    @status.setter
    def status(self, value):
        from agents.core.daemons import DaemonStatus
        self._status = value

    @property
    def token_budget(self) -> int:
        """Maximum tokens available for prose output."""
        return self._token_budget

    @property
    def tokens_used(self) -> int:
        """Tokens consumed so far."""
        return self._total_tokens

    @property
    def tokens_remaining(self) -> int:
        """Tokens remaining in budget."""
        return max(0, self._token_budget - self._total_tokens)

    def start(self) -> None:
        """Start the Scribe daemon."""
        from agents.core.daemons import DaemonStatus
        if not self._enabled:
            _logger.info("Scribe daemon disabled, skipping start")
            return
        self._status = DaemonStatus.RUNNING
        _logger.info(
            "Scribe daemon started (budget=%d tokens, tier=%s)",
            self._token_budget,
            self._tier,
        )

    def stop(self) -> None:
        """Stop the Scribe daemon and flush log."""
        from agents.core.daemons import DaemonStatus
        if self._log_path and self._entries:
            self._flush_log()
        self._status = DaemonStatus.STOPPED
        _logger.info(
            "Scribe daemon stopped (%d entries, %d tokens used)",
            len(self._entries),
            self._total_tokens,
        )

    def translate(self, event: dict[str, Any]) -> ProseEntry | None:
        """
        Translate a runtime event into InsAIts prose.

        If the token budget would be exceeded, the event is summarized
        instead of fully translated (best-effort degradation).

        Args:
            event: Runtime event with keys like 'type', 'tag', 'name',
                   'args', 'result', 'gas_cost', 'source'.

        Returns:
            ProseEntry if translation succeeded, None if daemon not running.
        """
        from agents.core.daemons import DaemonStatus
        if self._status != DaemonStatus.RUNNING:
            return None

        import datetime

        # Generate prose from event
        prose = self._generate_prose(event)
        token_count = self._estimate_tokens(prose)

        # Budget enforcement
        if self._total_tokens + token_count > self._token_budget:
            prose = self._summarize(event)
            token_count = self._estimate_tokens(prose)

        entry = ProseEntry(
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            event_type=event.get("type", "unknown"),
            prose=prose,
            token_count=token_count,
            source=event.get("source", ""),
            metadata={k: v for k, v in event.items() if k not in ("type", "source")},
        )

        self._entries.append(entry)
        self._total_tokens += token_count
        self._translate_count += 1

        # Emit to event bus
        if self._event_bus:
            from agents.core.daemons import DaemonEvent
            self._event_bus.emit(DaemonEvent(
                source="scribe",
                event_type="prose",
                severity="info",
                data={"prose": prose, "tokens": token_count},
            ))

        return entry

    def get_entries(self, count: int = 10) -> list[ProseEntry]:
        """Get recent prose entries."""
        return self._entries[-count:]

    def get_stats(self) -> dict[str, Any]:
        """Get Scribe statistics."""
        return {
            "status": self._status.value,
            "translate_count": self._translate_count,
            "total_entries": len(self._entries),
            "tokens_used": self._total_tokens,
            "token_budget": self._token_budget,
            "tokens_remaining": self.tokens_remaining,
            "budget_utilization": round(
                self._total_tokens / max(self._token_budget, 1) * 100, 1
            ),
        }

    def reset_budget(self) -> None:
        """Reset token budget (e.g., for a new session)."""
        self._total_tokens = 0
        self._entries.clear()

    # ─── Internal Methods ────────────────────────────────────────────────

    def _generate_prose(self, event: dict[str, Any]) -> str:
        """
        Generate human-readable InsAIts prose from a runtime event.

        This is the core translation engine. It maps event types to
        natural language descriptions.
        """
        event_type = event.get("type", "unknown")
        tag = event.get("tag", "")
        name = event.get("name", "")

        templates = {
            "intent_execution": f"Executing intent '{name}' — processing HLF logic block with structured reasoning.",
            "host_function_call": f"Dispatching host function '{name}' ({event.get('args', {})}) via backend '{event.get('backend', 'builtin')}'.",
            "module_import": f"Loading module '{name}' — merging namespace symbols into execution environment.",
            "gas_consumed": f"Gas consumed: {event.get('gas_cost', 0)} units for '{name}' (remaining: {event.get('gas_remaining', '?')}).",
            "tier_check": f"Tier authorization check: '{name}' requires {event.get('required_tier', '?')} (current: {event.get('tier', '?')}).",
            "align_check": f"ALIGN rule check: evaluating compliance for '{name}' against ledger rules.",
            "set_binding": f"Variable binding: SET '{name}' = {event.get('value', '?')} in execution environment.",
            "security_gate": f"Security gate '{name}' evaluated: {event.get('result', 'unknown')}.",
        }

        prose = templates.get(
            event_type,
            f"Runtime event '{event_type}': {name or tag or 'operation'} processed.",
        )
        return prose

    def _summarize(self, event: dict[str, Any]) -> str:
        """Generate a brief summary when budget is running low."""
        return f"[budget-constrained] {event.get('type', 'event')}: {event.get('name', 'op')}"

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Estimate token count (rough: ~4 chars per token)."""
        return max(1, len(text) // 4)

    def _flush_log(self) -> None:
        """Write accumulated entries to the log file."""
        if not self._log_path:
            return
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as f:
                for entry in self._entries:
                    f.write(json.dumps({
                        "timestamp": entry.timestamp,
                        "event_type": entry.event_type,
                        "prose": entry.prose,
                        "token_count": entry.token_count,
                        "source": entry.source,
                        "metadata": entry.metadata,
                    }) + "\n")
        except OSError as e:
            _logger.error("Failed to flush Scribe log: %s", e)
