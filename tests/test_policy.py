"""
Policy integration tests — synthetic intent replay matrix.
Tests: valid→202, malformed→422, ALIGN-blocked→403, gas-exhausted→429, replayed nonce→409.
Uses FastAPI TestClient (no real Redis needed via mocking).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient


def _make_mock_redis(**overrides):
    """Build a mock Redis client with sensible defaults."""
    mock = AsyncMock()
    mock.incr = AsyncMock(return_value=overrides.get("incr", 1))
    mock.expire = AsyncMock(return_value=True)
    mock.set = AsyncMock(return_value=overrides.get("set", True))
    mock.xadd = AsyncMock(return_value="1-0")
    # Global Per-Tier Gas Bucket: eval returns remaining gas (positive = OK, -1 = exhausted)
    mock.eval = AsyncMock(return_value=overrides.get("gas_eval", 999))
    return mock


def _fake_get_redis(mock_redis):
    async def _inner():
        return mock_redis
    return _inner


def test_health_endpoint() -> None:
    from agents.gateway.bus import app
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_valid_intent_returns_202() -> None:
    mock_redis = _make_mock_redis()
    with patch("agents.gateway.bus.get_redis", new=_fake_get_redis(mock_redis)):
        from agents.gateway import bus
        client = TestClient(bus.app, raise_server_exceptions=True)
        payload = {"hlf": "[HLF-v3]\n[INTENT] greet \"world\"\n[RESULT] code=0 message=\"ok\"\nΩ\n"}
        resp = client.post("/api/v1/intent", json=payload)
    assert resp.status_code == 202


def test_malformed_hlf_returns_422() -> None:
    mock_redis = _make_mock_redis()
    with patch("agents.gateway.bus.get_redis", new=_fake_get_redis(mock_redis)):
        from agents.gateway import bus
        client = TestClient(bus.app, raise_server_exceptions=False)
        payload = {"hlf": "this is not valid HLF at all"}
        resp = client.post("/api/v1/intent", json=payload)
    assert resp.status_code == 422


def test_align_blocked_returns_403() -> None:
    mock_redis = _make_mock_redis()
    with patch("agents.gateway.bus.get_redis", new=_fake_get_redis(mock_redis)):
        from agents.gateway import bus
        client = TestClient(bus.app, raise_server_exceptions=False)
        # R-006 pattern: sudo
        payload = {"hlf": "[HLF-v3]\n[INTENT] sudo rm\nΩ\n"}
        resp = client.post("/api/v1/intent", json=payload)
    assert resp.status_code == 403


def test_rate_limit_returns_429() -> None:
    mock_redis = _make_mock_redis(incr=51)  # over 50rpm limit
    with patch("agents.gateway.bus.get_redis", new=_fake_get_redis(mock_redis)):
        from agents.gateway import bus
        client = TestClient(bus.app, raise_server_exceptions=False)
        payload = {"hlf": "[HLF-v3]\n[INTENT] greet \"world\"\nΩ\n"}
        resp = client.post("/api/v1/intent", json=payload)
    assert resp.status_code == 429


def test_global_gas_bucket_exhausted_returns_429() -> None:
    """Global per-tier gas bucket exhausted → 429 (gas_eval=-1)."""
    mock_redis = _make_mock_redis(gas_eval=-1)
    with patch("agents.gateway.bus.get_redis", new=_fake_get_redis(mock_redis)):
        from agents.gateway import bus
        client = TestClient(bus.app, raise_server_exceptions=False)
        payload = {"hlf": "[HLF-v3]\n[INTENT] greet \"world\"\n[RESULT] code=0 message=\"ok\"\nΩ\n"}
        resp = client.post("/api/v1/intent", json=payload)
    assert resp.status_code == 429
    assert "gas" in resp.json()["detail"].lower()


def test_replayed_nonce_returns_409() -> None:
    mock_redis = _make_mock_redis(set=None)  # None = SETNX failed (key exists)
    with patch("agents.gateway.bus.get_redis", new=_fake_get_redis(mock_redis)):
        from agents.gateway import bus
        client = TestClient(bus.app, raise_server_exceptions=False)
        payload = {"hlf": "[HLF-v3]\n[INTENT] greet \"world\"\n[RESULT] code=0 message=\"ok\"\nΩ\n"}
        resp = client.post("/api/v1/intent", json=payload)
    assert resp.status_code == 409


def test_empty_body_returns_422() -> None:
    mock_redis = _make_mock_redis()
    with patch("agents.gateway.bus.get_redis", new=_fake_get_redis(mock_redis)):
        from agents.gateway import bus
        client = TestClient(bus.app, raise_server_exceptions=False)
        resp = client.post("/api/v1/intent", json={})
    assert resp.status_code == 422


def test_text_mode_valid_intent() -> None:
    """Text-mode intents (plain English) should be accepted as 202."""
    mock_redis = _make_mock_redis()
    with patch("agents.gateway.bus.get_redis", new=_fake_get_redis(mock_redis)):
        from agents.gateway import bus
        client = TestClient(bus.app, raise_server_exceptions=False)
        payload = {"text": "Can you review the seccomp files for vulnerabilities?"}
        resp = client.post("/api/v1/intent", json=payload)
    assert resp.status_code == 202
