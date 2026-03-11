"""
Tests for HLF Module Runtime (hlf/runtime.py).

Covers:
  - Host function registry loading
  - Gas metering
  - Tier enforcement
  - Module file loading and namespace merge
  - Sensitive output redaction
  - HLFRuntime end-to-end execution
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from hlf.runtime import (
    GasMeter,
    HlfGasExhausted,
    HlfHostFunctionError,
    HlfModuleError,
    HLFRuntime,
    HlfTierViolation,
    HostFunctionRegistry,
    HostFunctionResult,
    ModuleLoader,
)

# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def host_registry() -> HostFunctionRegistry:
    """Build a test host function registry from the real governance file."""
    return HostFunctionRegistry.from_json()


@pytest.fixture()
def gas_meter() -> GasMeter:
    """Build a gas meter with a small limit for testing."""
    return GasMeter(limit=20)


@pytest.fixture()
def module_dir(tmp_path: Path) -> Path:
    """Create a temp directory with sample HLF modules."""
    mod_dir = tmp_path / "modules"
    mod_dir.mkdir()

    # Simple module with SET and FUNCTION
    (mod_dir / "math_utils.hlf").write_text(
        '[HLF-v2]\n[SET] pi = 3\n[FUNCTION] square x\n[RESULT] 0 "ok"\nΩ\n',
        encoding="utf-8",
    )

    # Module that imports another
    (mod_dir / "advanced.hlf").write_text(
        '[HLF-v2]\n[IMPORT] math_utils\n[SET] tau = 6\n[RESULT] 0 "ok"\nΩ\n',
        encoding="utf-8",
    )

    return mod_dir


# ─── Host Function Registry ─────────────────────────────────────────────────


class TestHostFunctionRegistry:
    """Tests for loading and querying host functions."""

    def test_load_from_json(self, host_registry: HostFunctionRegistry) -> None:
        """Registry loads all functions from governance/host_functions.json."""
        assert len(host_registry.functions) >= 7
        assert "READ" in host_registry.functions
        assert "WRITE" in host_registry.functions
        assert "SLEEP" in host_registry.functions
        assert "WEB_SEARCH" in host_registry.functions

    def test_registry_version(self, host_registry: HostFunctionRegistry) -> None:
        assert host_registry.version == "1.4.0"

    def test_function_attributes(self, host_registry: HostFunctionRegistry) -> None:
        read_fn = host_registry.functions["READ"]
        assert read_fn.gas == 1
        assert read_fn.sensitive is False
        assert "hearth" in read_fn.tier

    def test_web_search_is_sensitive(self, host_registry: HostFunctionRegistry) -> None:
        ws = host_registry.functions["WEB_SEARCH"]
        assert ws.sensitive is True
        assert ws.gas == 5

    def test_openclaw_summarize_exists(self, host_registry: HostFunctionRegistry) -> None:
        oc = host_registry.functions.get("OPENCLAW_SUMMARIZE")
        assert oc is not None
        assert oc.gas == 7
        assert oc.sensitive is True
        assert "hearth" not in oc.tier

    def test_list_available_filters_by_tier(self, host_registry: HostFunctionRegistry) -> None:
        hearth = host_registry.list_available("hearth")
        forge = host_registry.list_available("forge")
        assert len(forge) >= len(hearth)
        hearth_names = {f["name"] for f in hearth}
        forge_names = {f["name"] for f in forge}
        assert "SPAWN" not in hearth_names
        assert "SPAWN" in forge_names


# ─── Tier Enforcement ────────────────────────────────────────────────────────


class TestTierEnforcement:
    """Verify tier restrictions on host functions."""

    def test_spawn_blocked_on_hearth(self, host_registry: HostFunctionRegistry) -> None:
        with pytest.raises(HlfTierViolation, match="not available on tier 'hearth'"):
            host_registry.dispatch(
                "SPAWN",
                {"image": "test", "env": {}},
                tier="hearth",
            )

    def test_spawn_allowed_on_forge(self, host_registry: HostFunctionRegistry) -> None:
        result = host_registry.dispatch(
            "SPAWN",
            {"image": "test", "env": {}},
            tier="forge",
        )
        assert isinstance(result, HostFunctionResult)

    def test_unknown_function_raises(self, host_registry: HostFunctionRegistry) -> None:
        with pytest.raises(HlfHostFunctionError, match="Unknown host function"):
            host_registry.dispatch("NONEXISTENT", {}, tier="hearth")


# ─── Gas Metering ────────────────────────────────────────────────────────────


class TestGasMeter:
    """Verify gas consumption tracking and limits."""

    def test_basic_consumption(self, gas_meter: GasMeter) -> None:
        gas_meter.consume(5, "test")
        assert gas_meter.consumed == 5
        assert gas_meter.remaining == 15

    def test_gas_exhaustion_raises(self, gas_meter: GasMeter) -> None:
        gas_meter.consume(15, "bulk")
        with pytest.raises(HlfGasExhausted):
            gas_meter.consume(10, "overflow")

    def test_gas_history_tracked(self, gas_meter: GasMeter) -> None:
        gas_meter.consume(3, "step_1")
        gas_meter.consume(2, "step_2")
        assert len(gas_meter.history) == 2
        assert gas_meter.history[0]["context"] == "step_1"
        assert gas_meter.history[1]["total"] == 5

    def test_dispatch_consumes_gas(self, host_registry: HostFunctionRegistry) -> None:
        meter = GasMeter(limit=50)
        # Use READ (gas=1) — SLEEP has gas=0 so would not show consumption
        host_registry.register_dispatcher("dapr_file_read", lambda name, args: "test content")
        host_registry.dispatch("READ", {"path": "/test"}, tier="hearth", gas_meter=meter)
        assert meter.consumed == 1

    def test_to_dict(self, gas_meter: GasMeter) -> None:
        gas_meter.consume(7, "test")
        d = gas_meter.to_dict()
        assert d["limit"] == 20
        assert d["consumed"] == 7
        assert d["remaining"] == 13


# ─── Sensitive Output Redaction ──────────────────────────────────────────────


class TestSensitiveRedaction:
    """Verify that sensitive host function outputs are SHA-256 hashed."""

    def test_sensitive_output_is_hashed(self, host_registry: HostFunctionRegistry) -> None:
        host_registry.register_dispatcher(
            "dapr_http_proxy",
            lambda name, args: "SECRET_SEARCH_RESULTS_12345",
        )

        result = host_registry.dispatch(
            "WEB_SEARCH",
            {"query": "test query"},
            tier="forge",
        )

        assert result.value == "SECRET_SEARCH_RESULTS_12345"
        assert result.log_value != result.value
        assert len(result.log_value) == 64  # SHA-256 hex digest

    def test_non_sensitive_output_is_raw(self, host_registry: HostFunctionRegistry) -> None:
        host_registry.register_dispatcher(
            "dapr_file_read",
            lambda name, args: "file contents here",
        )

        result = host_registry.dispatch(
            "READ",
            {"path": "/test"},
            tier="hearth",
        )

        assert result.log_value == result.value


# ─── Module Loading ──────────────────────────────────────────────────────────


class TestModuleLoader:
    """Test HLF module file loading and namespace merge."""

    def test_load_simple_module(self, module_dir: Path) -> None:
        loader = ModuleLoader(search_paths=[module_dir])
        ns = loader.load("math_utils")

        assert ns.name == "math_utils"
        assert "pi" in ns.bindings
        assert ns.bindings["pi"] == 3
        assert "square" in ns.functions

    def test_load_module_with_import(self, module_dir: Path) -> None:
        loader = ModuleLoader(search_paths=[module_dir])
        ns = loader.load("advanced")

        assert "tau" in ns.bindings
        assert ns.bindings["tau"] == 6
        assert "math_utils.pi" in ns.bindings

    def test_module_not_found_raises(self, module_dir: Path) -> None:
        loader = ModuleLoader(search_paths=[module_dir])
        with pytest.raises(HlfModuleError, match="not found"):
            loader.load("nonexistent_module")

    def test_circular_import_detected(self, tmp_path: Path) -> None:
        mod_dir = tmp_path / "circular"
        mod_dir.mkdir()

        (mod_dir / "a.hlf").write_text(
            '[HLF-v2]\n[IMPORT] b\n[RESULT] 0 "ok"\nΩ\n',
            encoding="utf-8",
        )
        (mod_dir / "b.hlf").write_text(
            '[HLF-v2]\n[IMPORT] a\n[RESULT] 0 "ok"\nΩ\n',
            encoding="utf-8",
        )

        loader = ModuleLoader(search_paths=[mod_dir])
        with pytest.raises(HlfModuleError, match="Circular import"):
            loader.load("a")

    def test_module_caching(self, module_dir: Path) -> None:
        loader = ModuleLoader(search_paths=[module_dir])
        ns1 = loader.load("math_utils")
        ns2 = loader.load("math_utils")
        assert ns1 is ns2

    def test_namespace_merge_into_env(self, module_dir: Path) -> None:
        loader = ModuleLoader(search_paths=[module_dir])
        ns = loader.load("math_utils")

        env: dict[str, Any] = {}
        loader.merge_into_env(ns, env)

        assert env["pi"] == 3
        assert env["math_utils.pi"] == 3

    def test_module_checksum_validation(self, tmp_path: Path) -> None:
        mod_dir = tmp_path / "secure_modules"
        mod_dir.mkdir()
        mod_file = mod_dir / "secure.hlf"
        content = "[HLF-v2]\n[SET] secret = 42\nΩ\n"
        content_bytes = content.encode("utf-8")
        mod_file.write_bytes(content_bytes)

        sha256 = hashlib.sha256(content_bytes).hexdigest()

        manifest_file = tmp_path / "manifest.yaml"
        manifest_file.write_text(f"modules:\n  secure: {sha256}\n", encoding="utf-8")

        loader = ModuleLoader(search_paths=[mod_dir], manifest_path=manifest_file)
        # Should load successfully
        ns = loader.load("secure")
        assert ns.bindings["secret"] == 42

        # Tamper with the file
        mod_file.write_bytes("[HLF-v2]\n[SET] secret = 666\nΩ\n".encode())

        # Clear cache and reload
        loader._cache.clear()
        with pytest.raises(HlfModuleError, match="Checksum mismatch"):
            loader.load("secure")


# ─── HLF Runtime ─────────────────────────────────────────────────────────────


class TestHLFRuntime:
    """End-to-end runtime execution tests."""

    def test_simple_execution(self) -> None:
        ast = {
            "program": [
                {"tag": "INTENT", "args": ["greet", "world"], "human_readable": "Execute INTENT"},
                {"tag": "RESULT", "code": 0, "message": "ok", "human_readable": "Return 0 ok"},
            ]
        }

        runtime = HLFRuntime(gas_limit=50)
        result = runtime.execute(ast)

        assert result.code == 0
        assert result.message == "ok"
        assert result.gas_used > 0

    def test_gas_exhaustion_terminates(self) -> None:
        program = [{"tag": "INTENT", "args": [f"step_{i}"], "human_readable": f"Step {i}"} for i in range(20)]
        ast = {"program": program}

        runtime = HLFRuntime(gas_limit=5)
        result = runtime.execute(ast)

        assert result.code == 1
        assert "Gas exhausted" in result.message

    def test_set_bindings_collected(self) -> None:
        ast = {
            "program": [
                {"tag": "SET", "name": "x", "value": 42, "human_readable": "Set x=42"},
                {"tag": "RESULT", "code": 0, "message": "ok", "human_readable": "ok"},
            ]
        }

        runtime = HLFRuntime(gas_limit=50)
        runtime.execute(ast)

        assert runtime.env["x"] == 42

    def test_execution_result_to_dict(self) -> None:
        ast = {
            "program": [
                {"tag": "RESULT", "code": 0, "message": "done", "human_readable": "done"},
            ]
        }

        runtime = HLFRuntime(gas_limit=50)
        result = runtime.execute(ast)
        d = result.to_dict()

        assert d["code"] == 0
        assert d["gas_limit"] == 50
        assert d["gas_used"] > 0
        assert isinstance(d["modules_loaded"], list)

    def test_module_import_in_runtime(self, module_dir: Path) -> None:
        ast = {
            "program": [
                {"tag": "IMPORT", "name": "math_utils", "human_readable": "Import math_utils"},
                {"tag": "RESULT", "code": 0, "message": "ok", "human_readable": "ok"},
            ]
        }

        runtime = HLFRuntime(
            gas_limit=50,
            module_loader=ModuleLoader(search_paths=[module_dir]),
        )
        result = runtime.execute(ast)

        assert result.code == 0
        assert "math_utils" in result.modules_loaded
        assert runtime.env.get("pi") == 3


# ─── Expression Evaluator (Expanded) ─────────────────────────────────────────


class TestExprEvaluator:
    """Tests for the expanded _eval_expr function with new operators."""

    def test_modulo(self) -> None:
        from hlf.hlfrun import _eval_expr
        node = {"op": "MATH", "operator": "%", "left": 10, "right": 3}
        assert _eval_expr(node, {}) == 1

    def test_floor_division(self) -> None:
        from hlf.hlfrun import _eval_expr
        node = {"op": "MATH", "operator": "//", "left": 7, "right": 2}
        assert _eval_expr(node, {}) == 3

    def test_power(self) -> None:
        from hlf.hlfrun import _eval_expr
        node = {"op": "MATH", "operator": "**", "left": 2, "right": 10}
        assert _eval_expr(node, {}) == 1024

    def test_unary_neg(self) -> None:
        from hlf.hlfrun import _eval_expr
        node = {"op": "UNARY_NEG", "operand": 42}
        assert _eval_expr(node, {}) == -42

    def test_func_call_abs(self) -> None:
        from hlf.hlfrun import _eval_expr
        node = {"op": "FUNC_CALL", "name": "abs", "args": [-5]}
        assert _eval_expr(node, {}) == 5

    def test_func_call_max(self) -> None:
        from hlf.hlfrun import _eval_expr
        node = {"op": "FUNC_CALL", "name": "max", "args": [3, 7, 1]}
        assert _eval_expr(node, {}) == 7

    def test_func_call_sqrt(self) -> None:
        from hlf.hlfrun import _eval_expr
        node = {"op": "FUNC_CALL", "name": "sqrt", "args": [16]}
        assert _eval_expr(node, {}) == 4.0

    def test_func_call_sum(self) -> None:
        from hlf.hlfrun import _eval_expr
        node = {"op": "FUNC_CALL", "name": "sum", "args": [1, 2, 3, 4]}
        assert _eval_expr(node, {}) == 10

    def test_member_access(self) -> None:
        from hlf.hlfrun import _eval_expr
        scope = {"agent": {"hat": {"name": "Gold"}}}
        node = {"op": "MEMBER_ACCESS", "object": "agent", "path": ["hat", "name"]}
        assert _eval_expr(node, scope) == "Gold"

    def test_member_access_missing(self) -> None:
        from hlf.hlfrun import _eval_expr
        node = {"op": "MEMBER_ACCESS", "object": "x", "path": ["y"]}
        assert _eval_expr(node, {}) is None

    def test_division_by_zero_raises(self) -> None:
        from hlf.hlfrun import _eval_expr
        from hlf.hlfc import HlfRuntimeError
        node = {"op": "MATH", "operator": "/", "left": 10, "right": 0}
        with pytest.raises(HlfRuntimeError, match="Division by zero"):
            _eval_expr(node, {})

    def test_nested_math(self) -> None:
        from hlf.hlfrun import _eval_expr
        # (2 + 3) * 4 = 20
        inner = {"op": "MATH", "operator": "+", "left": 2, "right": 3}
        outer = {"op": "MATH", "operator": "*", "left": inner, "right": 4}
        assert _eval_expr(outer, {}) == 20

    def test_variable_resolve_in_math(self) -> None:
        from hlf.hlfrun import _eval_expr
        scope = {"x": 10}
        node = {"op": "MATH", "operator": "+", "left": "x", "right": 5}
        assert _eval_expr(node, scope) == 15


# ─── ASSERT / RETURN / WHILE Execution ────────────────────────────────────────


class TestRuntimeAssert:
    """Tests for ASSERT execution — Gold Hat verification gate."""

    def test_assert_pass(self) -> None:
        ast = {
            "program": [
                {"tag": "ASSERT", "args": [True, "condition_holds"], "human_readable": "Assert"},
                {"tag": "RESULT", "code": 0, "message": "ok", "human_readable": "ok"},
            ]
        }
        runtime = HLFRuntime(gas_limit=50)
        result = runtime.execute(ast)
        assert result.code == 0

    def test_assert_fail_raises(self) -> None:
        from hlf.hlfc import HlfRuntimeError
        ast = {
            "program": [
                {"tag": "ASSERT", "args": [False, "should_fail"], "human_readable": "Assert"},
            ]
        }
        runtime = HLFRuntime(gas_limit=50)
        result = runtime.execute(ast)
        # Runtime catches HlfRuntimeError and returns error code
        assert result.code != 0


class TestRuntimeReturn:
    """Tests for RETURN execution — value propagation."""

    def test_return_stores_value(self) -> None:
        ast = {
            "program": [
                {"tag": "RETURN", "args": [42], "human_readable": "Return 42"},
            ]
        }
        runtime = HLFRuntime(gas_limit=50)
        runtime.execute(ast)
        assert runtime.env.get("_RETURN_VALUE") == 42

    def test_return_terminates_with_code_zero(self) -> None:
        ast = {
            "program": [
                {"tag": "RETURN", "args": ["done"], "human_readable": "Return done"},
                {"tag": "INTENT", "args": ["should_not_run"], "human_readable": "skip"},
            ]
        }
        runtime = HLFRuntime(gas_limit=50)
        result = runtime.execute(ast)
        assert result.code == 0


class TestRuntimeWhile:
    """Tests for WHILE execution — Blue Hat process flow."""

    def test_while_false_condition_no_iterations(self) -> None:
        ast = {
            "program": [
                {"tag": "WHILE", "args": ["nonexistent_var", "body"], "human_readable": "While"},
                {"tag": "RESULT", "code": 0, "message": "ok", "human_readable": "ok"},
            ]
        }
        runtime = HLFRuntime(gas_limit=50)
        result = runtime.execute(ast)
        assert result.code == 0

