"""MCP server entry point — wraps an mcp.server.Server with our tool list.

This is intentionally a thin layer. Construction registers every tool in
``WIKI_TOOLS``. The class exposes:
  - ``list_tools()`` → the registered ``ToolDefinition`` list
  - ``call_tool(name, args)`` → dispatches to the matching handler
  - ``run_stdio()`` → runs the SDK's stdio transport (the actual entry
    point used by ``llm-wiki mcp``)
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from llm_wiki.daemon.client import DaemonClient
from llm_wiki.mcp.tools import WIKI_TOOLS, ToolContext, ToolDefinition

logger = logging.getLogger(__name__)


class MCPServer:
    """Holds the tool registry and the daemon client; runs the SDK transport."""

    def __init__(self, vault_path: Path, client: DaemonClient) -> None:
        self._vault_path = vault_path
        self._client = client
        self._tools: dict[str, ToolDefinition] = {t.name: t for t in WIKI_TOOLS}
        # One UUID per MCP stdio session — stays stable for the entire
        # process lifetime so all tool calls from this MCP client land in
        # one daemon session keyed on (author, connection_id).
        self._connection_id = uuid.uuid4().hex
        self._ctx = ToolContext(client=client, connection_id=self._connection_id)
        logger.info("MCP server connection_id: %s", self._connection_id)

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    async def call_tool(self, name: str, args: dict[str, Any]) -> list:
        """Dispatch a tool call by name. Raises KeyError on unknown name."""
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        tool = self._tools[name]
        return await tool.handler(self._ctx, args)

    async def run_stdio(self) -> None:
        """Run the MCP stdio transport. Returns when the client disconnects."""
        from mcp.server import Server as SdkServer
        from mcp.server.stdio import stdio_server
        from mcp.types import Tool as SdkTool

        sdk_server = SdkServer("llm-wiki")

        @sdk_server.list_tools()
        async def _list_tools() -> list[SdkTool]:
            return [
                SdkTool(
                    name=t.name,
                    description=t.description,
                    inputSchema=t.input_schema,
                )
                for t in self._tools.values()
            ]

        @sdk_server.call_tool()
        async def _call_tool(name: str, arguments: dict) -> list:
            from llm_wiki.mcp.errors import McpToolError, format_error
            try:
                return await self.call_tool(name, arguments or {})
            except McpToolError as exc:
                # The SDK turns ValueError into a tool error response
                raise ValueError(format_error(exc)) from exc

        async with stdio_server() as (read_stream, write_stream):
            await sdk_server.run(
                read_stream,
                write_stream,
                sdk_server.create_initialization_options(),
            )
