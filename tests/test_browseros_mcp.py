"""
Tests for agents.core.native.browseros_mcp — BrowserOS MCP bridge.

Tests cover:
  - MCPToolCall / MCPToolResult dataclasses
  - BrowserOSMCPClient: connection check, tool listing, tool invocation
  - Singleton client management
  - Convenience functions: navigate, search, workflow, cowork
  - Tool registration: 6 tools registered
  - Error handling: timeout, connection refused
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Stub winreg for non-Windows ──────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _ensure_winreg():
    if "winreg" not in sys.modules:
        sys.modules["winreg"] = MagicMock()
    yield


# ── Dataclass Tests ──────────────────────────────────────────────────────────


class TestDataclasses:
    def test_mcp_tool_call_defaults(self):
        from agents.core.native.browseros_mcp import MCPToolCall
        tc = MCPToolCall(name="test_tool")
        assert tc.name == "test_tool"
        assert tc.arguments == {}

    def test_mcp_tool_result_success(self):
        from agents.core.native.browseros_mcp import MCPToolResult
        r = MCPToolResult(success=True, content="hello", tool_name="test")
        assert r.success
        assert r.content == "hello"

    def test_mcp_tool_result_failure(self):
        from agents.core.native.browseros_mcp import MCPToolResult
        r = MCPToolResult(success=False, error="timeout", tool_name="test")
        assert not r.success
        assert r.error == "timeout"

    def test_mcp_status_defaults(self):
        from agents.core.native.browseros_mcp import BrowserOSMCPStatus
        s = BrowserOSMCPStatus()
        assert not s.connected
        assert s.endpoint == ""
        assert s.available_tools == []


# ── Client Tests ─────────────────────────────────────────────────────────────


class TestMCPClient:
    def test_client_endpoint(self):
        from agents.core.native.browseros_mcp import BrowserOSMCPClient
        c = BrowserOSMCPClient(host="localhost", port=12007)
        assert c.endpoint == "http://localhost:12007"

    def test_client_custom_port(self):
        from agents.core.native.browseros_mcp import BrowserOSMCPClient
        c = BrowserOSMCPClient(port=8080)
        assert "8080" in c.endpoint

    @pytest.mark.asyncio
    async def test_check_connection_success(self):
        from agents.core.native.browseros_mcp import BrowserOSMCPClient

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as MockClient:
            ctx = AsyncMock()
            ctx.__aenter__ = AsyncMock(return_value=ctx)
            ctx.__aexit__ = AsyncMock(return_value=False)
            ctx.get = AsyncMock(return_value=mock_response)
            MockClient.return_value = ctx

            client = BrowserOSMCPClient()
            status = await client.check_connection()
            assert status.connected

    @pytest.mark.asyncio
    async def test_check_connection_failure(self):
        from agents.core.native.browseros_mcp import BrowserOSMCPClient

        with patch("httpx.AsyncClient") as MockClient:
            ctx = AsyncMock()
            ctx.__aenter__ = AsyncMock(return_value=ctx)
            ctx.__aexit__ = AsyncMock(return_value=False)
            ctx.get = AsyncMock(side_effect=Exception("connection refused"))
            MockClient.return_value = ctx

            client = BrowserOSMCPClient()
            status = await client.check_connection()
            assert not status.connected

    @pytest.mark.asyncio
    async def test_list_tools_cached(self):
        from agents.core.native.browseros_mcp import BrowserOSMCPClient
        client = BrowserOSMCPClient()
        client._tools_cache = [{"name": "cached_tool"}]
        tools = await client.list_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "cached_tool"

    def test_invalidate_cache(self):
        from agents.core.native.browseros_mcp import BrowserOSMCPClient
        client = BrowserOSMCPClient()
        client._tools_cache = [{"name": "test"}]
        client.invalidate_tools_cache()
        assert client._tools_cache is None

    @pytest.mark.asyncio
    async def test_call_tool_success(self):
        from agents.core.native.browseros_mcp import BrowserOSMCPClient, MCPToolCall

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"content": "page loaded"}

        with patch("httpx.AsyncClient") as MockClient:
            ctx = AsyncMock()
            ctx.__aenter__ = AsyncMock(return_value=ctx)
            ctx.__aexit__ = AsyncMock(return_value=False)
            ctx.post = AsyncMock(return_value=mock_response)
            MockClient.return_value = ctx

            client = BrowserOSMCPClient()
            result = await client.call_tool(MCPToolCall(
                name="browser_navigate",
                arguments={"url": "https://example.com"},
            ))
            assert result.success
            assert result.content == "page loaded"
            assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_call_tool_http_error(self):
        from agents.core.native.browseros_mcp import BrowserOSMCPClient, MCPToolCall

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        with patch("httpx.AsyncClient") as MockClient:
            ctx = AsyncMock()
            ctx.__aenter__ = AsyncMock(return_value=ctx)
            ctx.__aexit__ = AsyncMock(return_value=False)
            ctx.post = AsyncMock(return_value=mock_response)
            MockClient.return_value = ctx

            client = BrowserOSMCPClient()
            result = await client.call_tool(MCPToolCall(name="test"))
            assert not result.success
            assert "500" in result.error

    @pytest.mark.asyncio
    async def test_call_tool_exception(self):
        from agents.core.native.browseros_mcp import BrowserOSMCPClient, MCPToolCall

        with patch("httpx.AsyncClient") as MockClient:
            ctx = AsyncMock()
            ctx.__aenter__ = AsyncMock(return_value=ctx)
            ctx.__aexit__ = AsyncMock(return_value=False)
            ctx.post = AsyncMock(side_effect=Exception("network error"))
            MockClient.return_value = ctx

            client = BrowserOSMCPClient()
            result = await client.call_tool(MCPToolCall(name="test"))
            assert not result.success
            assert "network error" in result.error


# ── Singleton Tests ──────────────────────────────────────────────────────────


class TestSingleton:
    def test_get_creates_client(self):
        from agents.core.native.browseros_mcp import get_mcp_client, reset_mcp_client
        reset_mcp_client()
        client = get_mcp_client()
        assert client is not None
        assert client.endpoint == "http://localhost:12007"

    def test_get_returns_same(self):
        from agents.core.native.browseros_mcp import get_mcp_client, reset_mcp_client
        reset_mcp_client()
        c1 = get_mcp_client()
        c2 = get_mcp_client()
        assert c1 is c2

    def test_reset_clears(self):
        from agents.core.native.browseros_mcp import get_mcp_client, reset_mcp_client
        reset_mcp_client()
        c1 = get_mcp_client()
        reset_mcp_client()
        c2 = get_mcp_client()
        assert c1 is not c2


# ── Tool Registration Tests ──────────────────────────────────────────────────


class TestToolRegistration:
    @patch("agents.core.native._load_native_config",
           return_value={"features": {"browseros": True}})
    def test_registers_six_tools(self, mock_config):
        from agents.core.native.browseros_mcp import register_browseros_mcp_tools
        registry = MagicMock()
        register_browseros_mcp_tools(registry)
        assert registry.register.call_count == 6
        # Verify tool names via keyword args
        names = [c[1].get("name", "") if len(c) > 1 else c[0][0]
                 for c in [call for call in registry.register.call_args_list]]
        # Fallback: extract from kwargs
        if not any(names):
            names = []
            for call in registry.register.call_args_list:
                _, kwargs = call
                names.append(kwargs.get("name", ""))
        expected = {"browseros.navigate", "browseros.search", "browseros.workflow",
                    "browseros.cowork", "browseros.mcp_status", "browseros.get_content"}
        assert expected.issubset(set(names))

    @patch("agents.core.native._load_native_config",
           return_value={"features": {"browseros": False}})
    def test_disabled_registers_none(self, mock_config):
        from agents.core.native.browseros_mcp import register_browseros_mcp_tools
        registry = MagicMock()
        register_browseros_mcp_tools(registry)
        assert registry.register.call_count == 0


# ── Convenience Function Tests ───────────────────────────────────────────────


class TestConvenienceFunctions:
    @pytest.mark.asyncio
    async def test_browser_navigate(self):
        from agents.core.native.browseros_mcp import browser_navigate, reset_mcp_client

        reset_mcp_client()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.content = "navigated"

        with patch("agents.core.native.browseros_mcp.get_mcp_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.call_tool = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_client

            result = await browser_navigate("https://example.com")
            assert result.success
            mock_client.call_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_browser_search(self):
        from agents.core.native.browseros_mcp import browser_search, reset_mcp_client

        reset_mcp_client()
        mock_result = MagicMock()
        mock_result.success = True

        with patch("agents.core.native.browseros_mcp.get_mcp_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.call_tool = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_client

            result = await browser_search("test query")
            assert result.success

    @pytest.mark.asyncio
    async def test_browser_cowork_with_folder(self):
        from agents.core.native.browseros_mcp import browser_cowork, reset_mcp_client

        reset_mcp_client()
        mock_result = MagicMock()
        mock_result.success = True

        with patch("agents.core.native.browseros_mcp.get_mcp_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.call_tool = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_client

            result = await browser_cowork("research topic", "/tmp/output")
            assert result.success
            # Verify folder was passed in arguments
            call_args = mock_client.call_tool.call_args[0][0]
            assert call_args.arguments.get("folder") == "/tmp/output"
