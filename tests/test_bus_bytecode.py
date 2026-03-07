"""Tests for bytecode routing in the Gateway Bus."""
from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: F401 — MagicMock available for future sync mock tests

import pytest

from hlf.bytecode import compile_to_bytecode
from hlf.hlfc import compile as hlfc_compile

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HLF_SRC = '[HLF-v2]\n[SET] greeting = "hello"\n[RESULT] 0 "ok"\n\u03a9'


def _make_hlb_base64() -> str:
    """Compile a simple HLF program to base64-encoded .hlb."""
    ast = hlfc_compile(_HLF_SRC)
    hlb = compile_to_bytecode(ast)
    return base64.b64encode(hlb).decode("ascii")


# ---------------------------------------------------------------------------
# Unit tests for _is_bytecode_payload
# ---------------------------------------------------------------------------


class TestBytecodeDetection:
    def test_detects_valid_bytecode(self) -> None:
        from agents.gateway.bus import _is_bytecode_payload

        payload = _make_hlb_base64()
        assert _is_bytecode_payload(payload) is True

    def test_rejects_plain_hlf(self) -> None:
        from agents.gateway.bus import _is_bytecode_payload

        assert _is_bytecode_payload(_HLF_SRC) is False

    def test_rejects_plain_text(self) -> None:
        from agents.gateway.bus import _is_bytecode_payload

        assert _is_bytecode_payload("Hello, tell me about HLF") is False

    def test_rejects_empty(self) -> None:
        from agents.gateway.bus import _is_bytecode_payload

        assert _is_bytecode_payload("") is False

    def test_rejects_random_base64(self) -> None:
        from agents.gateway.bus import _is_bytecode_payload

        payload = base64.b64encode(b"NOT_HLF_DATA").decode("ascii")
        assert _is_bytecode_payload(payload) is False


# ---------------------------------------------------------------------------
# Integration tests for post_intent bytecode routing
# ---------------------------------------------------------------------------


def _make_fake_redis() -> AsyncMock:
    """Create a fake async Redis with all methods post_intent uses."""
    r = AsyncMock()
    r.incr = AsyncMock(return_value=1)
    r.expire = AsyncMock()
    r.set = AsyncMock(return_value=True)
    r.xadd = AsyncMock(return_value="fake-stream-id")
    return r


@pytest.mark.asyncio
class TestBytecodeRouting:
    async def test_bytecode_intent_executes(self) -> None:
        """Base64-encoded .hlb in hlf field should execute via VM."""
        from httpx import ASGITransport, AsyncClient

        import agents.gateway.bus as bus_mod
        from agents.gateway.bus import app

        fake_redis = _make_fake_redis()

        with (
            patch.object(bus_mod, "get_redis", new=AsyncMock(return_value=fake_redis)),
            patch("agents.gateway.router.is_gateway_healthy", new=AsyncMock(return_value=True)),
            patch.object(bus_mod, "consume_gas_async", new=AsyncMock(return_value=True)),
        ):
            payload = _make_hlb_base64()
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/v1/intent", json={"hlf": payload})

        # Should succeed (202) — not get rejected as invalid HLF
        assert resp.status_code == 202
        data = resp.json()
        assert data["ast"].get("bytecode_mode") is True
        assert data["ast"]["vm_result"]["code"] == 0

    async def test_regular_hlf_unchanged(self) -> None:
        """Regular HLF text should still route through the compiler."""
        from httpx import ASGITransport, AsyncClient

        import agents.gateway.bus as bus_mod
        from agents.gateway.bus import app

        fake_redis = _make_fake_redis()

        with (
            patch.object(bus_mod, "get_redis", new=AsyncMock(return_value=fake_redis)),
            patch("agents.gateway.router.is_gateway_healthy", new=AsyncMock(return_value=True)),
            patch.object(bus_mod, "consume_gas_async", new=AsyncMock(return_value=True)),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/v1/intent", json={"hlf": _HLF_SRC})

        assert resp.status_code == 202
        data = resp.json()
        assert data["ast"].get("bytecode_mode") is not True
