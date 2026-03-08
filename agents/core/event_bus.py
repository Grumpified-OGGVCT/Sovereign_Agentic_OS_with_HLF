"""
Spindle Event Bus — Pub/Sub for Inter-Agent Communication.

Provides a typed, in-process event bus for agent coordination during
DAG execution. Agents subscribe to event types and receive callbacks
when events are published.

Event types follow the Instinct architecture:
  - NODE_COMPLETED / NODE_FAILED — DAG lifecycle events
  - SPEC_CHANGED — Living Spec mutation notification
  - CONTEXT_INVALIDATED — Blast radius propagation
  - VALIDATION_REJECTED — CoVE inter-agent critique
  - INTERRUPT — Context propagation wave signal

Usage::

    bus = SpindleEventBus()

    def on_completion(event):
        print(f"Node {event.source} completed")

    bus.subscribe(EventType.NODE_COMPLETED, on_completion)
    bus.publish(SpindleEvent(
        event_type=EventType.NODE_COMPLETED,
        source="auth_node",
        payload={"result": "success"},
    ))
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Event types
# --------------------------------------------------------------------------- #


class EventType(StrEnum):
    """Typed event categories for the Spindle Event Bus."""

    # DAG lifecycle
    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    NODE_FAILED = "node_failed"
    NODE_COMPENSATED = "node_compensated"
    DAG_COMPLETED = "dag_completed"
    DAG_FAILED = "dag_failed"

    # Spec & context
    SPEC_CHANGED = "spec_changed"
    CONTEXT_INVALIDATED = "context_invalidated"
    REALIGNMENT_TRIGGERED = "realignment_triggered"

    # Agent coordination
    VALIDATION_REJECTED = "validation_rejected"
    VALIDATION_APPROVED = "validation_approved"
    INTERRUPT = "interrupt"

    # Session
    SESSION_SAVED = "session_saved"
    SESSION_RESUMED = "session_resumed"


# --------------------------------------------------------------------------- #
# Event data
# --------------------------------------------------------------------------- #


@dataclass
class SpindleEvent:
    """A single event on the bus.

    Attributes:
        event_type: Category of event.
        source: Identifier of the emitter (node_id, agent_id, etc.)
        payload: Arbitrary data associated with the event.
        semantic_refs: List of entity/symbol GUIDs affected (for intersection analysis).
        timestamp: When the event was created.
    """

    event_type: EventType
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    semantic_refs: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


# --------------------------------------------------------------------------- #
# Subscriber
# --------------------------------------------------------------------------- #


@dataclass
class _Subscription:
    """Internal subscription record."""

    callback: Callable[[SpindleEvent], None]
    subscriber_id: str
    filter_source: str | None = None


# --------------------------------------------------------------------------- #
# Event Bus
# --------------------------------------------------------------------------- #


class SpindleEventBus:
    """In-process pub/sub event bus for agent coordination.

    Synchronous dispatch — callbacks run in the publisher's thread.
    For async execution, wrap callbacks in your own executor.

    Features:
        - Typed event subscriptions (subscribe to specific EventType)
        - Source filtering (only receive events from specific emitters)
        - Wildcard subscriptions (receive ALL events)
        - Event history for replay/debugging
        - Unsubscribe by subscriber_id
    """

    def __init__(self, *, history_limit: int = 500) -> None:
        self._subscribers: dict[EventType | None, list[_Subscription]] = defaultdict(list)
        self._history: list[SpindleEvent] = []
        self._history_limit = history_limit
        self._paused = False

    def subscribe(
        self,
        event_type: EventType | None,
        callback: Callable[[SpindleEvent], None],
        subscriber_id: str = "",
        filter_source: str | None = None,
    ) -> str:
        """Subscribe to events of a specific type.

        Args:
            event_type: The event type to listen for, or None for all events.
            callback: Function called with the SpindleEvent when published.
            subscriber_id: Optional identifier for this subscription.
            filter_source: If set, only receive events from this source.

        Returns:
            The subscriber_id (generated if not provided).
        """
        if not subscriber_id:
            subscriber_id = f"sub_{id(callback)}_{time.time():.0f}"

        sub = _Subscription(
            callback=callback,
            subscriber_id=subscriber_id,
            filter_source=filter_source,
        )
        self._subscribers[event_type].append(sub)
        return subscriber_id

    def unsubscribe(self, subscriber_id: str) -> int:
        """Remove all subscriptions for a given subscriber_id.

        Returns:
            Number of subscriptions removed.
        """
        removed = 0
        for event_type in list(self._subscribers):
            before = len(self._subscribers[event_type])
            self._subscribers[event_type] = [
                s for s in self._subscribers[event_type]
                if s.subscriber_id != subscriber_id
            ]
            removed += before - len(self._subscribers[event_type])
            if not self._subscribers[event_type]:
                del self._subscribers[event_type]
        return removed

    def publish(self, event: SpindleEvent) -> int:
        """Publish an event to all matching subscribers.

        Args:
            event: The event to publish.

        Returns:
            Number of subscribers notified.
        """
        # Record history
        self._history.append(event)
        if len(self._history) > self._history_limit:
            self._history = self._history[-self._history_limit:]

        if self._paused:
            return 0

        notified = 0

        # Notify type-specific subscribers
        for sub in self._subscribers.get(event.event_type, []):
            if sub.filter_source and sub.filter_source != event.source:
                continue
            try:
                sub.callback(event)
                notified += 1
            except Exception:
                logger.exception(
                    "Event bus callback error: subscriber=%s event=%s",
                    sub.subscriber_id, event.event_type,
                )

        # Notify wildcard subscribers (event_type=None)
        for sub in self._subscribers.get(None, []):
            if sub.filter_source and sub.filter_source != event.source:
                continue
            try:
                sub.callback(event)
                notified += 1
            except Exception:
                logger.exception(
                    "Event bus wildcard callback error: subscriber=%s",
                    sub.subscriber_id,
                )

        return notified

    def pause(self) -> None:
        """Pause event delivery (events still recorded in history)."""
        self._paused = True

    def resume(self) -> None:
        """Resume event delivery."""
        self._paused = False

    @property
    def is_paused(self) -> bool:
        """Whether the bus is currently paused."""
        return self._paused

    def get_history(
        self,
        event_type: EventType | None = None,
        source: str | None = None,
        limit: int = 50,
    ) -> list[SpindleEvent]:
        """Get recent events, optionally filtered.

        Args:
            event_type: Filter to specific type. None = all.
            source: Filter to specific source. None = all.
            limit: Maximum events to return.

        Returns:
            List of matching events, most recent first.
        """
        filtered = self._history
        if event_type is not None:
            filtered = [e for e in filtered if e.event_type == event_type]
        if source is not None:
            filtered = [e for e in filtered if e.source == source]
        return list(reversed(filtered[-limit:]))

    def check_intersection(
        self,
        working_set: set[str],
        event_type: EventType | None = None,
    ) -> list[SpindleEvent]:
        """Find events whose semantic_refs overlap with a working set.

        This implements Intent's "Intersection Analysis" — detecting
        when a user edit or spec change affects entities that a running
        agent is currently working on.

        Args:
            working_set: Set of entity/symbol GUIDs the agent is using.
            event_type: Optionally filter to specific event type.

        Returns:
            List of events with overlapping semantic_refs.
        """
        conflicts = []
        for event in self._history:
            if event_type and event.event_type != event_type:
                continue
            if set(event.semantic_refs) & working_set:
                conflicts.append(event)
        return conflicts

    def subscriber_count(self, event_type: EventType | None = None) -> int:
        """Count subscribers for a given event type (None = all)."""
        if event_type is not None:
            return len(self._subscribers.get(event_type, []))
        return sum(len(subs) for subs in self._subscribers.values())

    def clear_history(self) -> None:
        """Clear the event history."""
        self._history.clear()
