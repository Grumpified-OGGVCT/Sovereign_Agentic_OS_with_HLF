"""
HLF Tool Lockfile — Reproducible tool installations.

Tracks exact versions of all installed tools + their dependencies
so installations can be reproduced identically on another machine.

Chronicler Hat: Version tracking, freshness monitoring, CVE alerting.

Usage::

    lockfile = ToolLockfile(path="tools/tool.lock.json")
    lockfile.lock("my_agent", manifest, deps_snapshot)
    lockfile.save()

    # Later, on another machine:
    lockfile.load()
    for entry in lockfile.entries():
        installer.install(entry.source_url)  # exact version
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_LOCKFILE = _PROJECT_ROOT / "tools" / "tool.lock.json"


@dataclass
class LockEntry:
    """Single entry in the lockfile — one installed tool."""

    name: str
    version: str
    source_url: str
    adapter: str = "python"
    manifest_sha256: str = ""
    dependencies: list[str] = field(default_factory=list)
    tier: list[str] = field(default_factory=lambda: ["hearth"])
    gas_cost: int = 1
    installed_at: float = field(default_factory=time.time)
    locked_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "source_url": self.source_url,
            "adapter": self.adapter,
            "manifest_sha256": self.manifest_sha256,
            "dependencies": self.dependencies,
            "tier": self.tier,
            "gas_cost": self.gas_cost,
            "installed_at": self.installed_at,
            "locked_at": self.locked_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LockEntry:
        return cls(
            name=data["name"],
            version=data["version"],
            source_url=data.get("source_url", ""),
            adapter=data.get("adapter", "python"),
            manifest_sha256=data.get("manifest_sha256", ""),
            dependencies=data.get("dependencies", []),
            tier=data.get("tier", ["hearth"]),
            gas_cost=data.get("gas_cost", 1),
            installed_at=data.get("installed_at", 0),
            locked_at=data.get("locked_at", 0),
        )


class ToolLockfile:
    """Manages tool.lock.json for reproducible installations.

    Guarantees that `hlf install --from-lockfile` reproduces the exact
    same tool set on any machine.
    """

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else _DEFAULT_LOCKFILE
        self._entries: dict[str, LockEntry] = {}
        self._metadata: dict[str, Any] = {
            "lockfile_version": "1.0.0",
            "created_at": time.time(),
            "updated_at": time.time(),
        }

    def lock(
        self,
        name: str,
        version: str,
        source_url: str,
        manifest_content: str = "",
        dependencies: list[str] | None = None,
        adapter: str = "python",
        tier: list[str] | None = None,
        gas_cost: int = 1,
    ) -> LockEntry:
        """Lock a tool at its current version."""
        manifest_hash = hashlib.sha256(manifest_content.encode()).hexdigest() if manifest_content else ""

        entry = LockEntry(
            name=name,
            version=version,
            source_url=source_url,
            adapter=adapter,
            manifest_sha256=manifest_hash,
            dependencies=dependencies or [],
            tier=tier or ["hearth"],
            gas_cost=gas_cost,
        )

        self._entries[name] = entry
        self._metadata["updated_at"] = time.time()
        return entry

    def unlock(self, name: str) -> bool:
        """Remove a tool from the lockfile."""
        if name in self._entries:
            del self._entries[name]
            self._metadata["updated_at"] = time.time()
            return True
        return False

    def get(self, name: str) -> LockEntry | None:
        """Get a locked tool entry."""
        return self._entries.get(name)

    def entries(self) -> list[LockEntry]:
        """Return all locked entries."""
        return list(self._entries.values())

    def save(self) -> None:
        """Persist lockfile to disk."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "metadata": self._metadata,
            "tools": {name: entry.to_dict() for name, entry in self._entries.items()},
        }
        self.path.write_text(
            json.dumps(data, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def load(self) -> None:
        """Load lockfile from disk."""
        if not self.path.exists():
            return

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._metadata = data.get("metadata", {})
            tools = data.get("tools", {})
            self._entries = {
                name: LockEntry.from_dict(entry)
                for name, entry in tools.items()
            }
        except (json.JSONDecodeError, KeyError):
            self._entries = {}

    def is_locked(self, name: str) -> bool:
        """Check if a tool is in the lockfile."""
        return name in self._entries

    def verify_integrity(self, name: str, manifest_content: str) -> bool:
        """Verify a tool's manifest hasn't changed since locking."""
        entry = self._entries.get(name)
        if not entry or not entry.manifest_sha256:
            return True  # No hash to verify against

        current_hash = hashlib.sha256(manifest_content.encode()).hexdigest()
        return current_hash == entry.manifest_sha256

    def stale_tools(self, max_age_days: int = 90) -> list[LockEntry]:
        """Find tools not updated in N days (Chronicler Hat: freshness tracking)."""
        cutoff = time.time() - (max_age_days * 86400)
        return [
            entry for entry in self._entries.values()
            if entry.locked_at < cutoff
        ]

    def to_dict(self) -> dict[str, Any]:
        """Full lockfile as dict."""
        return {
            "metadata": self._metadata,
            "tools": {n: e.to_dict() for n, e in self._entries.items()},
        }
