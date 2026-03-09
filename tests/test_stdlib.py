"""
Tests for HLF stdlib module loading.

Verifies that the stdlib modules exist, are valid HLF, and can be
resolved by the ModuleLoader search path system.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hlf.hlfc import compile as hlf_compile
from hlf.runtime import ModuleLoader

# Project root is two levels above tests/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STDLIB_DIR = _PROJECT_ROOT / "hlf" / "stdlib"
_MODULES_DIR = _PROJECT_ROOT / "hlf" / "modules"


# ── Directory existence ──────────────────────────────────────────────────────


class TestStdlibDirectories:
    """Verify stdlib and modules directories exist."""

    def test_stdlib_directory_exists(self) -> None:
        assert _STDLIB_DIR.exists(), f"hlf/stdlib/ directory not found at {_STDLIB_DIR}"
        assert _STDLIB_DIR.is_dir()

    def test_modules_directory_exists(self) -> None:
        assert _MODULES_DIR.exists(), f"hlf/modules/ directory not found at {_MODULES_DIR}"
        assert _MODULES_DIR.is_dir()


# ── Stdlib module inventory ──────────────────────────────────────────────────

EXPECTED_STDLIB_MODULES = ["math", "string", "io", "crypto", "collections"]


class TestStdlibInventory:
    """Verify all expected stdlib modules are present and valid HLF."""

    @pytest.mark.parametrize("module_name", EXPECTED_STDLIB_MODULES)
    def test_stdlib_module_exists(self, module_name: str) -> None:
        path = _STDLIB_DIR / f"{module_name}.hlf"
        assert path.exists(), f"Stdlib module '{module_name}.hlf' not found"

    @pytest.mark.parametrize("module_name", EXPECTED_STDLIB_MODULES)
    def test_stdlib_module_compiles(self, module_name: str) -> None:
        """Each stdlib module must be parseable by the HLF compiler."""
        path = _STDLIB_DIR / f"{module_name}.hlf"
        source = path.read_text(encoding="utf-8")
        ast = hlf_compile(source)
        assert ast is not None
        assert "program" in ast
        assert len(ast["program"]) > 0

    @pytest.mark.parametrize("module_name", EXPECTED_STDLIB_MODULES)
    def test_stdlib_module_has_module_tag(self, module_name: str) -> None:
        """Each stdlib module must declare its name via [MODULE] tag."""
        path = _STDLIB_DIR / f"{module_name}.hlf"
        source = path.read_text(encoding="utf-8")
        ast = hlf_compile(source)
        program = ast.get("program", [])
        module_nodes = [n for n in program if isinstance(n, dict) and n.get("tag") == "MODULE"]
        assert len(module_nodes) >= 1, f"No [MODULE] tag found in {module_name}.hlf"

    @pytest.mark.parametrize("module_name", EXPECTED_STDLIB_MODULES)
    def test_stdlib_module_has_functions(self, module_name: str) -> None:
        """Each stdlib module should declare at least one [FUNCTION]."""
        path = _STDLIB_DIR / f"{module_name}.hlf"
        source = path.read_text(encoding="utf-8")
        ast = hlf_compile(source)
        program = ast.get("program", [])
        fn_nodes = [n for n in program if isinstance(n, dict) and n.get("tag") == "FUNCTION"]
        assert len(fn_nodes) >= 1, f"No [FUNCTION] tags in {module_name}.hlf"


# ── ModuleLoader integration ─────────────────────────────────────────────────


class TestModuleLoaderResolves:
    """Test that ModuleLoader can resolve stdlib modules from search paths."""

    def test_resolve_math_module(self) -> None:
        loader = ModuleLoader(search_paths=[_STDLIB_DIR])
        path = loader.resolve_path("math")
        assert path is not None
        assert path.name == "math.hlf"

    def test_resolve_all_stdlib_modules(self) -> None:
        loader = ModuleLoader(search_paths=[_STDLIB_DIR])
        for mod_name in EXPECTED_STDLIB_MODULES:
            path = loader.resolve_path(mod_name)
            assert path is not None, f"ModuleLoader cannot resolve '{mod_name}'"

    def test_resolve_nonexistent_module(self) -> None:
        loader = ModuleLoader(search_paths=[_STDLIB_DIR])
        path = loader.resolve_path("nonexistent_module")
        assert path is None

    def test_load_math_module(self) -> None:
        loader = ModuleLoader(search_paths=[_STDLIB_DIR])
        ns = loader.load("math")
        assert ns is not None
        assert ns.name == "math"

    def test_module_caching(self) -> None:
        loader = ModuleLoader(search_paths=[_STDLIB_DIR])
        ns1 = loader.load("math")
        ns2 = loader.load("math")
        assert ns1 is ns2  # Same cached instance

    def test_load_all_stdlib(self) -> None:
        """Load every stdlib module to verify full compilation works."""
        loader = ModuleLoader(search_paths=[_STDLIB_DIR])
        for mod_name in EXPECTED_STDLIB_MODULES:
            ns = loader.load(mod_name)
            assert ns is not None, f"Failed to load stdlib module '{mod_name}'"
