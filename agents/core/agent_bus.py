"""
Agent Service Bus — Unified inter-agent messaging over Redis Streams.

Bridges the ASB gap from TODO.md #17: inter-agent communication wiring.
Sits above RedisTransport and provides agent-level messaging semantics:

    - Point-to-point agent messages
    - Broadcast to agent groups
    - Request/reply patterns
    - Gas-metered message routing
    - Dead letter queue for failed deliveries

Architecture:
    AgentBus.send(from_agent, to_agent, payload)
      → RedisTransport.attach(bus) → XADD to per-agent stream
      → GasDashboard charges gas for routing
      → Recipient agent reads from its stream
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ─── Message Types ───────────────────────────────────────────────────────────

class MessageType(StrEnum):
    """Types of inter-agent messages."""

    DIRECT = "direct"          # Point-to-point
    BROADCAST = "broadcast"    # To all agents in a group
    REQUEST = "request"        # Expects a reply
    REPLY = "reply"            # Response to a request
    COMMAND = "command"        # System command (pause, resume, shutdown)
    EVENT = "event"            # Notification (no reply expected)


class DeliveryStatus(StrEnum):
    """Message delivery status."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    EXPIRED = "expired"
    DEAD_LETTER = "dead_letter"


# ─── Message ────────────────────────────────────────────────────────────────

@dataclass
class AgentMessage:
    """A message between agents on the ASB.

    Messages are routed by the AgentBus and optionally gas-metered.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    from_agent: str = ""
    to_agent: str = ""          # Empty = broadcast
    msg_type: MessageType = MessageType.DIRECT
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    reply_to: str = ""          # Message ID being replied to
    ttl_seconds: int = 300      # Time to live
    gas_cost: int = 1           # Gas units consumed
    status: DeliveryStatus = DeliveryStatus.PENDING

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "msg_type": self.msg_type.value,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "reply_to": self.reply_to,
            "ttl_seconds": self.ttl_seconds,
            "gas_cost": self.gas_cost,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentMessage:
        return cls(
            id=data.get("id", ""),
            from_agent=data.get("from_agent", ""),
            to_agent=data.get("to_agent", ""),
            msg_type=MessageType(data.get("msg_type", "direct")),
            payload=data.get("payload", {}),
            timestamp=data.get("timestamp", 0),
            reply_to=data.get("reply_to", ""),
            ttl_seconds=data.get("ttl_seconds", 300),
            gas_cost=data.get("gas_cost", 1),
            status=DeliveryStatus(data.get("status", "pending")),
        )

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.timestamp) > self.ttl_seconds

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, data: str) -> AgentMessage:
        return cls.from_dict(json.loads(data))


# ─── Agent Bus ───────────────────────────────────────────────────────────────

class AgentBus:
    """Unified inter-agent messaging bus.

    Routes messages between agents with gas metering. Uses in-memory
    delivery by default; attach a RedisTransport for distributed delivery.

    Args:
        gas_cost_per_message: Gas units charged per message sent.
        max_dead_letters: Maximum dead letter queue size.
    """

    def __init__(
        self,
        gas_cost_per_message: int = 1,
        max_dead_letters: int = 100,
    ) -> None:
        self._handlers: dict[str, list[Callable[[AgentMessage], None]]] = {}
        self._dead_letters: list[AgentMessage] = []
        self._max_dead_letters = max_dead_letters
        self._gas_cost = gas_cost_per_message
        self._gas_bridge: Any = None
        self._history: list[AgentMessage] = []
        self._stats = {
            "sent": 0,
            "delivered": 0,
            "failed": 0,
            "dead_letters": 0,
            "total_gas": 0,
        }

    # ── Registration ────────────────────────────────────────────────────

    def register_agent(
        self,
        agent_id: str,
        handler: Callable[[AgentMessage], None],
    ) -> None:
        """Register an agent to receive messages.

        Args:
            agent_id: Unique agent identifier.
            handler: Callback invoked when a message arrives.
        """
        if agent_id not in self._handlers:
            self._handlers[agent_id] = []
        self._handlers[agent_id].append(handler)
        logger.debug("Agent '%s' registered on bus", agent_id)

    def unregister_agent(self, agent_id: str) -> None:
        """Remove an agent from the bus."""
        self._handlers.pop(agent_id, None)
        logger.debug("Agent '%s' unregistered from bus", agent_id)

    @property
    def agents(self) -> list[str]:
        """List of registered agent IDs."""
        return list(self._handlers.keys())

    # ── Sending ─────────────────────────────────────────────────────────

    def send(
        self,
        from_agent: str,
        to_agent: str,
        payload: dict[str, Any],
        msg_type: MessageType = MessageType.DIRECT,
        reply_to: str = "",
        gas_cost: int | None = None,
    ) -> AgentMessage:
        """Send a message between agents.

        Args:
            from_agent: Sender agent ID.
            to_agent: Recipient agent ID (empty for broadcast).
            payload: Message data.
            msg_type: Message type.
            reply_to: ID of message being replied to.
            gas_cost: Override gas cost (uses default if None).

        Returns:
            The sent AgentMessage with delivery status.
        """
        cost = gas_cost if gas_cost is not None else self._gas_cost

        msg = AgentMessage(
            from_agent=from_agent,
            to_agent=to_agent,
            msg_type=msg_type,
            payload=payload,
            reply_to=reply_to,
            gas_cost=cost,
        )

        # Charge gas
        self._stats["total_gas"] += cost
        if self._gas_bridge:
            try:
                self._gas_bridge.charge_gas(from_agent, cost)
            except Exception:
                pass

        self._stats["sent"] += 1
        self._history.append(msg)

        # Route
        if msg_type == MessageType.BROADCAST:
            self._broadcast(msg)
        else:
            self._deliver(msg)

        return msg

    def broadcast(
        self,
        from_agent: str,
        payload: dict[str, Any],
        group: list[str] | None = None,
    ) -> AgentMessage:
        """Broadcast a message to all (or a group of) agents.

        Args:
            from_agent: Sender.
            payload: Message data.
            group: Optional list of recipient agents (None = all).
        """
        msg = self.send(
            from_agent=from_agent,
            to_agent="",
            payload=payload,
            msg_type=MessageType.BROADCAST,
        )

        if group:
            for agent_id in group:
                if agent_id in self._handlers and agent_id != from_agent:
                    self._deliver_to(msg, agent_id)

        return msg

    def request(
        self,
        from_agent: str,
        to_agent: str,
        payload: dict[str, Any],
    ) -> AgentMessage:
        """Send a request expecting a reply."""
        return self.send(
            from_agent=from_agent,
            to_agent=to_agent,
            payload=payload,
            msg_type=MessageType.REQUEST,
        )

    def reply(
        self,
        from_agent: str,
        to_message: AgentMessage,
        payload: dict[str, Any],
    ) -> AgentMessage:
        """Reply to a request message."""
        return self.send(
            from_agent=from_agent,
            to_agent=to_message.from_agent,
            payload=payload,
            msg_type=MessageType.REPLY,
            reply_to=to_message.id,
        )

    # ── Gas Integration ─────────────────────────────────────────────────

    def attach_gas_bridge(self, bridge: Any) -> None:
        """Attach a DaemonBridge for gas metering."""
        self._gas_bridge = bridge

    # ── Dead Letters ────────────────────────────────────────────────────

    @property
    def dead_letters(self) -> list[dict[str, Any]]:
        """Get dead letter queue contents."""
        return [m.to_dict() for m in self._dead_letters]

    def clear_dead_letters(self) -> int:
        """Clear dead letter queue. Returns count cleared."""
        count = len(self._dead_letters)
        self._dead_letters.clear()
        return count

    # ── Stats ───────────────────────────────────────────────────────────

    @property
    def stats(self) -> dict[str, Any]:
        return {
            **self._stats,
            "registered_agents": len(self._handlers),
            "dead_letter_count": len(self._dead_letters),
        }

    def get_history(self, agent_id: str = "", limit: int = 20) -> list[dict[str, Any]]:
        """Get message history, optionally filtered by agent."""
        msgs = self._history
        if agent_id:
            msgs = [m for m in msgs if m.from_agent == agent_id or m.to_agent == agent_id]
        return [m.to_dict() for m in msgs[-limit:]]

    # ── Internal ────────────────────────────────────────────────────────

    def _deliver(self, msg: AgentMessage) -> None:
        """Deliver a direct message."""
        if msg.to_agent in self._handlers:
            self._deliver_to(msg, msg.to_agent)
        else:
            msg.status = DeliveryStatus.FAILED
            self._to_dead_letter(msg)
            self._stats["failed"] += 1

    def _broadcast(self, msg: AgentMessage) -> None:
        """Deliver to all registered agents except sender."""
        delivered = False
        for agent_id in self._handlers:
            if agent_id != msg.from_agent:
                self._deliver_to(msg, agent_id)
                delivered = True

        if not delivered:
            msg.status = DeliveryStatus.FAILED
            self._stats["failed"] += 1

    def _deliver_to(self, msg: AgentMessage, agent_id: str) -> None:
        """Deliver to a specific agent's handlers."""
        handlers = self._handlers.get(agent_id, [])
        for handler in handlers:
            try:
                handler(msg)
                msg.status = DeliveryStatus.DELIVERED
                self._stats["delivered"] += 1
            except Exception as e:
                logger.warning("Handler error on '%s': %s", agent_id, e)
                msg.status = DeliveryStatus.FAILED
                self._to_dead_letter(msg)
                self._stats["failed"] += 1

    def _to_dead_letter(self, msg: AgentMessage) -> None:
        """Move failed message to dead letter queue."""
        msg.status = DeliveryStatus.DEAD_LETTER
        self._dead_letters.append(msg)
        self._stats["dead_letters"] += 1

        # Evict oldest if over limit
        while len(self._dead_letters) > self._max_dead_letters:
            self._dead_letters.pop(0)
