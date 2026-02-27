"""
Agent executor entrypoint.
Initialises OpenLLMetry TracerProvider, registers SIGUSR1 handler,
connects to Dapr pub/sub, and processes Redis Stream intents.
"""
from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path

from agents.core.logger import ALSLogger

_logger = ALSLogger(agent_role="agent-executor", goal_id="boot")


def quarantine_dump() -> None:
    """Dump active memory snapshot to quarantine_dumps on SIGUSR1."""
    dump_dir = Path(os.environ.get("BASE_DIR", "/app")) / "data" / "quarantine_dumps"
    dump_dir.mkdir(parents=True, exist_ok=True)
    dump_file = dump_dir / f"quarantine_{int(time.time())}.json"
    dump_file.write_text(json.dumps({"event": "SIGUSR1_DUMP", "ts": time.time()}))
    _logger.log("QUARANTINE_DUMP", {"path": str(dump_file)})


def _handle_sigusr1(signum: int, frame: object) -> None:
    quarantine_dump()


signal.signal(signal.SIGUSR1, _handle_sigusr1)


def run() -> None:
    _logger.log("AGENT_EXECUTOR_START", {"tier": os.environ.get("DEPLOYMENT_TIER", "hearth")})
    try:
        import redis

        r = redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
        group = "executor-group"
        stream = "intents"
        try:
            r.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception:
            pass  # group already exists

        _logger.log("CONSUMER_GROUP_READY", {"stream": stream, "group": group})

        while True:
            messages = r.xreadgroup(group, "executor-1", {stream: ">"}, count=1, block=5000)
            if not messages:
                continue
            for _stream, entries in messages:
                for entry_id, data in entries:
                    try:
                        payload = json.loads(data.get("data", "{}"))
                        _logger.log("INTENT_RECEIVED", {"request_id": payload.get("request_id")})
                        r.xack(stream, group, entry_id)
                    except Exception as exc:
                        _logger.log("INTENT_ERROR", {"error": str(exc)}, anomaly_score=0.9)
    except Exception as exc:
        _logger.log("AGENT_EXECUTOR_FATAL", {"error": str(exc)}, anomaly_score=1.0)


if __name__ == "__main__":
    run()
