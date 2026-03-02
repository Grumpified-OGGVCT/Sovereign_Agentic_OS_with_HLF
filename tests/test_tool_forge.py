"""
Tests for Phase 6 — HLF Tool Forge: Sandboxed Agent Tool Creation & Decentralized Sharing.

Covers:
- Loop detection (record_task_attempt / should_forge)
- forge_tool: early-exit when loop_count < 3
- forge_tool: full pipeline with mocked LLM response (AST + ALIGN + sandbox gates)
- forge_tool: rejection of code with forbidden AST patterns
- export_tool / import_tool round-trip (decentralized sharing)
- import_tool: SHA-256 integrity rejection
- import_tool: AST gate rejection
- list_tools: discovers persisted JSON bundles
- FORGE_TOOL host function dispatch
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def reset_tool_forge_state():
    """Reset in-memory state before each test to ensure isolation."""
    import agents.core.tool_forge as tf
    tf._registered_tools.clear()
    tf._task_loop_counter.clear()
    yield
    tf._registered_tools.clear()
    tf._task_loop_counter.clear()


@pytest.fixture
def safe_tool_code() -> str:
    """A minimal valid Python function that passes all validation gates."""
    return (
        'def tool_add_two_numbers(a, b):\n'
        '    """Add two numbers together."""\n'
        '    return a + b\n'
    )


@pytest.fixture
def forge_dir(tmp_path, monkeypatch) -> Path:
    """Redirect the tool forge storage directory to a temp path."""
    store = tmp_path / "data" / "tool_forge"
    store.mkdir(parents=True)
    monkeypatch.setenv("BASE_DIR", str(tmp_path))
    return store


# --------------------------------------------------------------------------- #
# Loop detection
# --------------------------------------------------------------------------- #

class TestLoopDetection:
    def test_record_increments_counter(self) -> None:
        from agents.core.tool_forge import record_task_attempt

        count = record_task_attempt("do the thing")
        assert count == 1
        count = record_task_attempt("do the thing")
        assert count == 2

    def test_should_forge_false_below_threshold(self) -> None:
        from agents.core.tool_forge import record_task_attempt, should_forge

        record_task_attempt("task one")
        record_task_attempt("task one")
        assert should_forge("task one") is False

    def test_should_forge_true_at_threshold(self) -> None:
        from agents.core.tool_forge import record_task_attempt, should_forge

        for _ in range(3):
            record_task_attempt("task two")
        assert should_forge("task two") is True

    def test_should_forge_custom_threshold(self) -> None:
        from agents.core.tool_forge import record_task_attempt, should_forge

        record_task_attempt("task five")
        assert should_forge("task five", threshold=1) is True

    def test_key_normalization(self) -> None:
        """Descriptions differing only in case/leading whitespace share the same key."""
        from agents.core.tool_forge import record_task_attempt, should_forge

        record_task_attempt("  My Task  ")
        record_task_attempt("my task")
        record_task_attempt("MY TASK")
        assert should_forge("my task") is True


# --------------------------------------------------------------------------- #
# forge_tool — early exit
# --------------------------------------------------------------------------- #

class TestForgeToolEarlyExit:
    def test_returns_empty_when_loop_count_lt_3(self) -> None:
        from agents.core.tool_forge import forge_tool

        assert forge_tool("describe something", loop_count=0) == {}
        assert forge_tool("describe something", loop_count=2) == {}

    def test_returns_cached_on_second_call(self, forge_dir: Path, safe_tool_code: str) -> None:
        """Second forge_tool call with same name returns the cached entry."""
        import agents.core.tool_forge as tf

        # "describe something" (18 chars) fits within the 20-char task slice;
        # after sanitization + underscore-collapse the name is "tool_describe_something"
        name = "tool_describe_something"
        tf._registered_tools[name] = {"name": name, "description": "x", "approved": True}
        result = tf.forge_tool("describe something", loop_count=3)
        assert result["name"] == name
        assert result["approved"] is True


# --------------------------------------------------------------------------- #
# forge_tool — full pipeline (mocked LLM)
# --------------------------------------------------------------------------- #

class TestForgeToolPipeline:
    def test_successful_forge(self, forge_dir: Path, safe_tool_code: str) -> None:
        """Happy-path: valid code passes all gates and is registered + persisted."""
        from agents.core.tool_forge import forge_tool

        with patch("agents.core.tool_forge._generate_via_llm", return_value=safe_tool_code):
            result = forge_tool("add two numbers together", loop_count=3)

        assert result != {}
        assert result["approved"] is True
        assert "sha256" in result
        assert result["sha256"] == hashlib.sha256(safe_tool_code.encode()).hexdigest()
        # Persisted to disk
        persisted = forge_dir / f"{result['name']}.json"
        assert persisted.exists()

    def test_ast_gate_rejects_forbidden_code(self, forge_dir: Path) -> None:
        """Code containing os.system must be rejected at Gate 1."""
        from agents.core.tool_forge import forge_tool

        bad_code = 'def tool_bad(*a): os.system("rm -rf /")'
        with patch("agents.core.tool_forge._generate_via_llm", return_value=bad_code):
            result = forge_tool("do something dangerous", loop_count=3)

        assert result == {}

    def test_align_gate_rejects_policy_violating_code(self, forge_dir: Path) -> None:
        """Code matching an ALIGN rule must be rejected at Gate 2."""
        from agents.core.tool_forge import forge_tool

        # R-004 blocks .env patterns
        bad_code = 'def tool_x():\n    """read .env"""\n    path = ".env"\n    return open(path).read()'
        with patch("agents.core.tool_forge._generate_via_llm", return_value=bad_code):
            result = forge_tool("read env file", loop_count=3)

        assert result == {}

    def test_sandbox_load_failure_returns_empty(self, forge_dir: Path) -> None:
        """If the generated code has a syntax error, sandbox load fails."""
        from agents.core.tool_forge import forge_tool

        bad_syntax = "def tool_broken(:\n    pass"
        with patch("agents.core.tool_forge._generate_via_llm", return_value=bad_syntax):
            result = forge_tool("something broken", loop_count=3)

        assert result == {}

    def test_metadata_fields(self, forge_dir: Path, safe_tool_code: str) -> None:
        """Tool metadata contains all required fields."""
        from agents.core.tool_forge import forge_tool

        with patch("agents.core.tool_forge._generate_via_llm", return_value=safe_tool_code):
            result = forge_tool("add two numbers together", loop_count=3)

        assert "name" in result
        assert "description" in result
        assert "code" in result
        assert "test" in result
        assert "sha256" in result
        assert "version" in result
        assert result["version"] == "1.0.0"


# --------------------------------------------------------------------------- #
# export_tool / import_tool (decentralized sharing)
# --------------------------------------------------------------------------- #

class TestDecentralizedSharing:
    def _make_bundle(self, safe_tool_code: str) -> dict[str, Any]:
        sha256 = hashlib.sha256(safe_tool_code.encode()).hexdigest()
        return {
            "name": "tool_shared_util",
            "description": "A utility shared between OS instances",
            "code": safe_tool_code,
            "test": "def test_x(): pass",
            "sha256": sha256,
            "version": "1.0.0",
        }

    def test_export_from_registry(self, forge_dir: Path, safe_tool_code: str) -> None:
        """export_tool returns the bundle for a registered tool."""
        import agents.core.tool_forge as tf

        sha256 = hashlib.sha256(safe_tool_code.encode()).hexdigest()
        tf._registered_tools["tool_shared_util"] = {
            "name": "tool_shared_util",
            "description": "test",
            "code": safe_tool_code,
            "sha256": sha256,
        }
        bundle = tf.export_tool("tool_shared_util")
        assert bundle["name"] == "tool_shared_util"
        assert bundle["sha256"] == sha256

    def test_export_from_disk(self, forge_dir: Path, safe_tool_code: str) -> None:
        """export_tool loads from disk when not in memory."""
        import agents.core.tool_forge as tf

        sha256 = hashlib.sha256(safe_tool_code.encode()).hexdigest()
        bundle_data = {"name": "tool_disk_util", "description": "d", "code": safe_tool_code, "sha256": sha256}
        (forge_dir / "tool_disk_util.json").write_text(json.dumps(bundle_data))

        bundle = tf.export_tool("tool_disk_util")
        assert bundle["name"] == "tool_disk_util"

    def test_export_unknown_tool_returns_empty(self, forge_dir: Path) -> None:
        from agents.core.tool_forge import export_tool

        assert export_tool("nonexistent_tool_xyz") == {}

    def test_import_valid_bundle(self, forge_dir: Path, safe_tool_code: str) -> None:
        """import_tool with a valid bundle registers and persists the tool."""
        from agents.core.tool_forge import import_tool

        bundle = self._make_bundle(safe_tool_code)
        result = import_tool(bundle)

        assert result != {}
        assert result["name"] == "tool_shared_util"
        assert result["approved"] is True
        # Must be persisted
        persisted = forge_dir / "tool_shared_util.json"
        assert persisted.exists()

    def test_import_wrong_sha256_rejected(self, forge_dir: Path, safe_tool_code: str) -> None:
        """import_tool rejects bundles whose sha256 does not match code."""
        from agents.core.tool_forge import import_tool

        bundle = self._make_bundle(safe_tool_code)
        bundle["sha256"] = "deadbeef" * 8  # wrong hash
        result = import_tool(bundle)
        assert result == {}

    def test_import_missing_name_rejected(self, forge_dir: Path, safe_tool_code: str) -> None:
        from agents.core.tool_forge import import_tool

        bundle = {"code": safe_tool_code, "sha256": hashlib.sha256(safe_tool_code.encode()).hexdigest()}
        result = import_tool(bundle)
        assert result == {}

    def test_import_forbidden_code_rejected(self, forge_dir: Path) -> None:
        """import_tool rejects bundles with AST violations."""
        from agents.core.tool_forge import import_tool

        bad_code = 'def tool_x(*a): os.system("id")'
        sha256 = hashlib.sha256(bad_code.encode()).hexdigest()
        bundle = {"name": "tool_evil", "code": bad_code, "sha256": sha256}
        result = import_tool(bundle)
        assert result == {}

    def test_export_import_roundtrip(self, forge_dir: Path, safe_tool_code: str) -> None:
        """forge → export → import round-trip preserves metadata."""
        import agents.core.tool_forge as tf
        from agents.core.tool_forge import export_tool, forge_tool, import_tool

        with patch("agents.core.tool_forge._generate_via_llm", return_value=safe_tool_code):
            forged = forge_tool("add two numbers together", loop_count=3)

        assert forged != {}
        name = forged["name"]

        # Simulate a fresh process by clearing memory
        tf._registered_tools.clear()

        bundle = export_tool(name)
        assert bundle != {}

        tf._registered_tools.clear()
        imported = import_tool(bundle)
        assert imported["name"] == name
        assert imported["sha256"] == forged["sha256"]


# --------------------------------------------------------------------------- #
# list_tools
# --------------------------------------------------------------------------- #

class TestListTools:
    def test_list_empty_by_default(self, forge_dir: Path) -> None:
        from agents.core.tool_forge import list_tools

        tools = list_tools()
        assert isinstance(tools, list)
        assert tools == []

    def test_list_discovers_persisted_tools(self, forge_dir: Path, safe_tool_code: str) -> None:
        """list_tools scans disk and returns tools persisted in previous runs."""
        from agents.core.tool_forge import list_tools

        sha256 = hashlib.sha256(safe_tool_code.encode()).hexdigest()
        bundle = {
            "name": "tool_persisted_a",
            "description": "first persisted tool",
            "code": safe_tool_code,
            "sha256": sha256,
        }
        (forge_dir / "tool_persisted_a.json").write_text(json.dumps(bundle))

        tools = list_tools()
        names = [t["name"] for t in tools]
        assert "tool_persisted_a" in names

    def test_list_includes_memory_tools(self, forge_dir: Path, safe_tool_code: str) -> None:
        """list_tools includes tools only in the in-memory registry (not yet persisted)."""
        import agents.core.tool_forge as tf
        from agents.core.tool_forge import list_tools

        sha256 = hashlib.sha256(safe_tool_code.encode()).hexdigest()
        tf._registered_tools["tool_mem_only"] = {
            "name": "tool_mem_only",
            "description": "memory only",
            "sha256": sha256,
        }
        tools = list_tools()
        names = [t["name"] for t in tools]
        assert "tool_mem_only" in names

    def test_list_tool_summary_fields(self, forge_dir: Path, safe_tool_code: str) -> None:
        """Each entry in list_tools() has name, description, sha256."""
        import agents.core.tool_forge as tf
        from agents.core.tool_forge import list_tools

        sha256 = hashlib.sha256(safe_tool_code.encode()).hexdigest()
        tf._registered_tools["tool_summary_test"] = {
            "name": "tool_summary_test",
            "description": "a test tool",
            "sha256": sha256,
        }
        tools = list_tools()
        entry = next(t for t in tools if t["name"] == "tool_summary_test")
        assert "name" in entry
        assert "description" in entry
        assert "sha256" in entry
        assert "code" not in entry  # code is NOT included in the summary


# --------------------------------------------------------------------------- #
# FORGE_TOOL host function dispatch
# --------------------------------------------------------------------------- #

class TestForgeToolHostFunction:
    def test_dispatch_forge_tool_no_task(self) -> None:
        """Empty task returns an error string."""

        # Patch dispatch to skip tier check by calling _tool_forge directly
        from agents.core import host_function_dispatcher as hfd

        result = hfd._tool_forge([])
        assert "FORGE_TOOL_ERROR" in result

    def test_dispatch_forge_tool_rejected(self) -> None:
        """When forge_tool returns {}, the host function returns a rejection message."""
        from agents.core import host_function_dispatcher as hfd

        with patch("agents.core.tool_forge.forge_tool", return_value={}):
            result = hfd._tool_forge(["some failing task"])

        assert "FORGE_TOOL_REJECTED" in result

    def test_dispatch_forge_tool_success(self, safe_tool_code: str) -> None:
        """Successful forge returns a JSON string with name and sha256."""
        from agents.core import host_function_dispatcher as hfd

        sha256 = hashlib.sha256(safe_tool_code.encode()).hexdigest()
        fake_meta = {"name": "tool_something", "sha256": sha256, "approved": True}
        with patch("agents.core.tool_forge.forge_tool", return_value=fake_meta):
            result = hfd._tool_forge(["build a utility"])

        data = json.loads(result)
        assert data["name"] == "tool_something"
        assert data["sha256"] == sha256

    def test_forge_tool_in_host_functions_json(self) -> None:
        """FORGE_TOOL must be registered in governance/host_functions.json."""
        import json
        from pathlib import Path

        path = Path(__file__).parent.parent / "governance" / "host_functions.json"
        registry = json.loads(path.read_text())
        names = [f["name"] for f in registry["functions"]]
        assert "FORGE_TOOL" in names

    def test_forge_tool_host_fn_metadata(self) -> None:
        """FORGE_TOOL entry must have correct gas, tier, and backend values."""
        import json
        from pathlib import Path

        path = Path(__file__).parent.parent / "governance" / "host_functions.json"
        registry = json.loads(path.read_text())
        fn = next(f for f in registry["functions"] if f["name"] == "FORGE_TOOL")
        assert fn["gas"] >= 5
        assert "forge" in fn["tier"] or "sovereign" in fn["tier"]
        assert fn["backend"] == "tool_forge"


# --------------------------------------------------------------------------- #
# ACFS manifest
# --------------------------------------------------------------------------- #

class TestACFSManifest:
    def test_tool_forge_dir_in_manifest(self) -> None:
        """data/tool_forge must appear in acfs.manifest.yaml."""
        from pathlib import Path

        import yaml

        manifest_path = Path(__file__).parent.parent / "acfs.manifest.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        paths = [d["path"] for d in manifest.get("directories", [])]
        assert "/data/tool_forge" in paths
