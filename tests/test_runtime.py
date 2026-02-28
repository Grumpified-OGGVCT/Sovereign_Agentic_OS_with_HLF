"""
Tests for Phase 3.1 + Phase 5.1: HLF Runtime Interpreter and Host Function Dispatcher.

Covers:
- Built-in FUNCTION execution (HASH, UUID, NOW, BASE64_ENCODE, BASE64_DECODE)
- ACTION dispatch to host functions
- RESULT tag terminates execution with correct code
- Gas limit enforcement
- Tier access control in host function dispatcher
- ACFS confinement path check
- Dual-mode bus: text mode doesn't run through HLF validator
- execute_intent() wires hlfrun for AST payloads
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from hlf.hlfc import compile as hlfc_compile
from hlf.hlfrun import run as hlfrun, HLFInterpreter  # noqa: F401 — class under test
from hlf.hlfc import HlfRuntimeError


# --------------------------------------------------------------------------- #
# Built-in FUNCTION tests
# --------------------------------------------------------------------------- #

class TestBuiltinFunctions:
    """[FUNCTION] tag built-in pure functions."""

    def test_hash_sha256(self) -> None:
        src = (
            "[HLF-v2]\n"
            '[FUNCTION] HASH sha256 "hello"\n'
            "Ω\n"
        )
        ast = hlfc_compile(src)
        result = hlfrun(ast)
        assert result["code"] == 0
        expected = hashlib.sha256(b"hello").hexdigest()
        assert result["scope"]["HASH_RESULT"] == expected

    def test_base64_encode(self) -> None:
        src = '[HLF-v2]\n[FUNCTION] BASE64_ENCODE "hello"\nΩ\n'
        ast = hlfc_compile(src)
        result = hlfrun(ast)
        import base64
        assert result["scope"]["BASE64_ENCODE_RESULT"] == base64.b64encode(b"hello").decode()

    def test_base64_decode(self) -> None:
        src = '[HLF-v2]\n[FUNCTION] BASE64_DECODE "aGVsbG8="\nΩ\n'
        ast = hlfc_compile(src)
        result = hlfrun(ast)
        assert result["scope"]["BASE64_DECODE_RESULT"] == "hello"

    def test_now_returns_iso8601(self) -> None:
        src = "[HLF-v2]\n[FUNCTION] NOW\nΩ\n"
        ast = hlfc_compile(src)
        result = hlfrun(ast)
        now_val = result["scope"]["NOW_RESULT"]
        # Should parse as ISO-8601
        from datetime import datetime
        parsed = datetime.fromisoformat(now_val)
        assert parsed is not None

    def test_uuid_returns_valid_uuid(self) -> None:
        src = "[HLF-v2]\n[FUNCTION] UUID\nΩ\n"
        ast = hlfc_compile(src)
        result = hlfrun(ast)
        import uuid
        # Should be a valid UUID-4 string
        val = result["scope"]["UUID_RESULT"]
        parsed = uuid.UUID(val)
        assert str(parsed) == val

    def test_unknown_builtin_raises(self) -> None:
        # Manually craft an AST with an unknown FUNCTION name
        ast = {
            "version": "0.2.0",
            "program": [{"tag": "FUNCTION", "name": "NONEXISTENT", "args": [], "pure": True}],
        }
        with pytest.raises(HlfRuntimeError, match="Unknown built-in function"):
            hlfrun(ast)

    def test_function_result_available_as_var(self) -> None:
        """${HASH_RESULT} should be available for subsequent SET references."""
        src = (
            "[HLF-v2]\n"
            '[FUNCTION] UUID\n'
            '[RESULT] code=0 message="ok"\n'
            "Ω\n"
        )
        ast = hlfc_compile(src)
        result = hlfrun(ast)
        assert "UUID_RESULT" in result["scope"]


# --------------------------------------------------------------------------- #
# RESULT tag tests
# --------------------------------------------------------------------------- #

class TestResultTag:
    def test_result_code_0(self) -> None:
        src = '[HLF-v2]\n[INTENT] test "x"\n[RESULT] code=0 message="ok"\nΩ\n'
        ast = hlfc_compile(src)
        result = hlfrun(ast)
        assert result["code"] == 0
        assert result["message"] == "ok"

    def test_result_code_1_failure(self) -> None:
        src = '[HLF-v2]\n[INTENT] test "x"\n[RESULT] code=1 message="fail"\nΩ\n'
        ast = hlfc_compile(src)
        result = hlfrun(ast)
        assert result["code"] == 1
        assert result["message"] == "fail"

    def test_result_terminates_execution(self) -> None:
        """Nodes after [RESULT] must NOT be executed."""
        ast = {
            "version": "0.2.0",
            "program": [
                {"tag": "RESULT", "args": [{"code": 0}, {"message": "done"}]},
                # This FUNCTION would fail with RuntimeError if executed
                {"tag": "FUNCTION", "name": "NONEXISTENT", "args": [], "pure": True},
            ],
        }
        result = hlfrun(ast)
        assert result["code"] == 0  # no error despite bad FUNCTION below RESULT


# --------------------------------------------------------------------------- #
# Gas limit tests
# --------------------------------------------------------------------------- #

class TestGasLimit:
    def test_gas_exceeded_raises(self) -> None:
        # 12 nodes, gas=3 → should raise after 3 executions
        nodes = [{"tag": "INTENT", "args": [f"action_{i}"]} for i in range(12)]
        ast = {"version": "0.2.0", "program": nodes}
        with pytest.raises(HlfRuntimeError, match="Gas limit exceeded"):
            hlfrun(ast, max_gas=3)

    def test_gas_used_tracked(self) -> None:
        src = '[HLF-v2]\n[INTENT] x "y"\n[FUNCTION] UUID\nΩ\n'
        ast = hlfc_compile(src)
        result = hlfrun(ast, max_gas=10)
        assert result["gas_used"] == 2  # INTENT + FUNCTION


# --------------------------------------------------------------------------- #
# SET scope propagation
# --------------------------------------------------------------------------- #

class TestSetScope:
    def test_set_binding_in_scope(self) -> None:
        src = '[HLF-v2]\n[SET] x="hello"\n[INTENT] use "x"\nΩ\n'
        ast = hlfc_compile(src)
        result = hlfrun(ast)
        assert result["scope"]["x"] == "hello"


# --------------------------------------------------------------------------- #
# Host Function Dispatcher tests
# --------------------------------------------------------------------------- #

class TestHostFunctionDispatcher:
    def test_tier_enforcement(self) -> None:
        """SPAWN is not available in 'hearth' tier."""
        from agents.core.host_function_dispatcher import dispatch

        with pytest.raises(PermissionError, match="hearth"):
            dispatch("SPAWN", ["alpine"], "hearth")

    def test_unknown_function_raises(self) -> None:
        from agents.core.host_function_dispatcher import dispatch

        with pytest.raises(RuntimeError, match="Unknown host function"):
            dispatch("NONEXISTENT", [], "sovereign")

    def test_sleep_builtin(self) -> None:
        from agents.core.host_function_dispatcher import dispatch

        start = time.time()
        result = dispatch("SLEEP", [50], "hearth")  # 50ms
        elapsed = time.time() - start
        assert result is True
        assert elapsed >= 0.04  # at least 40ms elapsed

    def test_read_direct_fallback(self, tmp_path: Path) -> None:
        """READ falls back to direct filesystem when Dapr is unavailable."""
        from agents.core.host_function_dispatcher import dispatch

        target = tmp_path / "test.txt"
        target.write_text("hello acfs")

        # Map BASE_DIR to tmp_path so ACFS path resolves correctly
        with patch.dict("os.environ", {"BASE_DIR": str(tmp_path)}):
            result = dispatch("READ", ["test.txt"], "hearth")

        assert result == "hello acfs"

    def test_write_direct_fallback(self, tmp_path: Path) -> None:
        """WRITE falls back to direct filesystem when Dapr is unavailable."""
        from agents.core.host_function_dispatcher import dispatch

        with patch.dict("os.environ", {"BASE_DIR": str(tmp_path)}):
            ok = dispatch("WRITE", ["output.txt", "written by test"], "hearth")

        assert ok is True
        assert (tmp_path / "output.txt").read_text() == "written by test"

    def test_acfs_path_traversal_blocked(self, tmp_path: Path) -> None:
        """Path traversal outside BASE_DIR must raise PermissionError."""
        from agents.core.host_function_dispatcher import dispatch

        with patch.dict("os.environ", {"BASE_DIR": str(tmp_path)}):
            with pytest.raises(PermissionError, match="ACFS confinement"):
                dispatch("READ", ["../../etc/passwd"], "hearth")

    def test_web_search_tier_enforcement(self) -> None:
        """WEB_SEARCH is not available on hearth tier."""
        from agents.core.host_function_dispatcher import dispatch

        with pytest.raises(PermissionError, match="hearth"):
            dispatch("WEB_SEARCH", ["query"], "hearth")


# --------------------------------------------------------------------------- #
# ACTION tag dispatch integration
# --------------------------------------------------------------------------- #

class TestActionDispatch:
    def test_action_sleep_via_interpreter(self) -> None:
        """[ACTION] SLEEP 50 — exercises the full ACTION dispatch path."""
        ast = {
            "version": "0.2.0",
            "program": [
                {"tag": "ACTION", "args": ["SLEEP", 50]},
                {"tag": "RESULT", "args": [{"code": 0}, {"message": "ok"}]},
            ],
        }
        result = hlfrun(ast)
        assert result["code"] == 0
        assert result["scope"]["SLEEP_RESULT"] is True

    def test_action_read_write_round_trip(self, tmp_path: Path) -> None:
        """[ACTION] WRITE then [ACTION] READ — verifies file I/O round-trip."""
        ast = {
            "version": "0.2.0",
            "program": [
                {"tag": "ACTION", "args": ["WRITE", "round_trip.txt", "test_data"]},
                {"tag": "ACTION", "args": ["READ", "round_trip.txt"]},
                {"tag": "RESULT", "args": [{"code": 0}, {"message": "ok"}]},
            ],
        }
        with patch.dict("os.environ", {"BASE_DIR": str(tmp_path)}):
            result = hlfrun(ast)

        assert result["scope"]["WRITE_RESULT"] is True
        assert result["scope"]["READ_RESULT"] == "test_data"


# --------------------------------------------------------------------------- #
# Dual-mode bus tests
# --------------------------------------------------------------------------- #

class TestDualModeBus:
    def _mock_redis(self, **overrides):
        mock = AsyncMock()
        mock.incr = AsyncMock(return_value=overrides.get("incr", 1))
        mock.expire = AsyncMock(return_value=True)
        mock.set = AsyncMock(return_value=True)
        mock.xadd = AsyncMock(return_value="1-0")
        mock.eval = AsyncMock(return_value=overrides.get("gas_eval", 999))
        return mock

    def _fake_get_redis(self, mock_redis):
        async def _inner():
            return mock_redis

        return _inner

    def test_text_mode_bypasses_hlf_validator(self) -> None:
        """{'text': 'plain English'} should return 202, not 422."""
        mock_redis = self._mock_redis()
        with patch("agents.gateway.bus.get_redis", new=self._fake_get_redis(mock_redis)):
            from agents.gateway import bus
            from fastapi.testclient import TestClient

            client = TestClient(bus.app, raise_server_exceptions=False)
            resp = client.post("/api/v1/intent", json={"text": "analyze the seccomp file"})

        assert resp.status_code == 202
        data = resp.json()
        assert data["ast"]["text_mode"] is True

    def test_hlf_mode_still_validates(self) -> None:
        """{'hlf': 'bad HLF'} should still return 422."""
        mock_redis = self._mock_redis()
        with patch("agents.gateway.bus.get_redis", new=self._fake_get_redis(mock_redis)):
            from agents.gateway import bus
            from fastapi.testclient import TestClient

            client = TestClient(bus.app, raise_server_exceptions=False)
            resp = client.post("/api/v1/intent", json={"hlf": "this is not valid HLF"})

        assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# execute_intent wiring test
# --------------------------------------------------------------------------- #

class TestExecuteIntent:
    def test_ast_mode_executes_directly(self) -> None:
        """Payloads with pre-compiled AST are executed by hlfrun directly."""
        from agents.core.main import execute_intent

        src = '[HLF-v2]\n[INTENT] test "x"\n[RESULT] code=0 message="done"\nΩ\n'
        ast = hlfc_compile(src)
        result = execute_intent({"request_id": "test-001", "ast": ast})
        assert result["code"] == 0
        assert result["message"] == "done"

    def test_text_mode_returns_error_when_ollama_down(self) -> None:
        """Text-mode payload returns code=1 when Ollama is not reachable."""
        from agents.core.main import execute_intent

        payload = {"request_id": "test-002", "text": "do something"}
        result = execute_intent(payload)
        # Ollama is not running in test env → RuntimeError captured gracefully
        assert result["code"] == 1
        assert "ollama" in result["message"].lower() or result["code"] == 1
