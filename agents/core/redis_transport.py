"""
Redis Streams Transport — Distributed backend for SpindleEventBus.

Provides inter-process, inter-host event delivery via Redis Streams (XADD/XREADGROUP).
Falls back gracefully to in-memory-only mode when Redis is unavailable.

Architecture:
    SpindleEventBus → RedisTransport → Redis Streams → Remote SpindleEventBus instances
                    ↘ (fallback: local-only)

Configuration (settings.json):
    {
        "redis_url": "redis://localhost:6379/0",
        "redis_stream_prefix": "sovos:",
        "redis_consumer_group": "sovereign_agents",
        "redis_transport_enabled": true,
        "redis_read_timeout_ms": 1000,
        "redis_max_stream_len": 10000
    }

Usage:
    transport = RedisTransport.from_config()
    bus = SpindleEventBus()
    transport.attach(bus)      # Events now replicate to Redis
    transport.start_listener() # Background thread reads from Redis
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents.core.event_bus import EventType, SpindleEvent, SpindleEventBus

logger = logging.getLogger(__name__)


# ─── Config ──────────────────────────────────────────────────────────────────

@dataclass
class RedisTransportConfig:
    """Configuration for Redis Streams transport."""

    redis_url: str = "redis://localhost:6379/0"
    stream_prefix: str = "sovos:"
    consumer_group: str = "sovereign_agents"
    consumer_name: str = ""  # auto-generated if empty
    enabled: bool = True
    read_timeout_ms: int = 1000
    max_stream_len: int = 10_000
    reconnect_delay_sec: float = 5.0
    max_reconnect_attempts: int = 10

    @classmethod
    def from_settings(cls, config_path: Path | str | None = None) -> RedisTransportConfig:
        """Load config from settings.json."""
        cfg = cls()

        if config_path is None:
            candidates = [
                Path("config/settings.json"),
                Path(__file__).parent.parent.parent / "config" / "settings.json",
            ]
            for c in candidates:
                if c.exists():
                    config_path = c
                    break

        if config_path and Path(config_path).exists():
            try:
                data = json.loads(Path(config_path).read_text(encoding="utf-8"))
                cfg.redis_url = data.get("redis_url", cfg.redis_url)
                cfg.stream_prefix = data.get("redis_stream_prefix", cfg.stream_prefix)
                cfg.consumer_group = data.get("redis_consumer_group", cfg.consumer_group)
                cfg.enabled = data.get("redis_transport_enabled", cfg.enabled)
                cfg.read_timeout_ms = data.get("redis_read_timeout_ms", cfg.read_timeout_ms)
                cfg.max_stream_len = data.get("redis_max_stream_len", cfg.max_stream_len)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Failed to load Redis transport config: %s", e)

        if not cfg.consumer_name:
            cfg.consumer_name = f"agent_{int(time.time() * 1000) % 100000}"

        return cfg


# ─── Serialization ───────────────────────────────────────────────────────────

def _event_to_redis(event: SpindleEvent) -> dict[str, str]:
    """Serialize a SpindleEvent to Redis Stream fields."""
    return {
        "event_type": str(event.event_type),
        "source": event.source,
        "payload": json.dumps(event.payload, default=str),
        "semantic_refs": json.dumps(event.semantic_refs),
        "timestamp": str(event.timestamp),
    }


def _event_from_redis(fields: dict[str, str]) -> SpindleEvent | None:
    """Deserialize Redis Stream fields to a SpindleEvent."""
    try:
        event_type_str = fields.get("event_type", "")
        # Try to resolve to EventType enum
        try:
            event_type = EventType(event_type_str)
        except ValueError:
            logger.warning("Unknown event type from Redis: %s", event_type_str)
            return None

        return SpindleEvent(
            event_type=event_type,
            source=fields.get("source", "unknown"),
            payload=json.loads(fields.get("payload", "{}")),
            semantic_refs=json.loads(fields.get("semantic_refs", "[]")),
            timestamp=float(fields.get("timestamp", time.time())),
        )
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        logger.warning("Failed to deserialize Redis event: %s", e)
        return None


# ─── Transport ───────────────────────────────────────────────────────────────

class RedisTransport:
    """Redis Streams transport for SpindleEventBus.

    Intercepts publish() on the attached bus to replicate events to Redis,
    and runs a background listener to receive events from remote buses.

    Falls back to local-only mode if Redis is unavailable.
    """

    def __init__(self, config: RedisTransportConfig | None = None) -> None:
        self._config = config or RedisTransportConfig()
        self._bus: SpindleEventBus | None = None
        self._client: Any = None  # redis.Redis instance
        self._listener_thread: threading.Thread | None = None
        self._running = False
        self._connected = False
        self._reconnect_count = 0
        self._publish_count = 0
        self._receive_count = 0
        self._error_count = 0
        self._original_publish: Any = None  # saved original bus.publish
        self._origin_id = self._config.consumer_name  # unique per instance

    @classmethod
    def from_config(cls, config_path: Path | str | None = None) -> RedisTransport:
        """Create transport from settings.json configuration."""
        config = RedisTransportConfig.from_settings(config_path)
        return cls(config)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "connected": self._connected,
            "running": self._running,
            "published": self._publish_count,
            "received": self._receive_count,
            "errors": self._error_count,
            "reconnects": self._reconnect_count,
            "origin_id": self._origin_id,
            "redis_url": self._config.redis_url,
            "enabled": self._config.enabled,
        }

    # ── Connection ───────────────────────────────────────────────────────

    def connect(self) -> bool:
        """Attempt to connect to Redis. Returns True if successful."""
        if not self._config.enabled:
            logger.info("Redis transport disabled by config")
            return False

        try:
            import redis
            self._client = redis.Redis.from_url(
                self._config.redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
            )
            # Test connection
            self._client.ping()
            self._connected = True
            self._reconnect_count = 0
            logger.info("Redis transport connected: %s", self._config.redis_url)

            # Ensure consumer group exists for all event types
            self._ensure_consumer_groups()
            return True

        except ImportError:
            logger.warning("redis package not installed — transport disabled")
            self._connected = False
            return False
        except Exception as e:
            logger.warning("Redis connection failed: %s — falling back to local-only", e)
            self._connected = False
            self._error_count += 1
            return False

    def disconnect(self) -> None:
        """Disconnect from Redis."""
        self.stop_listener()
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
        self._client = None
        self._connected = False

    def _ensure_consumer_groups(self) -> None:
        """Create consumer groups for all event streams if they don't exist."""
        if not self._client:
            return

        for event_type in EventType:
            stream_key = f"{self._config.stream_prefix}{event_type.value}"
            try:
                self._client.xgroup_create(
                    stream_key,
                    self._config.consumer_group,
                    id="0",
                    mkstream=True,
                )
            except Exception:
                # Group already exists — that's fine
                pass

    # ── Bus Integration ──────────────────────────────────────────────────

    def attach(self, bus: SpindleEventBus) -> None:
        """Attach to a SpindleEventBus, intercepting publish() for replication."""
        self._bus = bus
        # Save original publish
        self._original_publish = bus.publish

        # Monkey-patch publish to also replicate to Redis
        def _publish_with_redis(event: SpindleEvent) -> int:
            # Local delivery first
            notified = self._original_publish(event)
            # Replicate to Redis (non-blocking)
            self._replicate_to_redis(event)
            return notified

        bus.publish = _publish_with_redis  # type: ignore[assignment]

    def detach(self) -> None:
        """Restore original bus publish and detach."""
        if self._bus and self._original_publish:
            self._bus.publish = self._original_publish  # type: ignore[assignment]
        self._bus = None
        self._original_publish = None

    def _replicate_to_redis(self, event: SpindleEvent) -> None:
        """Send event to Redis Stream (fire-and-forget)."""
        if not self._connected or not self._client:
            return

        stream_key = f"{self._config.stream_prefix}{event.event_type.value}"
        fields = _event_to_redis(event)
        fields["_origin"] = self._origin_id  # mark origin to prevent echo

        try:
            self._client.xadd(
                stream_key,
                fields,
                maxlen=self._config.max_stream_len,
            )
            self._publish_count += 1
        except Exception as e:
            logger.warning("Redis XADD failed: %s", e)
            self._error_count += 1

    # ── Listener ─────────────────────────────────────────────────────────

    def start_listener(self) -> None:
        """Start a background thread to read events from Redis Streams."""
        if self._running:
            return
        if not self._connected:
            if not self.connect():
                return

        self._running = True
        self._listener_thread = threading.Thread(
            target=self._listen_loop,
            name="redis-transport-listener",
            daemon=True,
        )
        self._listener_thread.start()

    def stop_listener(self) -> None:
        """Stop the background listener thread."""
        self._running = False
        if self._listener_thread:
            self._listener_thread.join(timeout=3.0)
            self._listener_thread = None

    def _listen_loop(self) -> None:
        """Background loop reading from Redis Streams and delivering to local bus."""
        streams = {
            f"{self._config.stream_prefix}{et.value}": ">"
            for et in EventType
        }

        while self._running:
            try:
                if not self._client:
                    break

                results = self._client.xreadgroup(
                    groupname=self._config.consumer_group,
                    consumername=self._config.consumer_name,
                    streams=streams,
                    count=10,
                    block=self._config.read_timeout_ms,
                )

                if not results:
                    continue

                for stream_name, messages in results:
                    for msg_id, fields in messages:
                        # Skip messages from this origin (echo prevention)
                        if fields.get("_origin") == self._origin_id:
                            # Acknowledge and skip
                            self._client.xack(
                                stream_name,
                                self._config.consumer_group,
                                msg_id,
                            )
                            continue

                        event = _event_from_redis(fields)
                        if event and self._bus and self._original_publish:
                            # Use original publish to avoid re-replicating
                            self._original_publish(event)
                            self._receive_count += 1

                        # Acknowledge
                        self._client.xack(
                            stream_name,
                            self._config.consumer_group,
                            msg_id,
                        )

            except Exception as e:
                logger.warning("Redis listener error: %s", e)
                self._error_count += 1
                if self._running:
                    time.sleep(self._config.reconnect_delay_sec)
                    self._reconnect_count += 1
                    if self._reconnect_count >= self._config.max_reconnect_attempts:
                        logger.error("Redis max reconnect attempts reached — listener stopping")
                        self._running = False
                        self._connected = False
                        break
