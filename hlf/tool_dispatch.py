"""
HLF Tool Dispatch Bridge — connects installed tools to the τ() runtime.

Bridges the gap between:
  - The tool registry (governance/tool_registry.json)
  - The host function dispatch (runtime.py HostFunctionRegistry)
  - The HLF τ(TOOL_NAME) syntax in the interpreter

Catalyst Hat: Lazy-loading — tool code is NOT imported until first invocation.
This keeps boot time constant regardless of installed tool count.

Usage (internal to runtime)::

    bridge = ToolDispatchBridge(tools_dir="./tools/installed")
    result = bridge.dispatch("my_tool", {"input": "hello"})

Or register with HostFunctionRegistry::

    bridge = ToolDispatchBridge()
    bridge.register_all(host_registry)
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TOOL_REGISTRY_PATH = _PROJECT_ROOT / "governance" / "tool_registry.json"
_DEFAULT_TOOLS_DIR = _PROJECT_ROOT / "tools" / "installed"


@dataclass
class ToolDispatchResult:
    """Result from dispatching a tool call."""

    tool: str
    success: bool
    value: Any = None
    error: str | None = None
    gas_used: int = 0
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "success": self.success,
            "value": self.value,
            "error": self.error,
            "gas_used": self.gas_used,
            "duration_ms": self.duration_ms,
        }


class ToolDispatchBridge:
    """Bridge between installed tools and the HLF runtime dispatch.

    Lazy-loads tool modules on first invocation (Catalyst Hat requirement).
    Caches loaded modules for subsequent calls.
    Respects ACFS sandbox permissions.
    """

    def __init__(self, tools_dir: Path | str | None = None):
        self.tools_dir = Path(tools_dir) if tools_dir else _DEFAULT_TOOLS_DIR
        self._registry: dict[str, dict[str, Any]] = {}
        self._loaded_modules: dict[str, Any] = {}

        self._load_registry()

    def dispatch(self, tool_name: str, args: dict[str, Any]) -> ToolDispatchResult:
        """Dispatch a call to an installed tool.

        Lazy-loads the tool's Python module on first call, then invokes
        the tool's registered function with the given arguments.
        """
        entry = self._registry.get(tool_name)
        if not entry:
            return ToolDispatchResult(
                tool=tool_name,
                success=False,
                error=f"Tool '{tool_name}' not found in registry",
            )

        # Check tool status
        if entry.get("status") != "active":
            return ToolDispatchResult(
                tool=tool_name,
                success=False,
                error=f"Tool '{tool_name}' is not active (status: {entry.get('status')})",
            )

        start = time.time()
        try:
            # Lazy-load the tool module
            module = self._load_tool_module(tool_name, entry)

            # Get the function to call
            func_name = entry.get("function", "run")
            func = getattr(module, func_name, None)
            if not func:
                return ToolDispatchResult(
                    tool=tool_name,
                    success=False,
                    error=f"Tool '{tool_name}' has no function '{func_name}'",
                )

            # Call the tool function
            result = func(**args)

            duration_ms = (time.time() - start) * 1000

            # Handle different return types
            if hasattr(result, "success"):
                # Tool returns a ToolResult-like object
                return ToolDispatchResult(
                    tool=tool_name,
                    success=result.success,
                    value=getattr(result, "value", None),
                    error=getattr(result, "error", None),
                    gas_used=entry.get("gas_cost", 1),
                    duration_ms=round(duration_ms, 2),
                )
            elif isinstance(result, dict):
                return ToolDispatchResult(
                    tool=tool_name,
                    success=True,
                    value=result,
                    gas_used=entry.get("gas_cost", 1),
                    duration_ms=round(duration_ms, 2),
                )
            else:
                return ToolDispatchResult(
                    tool=tool_name,
                    success=True,
                    value=str(result),
                    gas_used=entry.get("gas_cost", 1),
                    duration_ms=round(duration_ms, 2),
                )

        except Exception as e:
            duration_ms = (time.time() - start) * 1000
            logger.error(f"Tool '{tool_name}' dispatch failed: {e}")
            return ToolDispatchResult(
                tool=tool_name,
                success=False,
                error=str(e),
                duration_ms=round(duration_ms, 2),
            )

    def register_all(self, host_registry: Any) -> int:
        """Register all active installed tools with a HostFunctionRegistry.

        Creates backend dispatchers so τ(TOOL_NAME) calls are routed
        through this bridge.

        Args:
            host_registry: HostFunctionRegistry instance from runtime.py

        Returns:
            Number of tools registered
        """
        count = 0
        for tool_name, entry in self._registry.items():
            if entry.get("status") != "active":
                continue

            backend_name = f"tool:{tool_name}"

            def make_dispatcher(tn: str):
                """Closure to capture tool_name for each dispatcher."""
                def dispatcher(func_name: str, call_args: dict) -> Any:
                    result = self.dispatch(tn, call_args)
                    if result.success:
                        return result.value
                    raise RuntimeError(f"Tool {tn} failed: {result.error}")
                return dispatcher

            host_registry.register_dispatcher(backend_name, make_dispatcher(tool_name))
            count += 1
            logger.info(f"Registered tool dispatcher: {backend_name}")

        return count

    def list_active(self) -> list[str]:
        """Return names of all active tools."""
        return [
            name for name, entry in self._registry.items()
            if entry.get("status") == "active"
        ]

    def get_tool_info(self, tool_name: str) -> dict[str, Any] | None:
        """Get info about a specific tool."""
        return self._registry.get(tool_name)

    # ── Internal ─────────────────────────────────────────────────────────

    def _load_registry(self) -> None:
        """Load tool registry from governance/tool_registry.json."""
        if _TOOL_REGISTRY_PATH.exists():
            try:
                data = json.loads(_TOOL_REGISTRY_PATH.read_text(encoding="utf-8"))
                self._registry = data.get("tools", {})
            except (json.JSONDecodeError, KeyError):
                self._registry = {}

    def _load_tool_module(self, tool_name: str, entry: dict[str, Any]) -> Any:
        """Lazy-load a tool's Python module.

        Catalyst Hat: only loads when first invoked, not at boot time.
        Cached after first load.
        """
        if tool_name in self._loaded_modules:
            return self._loaded_modules[tool_name]

        install_path = Path(entry.get("install_path", ""))
        entrypoint = entry.get("entrypoint", "main.py")
        module_file = install_path / entrypoint

        if not module_file.exists():
            raise FileNotFoundError(f"Tool entrypoint not found: {module_file}")

        # Use importlib to load the module from its path
        module_name = f"_tool_{tool_name}"
        spec = importlib.util.spec_from_file_location(module_name, module_file)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load tool module: {module_file}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module

        # Add tool directory to sys.path temporarily for imports
        tool_dir = str(install_path)
        if tool_dir not in sys.path:
            sys.path.insert(0, tool_dir)

        try:
            spec.loader.exec_module(module)
        except Exception as e:
            # Clean up on failure
            sys.modules.pop(module_name, None)
            raise ImportError(f"Failed to load tool '{tool_name}': {e}")

        self._loaded_modules[tool_name] = module
        logger.info(f"Lazy-loaded tool module: {tool_name} from {module_file}")
        return module
