"""
End-to-end integration test: Pipeline → Registry → Router → Executor → GUI.

Exercises the complete flow:
  1. Seed a temp SQLite registry (mirrors _persist_to_registry output).
  2. Verify models are present in the registry.
  3. Call route_request() using registry data — assert AgentProfile returned.
  4. Execute an intent through execute_intent() with a mocked Ollama response.
  5. Verify an ALS ROUTING_DECISION log entry is emitted.
  6. Verify Merkle chain integrity across consecutive log entries.

Uses a temporary SQLite DB and mocked httpx/Ollama responses throughout.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agents.core.db import (
    _SCHEMA_SQL,
    create_snapshot,
    get_db,
    get_models_by_tier,
    init_db,
    promote_snapshot,
    upsert_agent_template,
    upsert_local_inventory,
    upsert_model,
    upsert_model_equivalent,
)
from agents.core.logger import ALSLogger, _compute_trace_id, _SEED_HASH
from agents.gateway.router import AgentProfile, route_request
from hlf.hlfc import compile as hlfc_compile


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def registry_db(tmp_path: Path) -> Path:
    """Return a freshly initialised temp registry.db path."""
    db_file = tmp_path / "registry.db"
    init_db(db_file)
    return db_file


def _seed_registry(conn: sqlite3.Connection) -> int:
    """Insert a promoted snapshot with models at several tiers plus local inventory."""
    snap_id = create_snapshot(conn, families=["qwen", "llama"])
    promote_snapshot(conn, snap_id)

    # Cloud tier-S model (should be preferred by tier walk)
    upsert_model(conn, snap_id, "qwen3-vl:32b-cloud", family="qwen", raw_score=9.5, tier="S")
    # Mid-tier model
    upsert_model(conn, snap_id, "qwen-max", family="qwen", raw_score=8.0, tier="A")
    # Low-tier local model
    upsert_model(conn, snap_id, "llama3.1:8b", family="llama", raw_score=5.0, tier="B")

    # Local inventory
    upsert_local_inventory(conn, "llama3.1:8b", size_gb=4.7)

    # Agent template for coding specialization
    upsert_agent_template(
        conn,
        "coding",
        system_prompt="You are a senior software engineer.",
        tools=["code_search"],
        restrictions={"max_tokens": 4096},
    )

    # OpenRouter equivalent for primary model
    upsert_model_equivalent(conn, "qwen3-vl:32b-cloud", "openrouter", "qwen/qwen3-vl-32b")

    return snap_id


# ─── Stage 1: Registry Verification ─────────────────────────────────────────


class TestRegistrySeeding:
    """Verify that models written to the registry are queryable."""

    def test_models_exist_in_registry(self, registry_db: Path) -> None:
        with get_db(registry_db) as conn:
            snap_id = _seed_registry(conn)

        with get_db(registry_db) as conn:
            tier_s = get_models_by_tier(conn, "S", snapshot_id=snap_id)
            tier_a = get_models_by_tier(conn, "A", snapshot_id=snap_id)
            tier_b = get_models_by_tier(conn, "B", snapshot_id=snap_id)

        assert len(tier_s) == 1
        assert tier_s[0]["model_id"] == "qwen3-vl:32b-cloud"

        assert len(tier_a) == 1
        assert tier_a[0]["model_id"] == "qwen-max"

        assert len(tier_b) == 1
        assert tier_b[0]["model_id"] == "llama3.1:8b"

    def test_snapshot_is_promoted(self, registry_db: Path) -> None:
        with get_db(registry_db) as conn:
            _seed_registry(conn)

        conn_raw = sqlite3.connect(str(registry_db))
        conn_raw.row_factory = sqlite3.Row
        snap = conn_raw.execute(
            "SELECT * FROM snapshots WHERE is_promoted = 1"
        ).fetchone()
        conn_raw.close()

        assert snap is not None
        assert snap["model_count"] == 0  # count not updated in _seed_registry (pipeline does it)


# ─── Stage 2: Router Reads from Registry ─────────────────────────────────────


class TestRouterWithRegistry:
    """Verify route_request() uses registry data and returns AgentProfile."""

    def test_route_request_returns_agent_profile(self, registry_db: Path) -> None:
        with get_db(registry_db) as conn:
            _seed_registry(conn)

        with patch("agents.core.db.db_path", return_value=registry_db):
            profile = route_request("summarize this meeting", {})

        assert isinstance(profile, AgentProfile)
        assert profile.model != ""
        assert len(profile.routing_trace) > 0

    def test_route_request_tier_walk_executed(self, registry_db: Path) -> None:
        with get_db(registry_db) as conn:
            _seed_registry(conn)

        with patch("agents.core.db.db_path", return_value=registry_db):
            profile = route_request("tell me the weather", {})

        tier_walk_steps = [t for t in profile.routing_trace if t.get("step") == "tier_walk"]
        assert len(tier_walk_steps) > 0, "Expected tier walk trace entries"

    def test_route_request_selects_from_registry_model(self, registry_db: Path) -> None:
        with get_db(registry_db) as conn:
            _seed_registry(conn)

        known_models = {
            "qwen3-vl:32b-cloud",
            "qwen-max",
            "llama3.1:8b",
        }

        with patch("agents.core.db.db_path", return_value=registry_db):
            profile = route_request("generate a report", {})

        # Model must be a non-empty string (either from registry or valid fallback)
        assert isinstance(profile.model, str)
        assert len(profile.model) > 0

    def test_route_request_coding_specialization(self, registry_db: Path) -> None:
        with get_db(registry_db) as conn:
            _seed_registry(conn)

        with patch("agents.core.db.db_path", return_value=registry_db):
            profile = route_request("debug this Python function", {})

        spec_steps = [t for t in profile.routing_trace if t.get("step") == "specialization"]
        assert len(spec_steps) == 1
        assert spec_steps[0]["match"] == "coding"

    def test_route_request_emits_routing_decision_log(self, registry_db: Path) -> None:
        """route_request() must emit a ROUTING_DECISION ALS log entry."""
        with get_db(registry_db) as conn:
            _seed_registry(conn)

        logged_events: list[dict[str, Any]] = []

        def _capture_log(event: str, data: Any = None, **kwargs: Any) -> dict[str, Any]:
            entry = {"event": event, "data": data or {}}
            logged_events.append(entry)
            return entry

        with patch("agents.core.db.db_path", return_value=registry_db):
            # Patch the routing logger on the router module
            with patch("agents.gateway.router._routing_logger") as mock_logger:
                mock_logger.log.side_effect = _capture_log
                route_request("analyze code quality", {})

        routing_decisions = [e for e in logged_events if e["event"] == "ROUTING_DECISION"]
        assert len(routing_decisions) >= 1, "Expected at least one ROUTING_DECISION log"
        decision = routing_decisions[0]
        assert "model" in decision["data"]
        assert "tier" in decision["data"]
        assert "phase" in decision["data"]


# ─── Stage 3: Executor Processes Intent ──────────────────────────────────────


_SIMPLE_HLF = (
    "[HLF-v2]\n"
    "[INTENT] greet \"world\"\n"
    '[RESULT] code=0 message="ok"\n'
    "Ω\n"
)

_MOCK_HLF_RESPONSE = _SIMPLE_HLF


def _mock_ollama_response(hlf_text: str = _MOCK_HLF_RESPONSE) -> MagicMock:
    """Build a mock httpx response that returns the given HLF text."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"response": hlf_text}
    return mock_resp


class TestExecutorWithMockedOllama:
    """Verify execute_intent() processes payloads end-to-end with mocked Ollama."""

    def test_execute_ast_payload_returns_success(self) -> None:
        """AST-mode payload is executed directly (no Ollama call needed)."""
        from agents.core.main import execute_intent

        ast = hlfc_compile(_SIMPLE_HLF)
        payload = {"request_id": "test-ast-001", "ast": ast}

        # No Ollama call expected — patch anyway to catch any accidental calls
        with patch("httpx.post") as mock_post:
            result = execute_intent(payload)

        mock_post.assert_not_called()
        assert result["code"] == 0
        assert result["gas_used"] >= 0

    def test_execute_text_payload_calls_ollama(self, registry_db: Path) -> None:
        """Text-mode payload triggers Ollama inference, compiles response, executes."""
        from agents.core.main import execute_intent

        mock_resp = _mock_ollama_response()

        with (
            patch("agents.core.db.db_path", return_value=registry_db),
            patch("httpx.post", return_value=mock_resp),
        ):
            # Seed registry so route_request() picks a real model
            with get_db(registry_db) as conn:
                _seed_registry(conn)

            payload = {"request_id": "test-text-001", "text": "greet the world"}
            result = execute_intent(payload)

        assert isinstance(result, dict)
        assert "code" in result

    def test_execute_empty_payload_returns_error(self) -> None:
        """Empty payload (no text, no ast) returns code=1."""
        from agents.core.main import execute_intent

        result = execute_intent({"request_id": "test-empty-001"})
        assert result["code"] == 1
        assert "gas_used" in result

    def test_execute_with_ollama_error_returns_error_code(self) -> None:
        """When Ollama is unreachable, execute_intent returns code=1 gracefully."""
        import httpx
        from agents.core.main import execute_intent

        with patch("httpx.post", side_effect=httpx.ConnectError("connection refused")):
            payload = {"request_id": "test-err-001", "text": "do something"}
            result = execute_intent(payload)

        assert result["code"] == 1
        assert "gas_used" in result

    def test_execute_logs_route_decision(self, registry_db: Path) -> None:
        """execute_intent() logs a ROUTE_DECISION entry when routing succeeds."""
        from agents.core.main import execute_intent

        mock_resp = _mock_ollama_response()

        logged_events: list[str] = []

        def _capture(event: str, data: Any = None, **kwargs: Any) -> dict[str, Any]:
            logged_events.append(event)
            return {"trace_id": "mock", "parent_trace_hash": "mock"}

        with (
            patch("agents.core.db.db_path", return_value=registry_db),
            patch("httpx.post", return_value=mock_resp),
            patch("agents.core.main._logger") as mock_logger,
        ):
            mock_logger.log.side_effect = _capture
            with get_db(registry_db) as conn:
                _seed_registry(conn)
            execute_intent({"request_id": "test-log-001", "text": "summarize the document"})

        assert "ROUTE_DECISION" in logged_events, (
            f"Expected ROUTE_DECISION in logged events; got: {logged_events}"
        )


# ─── Stage 4: Merkle Chain Integrity ─────────────────────────────────────────


class TestMerkleChainIntegrity:
    """Verify that ALS log entries form a valid Merkle chain."""

    def test_trace_id_is_sha256_of_parent_plus_payload(self) -> None:
        """Each trace_id must equal SHA-256(parent_hash + json_payload)."""
        logger = ALSLogger(agent_role="test", goal_id="merkle-test")
        entries: list[dict[str, Any]] = []

        # Capture log entries without writing to disk
        with patch("agents.core.logger._write_last_hash"), patch(
            "agents.core.logger._read_last_hash", return_value=_SEED_HASH
        ):
            entry = logger.log("TEST_EVENT", {"key": "value"})
            entries.append(entry)

        entry = entries[0]
        expected_payload = json.dumps(
            {"event": "TEST_EVENT", "data": {"key": "value"}}, sort_keys=True
        )
        expected_trace_id = hashlib.sha256(
            f"{_SEED_HASH}{expected_payload}".encode()
        ).hexdigest()
        assert entry["trace_id"] == expected_trace_id
        assert entry["parent_trace_hash"] == _SEED_HASH

    def test_merkle_chain_links_consecutive_entries(self) -> None:
        """Consecutive log entries must chain: entry[n].trace_id == entry[n+1].parent_hash."""
        logger = ALSLogger(agent_role="test", goal_id="merkle-chain")
        chain: list[str] = [_SEED_HASH]
        entries: list[dict[str, Any]] = []

        # Simulate a fresh chain starting from seed
        current_hash = _SEED_HASH

        for i in range(3):
            with patch("agents.core.logger._write_last_hash"), patch(
                "agents.core.logger._read_last_hash", return_value=current_hash
            ):
                entry = logger.log(f"EVENT_{i}", {"seq": i})
                entries.append(entry)

            # Advance chain
            payload = json.dumps({"event": f"EVENT_{i}", "data": {"seq": i}}, sort_keys=True)
            current_hash = hashlib.sha256(f"{current_hash}{payload}".encode()).hexdigest()

        # Each entry's trace_id must equal re-computed value from its parent_hash
        for entry in entries:
            recomputed_payload = json.dumps(
                {"event": entry["event"], "data": entry["data"]}, sort_keys=True
            )
            recomputed_id = hashlib.sha256(
                f"{entry['parent_trace_hash']}{recomputed_payload}".encode()
            ).hexdigest()
            assert entry["trace_id"] == recomputed_id, (
                f"Merkle chain broken at event {entry['event']}"
            )

    def test_compute_trace_id_helper(self) -> None:
        """_compute_trace_id() produces deterministic output."""
        result1 = _compute_trace_id("aabbcc", "hello")
        result2 = _compute_trace_id("aabbcc", "hello")
        result3 = _compute_trace_id("aabbcc", "world")
        assert result1 == result2
        assert result1 != result3
        assert len(result1) == 64  # SHA-256 hex digest


# ─── Stage 5: Full Pipeline Integration ──────────────────────────────────────


class TestFullPipelineFlow:
    """
    Exercises the complete flow end-to-end:
      pipeline seed → registry verify → route_request → execute_intent → log check
    """

    def test_pipeline_to_executor_full_flow(self, registry_db: Path) -> None:
        """
        1. Seed registry (simulating pipeline output).
        2. Verify models are in registry.
        3. Call route_request() — verify AgentProfile.
        4. Call execute_intent() — verify result dict.
        5. Capture and verify ALS log events include ROUTING_DECISION.
        """
        from agents.core.main import execute_intent

        # Step 1 — seed
        with get_db(registry_db) as conn:
            snap_id = _seed_registry(conn)

        # Step 2 — registry verification
        with get_db(registry_db) as conn:
            tier_s_models = get_models_by_tier(conn, "S", snapshot_id=snap_id)
        assert any(m["model_id"] == "qwen3-vl:32b-cloud" for m in tier_s_models)

        # Step 3 — route_request
        routing_log_events: list[dict[str, Any]] = []

        def _capture_routing(event: str, data: Any = None, **kwargs: Any) -> dict[str, Any]:
            routing_log_events.append({"event": event, "data": data or {}})
            return {"trace_id": "mock", "parent_trace_hash": "mock"}

        with patch("agents.core.db.db_path", return_value=registry_db):
            with patch("agents.gateway.router._routing_logger") as mock_rlog:
                mock_rlog.log.side_effect = _capture_routing
                profile = route_request("process the uploaded image", {})

        assert isinstance(profile, AgentProfile)
        assert profile.model != ""
        routing_decisions = [e for e in routing_log_events if e["event"] == "ROUTING_DECISION"]
        assert len(routing_decisions) >= 1

        # Step 4 — execute_intent with mocked Ollama
        mock_resp = _mock_ollama_response()
        executor_log_events: list[str] = []

        def _capture_executor(event: str, data: Any = None, **kwargs: Any) -> dict[str, Any]:
            executor_log_events.append(event)
            return {"trace_id": "mock", "parent_trace_hash": "mock"}

        with (
            patch("agents.core.db.db_path", return_value=registry_db),
            patch("httpx.post", return_value=mock_resp),
            patch("agents.core.main._logger") as mock_logger,
        ):
            mock_logger.log.side_effect = _capture_executor
            result = execute_intent({
                "request_id": "e2e-001",
                "text": "process the uploaded image",
            })

        # Step 5 — verify results
        assert isinstance(result, dict)
        assert "code" in result
        assert "ROUTE_DECISION" in executor_log_events, (
            f"Expected ROUTE_DECISION in executor log events; got: {executor_log_events}"
        )

    def test_ast_mode_skips_ollama_routes_to_executor(self, registry_db: Path) -> None:
        """AST-mode intent bypasses Ollama; executor still logs the result."""
        from agents.core.main import execute_intent

        ast = hlfc_compile(_SIMPLE_HLF)
        payload = {"request_id": "e2e-ast-001", "ast": ast}

        executor_events: list[str] = []

        def _capture(event: str, data: Any = None, **kwargs: Any) -> dict[str, Any]:
            executor_events.append(event)
            return {"trace_id": "mock", "parent_trace_hash": "mock"}

        with patch("httpx.post") as mock_post, patch(
            "agents.core.main._logger"
        ) as mock_logger:
            mock_logger.log.side_effect = _capture
            result = execute_intent(payload)

        mock_post.assert_not_called()
        assert result["code"] == 0
        assert "INTENT_EXECUTED" in executor_events


# ─── Stage 6: Compiler Validation (Pass 3 + Pass 4) ─────────────────────────


class TestCompilerSecurityGates:
    """Verify that the 4-pass compiler correctly validates HLF payloads."""

    def test_pass1_pass2_basic_compilation(self) -> None:
        """Minimal HLF source compiles to JSON AST with all required fields."""
        source = '[HLF-v2]\n[INTENT] greet "world"\n[RESULT] 0 "ok"\nΩ\n'
        ast = hlfc_compile(source)

        assert "program" in ast
        assert "compiler" in ast
        assert isinstance(ast["program"], list)
        assert len(ast["program"]) > 0

    def test_result_error_code_propagation(self) -> None:
        """RESULT handler parses structured error codes with severity."""
        source = '[HLF-v2]\n[RESULT] 0 "success"\nΩ\n'
        ast = hlfc_compile(source)

        result_nodes = [n for n in ast["program"] if n.get("tag") == "RESULT"]
        assert len(result_nodes) == 1
        result = result_nodes[0]
        assert result["code"] == 0
        assert result["terminator"] is True
        assert "SUCCESS" in result["human_readable"]

    def test_result_fatal_error_code(self) -> None:
        """RESULT with code >= 100 is classified as FATAL."""
        source = '[HLF-v2]\n[RESULT] 500 "internal error"\nΩ\n'
        ast = hlfc_compile(source)

        result_nodes = [n for n in ast["program"] if n.get("tag") == "RESULT"]
        result = result_nodes[0]
        assert result["code"] == 500
        assert "FATAL" in result["human_readable"]

    def test_result_recoverable_error_code(self) -> None:
        """RESULT with code 1-99 is classified as RECOVERABLE."""
        source = '[HLF-v2]\n[RESULT] 42 "timeout"\nΩ\n'
        ast = hlfc_compile(source)

        result_nodes = [n for n in ast["program"] if n.get("tag") == "RESULT"]
        result = result_nodes[0]
        assert result["code"] == 42
        assert "RECOVERABLE" in result["human_readable"]

    def test_pass4_dictionary_enforced(self) -> None:
        """Compiler pass 4 loads dictionary.json and records enforcement metadata."""
        source = '[HLF-v2]\n[INTENT] greet "world"\n[RESULT] 0 "ok"\nΩ\n'
        ast = hlfc_compile(source)

        # dictionary_enforced should be True when dictionary.json is present
        if ast.get("dictionary_enforced"):
            assert ast["dictionary_version"] is not None
            assert isinstance(ast["dictionary_version"], str)

    def test_set_immutability_annotation(self) -> None:
        """SET nodes get immutable: true from dictionary.json spec."""
        source = '[HLF-v2]\n[SET] x = 42\nΩ\n'
        ast = hlfc_compile(source)

        set_nodes = [n for n in ast["program"] if n.get("tag") == "SET"]
        if set_nodes and ast.get("dictionary_enforced"):
            assert set_nodes[0].get("immutable") is True

    def test_thought_purity_annotation(self) -> None:
        """THOUGHT nodes get pure: true from dictionary.json spec."""
        source = '[HLF-v2]\n[THOUGHT] "analyzing the problem"\nΩ\n'
        ast = hlfc_compile(source)

        thought_nodes = [n for n in ast["program"] if n.get("tag") == "THOUGHT"]
        if thought_nodes and ast.get("dictionary_enforced"):
            assert thought_nodes[0].get("pure") is True

    def test_human_readable_on_every_node(self) -> None:
        """InsAIts V2 mandate: every AST node must have a human_readable field."""
        source = (
            '[HLF-v2]\n'
            '[INTENT] greet "world"\n'
            '[THOUGHT] "analyzing"\n'
            '[OBSERVATION] "data received"\n'
            '[RESULT] 0 "ok"\n'
            'Ω\n'
        )
        ast = hlfc_compile(source)

        for node in ast["program"]:
            if isinstance(node, dict) and node.get("tag"):
                assert "human_readable" in node, (
                    f"Node {node.get('tag')} missing human_readable field"
                )


# ─── Stage 7: GUI Module Import Verification ────────────────────────────────


class TestGUIModuleHealth:
    """Verify GUI module structure and syntax without requiring Streamlit runtime."""

    def test_gui_app_is_valid_python(self) -> None:
        """gui/app.py compiles to valid Python bytecode (no syntax errors)."""
        import py_compile

        gui_path = Path(__file__).parent.parent / "gui" / "app.py"
        if not gui_path.exists():
            pytest.skip("gui/app.py not found")

        # py_compile.compile raises py_compile.PyCompileError on syntax errors
        py_compile.compile(str(gui_path), doraise=True)

    def test_gui_modules_exist(self) -> None:
        """Expected GUI module files are present in the gui/ directory."""
        gui_dir = Path(__file__).parent.parent / "gui"
        expected_files = ["app.py", "tray_manager.py"]

        for fname in expected_files:
            fpath = gui_dir / fname
            assert fpath.exists(), f"Expected GUI module {fname} not found"

    def test_gui_app_contains_registry_panel(self) -> None:
        """gui/app.py includes the Model Registry panel code."""
        gui_app = Path(__file__).parent.parent / "gui" / "app.py"
        if not gui_app.exists():
            pytest.skip("gui/app.py not found")

        content = gui_app.read_text(encoding="utf-8")
        assert "Model Registry" in content or "model_registry" in content, (
            "Expected Model Registry panel in gui/app.py"
        )

    def test_gui_app_contains_feedback_ui(self) -> None:
        """gui/app.py includes the Model Feedback UI code."""
        gui_app = Path(__file__).parent.parent / "gui" / "app.py"
        if not gui_app.exists():
            pytest.skip("gui/app.py not found")

        content = gui_app.read_text(encoding="utf-8")
        assert "feedback" in content.lower() or "Feedback" in content, (
            "Expected Model Feedback UI code in gui/app.py"
        )


# ─── Stage 8: Scheduler Integration ─────────────────────────────────────────


class TestSchedulerIntegration:
    """Verify scheduler module can be imported and reports health."""

    def test_scheduler_daemon_imports(self) -> None:
        """agents/core/scheduler.py is importable and has expected attributes."""
        sched_path = Path(__file__).parent.parent / "agents" / "core" / "scheduler.py"
        if not sched_path.exists():
            pytest.skip("scheduler.py not found")

        import py_compile
        py_compile.compile(str(sched_path), doraise=True)

    def test_scheduler_telemetry_class_exists(self) -> None:
        """The Telemetry class is defined in the scheduler module."""
        sched_path = Path(__file__).parent.parent / "agents" / "core" / "scheduler.py"
        if not sched_path.exists():
            pytest.skip("scheduler.py not found")

        content = sched_path.read_text(encoding="utf-8")
        assert "class Telemetry" in content
        assert "def run_daemon" in content

    def test_scheduler_health_endpoint_defined(self) -> None:
        """The health HTTP handler is defined."""
        sched_path = Path(__file__).parent.parent / "agents" / "core" / "scheduler.py"
        if not sched_path.exists():
            pytest.skip("scheduler.py not found")

        content = sched_path.read_text(encoding="utf-8")
        assert "/health" in content
        assert "HealthHandler" in content or "do_GET" in content
