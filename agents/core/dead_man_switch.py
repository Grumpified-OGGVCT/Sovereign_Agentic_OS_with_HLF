"""
Dead Man's Switch — Cascading failure detector.

From Phase 4.3 of the Master Build Plan: if >3 container panic resets
in 5 minutes, sever the network. Tracks failure patterns and triggers
protective shutdowns.

Usage:
    switch = DeadManSwitch(max_panics=3, window_minutes=5)
    switch.record_panic("container-sentinel")
    switch.record_panic("container-scribe")
    switch.record_panic("container-arbiter")
    switch.record_panic("container-sentinel")  # 4th in window → TRIGGERED
    if switch.is_triggered:
        print("Cascading failure detected!")
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class PanicEvent:
    event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    container_id: str = ""
    reason: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class SwitchTrigger:
    trigger_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    panic_count: int = 0
    window_seconds: float = 0.0
    containers_affected: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    action_taken: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger_id": self.trigger_id,
            "panic_count": self.panic_count,
            "containers_affected": self.containers_affected,
            "action_taken": self.action_taken,
            "timestamp": self.timestamp,
        }


class DeadManSwitch:
    """Tracks container failures and triggers protective actions.

    Arms when instantiated. Records panic events. If the count
    exceeds max_panics within window_minutes, the switch triggers
    and calls the on_trigger callback.
    """

    def __init__(
        self,
        *,
        max_panics: int = 3,
        window_minutes: float = 5.0,
        on_trigger: Callable[[SwitchTrigger], None] | None = None,
    ) -> None:
        self._max_panics = max_panics
        self._window_seconds = window_minutes * 60
        self._on_trigger = on_trigger
        self._panics: deque[PanicEvent] = deque()
        self._triggers: list[SwitchTrigger] = []
        self._armed = True
        self._triggered = False

    @property
    def is_armed(self) -> bool:
        return self._armed

    @property
    def is_triggered(self) -> bool:
        return self._triggered

    @property
    def panic_count(self) -> int:
        self._prune_old()
        return len(self._panics)

    @property
    def trigger_count(self) -> int:
        return len(self._triggers)

    def arm(self) -> None:
        """Re-arm the switch after a trigger."""
        self._armed = True
        self._triggered = False
        self._panics.clear()

    def disarm(self) -> None:
        """Disarm the switch (for maintenance windows)."""
        self._armed = False

    def record_panic(
        self,
        container_id: str,
        reason: str = "panic_reset",
    ) -> SwitchTrigger | None:
        """Record a container panic event.

        Returns a SwitchTrigger if this panic crosses the threshold.
        """
        event = PanicEvent(container_id=container_id, reason=reason)
        self._panics.append(event)
        self._prune_old()

        if (
            self._armed
            and not self._triggered
            and len(self._panics) > self._max_panics
        ):
            return self._fire()
        return None

    def _prune_old(self) -> None:
        """Remove events outside the time window."""
        cutoff = time.time() - self._window_seconds
        while self._panics and self._panics[0].timestamp < cutoff:
            self._panics.popleft()

    def _fire(self) -> SwitchTrigger:
        """Trigger the dead man's switch."""
        self._triggered = True
        containers = list(set(p.container_id for p in self._panics))

        trigger = SwitchTrigger(
            panic_count=len(self._panics),
            window_seconds=self._window_seconds,
            containers_affected=containers,
            action_taken="network_severed",
        )
        self._triggers.append(trigger)

        if self._on_trigger:
            self._on_trigger(trigger)

        return trigger

    def get_recent_panics(self, limit: int = 20) -> list[dict[str, Any]]:
        self._prune_old()
        return [
            {
                "event_id": p.event_id,
                "container_id": p.container_id,
                "reason": p.reason,
                "timestamp": p.timestamp,
            }
            for p in list(self._panics)[-limit:]
        ]

    def get_triggers(self) -> list[dict[str, Any]]:
        return [t.to_dict() for t in self._triggers]

    def get_status(self) -> dict[str, Any]:
        self._prune_old()
        return {
            "armed": self._armed,
            "triggered": self._triggered,
            "panics_in_window": len(self._panics),
            "max_panics": self._max_panics,
            "window_minutes": self._window_seconds / 60,
            "total_triggers": len(self._triggers),
        }
