"""
HLF Package Manager — Install, update, and manage HLF modules via OCI.

Usage::

    # CLI
    python -m hlf.hlfpm install math@v1.0.0
    python -m hlf.hlfpm list
    python -m hlf.hlfpm search math
    python -m hlf.hlfpm uninstall math
    python -m hlf.hlfpm freeze

    # API
    from hlf.hlfpm import HLFPackageManager
    pm = HLFPackageManager()
    pm.install("math@v1.0.0")

Part of Phase 5.3 — DX Tooling (Sovereign OS Master Build Plan).
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from hlf.oci_client import OCIClient, OCIModuleRef, OCIError, OCIRegistryError

_logger = logging.getLogger("hlf.pm")

# Default module installation directory
_DEFAULT_MODULES_DIR = Path("hlf_modules")

# Lockfile name
_LOCKFILE = "hlf-lock.json"


# ─── Installed Module Record ────────────────────────────────────────────────


@dataclass
class InstalledModule:
    """Record of an installed HLF module."""

    name: str
    version: str
    ref: str          # Full OCI reference
    sha256: str       # Content checksum
    size_bytes: int
    installed_path: str


# ─── Package Manager ────────────────────────────────────────────────────────


class HLFPackageManager:
    """
    HLF Package Manager for OCI-backed module distribution.

    Manages local HLF module installations with checksum verification,
    version tracking, and lockfile generation.

    Args:
        modules_dir: Local directory for installed modules.
        oci_client: OCI client instance (created if not provided).
        lockfile_path: Path to the lockfile (created in modules_dir if not given).
    """

    def __init__(
        self,
        modules_dir: Path | None = None,
        oci_client: OCIClient | None = None,
        lockfile_path: Path | None = None,
    ):
        self.modules_dir = modules_dir or _DEFAULT_MODULES_DIR
        self.modules_dir.mkdir(parents=True, exist_ok=True)

        self._client = oci_client or OCIClient()
        self._lockfile_path = lockfile_path or (self.modules_dir / _LOCKFILE)
        self._installed: dict[str, InstalledModule] = {}

        # Load existing lockfile
        self._load_lockfile()

    # ─── Public API ──────────────────────────────────────────────────────

    def install(self, spec: str, force: bool = False) -> InstalledModule:
        """
        Install a module from the OCI registry.

        Args:
            spec: Module specification, e.g. "math", "math@v1.0.0",
                  or full "ghcr.io/org/mods/math:v1.0.0".
            force: Re-install even if already present.

        Returns:
            InstalledModule record.

        Raises:
            OCIError: If pull fails.
        """
        name, ref = self._parse_spec(spec)

        # Check if already installed
        if not force and name in self._installed:
            existing = self._installed[name]
            if existing.version == ref.tag:
                _logger.info("Module '%s@%s' already installed", name, ref.tag)
                return existing

        # Pull from registry
        result = self._client.pull(ref)

        # Copy to modules directory
        dest = self.modules_dir / f"{name}.hlf"
        if dest.exists():
            dest.unlink()
        shutil.copy2(result.local_path, dest)

        # Record installation
        record = InstalledModule(
            name=name,
            version=ref.tag,
            ref=ref.full_ref,
            sha256=result.sha256,
            size_bytes=result.size_bytes,
            installed_path=str(dest),
        )
        self._installed[name] = record
        self._save_lockfile()

        _logger.info("Installed %s@%s (%d bytes)", name, ref.tag, result.size_bytes)
        return record

    def uninstall(self, name: str) -> bool:
        """
        Uninstall a module.

        Args:
            name: Module name to remove.

        Returns:
            True if module was removed, False if not found.
        """
        if name not in self._installed:
            _logger.warning("Module '%s' is not installed", name)
            return False

        record = self._installed[name]
        module_path = Path(record.installed_path)
        if module_path.exists():
            module_path.unlink()

        del self._installed[name]
        self._save_lockfile()

        _logger.info("Uninstalled %s", name)
        return True

    def list_installed(self) -> list[InstalledModule]:
        """List all installed modules."""
        return sorted(self._installed.values(), key=lambda m: m.name)

    def search(self, query: str) -> list[str]:
        """
        Search for available module versions in the registry.

        Args:
            query: Module name to search for.

        Returns:
            List of available tags.
        """
        try:
            ref = OCIModuleRef.parse(query)
            return self._client.list_tags(ref)
        except OCIError as e:
            _logger.error("Search failed for '%s': %s", query, e)
            return []

    def update(self, name: str) -> InstalledModule | None:
        """
        Update a module to the latest version.

        Args:
            name: Module name to update.

        Returns:
            Updated InstalledModule record, or None if not installed.
        """
        if name not in self._installed:
            _logger.warning("Module '%s' is not installed", name)
            return None

        # Pull latest
        return self.install(f"{name}@latest", force=True)

    def freeze(self) -> dict[str, Any]:
        """
        Generate a lockfile-compatible requirements dict.

        Returns:
            Dictionary mapping module names to version+checksum info.
        """
        return {
            name: {
                "version": rec.version,
                "ref": rec.ref,
                "sha256": rec.sha256,
                "size_bytes": rec.size_bytes,
            }
            for name, rec in sorted(self._installed.items())
        }

    def is_installed(self, name: str) -> bool:
        """Check if a module is installed."""
        return name in self._installed

    def get_module_path(self, name: str) -> Path | None:
        """Get the local path to an installed module."""
        if name in self._installed:
            return Path(self._installed[name].installed_path)
        return None

    # ─── Internals ───────────────────────────────────────────────────────

    @staticmethod
    def _parse_spec(spec: str) -> tuple[str, OCIModuleRef]:
        """
        Parse a module specifier into (name, OCIModuleRef).

        Formats:
            "math"              -> name="math", tag="latest"
            "math@v1.0.0"      -> name="math", tag="v1.0.0"
            "ghcr.io/o/m/x:v1" -> name="x", tag="v1"
        """
        if "@" in spec:
            name, tag = spec.rsplit("@", 1)
            ref = OCIModuleRef.parse(f"{name}:{tag}")
        else:
            ref = OCIModuleRef.parse(spec)
            name = ref.module

        return name, ref

    def _load_lockfile(self) -> None:
        """Load installed modules from lockfile."""
        if not self._lockfile_path.exists():
            return
        try:
            data = json.loads(self._lockfile_path.read_text(encoding="utf-8"))
            for name, info in data.get("modules", {}).items():
                self._installed[name] = InstalledModule(
                    name=name,
                    version=info.get("version", "latest"),
                    ref=info.get("ref", ""),
                    sha256=info.get("sha256", ""),
                    size_bytes=info.get("size_bytes", 0),
                    installed_path=info.get("installed_path", ""),
                )
        except (json.JSONDecodeError, KeyError) as e:
            _logger.warning("Failed to load lockfile: %s", e)

    def _save_lockfile(self) -> None:
        """Save installed modules to lockfile."""
        data = {
            "version": 1,
            "modules": {
                name: asdict(rec)
                for name, rec in sorted(self._installed.items())
            },
        }
        self._lockfile_path.write_text(
            json.dumps(data, indent=2) + "\n",
            encoding="utf-8",
        )


# ─── CLI Entry Point ────────────────────────────────────────────────────────


def _cli_main(argv: list[str] | None = None) -> int:
    """CLI entry point for hlfpm."""
    args = argv if argv is not None else sys.argv[1:]

    if not args:
        print("Usage: python -m hlf.hlfpm <command> [args]")
        print("\nCommands:")
        print("  install <module[@version]>  Install a module")
        print("  uninstall <module>          Remove a module")
        print("  list                        List installed modules")
        print("  search <module>             Search registry for tags")
        print("  update <module>             Update to latest")
        print("  freeze                      Show lockfile requirements")
        return 0

    cmd = args[0]
    pm = HLFPackageManager()

    if cmd == "install" and len(args) > 1:
        try:
            rec = pm.install(args[1], force="--force" in args)
            print(f"✓ Installed {rec.name}@{rec.version} ({rec.size_bytes} bytes)")
        except OCIError as e:
            print(f"✗ Install failed: {e}", file=sys.stderr)
            return 1

    elif cmd == "uninstall" and len(args) > 1:
        if pm.uninstall(args[1]):
            print(f"✓ Uninstalled {args[1]}")
        else:
            print(f"✗ Module '{args[1]}' not installed", file=sys.stderr)
            return 1

    elif cmd == "list":
        modules = pm.list_installed()
        if not modules:
            print("No modules installed.")
        else:
            for m in modules:
                print(f"  {m.name}@{m.version}  sha256:{m.sha256[:12]}…  {m.size_bytes}B")

    elif cmd == "search" and len(args) > 1:
        tags = pm.search(args[1])
        if tags:
            print(f"Available tags for '{args[1]}':")
            for t in tags:
                print(f"  {t}")
        else:
            print(f"No tags found for '{args[1]}'")

    elif cmd == "update" and len(args) > 1:
        rec = pm.update(args[1])
        if rec:
            print(f"✓ Updated {rec.name} → {rec.version}")
        else:
            print(f"✗ Module '{args[1]}' not installed", file=sys.stderr)
            return 1

    elif cmd == "freeze":
        frozen = pm.freeze()
        print(json.dumps(frozen, indent=2))

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(_cli_main())
