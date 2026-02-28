"""
Tests for Phase 5 v0.3 — HLF Module System and Two-Pass Parser.

Covers:
- [MODULE] and [IMPORT] tag grammar support
- Two-pass compiler: SET binding collection and ${VAR} expansion
- Immutable variable reassignment rejection
- Module/Import round-trip through formatter
"""
from __future__ import annotations

import pytest

from hlf.hlfc import compile, HlfSyntaxError


class TestModuleImportGrammar:
    """[MODULE] and [IMPORT] tag parsing (Phase 5 v0.3 grammar extension)."""

    def test_module_tag_parsed(self) -> None:
        src = "[HLF-v2]\n[MODULE] my_lib\n[INTENT] test \"hello\"\nΩ\n"
        ast = compile(src)
        tags = [n["tag"] for n in ast["program"]]
        assert "MODULE" in tags

    def test_import_tag_parsed(self) -> None:
        src = "[HLF-v2]\n[IMPORT] stdlib\n[INTENT] test \"hello\"\nΩ\n"
        ast = compile(src)
        tags = [n["tag"] for n in ast["program"]]
        assert "IMPORT" in tags

    def test_module_node_has_name(self) -> None:
        src = "[HLF-v2]\n[MODULE] my_module\n[INTENT] do \"something\"\nΩ\n"
        ast = compile(src)
        module_nodes = [n for n in ast["program"] if n["tag"] == "MODULE"]
        assert len(module_nodes) == 1
        assert module_nodes[0]["name"] == "my_module"

    def test_import_node_has_name(self) -> None:
        src = "[HLF-v2]\n[IMPORT] utils\n[INTENT] do \"something\"\nΩ\n"
        ast = compile(src)
        import_nodes = [n for n in ast["program"] if n["tag"] == "IMPORT"]
        assert len(import_nodes) == 1
        assert import_nodes[0]["name"] == "utils"

    def test_module_and_import_together(self) -> None:
        src = (
            "[HLF-v2]\n"
            "[MODULE] my_module\n"
            "[IMPORT] stdlib\n"
            "[IMPORT] net_utils\n"
            "[INTENT] process \"data\"\n"
            "Ω\n"
        )
        ast = compile(src)
        tags = [n["tag"] for n in ast["program"]]
        assert tags.count("MODULE") == 1
        assert tags.count("IMPORT") == 2

    def test_module_import_roundtrip(self) -> None:
        """MODULE/IMPORT survive format → re-parse round-trip."""
        from hlf.hlffmt import format_hlf

        src = "[HLF-v2]\n[MODULE] lib_a\n[IMPORT] lib_b\n[INTENT] do \"it\"\nΩ\n"
        formatted = format_hlf(src)
        re_ast = compile(formatted)
        tags = [n["tag"] for n in re_ast["program"]]
        assert "MODULE" in tags
        assert "IMPORT" in tags


class TestTwoPassCompiler:
    """Two-pass parser: Pass 1 (SET collection) + Pass 2 (${VAR} expansion)."""

    def test_set_binding_expanded_in_intent(self) -> None:
        src = (
            "[HLF-v2]\n"
            '[SET] target="/data/file.txt"\n'
            "[INTENT] read ${target}\n"
            "Ω\n"
        )
        ast = compile(src)
        # After Pass 2, ${target} in INTENT args must be expanded to "/data/file.txt"
        intent_nodes = [n for n in ast["program"] if n.get("tag") == "INTENT"]
        assert len(intent_nodes) == 1
        args_flat = str(intent_nodes[0]["args"])
        assert "/data/file.txt" in args_flat

    def test_set_binding_is_collected(self) -> None:
        src = (
            "[HLF-v2]\n"
            '[SET] filename="report.pdf"\n'
            "[INTENT] open \"report\"\n"
            "Ω\n"
        )
        ast = compile(src)
        set_nodes = [n for n in ast["program"] if n.get("tag") == "SET"]
        assert len(set_nodes) == 1
        assert set_nodes[0]["name"] == "filename"
        assert set_nodes[0]["value"] == "report.pdf"

    def test_immutable_reassignment_raises(self) -> None:
        src = (
            "[HLF-v2]\n"
            '[SET] x="first"\n'
            '[SET] x="second"\n'
            "[INTENT] do \"something\"\n"
            "Ω\n"
        )
        with pytest.raises(HlfSyntaxError, match="Immutable"):
            compile(src)

    def test_unexpanded_var_passes_gracefully(self) -> None:
        """${UNDEFINED} variables — grammar parses VAR_REF token, no error."""
        src = (
            "[HLF-v2]\n"
            "[INTENT] greet ${name}\n"
            "Ω\n"
        )
        ast = compile(src)
        assert ast["program"]  # parsed without exception
        # ${name} remains as-is (no SET binding defined)
        intent_nodes = [n for n in ast["program"] if n.get("tag") == "INTENT"]
        args_flat = str(intent_nodes[0]["args"])
        assert "${name}" in args_flat

    def test_pass2_preserves_non_string_args(self) -> None:
        """Numeric/bool args must not be corrupted by Pass 2 expansion."""
        src = (
            "[HLF-v2]\n"
            "[RESULT] code=0 message=\"ok\"\n"
            "Ω\n"
        )
        ast = compile(src)
        result_nodes = [n for n in ast["program"] if n.get("tag") == "RESULT"]
        assert len(result_nodes) == 1
        # code=0 must remain integer
        args = result_nodes[0]["args"]
        codes = [a.get("code") for a in args if isinstance(a, dict) and "code" in a]
        assert codes == [0]


class TestCanaryAgent:
    """Canary Agent — unit tests for probe and idle curiosity logic."""

    def test_idle_curiosity_scan_no_db(self, tmp_path, monkeypatch) -> None:
        """Scan should return empty list when DB doesn't exist yet."""
        from agents.core.canary_agent import _idle_curiosity_scan

        # Pass a non-existent db_path directly — no env patching needed
        result = _idle_curiosity_scan(db_path=tmp_path / "nonexistent.db")
        assert result == []

    def test_is_system_idle_immediately_false(self) -> None:
        """System is not immediately idle after recording activity."""
        from agents.gateway.router import record_intent_activity, is_system_idle

        record_intent_activity()
        assert is_system_idle(idle_threshold_sec=3600) is False

    def test_is_system_idle_with_long_elapsed(self) -> None:
        """System is idle if threshold is 0 seconds."""
        from agents.gateway.router import is_system_idle

        assert is_system_idle(idle_threshold_sec=0) is True


class TestGlobalGasBucket:
    """Global Per-Tier Gas Bucket (Phase 2.2)."""

    def test_replenish_and_consume(self) -> None:
        """replenish_gas + consume_gas (sync) logic is consistent."""
        from agents.gateway.router import consume_gas, replenish_gas, _TIER_GAS_CAPS

        class FakeRedis:
            def __init__(self):
                self._store = {}

            def get(self, key):
                return self._store.get(key)

            def set(self, key, value):
                self._store[key] = str(value)

            def eval(self, script, numkeys, *args):
                key = args[0]
                cost = int(args[1])
                cap = int(args[2])
                curr = self._store.get(key)
                if curr is None:
                    self._store[key] = str(cap - cost)
                    return cap - cost
                curr_val = int(curr)
                if curr_val < cost:
                    return -1
                self._store[key] = str(curr_val - cost)
                return curr_val - cost

        r = FakeRedis()
        # Initial call seeds the bucket
        ok = consume_gas("hearth", 1, r)
        assert ok is True

        # Drain bucket
        r._store["gas:hearth"] = "0"
        ok = consume_gas("hearth", 1, r)
        assert ok is False  # exhausted

        # Replenish
        replenish_gas("hearth", r)
        assert int(r._store["gas:hearth"]) == _TIER_GAS_CAPS["hearth"]

    def test_tier_caps_defined(self) -> None:
        from agents.gateway.router import _TIER_GAS_CAPS

        assert "hearth" in _TIER_GAS_CAPS
        assert "forge" in _TIER_GAS_CAPS
        assert "sovereign" in _TIER_GAS_CAPS
        assert _TIER_GAS_CAPS["sovereign"] > _TIER_GAS_CAPS["forge"] > _TIER_GAS_CAPS["hearth"]
