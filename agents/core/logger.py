"""
ALS (Agentic Log Standard) logger.
Every log entry is JSON with Merkle chain trace IDs.
"""

from __future__ import annotations

import hashlib
import json
import time
import warnings
from pathlib import Path
from typing import Any

_DEFAULT_HASH_DIR = Path(__file__).parent.parent.parent / "observability" / "openllmetry"
_LAST_HASH_FILE = _DEFAULT_HASH_DIR / "last_hash.txt"
_SEED_HASH = "0" * 64


def _read_last_hash() -> str:
    """Read the last Merkle hash from disk. Gracefully handles Windows PermissionError."""
    try:
        if _LAST_HASH_FILE.exists():
            return _LAST_HASH_FILE.read_text().strip() or _SEED_HASH
    except PermissionError:
        warnings.warn(
            f"ALS: PermissionError reading {_LAST_HASH_FILE}. "
            "Using seed hash. This is normal on Windows in virtualenv/test contexts.",
            stacklevel=2,
        )
    except Exception:
        pass
    return _SEED_HASH


def _write_last_hash(h: str) -> None:
    """Persist the latest Merkle hash. Gracefully handles Windows PermissionError."""
    try:
        _LAST_HASH_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LAST_HASH_FILE.write_text(h)
    except PermissionError:
        # Windows volume-locked paths (\\?\Volume{GUID}\...) trigger this.
        # Silently skip — the Merkle chain is best-effort in local dev.
        pass
    except Exception:
        pass


def _compute_trace_id(parent_hash: str, payload: str) -> str:
    return hashlib.sha256(f"{parent_hash}{payload}".encode()).hexdigest()


class ALSLogger:
    def __init__(self, agent_role: str = "unknown", goal_id: str = "") -> None:
        self.agent_role = agent_role
        self.goal_id = goal_id

    def log(
        self,
        event: str,
        data: dict[str, Any] | None = None,
        confidence_score: float = 1.0,
        anomaly_score: float = 0.0,
        token_cost: int = 0,
    ) -> dict[str, Any]:
        parent_hash = _read_last_hash()
        payload = json.dumps({"event": event, "data": data or {}}, sort_keys=True)
        trace_id = _compute_trace_id(parent_hash, payload)
        _write_last_hash(trace_id)

        entry = {
            "trace_id": trace_id,
            "parent_trace_hash": parent_hash,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
            "goal_id": self.goal_id,
            "agent_role": self.agent_role,
            "event": event,
            "data": data or {},
            "confidence_score": confidence_score,
            "anomaly_score": anomaly_score,
            "token_cost": token_cost,
        }

        if anomaly_score > 0.85:
            self._fire_sentinel_webhook(entry)

        print(json.dumps(entry))
        return entry

    def _fire_sentinel_webhook(self, entry: dict[str, Any]) -> None:
        try:
            from agents.gateway.sentinel_gate import enforce_align

            enforce_align(json.dumps(entry))
        except Exception:
            pass


# Module-level convenience instance for backward compatibility
log = ALSLogger(agent_role="system", goal_id="default").log
