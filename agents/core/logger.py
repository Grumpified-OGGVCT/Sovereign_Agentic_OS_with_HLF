"""
ALS (Agentic Log Standard) logger.
Every log entry is JSON with Merkle chain trace IDs.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import warnings
from pathlib import Path
from typing import Any

_DEFAULT_HASH_DIR = Path(__file__).parent.parent.parent / "observability" / "openllmetry"
_LAST_HASH_FILE = _DEFAULT_HASH_DIR / "last_hash.txt"
_SEED_HASH = "0" * 64

# Log levels in ascending severity order.
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
# Levels at or above this threshold are written to stderr in addition to stdout.
_STDERR_THRESHOLD = "WARNING"
_STDERR_THRESHOLD_IDX = LOG_LEVELS.index(_STDERR_THRESHOLD)


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
        level: str = "INFO",
        correlation_id: str = "",
    ) -> dict[str, Any]:
        """Emit a structured ALS log entry.

        Args:
            event: Event name / identifier.
            data: Arbitrary key-value metadata.
            confidence_score: Confidence in [0.0, 1.0].
            anomaly_score: Anomaly likelihood in [0.0, 1.0]; values >0.85 trigger
                the sentinel webhook.
            token_cost: Tokens consumed by this operation.
            level: Log severity — one of DEBUG, INFO, WARNING, ERROR, CRITICAL.
                Entries at WARNING or above are also written to stderr so that
                container log collectors that separate stdout/stderr can route
                them to alerting pipelines without additional filtering.
            correlation_id: Optional request / trace correlation ID that links
                this entry to an end-to-end request across multiple agents.
        """
        level = level.upper()
        if level not in LOG_LEVELS:
            warnings.warn(
                f"ALS: Invalid log level '{level}'; falling back to 'INFO'. "
                "Valid levels: " + ", ".join(LOG_LEVELS),
                stacklevel=2,
            )
            level = "INFO"
        level_idx = LOG_LEVELS.index(level)

        parent_hash = _read_last_hash()
        payload = json.dumps({"event": event, "data": data or {}}, sort_keys=True)
        trace_id = _compute_trace_id(parent_hash, payload)
        _write_last_hash(trace_id)

        entry: dict[str, Any] = {
            "trace_id": trace_id,
            "parent_trace_hash": parent_hash,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
            "goal_id": self.goal_id,
            "agent_role": self.agent_role,
            "level": level,
            "event": event,
            "data": data or {},
            "confidence_score": confidence_score,
            "anomaly_score": anomaly_score,
            "token_cost": token_cost,
        }
        if correlation_id:
            entry["correlation_id"] = correlation_id

        if anomaly_score > 0.85:
            self._fire_sentinel_webhook(entry)

        serialised = json.dumps(entry)
        print(serialised)
        # Mirror WARNING / ERROR / CRITICAL to stderr so ops tooling can
        # capture high-severity events without parsing all stdout.
        if level_idx >= _STDERR_THRESHOLD_IDX:
            print(serialised, file=sys.stderr)

        return entry

    def _fire_sentinel_webhook(self, entry: dict[str, Any]) -> None:
        try:
            from agents.gateway.sentinel_gate import enforce_align

            enforce_align(json.dumps(entry))
        except Exception:
            pass


# Module-level convenience instance for backward compatibility
log = ALSLogger(agent_role="system", goal_id="default").log
