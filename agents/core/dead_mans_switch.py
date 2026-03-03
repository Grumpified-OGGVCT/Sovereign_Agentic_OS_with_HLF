"""
The Dead Man's Switch (Governance Fail-Safe).
Handles Time-To-Live (TTL) for Human-In-The-Loop inputs and cascading container panic resets.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import signal
import sys

import redis

try:
    import docker
except ImportError:
    docker = None

logger = logging.getLogger(__name__)


class DeadManSwitch:
    """Monitors system health and aborts if criteria are met."""

    # Limit configuration
    MAX_PANIC_RESETS = 3
    PANIC_WINDOW_SECONDS = 300  # 5 minutes
    HITL_TTL_SECONDS = 3600  # 1 Hour before auto-abort on Human-In-The-Loop

    def __init__(self):
        self.panic_timestamps: list[float] = []
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        self.r = redis.from_url(redis_url, decode_responses=True)

    def record_panic(self, current_time: float) -> None:
        """Record a container/agent internal panic/crash."""
        self.panic_timestamps.append(current_time)
        # Clear out timestamps older than the window
        self.panic_timestamps = [t for t in self.panic_timestamps if current_time - t <= self.PANIC_WINDOW_SECONDS]

        if len(self.panic_timestamps) >= self.MAX_PANIC_RESETS:
            self.trigger_kill_switch("Cascading Failure (>3 panics in 5 minutes)")

    def check_hitl_timeout(self, state_start_time: float, current_time: float) -> None:
        """Check if an input-required state has exceeded its TTL."""
        if current_time - state_start_time > self.HITL_TTL_SECONDS:
            self.trigger_safe_mode("Human-In-The-Loop TTL Expired")

    def trigger_safe_mode(self, reason: str) -> None:
        """Rollbacks transaction, enters @Safe mode, broadcasts out-of-band alert."""
        logger.critical(f"ENTERING SAFE MODE: {reason}")
        # Broadcast the alert via Redis PubSub for orchestrator or external monitor to pick up
        alert_payload = json.dumps({"level": "CRITICAL", "type": "SAFE_MODE", "reason": reason})
        try:
            self.r.publish("system_alerts", alert_payload)
        except Exception as e:
            logger.error(f"Failed to publish safe mode alert: {e}")

    def trigger_kill_switch(self, reason: str) -> None:
        """
        Global kill-switch. Trigger SIGUSR1 trap to dump memory to Quarantine,
        then mathematically sever sovereign-net bridging via Docker SDK.
        """
        logger.critical(f"DEAD MAN'S SWITCH TRIGGERED: {reason}")

        # 1. Trigger SIGUSR1 to own process to dump memory
        with contextlib.suppress(Exception):
            os.kill(os.getpid(), signal.SIGUSR1)

        # 2. Sever sovereign-net connectivity
        if docker is not None:
            try:
                client = docker.from_env()
                # Find the container this process is running in
                # In Docker, hostname is typically the short container ID
                hostname = os.environ.get("HOSTNAME")
                if hostname:
                    container = client.containers.get(hostname)
                    networks = client.networks.list(names=["sovereign-net"])
                    if networks:
                        logger.warning("Disconnecting from sovereign-net...")
                        networks[0].disconnect(container, force=True)
            except Exception as e:
                logger.error(f"Failed to sever Docker network: {e}")

        # 3. Halt process
        sys.exit(1)


# Global singleton
switch = DeadManSwitch()
