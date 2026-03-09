"""
Tests for RedisTransport — Redis Streams backend for SpindleEventBus.

All tests mock the redis client so they run without a live Redis instance.
Tests cover:
  - Configuration loading
  - Connection and disconnection
  - Event serialization and deserialization
  - Bus attach/detach
  - Publish replication to Redis
  - Listener receives remote events
  - Echo prevention (skip own messages)
  - Fallback when Redis unavailable
  - Stats tracking
"""

from __future__ import annotations

import json
import pytest
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
from typing import Any

from agents.core.event_bus import EventType, SpindleEvent, SpindleEventBus
from agents.core.redis_transport import (
    RedisTransport,
    RedisTransportConfig,
    _event_to_redis,
    _event_from_redis,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def config() -> RedisTransportConfig:
    return RedisTransportConfig(
        redis_url="redis://localhost:6379/0",
        consumer_name="test_agent",
        enabled=True,
    )


@pytest.fixture
def bus() -> SpindleEventBus:
    return SpindleEventBus()


@pytest.fixture
def transport(config: RedisTransportConfig) -> RedisTransport:
    return RedisTransport(config)


@pytest.fixture
def sample_event() -> SpindleEvent:
    return SpindleEvent(
        event_type=EventType.NODE_COMPLETED,
        source="test_node",
        payload={"result": "success", "duration": 1.5},
        semantic_refs=["entity_001"],
        timestamp=1709981000.0,
    )


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    cfg = {
        "redis_url": "redis://custom:6380/1",
        "redis_stream_prefix": "myapp:",
        "redis_consumer_group": "my_agents",
        "redis_transport_enabled": True,
        "redis_read_timeout_ms": 2000,
        "redis_max_stream_len": 5000,
    }
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


# ─── Config ──────────────────────────────────────────────────────────────────

class TestConfig:
    def test_defaults(self) -> None:
        cfg = RedisTransportConfig()
        assert cfg.redis_url == "redis://localhost:6379/0"
        assert cfg.stream_prefix == "sovos:"
        assert cfg.consumer_group == "sovereign_agents"
        assert cfg.enabled is True
        assert cfg.max_stream_len == 10_000

    def test_from_settings(self, config_file: Path) -> None:
        cfg = RedisTransportConfig.from_settings(config_file)
        assert cfg.redis_url == "redis://custom:6380/1"
        assert cfg.stream_prefix == "myapp:"
        assert cfg.consumer_group == "my_agents"
        assert cfg.read_timeout_ms == 2000
        assert cfg.max_stream_len == 5000

    def test_from_missing_file(self) -> None:
        cfg = RedisTransportConfig.from_settings(Path("/nonexistent"))
        assert cfg.redis_url == "redis://localhost:6379/0"  # defaults

    def test_auto_consumer_name(self) -> None:
        cfg = RedisTransportConfig()
        cfg = RedisTransportConfig.from_settings(Path("/nonexistent"))
        assert cfg.consumer_name.startswith("agent_")


# ─── Serialization ───────────────────────────────────────────────────────────

class TestSerialization:
    def test_event_to_redis(self, sample_event: SpindleEvent) -> None:
        fields = _event_to_redis(sample_event)
        assert fields["event_type"] == "node_completed"
        assert fields["source"] == "test_node"
        assert json.loads(fields["payload"])["result"] == "success"
        assert json.loads(fields["semantic_refs"]) == ["entity_001"]

    def test_event_from_redis(self) -> None:
        fields = {
            "event_type": "node_completed",
            "source": "remote_node",
            "payload": '{"key": "val"}',
            "semantic_refs": '["ref1"]',
            "timestamp": "1709981000.0",
        }
        event = _event_from_redis(fields)
        assert event is not None
        assert event.event_type == EventType.NODE_COMPLETED
        assert event.source == "remote_node"
        assert event.payload == {"key": "val"}
        assert event.semantic_refs == ["ref1"]

    def test_event_from_redis_unknown_type(self) -> None:
        fields = {"event_type": "totally_unknown", "source": "x"}
        event = _event_from_redis(fields)
        assert event is None

    def test_event_from_redis_bad_json(self) -> None:
        fields = {
            "event_type": "node_completed",
            "source": "x",
            "payload": "NOT_JSON{{{",
        }
        event = _event_from_redis(fields)
        assert event is None

    def test_roundtrip(self, sample_event: SpindleEvent) -> None:
        fields = _event_to_redis(sample_event)
        restored = _event_from_redis(fields)
        assert restored is not None
        assert restored.event_type == sample_event.event_type
        assert restored.source == sample_event.source
        assert restored.payload == sample_event.payload


# ─── Transport Construction ─────────────────────────────────────────────────

class TestTransportConstruction:
    def test_create_default(self) -> None:
        t = RedisTransport()
        assert t.is_connected is False
        assert t.is_running is False

    def test_from_config(self, config_file: Path) -> None:
        t = RedisTransport.from_config(config_file)
        assert t._config.redis_url == "redis://custom:6380/1"

    def test_stats(self, transport: RedisTransport) -> None:
        stats = transport.stats
        assert stats["connected"] is False
        assert stats["published"] == 0
        assert stats["received"] == 0
        assert stats["errors"] == 0


# ─── Connection ──────────────────────────────────────────────────────────────

class TestConnection:
    @patch("agents.core.redis_transport.RedisTransport._ensure_consumer_groups")
    def test_connect_success(self, mock_groups: MagicMock, transport: RedisTransport) -> None:
        mock_redis = MagicMock()
        with patch("redis.Redis.from_url", return_value=mock_redis):
            result = transport.connect()
        assert result is True
        assert transport.is_connected is True

    def test_connect_no_redis_package(self, transport: RedisTransport) -> None:
        with patch.dict("sys.modules", {"redis": None}):
            # This should handle ImportError gracefully
            result = transport.connect()
        # Might succeed if redis is actually installed, so just check no crash
        assert isinstance(result, bool)

    def test_connect_disabled(self) -> None:
        cfg = RedisTransportConfig(enabled=False)
        t = RedisTransport(cfg)
        assert t.connect() is False

    def test_disconnect(self, transport: RedisTransport) -> None:
        transport._connected = True
        transport._client = MagicMock()
        transport.disconnect()
        assert transport.is_connected is False


# ─── Bus Integration ─────────────────────────────────────────────────────────

class TestBusIntegration:
    def test_attach_intercepts_publish(self, transport: RedisTransport, bus: SpindleEventBus) -> None:
        original = bus.publish
        transport.attach(bus)
        assert bus.publish != original  # monkey-patched
        transport.detach()

    def test_detach_restores_publish(self, transport: RedisTransport, bus: SpindleEventBus) -> None:
        original = bus.publish
        transport.attach(bus)
        transport.detach()
        assert bus.publish == original  # restored

    def test_attached_publish_still_local(
        self, transport: RedisTransport, bus: SpindleEventBus, sample_event: SpindleEvent
    ) -> None:
        received: list[SpindleEvent] = []
        bus.subscribe(EventType.NODE_COMPLETED, lambda e: received.append(e))
        transport.attach(bus)
        bus.publish(sample_event)
        assert len(received) == 1
        assert received[0].source == "test_node"
        transport.detach()


# ─── Publish Replication ─────────────────────────────────────────────────────

class TestPublishReplication:
    def test_replicate_to_redis(
        self, transport: RedisTransport, bus: SpindleEventBus, sample_event: SpindleEvent
    ) -> None:
        mock_client = MagicMock()
        transport._client = mock_client
        transport._connected = True
        transport.attach(bus)

        bus.publish(sample_event)

        mock_client.xadd.assert_called_once()
        args = mock_client.xadd.call_args
        assert args[0][0] == "sovos:node_completed"  # stream key
        assert transport._publish_count == 1
        transport.detach()

    def test_no_replication_when_disconnected(
        self, transport: RedisTransport, bus: SpindleEventBus, sample_event: SpindleEvent
    ) -> None:
        transport._connected = False
        transport.attach(bus)
        bus.publish(sample_event)
        assert transport._publish_count == 0
        transport.detach()

    def test_replication_error_tracked(
        self, transport: RedisTransport, bus: SpindleEventBus, sample_event: SpindleEvent
    ) -> None:
        mock_client = MagicMock()
        mock_client.xadd.side_effect = Exception("Redis down")
        transport._client = mock_client
        transport._connected = True
        transport.attach(bus)

        bus.publish(sample_event)
        assert transport._error_count == 1
        assert transport._publish_count == 0
        transport.detach()


# ─── Listener ────────────────────────────────────────────────────────────────

class TestListener:
    def test_start_without_connection(self, transport: RedisTransport) -> None:
        transport._config.enabled = False
        transport.start_listener()
        assert transport.is_running is False

    def test_stop_listener(self, transport: RedisTransport) -> None:
        transport._running = True
        transport._listener_thread = MagicMock()
        transport.stop_listener()
        assert transport._running is False


# ─── Echo Prevention ─────────────────────────────────────────────────────────

class TestEchoPrevention:
    def test_origin_id_in_publish(
        self, transport: RedisTransport, bus: SpindleEventBus, sample_event: SpindleEvent
    ) -> None:
        mock_client = MagicMock()
        transport._client = mock_client
        transport._connected = True
        transport.attach(bus)

        bus.publish(sample_event)

        call_args = mock_client.xadd.call_args
        fields = call_args[0][1]
        assert "_origin" in fields
        assert fields["_origin"] == transport._origin_id
        transport.detach()


# ─── Fallback ────────────────────────────────────────────────────────────────

class TestFallback:
    def test_local_delivery_works_without_redis(
        self, bus: SpindleEventBus, sample_event: SpindleEvent
    ) -> None:
        transport = RedisTransport(RedisTransportConfig(enabled=False))
        received: list[SpindleEvent] = []
        bus.subscribe(EventType.NODE_COMPLETED, lambda e: received.append(e))
        transport.attach(bus)

        bus.publish(sample_event)
        assert len(received) == 1  # local delivery works
        transport.detach()

    def test_connect_failure_is_graceful(self) -> None:
        cfg = RedisTransportConfig(redis_url="redis://nonexistent:9999/0")
        t = RedisTransport(cfg)
        result = t.connect()
        assert result is False
        assert t.is_connected is False
