"""
BrowserOS MCP Bridge — Connects Sovereign OS agents to BrowserOS's built-in MCP server.

BrowserOS exposes an MCP server (typically on a stable port or SSE endpoint)
that provides access to its 54 browser tools. This module:

1. Discovers the BrowserOS MCP server endpoint
2. Provides a typed client for invoking BrowserOS tools
3. Registers browser-automation tools in the Sovereign OS ToolRegistry
4. Adds HLF host-function verbs for browser control

Architecture:
    Sovereign Agent → ToolRegistry → BrowserOSMCPClient → HTTP/SSE → BrowserOS MCP Server

The bridge uses httpx for async HTTP calls and supports both:
  - Fire-and-forget navigation (BROWSER_NAVIGATE)
  - Request-response tool invocation (BROWSER_SEARCH, BROWSER_WORKFLOW)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from agents.core.logger import ALSLogger

_logger = ALSLogger(agent_role="browseros-mcp-bridge", goal_id="integration")


# ── Configuration ────────────────────────────────────────────────────────────

_DEFAULT_MCP_PORT = 12007
_DEFAULT_MCP_HOST = "localhost"
_CONNECTION_TIMEOUT = 5.0
_REQUEST_TIMEOUT = 30.0


# ── Data Structures ──────────────────────────────────────────────────────────


@dataclass
class MCPToolCall:
    """A tool invocation request to BrowserOS MCP server."""
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPToolResult:
    """Result from a BrowserOS MCP tool invocation."""
    success: bool
    content: Any = None
    error: str | None = None
    tool_name: str = ""
    duration_ms: float = 0.0


@dataclass
class BrowserOSMCPStatus:
    """Status of the BrowserOS MCP server connection."""
    connected: bool = False
    endpoint: str = ""
    available_tools: list[str] = field(default_factory=list)
    server_info: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


# ── MCP Client ───────────────────────────────────────────────────────────────


class BrowserOSMCPClient:
    """HTTP client for BrowserOS's built-in MCP server.

    Manages connection, tool discovery, and tool invocation.
    Thread-safe via httpx.AsyncClient.
    """

    def __init__(
        self,
        host: str = _DEFAULT_MCP_HOST,
        port: int = _DEFAULT_MCP_PORT,
        timeout: float = _REQUEST_TIMEOUT,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        self._base_url = f"http://{host}:{port}"
        self._tools_cache: list[dict] | None = None

    @property
    def endpoint(self) -> str:
        return self._base_url

    async def check_connection(self) -> BrowserOSMCPStatus:
        """Check if the BrowserOS MCP server is reachable and get server info."""
        try:
            async with httpx.AsyncClient(timeout=_CONNECTION_TIMEOUT) as client:
                # Try SSE endpoint first (standard MCP transport)
                resp = await client.get(f"{self._base_url}/sse")
                if resp.status_code < 500:
                    return BrowserOSMCPStatus(
                        connected=True,
                        endpoint=f"{self._base_url}/sse",
                        server_info={"transport": "sse", "status_code": resp.status_code},
                    )
        except Exception as exc:
            pass

        # Try health/info endpoint
        try:
            async with httpx.AsyncClient(timeout=_CONNECTION_TIMEOUT) as client:
                resp = await client.get(f"{self._base_url}/health")
                if resp.status_code == 200:
                    return BrowserOSMCPStatus(
                        connected=True,
                        endpoint=self._base_url,
                        server_info=resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {},
                    )
        except Exception as exc:
            return BrowserOSMCPStatus(
                connected=False,
                endpoint=self._base_url,
                error=str(exc),
            )

        return BrowserOSMCPStatus(connected=False, endpoint=self._base_url, error="No MCP endpoint found")

    async def list_tools(self) -> list[dict]:
        """Discover available tools from the BrowserOS MCP server.

        Returns a list of tool definitions with name, description, and schema.
        """
        if self._tools_cache is not None:
            return self._tools_cache

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                # Standard MCP tools/list endpoint
                resp = await client.post(
                    f"{self._base_url}/mcp/tools/list",
                    json={},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    tools = data.get("tools", data.get("result", []))
                    self._tools_cache = tools
                    _logger.log("BROWSEROS_MCP_TOOLS_DISCOVERED", {
                        "count": len(tools),
                        "names": [t.get("name", "") for t in tools[:10]],
                    })
                    return tools
        except Exception as exc:
            _logger.log("BROWSEROS_MCP_TOOLS_ERROR", {"error": str(exc)})

        return []

    async def call_tool(self, tool_call: MCPToolCall) -> MCPToolResult:
        """Invoke a tool on the BrowserOS MCP server.

        Returns MCPToolResult with success/failure and content/error.
        """
        import time
        start = time.monotonic()

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/mcp/tools/call",
                    json={
                        "name": tool_call.name,
                        "arguments": tool_call.arguments,
                    },
                )

                elapsed = (time.monotonic() - start) * 1000

                if resp.status_code == 200:
                    data = resp.json()
                    result = MCPToolResult(
                        success=True,
                        content=data.get("content", data.get("result", data)),
                        tool_name=tool_call.name,
                        duration_ms=elapsed,
                    )
                    _logger.log("BROWSEROS_MCP_TOOL_CALLED", {
                        "tool": tool_call.name,
                        "success": True,
                        "duration_ms": round(elapsed, 1),
                    })
                    return result
                else:
                    return MCPToolResult(
                        success=False,
                        error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                        tool_name=tool_call.name,
                        duration_ms=elapsed,
                    )

        except httpx.TimeoutException:
            elapsed = (time.monotonic() - start) * 1000
            return MCPToolResult(
                success=False,
                error=f"Timeout after {self._timeout}s",
                tool_name=tool_call.name,
                duration_ms=elapsed,
            )
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            return MCPToolResult(
                success=False,
                error=str(exc),
                tool_name=tool_call.name,
                duration_ms=elapsed,
            )

    def invalidate_tools_cache(self) -> None:
        """Clear the cached tools list (call after BrowserOS update)."""
        self._tools_cache = None


# ── Singleton Client ─────────────────────────────────────────────────────────

_client: BrowserOSMCPClient | None = None


def get_mcp_client(port: int | None = None) -> BrowserOSMCPClient:
    """Get or create the singleton BrowserOS MCP client."""
    global _client
    if _client is None:
        _client = BrowserOSMCPClient(port=port or _DEFAULT_MCP_PORT)
    return _client


def reset_mcp_client() -> None:
    """Reset the singleton (for testing)."""
    global _client
    _client = None


# ── Convenience Functions (for HLF host-function dispatch) ───────────────────


async def browser_navigate(url: str) -> MCPToolResult:
    """Navigate BrowserOS to a URL.

    HLF verb: [ACTION] BROWSER_NAVIGATE <url>
    """
    client = get_mcp_client()
    return await client.call_tool(MCPToolCall(
        name="browser_navigate",
        arguments={"url": url},
    ))


async def browser_search(query: str) -> MCPToolResult:
    """Search using BrowserOS's agent.

    HLF verb: [ACTION] BROWSER_SEARCH <query>
    """
    client = get_mcp_client()
    return await client.call_tool(MCPToolCall(
        name="browser_search",
        arguments={"query": query},
    ))


async def browser_run_workflow(workflow_name: str) -> MCPToolResult:
    """Run a saved BrowserOS workflow.

    HLF verb: [ACTION] BROWSER_WORKFLOW <workflow_name>
    """
    client = get_mcp_client()
    return await client.call_tool(MCPToolCall(
        name="run_workflow",
        arguments={"name": workflow_name},
    ))


async def browser_cowork(task: str, folder: str | None = None) -> MCPToolResult:
    """Execute a Cowork task using BrowserOS's filesystem + browser agent.

    HLF verb: [ACTION] BROWSER_COWORK <task>
    """
    client = get_mcp_client()
    args: dict[str, Any] = {"task": task}
    if folder:
        args["folder"] = folder
    return await client.call_tool(MCPToolCall(
        name="cowork",
        arguments=args,
    ))


async def browser_get_page_content(url: str | None = None) -> MCPToolResult:
    """Get the current page content (text) from BrowserOS."""
    client = get_mcp_client()
    args: dict[str, Any] = {}
    if url:
        args["url"] = url
    return await client.call_tool(MCPToolCall(
        name="get_page_content",
        arguments=args,
    ))


# ── Tool Registration ────────────────────────────────────────────────────────


def register_browseros_mcp_tools(registry: Any) -> None:
    """Register BrowserOS MCP bridge tools into the ToolRegistry.

    These tools allow Sovereign OS agents to control BrowserOS
    programmatically for web browsing, research, and automation.
    """
    from agents.core.native import _load_native_config

    config = _load_native_config()
    features = config.get("features", {})
    registered = 0

    if not features.get("browseros", True):
        _logger.log("BROWSEROS_MCP_DISABLED", {"reason": "feature_flag"})
        return

    # Tool 1: Navigate
    def _handle_navigate(params: dict) -> dict:
        import asyncio
        url = params.get("url", "")
        if not url:
            return {"status": "error", "data": {"message": "URL required"}}
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(browser_navigate(url))
            return {"status": "ok" if result.success else "error", "data": {
                "content": result.content, "error": result.error,
                "duration_ms": result.duration_ms,
            }}
        finally:
            loop.close()

    registry.register(
        name="browseros.navigate",
        handler=_handle_navigate,
        schema={
            "description": "Navigate BrowserOS to a URL using its AI agent",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "URL to navigate to"}},
                "required": ["url"],
            },
        },
        gas_cost=3,
        permissions=["hearth", "forge", "sovereign"],
        tags=["browseros", "browser", "mcp", "navigate"],
    )
    registered += 1

    # Tool 2: Search
    def _handle_search(params: dict) -> dict:
        import asyncio
        query = params.get("query", "")
        if not query:
            return {"status": "error", "data": {"message": "Query required"}}
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(browser_search(query))
            return {"status": "ok" if result.success else "error", "data": {
                "content": result.content, "error": result.error,
                "duration_ms": result.duration_ms,
            }}
        finally:
            loop.close()

    registry.register(
        name="browseros.search",
        handler=_handle_search,
        schema={
            "description": "Search the web using BrowserOS's AI agent",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Search query"}},
                "required": ["query"],
            },
        },
        gas_cost=5,
        permissions=["hearth", "forge", "sovereign"],
        tags=["browseros", "browser", "mcp", "search"],
    )
    registered += 1

    # Tool 3: Run Workflow
    def _handle_workflow(params: dict) -> dict:
        import asyncio
        name = params.get("name", "")
        if not name:
            return {"status": "error", "data": {"message": "Workflow name required"}}
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(browser_run_workflow(name))
            return {"status": "ok" if result.success else "error", "data": {
                "content": result.content, "error": result.error,
                "duration_ms": result.duration_ms,
            }}
        finally:
            loop.close()

    registry.register(
        name="browseros.workflow",
        handler=_handle_workflow,
        schema={
            "description": "Run a saved BrowserOS workflow automation",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Name of the saved workflow"}},
                "required": ["name"],
            },
        },
        gas_cost=8,
        permissions=["forge", "sovereign"],
        tags=["browseros", "browser", "mcp", "workflow"],
    )
    registered += 1

    # Tool 4: Cowork (filesystem + browser automation)
    def _handle_cowork(params: dict) -> dict:
        import asyncio
        task = params.get("task", "")
        folder = params.get("folder")
        if not task:
            return {"status": "error", "data": {"message": "Task description required"}}
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(browser_cowork(task, folder))
            return {"status": "ok" if result.success else "error", "data": {
                "content": result.content, "error": result.error,
                "duration_ms": result.duration_ms,
            }}
        finally:
            loop.close()

    registry.register(
        name="browseros.cowork",
        handler=_handle_cowork,
        schema={
            "description": "Execute a Cowork task using BrowserOS's combined filesystem and browser agent",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Task description for the Cowork agent"},
                    "folder": {"type": "string", "description": "Optional: working folder for filesystem ops"},
                },
                "required": ["task"],
            },
        },
        gas_cost=10,
        permissions=["forge", "sovereign"],
        tags=["browseros", "browser", "mcp", "cowork"],
    )
    registered += 1

    # Tool 5: MCP Connection Status
    def _handle_mcp_status(params: dict) -> dict:
        import asyncio
        client = get_mcp_client()
        loop = asyncio.new_event_loop()
        try:
            status = loop.run_until_complete(client.check_connection())
            tools = loop.run_until_complete(client.list_tools()) if status.connected else []
            return {"status": "ok", "data": {
                "connected": status.connected,
                "endpoint": status.endpoint,
                "available_tools": len(tools),
                "tool_names": [t.get("name", "") for t in tools[:20]],
                "server_info": status.server_info,
                "error": status.error,
            }}
        finally:
            loop.close()

    registry.register(
        name="browseros.mcp_status",
        handler=_handle_mcp_status,
        schema={
            "description": "Check BrowserOS MCP server connection and list available browser tools",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        gas_cost=2,
        permissions=["hearth", "forge", "sovereign"],
        tags=["browseros", "browser", "mcp", "status"],
    )
    registered += 1

    # Tool 6: Get Page Content
    def _handle_get_content(params: dict) -> dict:
        import asyncio
        url = params.get("url")
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(browser_get_page_content(url))
            return {"status": "ok" if result.success else "error", "data": {
                "content": result.content, "error": result.error,
                "duration_ms": result.duration_ms,
            }}
        finally:
            loop.close()

    registry.register(
        name="browseros.get_content",
        handler=_handle_get_content,
        schema={
            "description": "Get web page content via BrowserOS (renders JavaScript, handles auth)",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "Optional URL (uses current page if omitted)"}},
                "required": [],
            },
        },
        gas_cost=3,
        permissions=["hearth", "forge", "sovereign"],
        tags=["browseros", "browser", "mcp", "content"],
    )
    registered += 1

    _logger.log("BROWSEROS_MCP_TOOLS_REGISTERED", {"count": registered})
