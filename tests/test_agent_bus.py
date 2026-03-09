"""
Tests for Agent Service Bus — inter-agent messaging.

Tests cover:
  - Message creation and serialization
  - Agent registration / unregistration
  - Direct messaging
  - Broadcast messaging
  - Request / reply patterns
  - Gas metering integration
  - Dead letter queue
  - Message history
  - Edge cases (unregistered target, handler errors)
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from agents.core.agent_bus import (
    MessageType,
    DeliveryStatus,
    AgentMessage,
    AgentBus,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def bus() -> AgentBus:
    return AgentBus(gas_cost_per_message=2)


@pytest.fixture
def handler() -> MagicMock:
    return MagicMock()


# ─── Message Model ───────────────────────────────────────────────────────────

class TestAgentMessage:
    def test_create(self) -> None:
        msg = AgentMessage(from_agent="sentinel", to_agent="scribe")
        assert msg.from_agent == "sentinel"
        assert msg.msg_type == MessageType.DIRECT
        assert msg.status == DeliveryStatus.PENDING

    def test_to_dict(self) -> None:
        msg = AgentMessage(from_agent="a", to_agent="b", payload={"key": "val"})
        d = msg.to_dict()
        assert d["from_agent"] == "a"
        assert d["payload"]["key"] == "val"

    def test_from_dict(self) -> None:
        data = {
            "id": "x",
            "from_agent": "a",
            "to_agent": "b",
            "msg_type": "request",
            "payload": {"q": 1},
            "timestamp": 100.0,
            "status": "pending",
        }
        msg = AgentMessage.from_dict(data)
        assert msg.msg_type == MessageType.REQUEST
        assert msg.payload == {"q": 1}

    def test_json_roundtrip(self) -> None:
        msg = AgentMessage(from_agent="x", to_agent="y", payload={"z": 42})
        restored = AgentMessage.from_json(msg.to_json())
        assert restored.from_agent == msg.from_agent
        assert restored.payload == msg.payload

    def test_is_expired(self) -> None:
        msg = AgentMessage(timestamp=0, ttl_seconds=1)
        assert msg.is_expired is True

    def test_not_expired(self) -> None:
        msg = AgentMessage(ttl_seconds=9999)
        assert msg.is_expired is False


# ─── Registration ────────────────────────────────────────────────────────────

class TestRegistration:
    def test_register_agent(self, bus: AgentBus, handler: MagicMock) -> None:
        bus.register_agent("sentinel", handler)
        assert "sentinel" in bus.agents

    def test_unregister_agent(self, bus: AgentBus, handler: MagicMock) -> None:
        bus.register_agent("sentinel", handler)
        bus.unregister_agent("sentinel")
        assert "sentinel" not in bus.agents

    def test_multiple_agents(self, bus: AgentBus) -> None:
        bus.register_agent("a", MagicMock())
        bus.register_agent("b", MagicMock())
        assert len(bus.agents) == 2


# ─── Direct Messaging ───────────────────────────────────────────────────────

class TestDirect:
    def test_send(self, bus: AgentBus, handler: MagicMock) -> None:
        bus.register_agent("scribe", handler)
        msg = bus.send("sentinel", "scribe", {"data": "hello"})
        handler.assert_called_once()
        assert msg.status == DeliveryStatus.DELIVERED

    def test_send_unregistered(self, bus: AgentBus) -> None:
        msg = bus.send("sentinel", "nonexistent", {"data": "fail"})
        assert msg.status == DeliveryStatus.DEAD_LETTER

    def test_gas_charged(self, bus: AgentBus, handler: MagicMock) -> None:
        bus.register_agent("scribe", handler)
        bus.send("sentinel", "scribe", {"data": "x"})
        assert bus.stats["total_gas"] == 2  # gas_cost_per_message=2


# ─── Broadcast ───────────────────────────────────────────────────────────────

class TestBroadcast:
    def test_broadcast_all(self, bus: AgentBus) -> None:
        h1 = MagicMock()
        h2 = MagicMock()
        bus.register_agent("sentinel", MagicMock())
        bus.register_agent("scribe", h1)
        bus.register_agent("arbiter", h2)

        bus.broadcast("sentinel", {"alert": "test"})
        h1.assert_called_once()
        h2.assert_called_once()

    def test_broadcast_group(self, bus: AgentBus) -> None:
        h1 = MagicMock()
        h2 = MagicMock()
        bus.register_agent("scribe", h1)
        bus.register_agent("arbiter", h2)

        bus.broadcast("sentinel", {"alert": "grouped"}, group=["scribe"])
        # Only scribe gets the group-targeted delivery
        assert h1.call_count >= 1


# ─── Request / Reply ────────────────────────────────────────────────────────

class TestRequestReply:
    def test_request(self, bus: AgentBus, handler: MagicMock) -> None:
        bus.register_agent("scribe", handler)
        msg = bus.request("sentinel", "scribe", {"question": "status?"})
        assert msg.msg_type == MessageType.REQUEST

    def test_reply(self, bus: AgentBus) -> None:
        h_sentinel = MagicMock()
        h_scribe = MagicMock()
        bus.register_agent("sentinel", h_sentinel)
        bus.register_agent("scribe", h_scribe)

        # Sentinel sends request
        req = bus.request("sentinel", "scribe", {"q": "status?"})
        # Scribe replies
        reply = bus.reply("scribe", req, {"a": "all good"})
        assert reply.msg_type == MessageType.REPLY
        assert reply.reply_to == req.id
        assert reply.to_agent == "sentinel"


# ─── Gas Integration ────────────────────────────────────────────────────────

class TestGas:
    def test_attach_gas_bridge(self, bus: AgentBus, handler: MagicMock) -> None:
        bridge = MagicMock()
        bus.attach_gas_bridge(bridge)
        bus.register_agent("scribe", handler)
        bus.send("sentinel", "scribe", {"x": 1})
        bridge.charge_gas.assert_called_once_with("sentinel", 2)

    def test_custom_gas_cost(self, bus: AgentBus, handler: MagicMock) -> None:
        bus.register_agent("scribe", handler)
        bus.send("sentinel", "scribe", {"x": 1}, gas_cost=10)
        assert bus.stats["total_gas"] == 10


# ─── Dead Letters ────────────────────────────────────────────────────────────

class TestDeadLetters:
    def test_dead_letter_on_failure(self, bus: AgentBus) -> None:
        bus.send("sentinel", "nonexistent", {"data": "fail"})
        assert len(bus.dead_letters) == 1

    def test_clear_dead_letters(self, bus: AgentBus) -> None:
        bus.send("s", "missing", {})
        bus.send("s", "also_missing", {})
        count = bus.clear_dead_letters()
        assert count == 2
        assert len(bus.dead_letters) == 0

    def test_handler_error_to_dead_letter(self, bus: AgentBus) -> None:
        def bad_handler(_: AgentMessage) -> None:
            raise RuntimeError("boom")

        bus.register_agent("scribe", bad_handler)
        bus.send("sentinel", "scribe", {"data": "bomb"})
        assert bus.stats["failed"] >= 1


# ─── History & Stats ────────────────────────────────────────────────────────

class TestHistoryStats:
    def test_stats(self, bus: AgentBus, handler: MagicMock) -> None:
        bus.register_agent("scribe", handler)
        bus.send("sentinel", "scribe", {"x": 1})
        s = bus.stats
        assert s["sent"] == 1
        assert s["delivered"] == 1

    def test_history(self, bus: AgentBus, handler: MagicMock) -> None:
        bus.register_agent("scribe", handler)
        bus.send("sentinel", "scribe", {"x": 1})
        bus.send("sentinel", "scribe", {"x": 2})
        h = bus.get_history()
        assert len(h) == 2

    def test_history_filter(self, bus: AgentBus) -> None:
        h1 = MagicMock()
        h2 = MagicMock()
        bus.register_agent("scribe", h1)
        bus.register_agent("arbiter", h2)
        bus.send("sentinel", "scribe", {"x": 1})
        bus.send("sentinel", "arbiter", {"x": 2})
        h = bus.get_history(agent_id="scribe")
        assert len(h) == 1
