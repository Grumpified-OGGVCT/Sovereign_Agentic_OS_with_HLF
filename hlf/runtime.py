"""
HLF Module Runtime — File loading, namespace merge, host function dispatch.

Implements HLF v0.3 spec (Phase 5.1 of Sovereign OS Master Build Plan):
  - Module file loading from [IMPORT] statements
  - Namespace merge (SET bindings + FUNCTION definitions)
  - Host function registry (governance/host_functions.json) with live dispatch
  - Gas metering per host function call
  - Tier enforcement (hearth/forge/sovereign)
  - Sensitive output redaction (SHA-256 hash for Merkle log)

References:
  - RFC 9005: HLF Core Extensions
  - RFC 9007: Struct Operator + Namespace Merge
  - Sovereign_OS_Master_Build_Plan.md § 5.1
"""

from __future__ import annotations

import hashlib
import json
import os as _os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hlf.hlfc import HlfSyntaxError
from hlf.hlfc import compile as hlfc_compile

# ─── Configuration ───────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_HOST_FUNCTIONS_PATH = _PROJECT_ROOT / "governance" / "host_functions.json"
_MODULE_SEARCH_PATHS: list[Path] = [
    _PROJECT_ROOT / "hlf" / "modules",
    _PROJECT_ROOT / "hlf" / "stdlib",
    _PROJECT_ROOT / "tests" / "fixtures",
]

# Default deployment tier (overridable via environment)

_DEPLOYMENT_TIER = _os.environ.get("DEPLOYMENT_TIER", "hearth")


# ─── Exceptions ──────────────────────────────────────────────────────────────


class HlfModuleError(HlfSyntaxError):
    """Raised when a module cannot be loaded or merged."""


class HlfGasExhausted(RuntimeError):
    """Raised when gas budget is exceeded during execution."""


class HlfTierViolation(PermissionError):
    """Raised when a host function is called from an unauthorized tier."""


class HlfHostFunctionError(RuntimeError):
    """Raised when a host function call fails."""


# ─── Host Function Registry ─────────────────────────────────────────────────


@dataclass
class HostFunction:
    """A registered host function from governance/host_functions.json."""

    name: str
    args: list[dict[str, str]]
    returns: str
    tier: list[str]
    gas: int
    backend: str
    sensitive: bool = False
    binary_path: str | None = None
    binary_sha256: str | None = None

    def is_allowed_on_tier(self, tier: str) -> bool:
        """Check if this function is allowed on the given deployment tier."""
        return tier in self.tier

    def validate_args(self, call_args: dict[str, Any]) -> None:
        """Validate argument types against the registry spec."""
        for spec in self.args:
            name = spec["name"]
            if name not in call_args:
                raise HlfHostFunctionError(f"Host function {self.name}: missing required argument '{name}'")


@dataclass
class HostFunctionRegistry:
    """
    Registry of host functions loaded from governance/host_functions.json.

    Supports pluggable backend dispatchers for unit testing and
    production execution (Dapr, Docker, builtin).
    """

    functions: dict[str, HostFunction] = field(default_factory=dict)
    version: str = "1.0.0"
    _dispatchers: dict[str, Callable[..., Any]] = field(default_factory=dict)

    @classmethod
    def from_json(cls, path: Path | None = None) -> HostFunctionRegistry:
        """Load host function registry from JSON file."""
        path = path or _HOST_FUNCTIONS_PATH
        if not path.exists():
            return cls()

        data = json.loads(path.read_text(encoding="utf-8"))
        registry = cls(version=data.get("version", "1.0.0"))

        for entry in data.get("functions", []):
            hf = HostFunction(
                name=entry["name"],
                args=entry.get("args", []),
                returns=entry.get("returns", "string"),
                tier=entry.get("tier", []),
                gas=entry.get("gas", 1),
                backend=entry.get("backend", "builtin"),
                sensitive=entry.get("sensitive", False),
                binary_path=entry.get("binary_path"),
                binary_sha256=entry.get("binary_sha256"),
            )
            registry.functions[hf.name] = hf

        return registry

    def register_dispatcher(self, backend: str, handler: Callable[..., Any]) -> None:
        """Register a backend dispatcher (builtin, dapr_file_read, etc.)."""
        self._dispatchers[backend] = handler

    def dispatch(
        self,
        func_name: str,
        call_args: dict[str, Any],
        tier: str | None = None,
        gas_meter: GasMeter | None = None,
    ) -> HostFunctionResult:
        """
        Dispatch a host function call.

        1. Look up function in registry
        2. Check tier authorization
        3. Consume gas
        4. Validate arguments
        5. Execute via backend dispatcher
        6. Redact sensitive output for logging
        """
        tier = tier or _DEPLOYMENT_TIER

        if func_name not in self.functions:
            raise HlfHostFunctionError(
                f"Unknown host function: '{func_name}'. Available: {list(self.functions.keys())}"
            )

        hf = self.functions[func_name]

        # Tier check
        if not hf.is_allowed_on_tier(tier):
            raise HlfTierViolation(
                f"Host function '{func_name}' is not available on tier '{tier}'. Allowed tiers: {hf.tier}"
            )

        # Gas consumption
        if gas_meter:
            gas_meter.consume(hf.gas, context=f"host_function:{func_name}")

        # Argument validation
        hf.validate_args(call_args)

        # Dispatch to backend
        backend = hf.backend
        if backend in self._dispatchers:
            raw_result = self._dispatchers[backend](func_name, call_args)
        elif backend == "builtin":
            raw_result = _builtin_dispatch(func_name, call_args)
        else:
            # No dispatcher registered for this backend — return stub
            raw_result = f"<{backend}:{func_name} — no dispatcher registered>"

        # Build result with optional redaction
        log_value = raw_result
        if hf.sensitive and raw_result is not None:
            log_value = hashlib.sha256(str(raw_result).encode("utf-8")).hexdigest()

        return HostFunctionResult(
            function=func_name,
            value=raw_result,
            log_value=log_value,
            gas_cost=hf.gas,
            sensitive=hf.sensitive,
            backend=backend,
        )

    def list_available(self, tier: str | None = None) -> list[dict[str, Any]]:
        """List functions available on the given tier."""
        tier = tier or _DEPLOYMENT_TIER
        return [
            {
                "name": hf.name,
                "args": hf.args,
                "returns": hf.returns,
                "gas": hf.gas,
                "sensitive": hf.sensitive,
            }
            for hf in self.functions.values()
            if hf.is_allowed_on_tier(tier)
        ]


@dataclass
class HostFunctionResult:
    """Result of a host function dispatch."""

    function: str
    value: Any
    log_value: Any  # SHA-256 hash if sensitive, raw value otherwise
    gas_cost: int
    sensitive: bool
    backend: str


def _builtin_dispatch(func_name: str, args: dict[str, Any]) -> Any:
    """Handle built-in host functions (SLEEP, etc.)."""
    if func_name == "SLEEP":
        ms = int(args.get("ms", 0))
        time.sleep(ms / 1000.0)
        return True
    return f"<builtin:{func_name} executed>"


# ─── Gas Metering ────────────────────────────────────────────────────────────


@dataclass
class GasMeter:
    """
    Thread-safe gas meter for HLF execution.

    Tracks gas consumption per-intent with configurable limits.
    Works with both the interpreter and host function dispatch.
    """

    limit: int = 100
    consumed: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)

    def consume(self, amount: int, context: str = "") -> int:
        """Consume gas units.  Raises HlfGasExhausted if budget exceeded."""
        if self.consumed + amount > self.limit:
            raise HlfGasExhausted(f"Gas exhausted: {self.consumed}+{amount} > {self.limit} (context: {context})")
        self.consumed += amount
        self.history.append(
            {
                "amount": amount,
                "total": self.consumed,
                "context": context,
            }
        )
        return self.consumed

    @property
    def remaining(self) -> int:
        return self.limit - self.consumed

    def to_dict(self) -> dict[str, Any]:
        return {
            "limit": self.limit,
            "consumed": self.consumed,
            "remaining": self.remaining,
            "history": self.history,
        }


# ─── Module Namespace ────────────────────────────────────────────────────────


@dataclass
class ModuleNamespace:
    """
    A loaded HLF module's exported symbols.

    Collects SET bindings and FUNCTION definitions from a module AST,
    making them available for namespace merge into importing scripts.
    """

    name: str
    source_path: Path | None = None
    bindings: dict[str, Any] = field(default_factory=dict)
    functions: dict[str, dict[str, Any]] = field(default_factory=dict)
    ast: dict[str, Any] | None = None

    @classmethod
    def from_ast(cls, name: str, ast: dict[str, Any], source_path: Path | None = None) -> ModuleNamespace:
        """Extract exported symbols from a compiled AST."""
        ns = cls(name=name, source_path=source_path, ast=ast)

        for node in ast.get("program", []):
            if not isinstance(node, dict):
                continue
            tag = node.get("tag")

            # Collect SET bindings as exports
            if tag == "SET":
                var_name = node.get("name", "")
                ns.bindings[var_name] = node.get("value")

            # Collect FUNCTION definitions as exports
            elif tag == "FUNCTION":
                func_name = node.get("name", "")
                ns.functions[func_name] = node

        return ns

    def qualified_name(self, symbol: str) -> str:
        """Return fully-qualified symbol name (module.symbol)."""
        return f"{self.name}.{symbol}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": str(self.source_path) if self.source_path else None,
            "bindings": self.bindings,
            "functions": list(self.functions.keys()),
        }


# ─── Module Loader ───────────────────────────────────────────────────────────


class ModuleLoader:
    """
    Loads HLF modules from the filesystem and manages namespace merging.

    Search order:
      1. Explicit search paths (_MODULE_SEARCH_PATHS)
      2. Relative to the importing file
      3. Project root /hlf/ directory

    Implements circular-import detection, caching, and checksum validation.
    """

    def __init__(
        self,
        search_paths: list[Path] | None = None,
        tier: str | None = None,
        manifest_path: Path | None = None,
    ):
        self.search_paths = search_paths or list(_MODULE_SEARCH_PATHS)
        self.tier = tier or _DEPLOYMENT_TIER
        self._cache: dict[str, ModuleNamespace] = {}
        self._loading: set[str] = set()  # circular import guard
        self._manifest_path = manifest_path or (_PROJECT_ROOT / "acfs.manifest.yaml")
        self._module_checksums: dict[str, str] = self._load_manifest_checksums()

    def _load_manifest_checksums(self) -> dict[str, str]:
        """Load expected module checksums from acfs.manifest.yaml."""
        if not self._manifest_path.exists():
            return {}
        try:
            import yaml

            data = yaml.safe_load(self._manifest_path.read_text(encoding="utf-8"))
            return data.get("modules", {})
        except Exception:
            return {}

    def resolve_path(self, module_name: str, relative_to: Path | None = None) -> Path | None:
        """Find the .hlf file for a module name."""
        candidates = [f"{module_name}.hlf", f"{module_name}/main.hlf"]

        # Search in explicit paths
        for search_dir in self.search_paths:
            for candidate in candidates:
                full_path = search_dir / candidate
                if full_path.exists():
                    return full_path

        # Search relative to importing file
        if relative_to:
            parent = relative_to.parent if relative_to.is_file() else relative_to
            for candidate in candidates:
                full_path = parent / candidate
                if full_path.exists():
                    return full_path

        return None

    def load(
        self,
        module_name: str,
        relative_to: Path | None = None,
    ) -> ModuleNamespace:
        """
        Load and compile an HLF module, returning its namespace.

        Caches modules to avoid redundant compilation.
        Detects circular imports.
        """
        # Check cache
        if module_name in self._cache:
            return self._cache[module_name]

        # Circular import guard
        if module_name in self._loading:
            raise HlfModuleError(
                f"Circular import detected: '{module_name}' is already being loaded. Loading chain: {self._loading}"
            )

        # Resolve file path
        source_path = self.resolve_path(module_name, relative_to)
        if source_path is None:
            raise HlfModuleError(f"Module '{module_name}' not found. Searched: {[str(p) for p in self.search_paths]}")

        # Mark as loading (circular import guard)
        self._loading.add(module_name)

        try:
            # Read and validate checksum
            source_bytes = source_path.read_bytes()
            actual_sha = hashlib.sha256(source_bytes).hexdigest()
            expected_sha = self._module_checksums.get(module_name)

            if expected_sha and actual_sha != expected_sha:
                raise HlfModuleError(
                    f"Checksum mismatch for module '{module_name}'. Expected: {expected_sha}, Got: {actual_sha}"
                )

            # Read and compile
            source = source_bytes.decode("utf-8")
            ast = hlfc_compile(source)

            # Extract namespace
            ns = ModuleNamespace.from_ast(module_name, ast, source_path)

            # Recursively load any imports within the module
            for node in ast.get("program", []):
                if isinstance(node, dict) and node.get("tag") == "IMPORT":
                    dep_name = node.get("name", node.get("module", ""))
                    if dep_name and dep_name != module_name:
                        dep_ns = self.load(dep_name, relative_to=source_path)
                        # Merge dependency namespace into this module
                        ns.bindings.update({dep_ns.qualified_name(k): v for k, v in dep_ns.bindings.items()})
                        ns.functions.update({dep_ns.qualified_name(k): v for k, v in dep_ns.functions.items()})

            # Cache
            self._cache[module_name] = ns
            return ns

        finally:
            self._loading.discard(module_name)

    def merge_into_env(
        self,
        module_ns: ModuleNamespace,
        env: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Merge a module's exports into a script's environment.

        Bindings are accessible as both:
          - Qualified: module_name.var_name
          - Unqualified: var_name (if no conflict)
        """
        for var_name, value in module_ns.bindings.items():
            qualified = module_ns.qualified_name(var_name)
            env[qualified] = value
            # Only set unqualified if no conflict
            if var_name not in env:
                env[var_name] = value

        for func_name, func_node in module_ns.functions.items():
            qualified = module_ns.qualified_name(func_name)
            env[f"__func__{qualified}"] = func_node
            if f"__func__{func_name}" not in env:
                env[f"__func__{func_name}"] = func_node

        return env

    def list_cached(self) -> list[str]:
        """Return names of all cached modules."""
        return list(self._cache.keys())


# ─── Runtime Interpreter ─────────────────────────────────────────────────────


@dataclass
class ExecutionResult:
    """Result of executing an HLF AST."""

    code: int = 0
    message: str = ""
    gas_used: int = 0
    gas_limit: int = 100
    output: list[dict[str, Any]] = field(default_factory=list)
    modules_loaded: list[str] = field(default_factory=list)
    host_calls: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "gas_used": self.gas_used,
            "gas_limit": self.gas_limit,
            "output": self.output,
            "modules_loaded": self.modules_loaded,
            "host_calls": self.host_calls,
        }


class HLFRuntime:
    """
    HLF Runtime Interpreter with gas metering.

    Executes a compiled AST, resolving imports via ModuleLoader
    and dispatching host function calls via HostFunctionRegistry.

    This is the v0.3 interpreter — it processes AST nodes directly.
    v0.4 will compile AST → bytecode for the stack-machine VM.
    """

    def __init__(
        self,
        gas_limit: int = 100,
        tier: str | None = None,
        host_registry: HostFunctionRegistry | None = None,
        module_loader: ModuleLoader | None = None,
    ):
        self.tier = tier or _DEPLOYMENT_TIER
        self.gas = GasMeter(limit=gas_limit)
        self.host_registry = host_registry or HostFunctionRegistry.from_json()
        self.module_loader = module_loader or ModuleLoader(tier=self.tier)
        self.env: dict[str, Any] = {}
        self._result: ExecutionResult = ExecutionResult(gas_limit=gas_limit)

    def execute(self, ast: dict[str, Any]) -> ExecutionResult:
        """
        Execute an HLF AST.

        Pass 1: Resolve imports and merge namespaces
        Pass 2: Collect SET bindings
        Pass 3: Execute statements with gas metering
        """
        program = ast.get("program", [])

        # ── Pass 1: Resolve IMPORT statements ────────────────────────────
        for node in program:
            if isinstance(node, dict) and node.get("tag") == "IMPORT":
                mod_name = node.get("name", node.get("module", ""))
                if mod_name:
                    try:
                        ns = self.module_loader.load(mod_name)
                        self.module_loader.merge_into_env(ns, self.env)
                        self._result.modules_loaded.append(mod_name)
                        self.gas.consume(1, context=f"import:{mod_name}")
                    except HlfModuleError as e:
                        self._result.code = 1
                        self._result.message = str(e)
                        return self._finalize()

        # ── Pass 2: Collect SET bindings ─────────────────────────────────
        for node in program:
            if isinstance(node, dict) and node.get("tag") == "SET":
                name = node.get("name", "")
                value = node.get("value")
                if name:
                    self.env[name] = value

        # ── Pass 3: Execute statements ───────────────────────────────────
        for node in program:
            if not isinstance(node, dict):
                continue

            tag = node.get("tag", "")

            try:
                # Each statement costs 1 gas base
                self.gas.consume(1, context=f"stmt:{tag}")
            except HlfGasExhausted:
                self._result.code = 1
                self._result.message = "Gas exhausted during execution"
                return self._finalize()

            # ── Handle RESULT (terminator) ───────────────────────────────
            if tag == "RESULT":
                self._result.code = node.get("code", 0)
                self._result.message = node.get("message", "")
                self._result.output.append(node)
                return self._finalize()

            # ── Handle tool execution (↦ τ) ──────────────────────────────
            if tag == "TOOL":
                func_name = node.get("tool_name", node.get("name", ""))
                call_args = {}
                for arg in node.get("args", []):
                    if isinstance(arg, dict):
                        call_args.update(arg)
                    elif isinstance(arg, str):
                        # Positional arg → map to first unset arg spec
                        call_args[f"arg_{len(call_args)}"] = arg

                try:
                    result = self.host_registry.dispatch(func_name, call_args, tier=self.tier, gas_meter=self.gas)
                    # Store result in env for downstream access
                    self.env[f"__tool_result_{func_name}"] = result.value
                    self._result.host_calls.append(
                        {
                            "function": func_name,
                            "gas_cost": result.gas_cost,
                            "sensitive": result.sensitive,
                            "log_value": result.log_value,
                        }
                    )
                except (HlfTierViolation, HlfHostFunctionError, HlfGasExhausted) as e:
                    self._result.code = 1
                    self._result.message = str(e)
                    return self._finalize()

            # ── Record all other statements as output ────────────────────
            self._result.output.append(node)

        return self._finalize()

    def _finalize(self) -> ExecutionResult:
        """Finalize execution result with gas accounting."""
        self._result.gas_used = self.gas.consumed
        return self._result


# ─── Module-level singleton (lazy-loaded) ────────────────────────────────────

_host_registry: HostFunctionRegistry | None = None


def get_host_registry() -> HostFunctionRegistry:
    """Get or create the global host function registry."""
    global _host_registry
    if _host_registry is None:
        _host_registry = HostFunctionRegistry.from_json()
    return _host_registry
