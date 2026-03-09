"""
Tests for SpindleEventBus — Inter-Agent Communication.

Covers:
  - Typed pub/sub dispatch
  - Wildcard subscriptions (None = all events)
  - Source filtering
  - Unsubscribe by subscriber_id
  - Event history and replay
  - Intersection analysis (working set overlap)
  - Pause/resume delivery
  - Callback error isolation
"""

from __future__ import annotations

from agents.core.event_bus import (
    EventType,
    SpindleEvent,
    SpindleEventBus,
)

# --------------------------------------------------------------------------- #
# Basic Pub/Sub
# --------------------------------------------------------------------------- #


class TestPubSub:
    """Core publish/subscribe functionality."""

    def test_subscribe_and_publish(self) -> None:
        """Subscriber receives matching events."""
        bus = SpindleEventBus()
        received: list[SpindleEvent] = []

        bus.subscribe(EventType.NODE_COMPLETED, received.append)
        bus.publish(
            SpindleEvent(
                event_type=EventType.NODE_COMPLETED,
                source="node_a",
                payload={"result": 42},
            )
        )

        assert len(received) == 1
        assert received[0].source == "node_a"
        assert received[0].payload["result"] == 42

    def test_no_cross_delivery(self) -> None:
        """Events only go to subscribers of the matching type."""
        bus = SpindleEventBus()
        received: list[SpindleEvent] = []

        bus.subscribe(EventType.NODE_COMPLETED, received.append)
        bus.publish(
            SpindleEvent(
                event_type=EventType.NODE_FAILED,
                source="node_b",
            )
        )

        assert len(received) == 0

    def test_multiple_subscribers(self) -> None:
        """Multiple subscribers for the same type all get notified."""
        bus = SpindleEventBus()
        r1: list[SpindleEvent] = []
        r2: list[SpindleEvent] = []

        bus.subscribe(EventType.SPEC_CHANGED, r1.append)
        bus.subscribe(EventType.SPEC_CHANGED, r2.append)
        notified = bus.publish(
            SpindleEvent(
                event_type=EventType.SPEC_CHANGED,
                source="coordinator",
            )
        )

        assert notified == 2
        assert len(r1) == 1
        assert len(r2) == 1

    def test_wildcard_subscription(self) -> None:
        """None event_type receives ALL events."""
        bus = SpindleEventBus()
        received: list[SpindleEvent] = []

        bus.subscribe(None, received.append)
        bus.publish(SpindleEvent(event_type=EventType.NODE_COMPLETED, source="a"))
        bus.publish(SpindleEvent(event_type=EventType.NODE_FAILED, source="b"))
        bus.publish(SpindleEvent(event_type=EventType.SPEC_CHANGED, source="c"))

        assert len(received) == 3


# --------------------------------------------------------------------------- #
# Filtering & Unsubscribe
# --------------------------------------------------------------------------- #


class TestFiltering:
    """Source filtering and unsubscription."""

    def test_source_filter(self) -> None:
        """filter_source restricts delivery to matching source only."""
        bus = SpindleEventBus()
        received: list[SpindleEvent] = []

        bus.subscribe(
            EventType.NODE_COMPLETED,
            received.append,
            filter_source="agent_x",
        )

        bus.publish(SpindleEvent(event_type=EventType.NODE_COMPLETED, source="agent_x"))
        bus.publish(SpindleEvent(event_type=EventType.NODE_COMPLETED, source="agent_y"))

        assert len(received) == 1
        assert received[0].source == "agent_x"

    def test_unsubscribe(self) -> None:
        """Unsubscribed callbacks no longer receive events."""
        bus = SpindleEventBus()
        received: list[SpindleEvent] = []

        sid = bus.subscribe(EventType.NODE_COMPLETED, received.append, subscriber_id="sub_1")
        bus.publish(SpindleEvent(event_type=EventType.NODE_COMPLETED, source="a"))
        assert len(received) == 1

        removed = bus.unsubscribe(sid)
        assert removed == 1

        bus.publish(SpindleEvent(event_type=EventType.NODE_COMPLETED, source="b"))
        assert len(received) == 1  # still 1, not 2

    def test_unsubscribe_nonexistent(self) -> None:
        """Unsubscribing a nonexistent ID returns 0."""
        bus = SpindleEventBus()
        assert bus.unsubscribe("ghost") == 0


# --------------------------------------------------------------------------- #
# History & Intersection
# --------------------------------------------------------------------------- #


class TestHistoryAndIntersection:
    """Event history and semantic intersection analysis."""

    def test_history_recorded(self) -> None:
        """Published events are recorded in history."""
        bus = SpindleEventBus()
        bus.publish(SpindleEvent(event_type=EventType.NODE_COMPLETED, source="a"))
        bus.publish(SpindleEvent(event_type=EventType.NODE_FAILED, source="b"))

        history = bus.get_history()
        assert len(history) == 2
        assert history[0].source == "b"  # most recent first

    def test_history_filtering(self) -> None:
        """History can be filtered by event_type and source."""
        bus = SpindleEventBus()
        bus.publish(SpindleEvent(event_type=EventType.NODE_COMPLETED, source="a"))
        bus.publish(SpindleEvent(event_type=EventType.NODE_FAILED, source="b"))
        bus.publish(SpindleEvent(event_type=EventType.NODE_COMPLETED, source="c"))

        completed = bus.get_history(event_type=EventType.NODE_COMPLETED)
        assert len(completed) == 2

        from_a = bus.get_history(source="a")
        assert len(from_a) == 1

    def test_history_limit(self) -> None:
        """History respects the configured limit."""
        bus = SpindleEventBus(history_limit=5)
        for i in range(10):
            bus.publish(SpindleEvent(event_type=EventType.NODE_COMPLETED, source=f"n{i}"))

        history = bus.get_history(limit=100)
        assert len(history) == 5

    def test_intersection_analysis(self) -> None:
        """check_intersection finds events overlapping a working set."""
        bus = SpindleEventBus()
        bus.publish(
            SpindleEvent(
                event_type=EventType.SPEC_CHANGED,
                source="user",
                semantic_refs=["auth_module", "user_model"],
            )
        )
        bus.publish(
            SpindleEvent(
                event_type=EventType.SPEC_CHANGED,
                source="user",
                semantic_refs=["payment_module"],
            )
        )

        # Agent working on auth_module
        conflicts = bus.check_intersection({"auth_module", "session_store"})
        assert len(conflicts) == 1
        assert "auth_module" in conflicts[0].semantic_refs

    def test_intersection_no_overlap(self) -> None:
        """No overlap returns empty list."""
        bus = SpindleEventBus()
        bus.publish(
            SpindleEvent(
                event_type=EventType.SPEC_CHANGED,
                source="user",
                semantic_refs=["payment_module"],
            )
        )

        conflicts = bus.check_intersection({"auth_module"})
        assert conflicts == []


# --------------------------------------------------------------------------- #
# Pause/Resume & Error Isolation
# --------------------------------------------------------------------------- #


class TestPauseAndErrors:
    """Event delivery control and error isolation."""

    def test_pause_stops_delivery(self) -> None:
        """Paused bus records history but doesn't deliver."""
        bus = SpindleEventBus()
        received: list[SpindleEvent] = []

        bus.subscribe(EventType.NODE_COMPLETED, received.append)
        bus.pause()
        assert bus.is_paused

        notified = bus.publish(
            SpindleEvent(
                event_type=EventType.NODE_COMPLETED,
                source="a",
            )
        )
        assert notified == 0
        assert len(received) == 0
        assert len(bus.get_history()) == 1  # still recorded

    def test_resume_restores_delivery(self) -> None:
        """Resumed bus delivers events normally."""
        bus = SpindleEventBus()
        received: list[SpindleEvent] = []

        bus.subscribe(EventType.NODE_COMPLETED, received.append)
        bus.pause()
        bus.resume()
        assert not bus.is_paused

        bus.publish(SpindleEvent(event_type=EventType.NODE_COMPLETED, source="a"))
        assert len(received) == 1

    def test_callback_error_isolation(self) -> None:
        """A failing callback doesn't prevent other subscribers from receiving."""
        bus = SpindleEventBus()
        received: list[SpindleEvent] = []

        def bad_callback(_event: SpindleEvent) -> None:
            msg = "intentional test error"
            raise RuntimeError(msg)

        bus.subscribe(EventType.NODE_COMPLETED, bad_callback)
        bus.subscribe(EventType.NODE_COMPLETED, received.append)

        notified = bus.publish(
            SpindleEvent(
                event_type=EventType.NODE_COMPLETED,
                source="a",
            )
        )
        # bad_callback errors but second subscriber still gets notified
        assert len(received) == 1
        assert notified == 1  # only successful deliveries counted

    def test_subscriber_count(self) -> None:
        """subscriber_count reports accurately."""
        bus = SpindleEventBus()
        bus.subscribe(EventType.NODE_COMPLETED, lambda _: None)
        bus.subscribe(EventType.NODE_COMPLETED, lambda _: None)
        bus.subscribe(EventType.NODE_FAILED, lambda _: None)

        assert bus.subscriber_count(EventType.NODE_COMPLETED) == 2
        assert bus.subscriber_count(EventType.NODE_FAILED) == 1
        assert bus.subscriber_count() == 3
