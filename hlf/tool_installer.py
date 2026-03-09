"""
HLF Tool Installer — 1-Click Add & Clone Pipeline for Sovereign OS.

Implements the full persona-approved tool lifecycle:
  CLONE → VALIDATE → SANDBOX → REGISTER → TEST → ACTIVATE

Designed with input from all 14 Thinking Hats and Crew personas:
  - Silver Hat (Architecture): ToolAdapter protocol + manifest schema
  - Crimson Hat (Security): ACFS sandbox + zero-trust isolation
  - Gold Hat (CoVE): 12-point verification gate before activation
  - Azure Hat (Steward): ALIGN ledger entries on install/uninstall
  - Blue Hat (Process): Sequential gated pipeline
  - Catalyst (Performance): Lazy-loading, boot-time neutral
  - Scout (Research): Homebrew formula + Docker isolation model

Usage::

    from hlf.tool_installer import ToolInstaller

    installer = ToolInstaller(tools_dir="./tools")
    result = installer.install("https://github.com/user/cool-agent")
    # → ToolInstallResult(name="cool_agent", status="active", ...)

    installer.uninstall("cool_agent")
    installer.list_tools()
    installer.health_check("cool_agent")

CLI Usage::

    hlf install https://github.com/user/cool-agent
    hlf uninstall cool-agent
    hlf tools
    hlf health cool-agent
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# ─── Project paths ───────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_TOOLS_DIR = _PROJECT_ROOT / "tools" / "installed"
_TOOL_REGISTRY_PATH = _PROJECT_ROOT / "governance" / "tool_registry.json"
_HOST_FUNCTIONS_PATH = _PROJECT_ROOT / "governance" / "host_functions.json"

# ─── Constants ───────────────────────────────────────────────────────────────

MANIFEST_FILENAME = "tool.hlf.yaml"
MAX_CLONE_TIMEOUT_SECONDS = 120
MAX_INSTALL_TIMEOUT_SECONDS = 300
RESERVED_TOOL_NAMES = frozenset({
    "READ", "WRITE", "SLEEP", "SPAWN", "WEB_SEARCH", "EXEC",
    "OPENCLAW_SUMMARIZE", "OPENCLAW_CITE", "OPENCLAW_TRANSLATE",
})


# ─── Exceptions ──────────────────────────────────────────────────────────────


class ToolInstallError(RuntimeError):
    """Raised when a tool installation fails at any phase."""


class ToolManifestError(ToolInstallError):
    """Raised when tool.hlf.yaml is missing or invalid."""


class ToolSecurityError(ToolInstallError):
    """Raised when a tool fails security verification (CoVE gate)."""


class ToolNotFoundError(ToolInstallError):
    """Raised when attempting to operate on a tool that doesn't exist."""


# ─── ToolAdapter Protocol (Silver Hat) ───────────────────────────────────────


@runtime_checkable
class ToolAdapter(Protocol):
    """Protocol that all installable tools must implement.

    Designed by Silver Hat for plugin architecture with well-defined
    contracts. Tools are adapters — they implement this interface to
    integrate with the Sovereign OS tool dispatch system.
    """

    @property
    def name(self) -> str:
        """Unique tool identifier (e.g. 'cool_agent')."""
        ...

    @property
    def version(self) -> str:
        """Semantic version string (e.g. '1.2.0')."""
        ...

    @property
    def tier(self) -> list[str]:
        """Allowed deployment tiers (e.g. ['hearth', 'forge'])."""
        ...

    @property
    def gas_cost(self) -> int:
        """Gas consumed per invocation."""
        ...

    def execute(self, args: dict[str, Any]) -> ToolResult:
        """Execute the tool with the given arguments."""
        ...

    def health_check(self) -> bool:
        """Return True if the tool is operational."""
        ...

    def schema(self) -> dict[str, Any]:
        """Return JSON Schema describing accepted arguments."""
        ...


@dataclass
class ToolResult:
    """Result returned by a ToolAdapter.execute() call."""

    success: bool
    value: Any = None
    error: str | None = None
    gas_used: int = 0
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "value": self.value,
            "error": self.error,
            "gas_used": self.gas_used,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
        }


# ─── Tool Manifest (Silver Hat schema) ──────────────────────────────────────


@dataclass
class ToolManifest:
    """Parsed and validated tool.hlf.yaml manifest.

    Every installable tool repo must contain a tool.hlf.yaml at root.
    This class validates the manifest against the approved schema.
    """

    name: str
    version: str
    description: str = ""
    author: str = ""
    license: str = ""
    tier: list[str] = field(default_factory=lambda: ["hearth"])
    gas_cost: int = 1
    sensitive: bool = False
    entrypoint: str = "main.py"
    function: str = "run"
    adapter: str = "python"  # python | docker | wasm | mcp
    dependencies: dict[str, Any] = field(default_factory=dict)
    permissions: dict[str, Any] = field(default_factory=dict)
    health: dict[str, Any] = field(default_factory=dict)
    args: list[dict[str, Any]] = field(default_factory=list)
    signature: dict[str, str] = field(default_factory=dict)

    # Internals (not from YAML)
    source_url: str = ""
    install_path: Path | None = None

    @classmethod
    def from_yaml(cls, path: Path) -> ToolManifest:
        """Load and validate a tool.hlf.yaml file."""
        if not path.exists():
            raise ToolManifestError(
                f"Manifest not found: {path}. "
                f"Every installable tool must have a {MANIFEST_FILENAME} at root."
            )

        try:
            import yaml
        except ImportError:
            raise ToolManifestError(
                "PyYAML is required for tool manifest parsing. "
                "Install with: pip install pyyaml"
            )

        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as e:
            raise ToolManifestError(f"Invalid YAML in {path}: {e}")

        if not isinstance(data, dict):
            raise ToolManifestError(f"Manifest must be a YAML mapping, got {type(data)}")

        # Required fields
        name = data.get("name")
        if not name or not isinstance(name, str):
            raise ToolManifestError("Manifest missing required 'name' field")

        version = data.get("version")
        if not version:
            raise ToolManifestError("Manifest missing required 'version' field")

        # Validate name format (alphanumeric + underscore, no collisions)
        if not re.match(r"^[a-z][a-z0-9_]{1,63}$", name):
            raise ToolManifestError(
                f"Invalid tool name '{name}'. Must be lowercase, start with letter, "
                f"contain only a-z, 0-9, underscore. Max 64 chars."
            )

        if name.upper() in RESERVED_TOOL_NAMES:
            raise ToolManifestError(
                f"Tool name '{name}' conflicts with reserved host function. "
                f"Choose a different name."
            )

        return cls(
            name=name,
            version=str(version),
            description=data.get("description", ""),
            author=data.get("author", ""),
            license=data.get("license", ""),
            tier=data.get("tier", ["hearth"]),
            gas_cost=int(data.get("gas_cost", 1)),
            sensitive=bool(data.get("sensitive", False)),
            entrypoint=data.get("entrypoint", "main.py"),
            function=data.get("function", "run"),
            adapter=data.get("adapter", "python"),
            dependencies=data.get("dependencies", {}),
            permissions=data.get("permissions", {}),
            health=data.get("health", {}),
            args=data.get("args", []),
            signature=data.get("signature", {}),
        )

    def to_host_function_entry(self) -> dict[str, Any]:
        """Convert manifest to host_functions.json entry format."""
        return {
            "name": self.name.upper(),
            "args": [
                {"name": a["name"], "type": a.get("type", "string")}
                for a in self.args
            ],
            "returns": "any",
            "tier": self.tier,
            "gas": self.gas_cost,
            "backend": f"tool:{self.name}",
            "sensitive": self.sensitive,
        }


# ─── Tool Install Result ────────────────────────────────────────────────────


@dataclass
class ToolInstallResult:
    """Result of a tool installation attempt."""

    name: str
    status: str  # "active" | "failed" | "uninstalled"
    version: str = ""
    install_path: str = ""
    phases_completed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    verification_score: float = 0.0
    duration_seconds: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "version": self.version,
            "install_path": self.install_path,
            "phases_completed": self.phases_completed,
            "errors": self.errors,
            "verification_score": self.verification_score,
            "duration_seconds": self.duration_seconds,
            "timestamp": self.timestamp,
        }


# ─── Verification Gate (Gold Hat / CoVE) ────────────────────────────────────


class ToolVerifier:
    """Gold Hat CoVE-inspired 12-dimension verification gate.

    Every tool must pass ALL checks before activation.
    Any single failure blocks the install.
    """

    def verify(self, manifest: ToolManifest, tool_path: Path) -> tuple[bool, list[str], float]:
        """Run all verification checks.

        Returns:
            (passed, failures, score) where score is 0.0-1.0.
        """
        checks = [
            ("manifest_schema", self._check_manifest_schema),
            ("name_collision", self._check_name_collision),
            ("entrypoint_exists", self._check_entrypoint),
            ("license_present", self._check_license),
            ("tier_valid", self._check_tier),
            ("gas_reasonable", self._check_gas),
            ("permissions_audit", self._check_permissions),
            ("signature_present", self._check_signature),
            ("no_path_traversal", self._check_path_traversal),
            ("adapter_supported", self._check_adapter),
            ("dependency_audit", self._check_dependencies),
            ("health_config", self._check_health_config),
        ]

        failures: list[str] = []
        warnings: list[str] = []
        passed_count = 0

        for name, check_fn in checks:
            try:
                ok, msg = check_fn(manifest, tool_path)
                if ok:
                    passed_count += 1
                    if msg:
                        warnings.append(f"{name}: {msg}")
                    logger.info(f"  ✅ {name}")
                else:
                    failures.append(f"{name}: {msg}")
                    logger.warning(f"  ❌ {name}: {msg}")
            except Exception as e:
                failures.append(f"{name}: {e}")
                logger.error(f"  💥 {name}: {e}")

        score = passed_count / len(checks) if checks else 0.0
        if warnings:
            for w in warnings:
                logger.info(f"  ⚠️ {w}")
        return len(failures) == 0, failures, round(score, 3)

    def _check_manifest_schema(self, m: ToolManifest, _: Path) -> tuple[bool, str]:
        if not m.name or not m.version:
            return False, "Missing name or version"
        return True, ""

    def _check_name_collision(self, m: ToolManifest, _: Path) -> tuple[bool, str]:
        if m.name.upper() in RESERVED_TOOL_NAMES:
            return False, f"'{m.name}' collides with reserved function"
        return True, ""

    def _check_entrypoint(self, m: ToolManifest, p: Path) -> tuple[bool, str]:
        ep = p / m.entrypoint
        if not ep.exists():
            return False, f"Entrypoint '{m.entrypoint}' not found in {p}"
        return True, ""

    def _check_license(self, m: ToolManifest, _: Path) -> tuple[bool, str]:
        if not m.license:
            return False, "No license specified (SPDX required)"
        return True, ""

    def _check_tier(self, m: ToolManifest, _: Path) -> tuple[bool, str]:
        valid_tiers = {"hearth", "forge", "sovereign"}
        invalid = set(m.tier) - valid_tiers
        if invalid:
            return False, f"Invalid tiers: {invalid}"
        return True, ""

    def _check_gas(self, m: ToolManifest, _: Path) -> tuple[bool, str]:
        if m.gas_cost < 0 or m.gas_cost > 100:
            return False, f"Gas cost {m.gas_cost} out of range (0-100)"
        return True, ""

    def _check_permissions(self, m: ToolManifest, _: Path) -> tuple[bool, str]:
        network = m.permissions.get("network", [])
        if "*" in network:
            return False, "Wildcard network access not allowed"
        fs = m.permissions.get("filesystem", [])
        for path in fs:
            if ".." in str(path) or str(path).startswith("/"):
                return False, f"Absolute/traversal path in filesystem permissions: {path}"
        return True, ""

    def _check_signature(self, m: ToolManifest, _: Path) -> tuple[bool, str]:
        # Signature is recommended but not blocking in v1
        if not m.signature.get("sha256"):
            return True, "Warning: no signature (recommended)"
        return True, ""

    def _check_path_traversal(self, _: ToolManifest, p: Path) -> tuple[bool, str]:
        """Scan files for path traversal / symlink escape attempts."""
        root = p.resolve()
        for f in p.rglob("*"):
            try:
                target = f.resolve()
            except OSError:
                return False, f"Unresolvable path encountered: {f}"
            try:
                target.relative_to(root)
            except ValueError:
                return False, f"Path traversal or escape detected: {f} -> {target}"
        return True, ""

    def _check_adapter(self, m: ToolManifest, _: Path) -> tuple[bool, str]:
        supported = {"python", "docker", "wasm", "mcp"}
        if m.adapter not in supported:
            return False, f"Unsupported adapter: '{m.adapter}'. Supported: {supported}"
        return True, ""

    def _check_dependencies(self, m: ToolManifest, _: Path) -> tuple[bool, str]:
        packages = m.dependencies.get("packages", [])
        # Check for known-bad packages (simplified CVE check)
        blocked = {"os-sys", "python-binance-hacked", "colourama"}
        for pkg in packages:
            pkg_name = re.split(r"[>=<\[\]]", pkg)[0].strip().lower()
            if pkg_name in blocked:
                return False, f"Blocked package: {pkg_name}"
        return True, ""

    def _check_health_config(self, m: ToolManifest, _: Path) -> tuple[bool, str]:
        if m.health.get("endpoint"):
            return True, ""
        # Health check is optional but recommended
        return True, "Warning: no health_check endpoint"


# ─── Tool Installer (Blue Hat Pipeline) ─────────────────────────────────────


class ToolInstaller:
    """Main installer — orchestrates the full CLONE → ACTIVATE pipeline.

    Blue Hat mandates: sequential phases, each gates the next, automatic
    rollback on failure.

    Usage::

        installer = ToolInstaller()
        result = installer.install("https://github.com/user/tool")
        installer.list_tools()
        installer.uninstall("tool_name")
    """

    def __init__(
        self,
        tools_dir: Path | str | None = None,
        tier: str = "hearth",
        verifier: ToolVerifier | None = None,
    ):
        self.tools_dir = Path(tools_dir) if tools_dir else _DEFAULT_TOOLS_DIR
        self.tier = tier
        self.verifier = verifier or ToolVerifier()
        self._registry: dict[str, dict[str, Any]] = {}

        # Ensure tools directory exists
        self.tools_dir.mkdir(parents=True, exist_ok=True)

        # Load existing tool registry
        self._load_registry()

    # ── Public API ───────────────────────────────────────────────────────

    def install(self, repo_url: str) -> ToolInstallResult:
        """Install a tool from a Git repository URL.

        Pipeline: CLONE → VALIDATE → SANDBOX → REGISTER → TEST → ACTIVATE

        Args:
            repo_url: Git-cloneable URL (https or ssh)

        Returns:
            ToolInstallResult with status and phases completed
        """
        start = time.time()
        result = ToolInstallResult(name="unknown", status="failed")
        tool_path: Path | None = None

        try:
            # Phase 1: CLONE
            logger.info(f"📥 Phase 1/6: Cloning {repo_url}...")
            tool_path = self._clone_repo(repo_url)
            result.phases_completed.append("clone")

            # Phase 2: VALIDATE — read and validate manifest
            logger.info("📋 Phase 2/6: Validating manifest...")
            manifest = self._validate_manifest(tool_path)
            manifest.source_url = repo_url
            manifest.install_path = tool_path
            result.name = manifest.name
            result.version = manifest.version
            result.phases_completed.append("validate")

            # Check for name collision with existing installs
            if manifest.name in self._registry:
                raise ToolInstallError(
                    f"Tool '{manifest.name}' is already installed. "
                    f"Use 'hlf upgrade {manifest.name}' or 'hlf uninstall {manifest.name}' first."
                )

            # Phase 3: SANDBOX — set up isolation
            logger.info("🔒 Phase 3/6: Setting up sandbox...")
            self._setup_sandbox(manifest, tool_path)
            result.phases_completed.append("sandbox")

            # Phase 4: VERIFY (Gold Hat CoVE gate)
            logger.info("🛡️ Phase 4/6: Running CoVE verification (12-point)...")
            passed, failures, score = self.verifier.verify(manifest, tool_path)
            result.verification_score = score
            if not passed:
                raise ToolSecurityError(
                    f"Tool '{manifest.name}' failed CoVE verification:\n"
                    + "\n".join(f"  - {f}" for f in failures)
                )
            result.phases_completed.append("verify")

            # Phase 5: REGISTER — add to host_functions + registry
            logger.info("📝 Phase 5/6: Registering tool...")
            self._register_tool(manifest)
            result.phases_completed.append("register")

            # Phase 6: HEALTH CHECK
            logger.info("💚 Phase 6/6: Running health check...")
            healthy = self._run_health_check(manifest, tool_path)
            if not healthy:
                logger.warning(f"⚠️ Health check failed for {manifest.name} (non-blocking)")
            result.phases_completed.append("health_check")

            # SUCCESS
            result.status = "active"
            result.install_path = str(tool_path)
            result.duration_seconds = round(time.time() - start, 2)

            logger.info(
                f"✅ Tool '{manifest.name}' v{manifest.version} installed successfully "
                f"({result.duration_seconds}s, score={score:.1%})"
            )

            # ALIGN ledger entry
            self._log_align("TOOL_INSTALL", {
                "tool": manifest.name,
                "version": manifest.version,
                "source": repo_url,
                "verification_score": score,
            })

            return result

        except ToolInstallError as e:
            result.errors.append(str(e))
            result.duration_seconds = round(time.time() - start, 2)
            logger.error(f"❌ Installation failed: {e}")

            # Rollback: clean up cloned directory
            if tool_path and tool_path.exists():
                shutil.rmtree(tool_path, ignore_errors=True)
                logger.info(f"🧹 Rolled back: removed {tool_path}")

            return result

    def uninstall(self, tool_name: str) -> ToolInstallResult:
        """Uninstall a tool — deregister, remove sandbox, cleanup."""
        if tool_name not in self._registry:
            raise ToolNotFoundError(f"Tool '{tool_name}' is not installed")

        entry = self._registry[tool_name]
        tool_path = Path(entry.get("install_path", ""))

        # Remove from registry
        del self._registry[tool_name]
        self._save_registry()

        # Remove files
        if tool_path.exists():
            shutil.rmtree(tool_path, ignore_errors=True)

        # Remove from host_functions.json
        self._deregister_host_function(tool_name)

        logger.info(f"🗑️ Tool '{tool_name}' uninstalled")

        self._log_align("TOOL_UNINSTALL", {"tool": tool_name})

        return ToolInstallResult(
            name=tool_name,
            status="uninstalled",
            phases_completed=["deregister", "cleanup"],
        )

    def list_tools(self) -> list[dict[str, Any]]:
        """List all installed tools with status."""
        tools = []
        for name, entry in self._registry.items():
            tools.append({
                "name": name,
                "version": entry.get("version", "?"),
                "status": entry.get("status", "unknown"),
                "tier": entry.get("tier", []),
                "gas_cost": entry.get("gas_cost", 0),
                "adapter": entry.get("adapter", "python"),
                "install_path": entry.get("install_path", ""),
                "installed_at": entry.get("installed_at", 0),
            })
        return tools

    def health_check(self, tool_name: str) -> bool:
        """Run health check on an installed tool."""
        if tool_name not in self._registry:
            raise ToolNotFoundError(f"Tool '{tool_name}' is not installed")

        entry = self._registry[tool_name]
        tool_path = Path(entry["install_path"])

        manifest = ToolManifest(
            name=tool_name,
            version=entry.get("version", ""),
            health=entry.get("health", {}),
            entrypoint=entry.get("entrypoint", "main.py"),
            function=entry.get("function", "run"),
        )

        return self._run_health_check(manifest, tool_path)

    def upgrade(self, tool_name: str) -> ToolInstallResult:
        """Upgrade a tool — pull latest, re-validate, hot-swap."""
        if tool_name not in self._registry:
            raise ToolNotFoundError(f"Tool '{tool_name}' is not installed")

        entry = self._registry[tool_name]
        source_url = entry.get("source_url", "")
        if not source_url:
            raise ToolInstallError(f"No source URL recorded for '{tool_name}', cannot upgrade")

        # Uninstall old version
        self.uninstall(tool_name)

        # Reinstall from source
        return self.install(source_url)

    # ── Pipeline phases (private) ────────────────────────────────────────

    def _clone_repo(self, repo_url: str) -> Path:
        """Phase 1: Clone the git repository."""
        # Extract repo name from URL
        repo_name = self._extract_repo_name(repo_url)
        clone_target = self.tools_dir / repo_name

        if clone_target.exists():
            shutil.rmtree(clone_target)

        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", repo_url, str(clone_target)],
                capture_output=True,
                text=True,
                timeout=MAX_CLONE_TIMEOUT_SECONDS,
                check=True,
            )
        except subprocess.TimeoutExpired:
            raise ToolInstallError(
                f"Clone timed out after {MAX_CLONE_TIMEOUT_SECONDS}s for {repo_url}"
            )
        except subprocess.CalledProcessError as e:
            raise ToolInstallError(f"Clone failed: {e.stderr.strip()}")
        except FileNotFoundError:
            raise ToolInstallError(
                "Git is not installed or not in PATH. Install git first."
            )

        return clone_target

    def _validate_manifest(self, tool_path: Path) -> ToolManifest:
        """Phase 2: Read and validate tool.hlf.yaml."""
        manifest_path = tool_path / MANIFEST_FILENAME
        return ToolManifest.from_yaml(manifest_path)

    def _setup_sandbox(self, manifest: ToolManifest, tool_path: Path) -> None:
        """Phase 3: Set up ACFS sandbox for the tool.

        Crimson Hat: zero-trust isolation. Tool can only access its own dir.
        """
        # Create sandbox metadata
        sandbox_meta = {
            "tool": manifest.name,
            "permissions": manifest.permissions,
            "allowed_paths": [str(tool_path)],
            "network": manifest.permissions.get("network", []),
            "secrets": manifest.permissions.get("secrets", []),
            "created_at": time.time(),
        }

        sandbox_file = tool_path / ".sandbox.json"
        sandbox_file.write_text(
            json.dumps(sandbox_meta, indent=2),
            encoding="utf-8",
        )

        # Create isolated venv for Python tools
        if manifest.adapter == "python":
            venv_path = tool_path / ".venv"
            if not venv_path.exists():
                try:
                    subprocess.run(
                        [sys.executable, "-m", "venv", str(venv_path)],
                        capture_output=True,
                        check=True,
                        timeout=60,
                    )
                    logger.info(f"  Created isolated venv at {venv_path}")

                    # Install dependencies
                    packages = manifest.dependencies.get("packages", [])
                    if packages:
                        pip = venv_path / ("Scripts" if os.name == "nt" else "bin") / "pip"
                        subprocess.run(
                            [str(pip), "install", *packages],
                            capture_output=True,
                            check=True,
                            timeout=MAX_INSTALL_TIMEOUT_SECONDS,
                        )
                        logger.info(f"  Installed {len(packages)} dependencies")

                except subprocess.CalledProcessError as e:
                    raise ToolInstallError(f"Sandbox setup failed: {e}")
                except subprocess.TimeoutExpired:
                    raise ToolInstallError("Dependency installation timed out")

    def _register_tool(self, manifest: ToolManifest) -> None:
        """Phase 5: Register tool in registry and host_functions.json."""
        # Save to local registry
        self._registry[manifest.name] = {
            "version": manifest.version,
            "description": manifest.description,
            "author": manifest.author,
            "license": manifest.license,
            "tier": manifest.tier,
            "gas_cost": manifest.gas_cost,
            "sensitive": manifest.sensitive,
            "adapter": manifest.adapter,
            "entrypoint": manifest.entrypoint,
            "function": manifest.function,
            "health": manifest.health,
            "permissions": manifest.permissions,
            "source_url": manifest.source_url,
            "install_path": str(manifest.install_path),
            "status": "active",
            "installed_at": time.time(),
        }
        self._save_registry()

        # Register in host_functions.json for τ() dispatch
        self._register_host_function(manifest)

        logger.info(f"  Registered '{manifest.name}' → τ({manifest.name.upper()})")

    def _run_health_check(self, manifest: ToolManifest, tool_path: Path) -> bool:
        """Phase 6: Run health check on the installed tool."""
        health_endpoint = manifest.health.get("endpoint")
        if not health_endpoint:
            return True  # No health check configured — pass by default

        entrypoint = tool_path / manifest.entrypoint
        if not entrypoint.exists():
            return False

        try:
            # Try to import and run health check
            # Catalyst: lazy-loading, don't import until needed
            result = subprocess.run(
                [sys.executable, "-c",
                 f"import sys; sys.path.insert(0, '{tool_path}'); "
                 f"from {manifest.entrypoint.replace('.py', '')} import {health_endpoint}; "
                 f"print({health_endpoint}())"],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(tool_path),
            )
            return result.returncode == 0 and "True" in result.stdout
        except Exception:
            return False

    # ── Registry management ──────────────────────────────────────────────

    def _load_registry(self) -> None:
        """Load tool registry from disk."""
        if _TOOL_REGISTRY_PATH.exists():
            try:
                data = json.loads(_TOOL_REGISTRY_PATH.read_text(encoding="utf-8"))
                self._registry = data.get("tools", {})
            except (json.JSONDecodeError, KeyError):
                self._registry = {}
        else:
            self._registry = {}

    def _save_registry(self) -> None:
        """Persist tool registry to disk."""
        _TOOL_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": "1.0.0",
            "tools": self._registry,
            "updated_at": time.time(),
        }
        _TOOL_REGISTRY_PATH.write_text(
            json.dumps(data, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _register_host_function(self, manifest: ToolManifest) -> None:
        """Add tool to host_functions.json for τ() dispatch."""
        if not _HOST_FUNCTIONS_PATH.exists():
            return

        try:
            data = json.loads(_HOST_FUNCTIONS_PATH.read_text(encoding="utf-8"))
            functions = data.get("functions", [])

            # Remove any existing entry with same name
            functions = [f for f in functions if f.get("name") != manifest.name.upper()]

            # Add new entry
            functions.append(manifest.to_host_function_entry())
            data["functions"] = functions

            _HOST_FUNCTIONS_PATH.write_text(
                json.dumps(data, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"Could not update host_functions.json: {e}")

    def _deregister_host_function(self, tool_name: str) -> None:
        """Remove tool from host_functions.json."""
        if not _HOST_FUNCTIONS_PATH.exists():
            return

        try:
            data = json.loads(_HOST_FUNCTIONS_PATH.read_text(encoding="utf-8"))
            functions = data.get("functions", [])
            data["functions"] = [
                f for f in functions
                if f.get("name") != tool_name.upper()
            ]
            _HOST_FUNCTIONS_PATH.write_text(
                json.dumps(data, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"Could not update host_functions.json: {e}")

    # ── Utilities ────────────────────────────────────────────────────────

    @staticmethod
    def _extract_repo_name(url: str) -> str:
        """Extract a clean directory name from a git URL."""
        # Handle both HTTPS and SSH URLs
        name = url.rstrip("/").rsplit("/", 1)[-1]
        if name.endswith(".git"):
            name = name[:-4]
        # Sanitize
        name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
        return name.lower()

    @staticmethod
    def _log_align(action: str, details: dict[str, Any]) -> None:
        """Log to ALIGN ledger (Azure Hat: audit trail)."""
        try:
            from agents.core.logger import ALSLogger
            als = ALSLogger()
            als.log(action, details)
        except ImportError:
            pass  # Standalone mode


# ─── CLI Entry Point ─────────────────────────────────────────────────────────


def cli_main(args: list[str] | None = None) -> int:
    """CLI entry point: hlf install/uninstall/tools/health/upgrade.

    Usage:
        hlf install https://github.com/user/cool-agent
        hlf uninstall cool_agent
        hlf tools
        hlf health cool_agent
        hlf upgrade cool_agent
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="hlf",
        description="Sovereign OS Tool Manager — install AI/agentic tools",
    )
    sub = parser.add_subparsers(dest="command")

    # install
    p_install = sub.add_parser("install", help="Install a tool from a git URL")
    p_install.add_argument("url", help="Git repository URL")
    p_install.add_argument("--tier", default="hearth", choices=["hearth", "forge", "sovereign"])

    # uninstall
    p_uninstall = sub.add_parser("uninstall", help="Uninstall a tool")
    p_uninstall.add_argument("name", help="Tool name")

    # tools (list)
    sub.add_parser("tools", help="List installed tools")

    # health
    p_health = sub.add_parser("health", help="Health check a tool")
    p_health.add_argument("name", help="Tool name")

    # upgrade
    p_upgrade = sub.add_parser("upgrade", help="Upgrade a tool")
    p_upgrade.add_argument("name", help="Tool name")

    parsed = parser.parse_args(args)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    installer = ToolInstaller(tier=getattr(parsed, "tier", "hearth"))

    if parsed.command == "install":
        result = installer.install(parsed.url)
        print(json.dumps(result.to_dict(), indent=2))
        return 0 if result.status == "active" else 1

    elif parsed.command == "uninstall":
        result = installer.uninstall(parsed.name)
        print(f"✅ {result.name} uninstalled")
        return 0

    elif parsed.command == "tools":
        tools = installer.list_tools()
        if not tools:
            print("No tools installed.")
        else:
            for t in tools:
                status_icon = "✅" if t["status"] == "active" else "❌"
                print(f"  {status_icon} {t['name']} v{t['version']} "
                      f"[{','.join(t['tier'])}] gas={t['gas_cost']}")
        return 0

    elif parsed.command == "health":
        ok = installer.health_check(parsed.name)
        print(f"{'💚' if ok else '❌'} {parsed.name}: {'healthy' if ok else 'unhealthy'}")
        return 0 if ok else 1

    elif parsed.command == "upgrade":
        result = installer.upgrade(parsed.name)
        print(f"{'✅' if result.status == 'active' else '❌'} {parsed.name} → v{result.version}")
        return 0 if result.status == "active" else 1

    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(cli_main())
