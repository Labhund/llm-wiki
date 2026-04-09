from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from llm_wiki.daemon.client import DaemonClient
from llm_wiki.mcp.tools import WIKI_TOOLS


def test_mcp_server_registers_all_tools(tmp_path):
    """Constructing an MCPServer registers every tool in WIKI_TOOLS."""
    from llm_wiki.mcp.server import MCPServer

    client = MagicMock(spec=DaemonClient)
    server = MCPServer(vault_path=tmp_path, client=client)
    registered = server.list_tools()
    registered_names = {t.name for t in registered}
    expected_names = {t.name for t in WIKI_TOOLS}
    assert registered_names == expected_names


def test_mcp_server_tool_descriptions_present(tmp_path):
    """Every registered tool has a non-empty description (agent-facing)."""
    from llm_wiki.mcp.server import MCPServer

    client = MagicMock(spec=DaemonClient)
    server = MCPServer(vault_path=tmp_path, client=client)
    for tool in server.list_tools():
        assert tool.description, f"Tool {tool.name} has no description"
        assert len(tool.description) > 20  # not just a placeholder


@pytest.mark.asyncio
async def test_mcp_server_dispatches_tool_call(tmp_path):
    """A call_tool invocation routes to the corresponding handler."""
    from llm_wiki.mcp.server import MCPServer

    client = MagicMock(spec=DaemonClient)
    client.arequest.return_value = {"status": "ok", "page_count": 4}

    server = MCPServer(vault_path=tmp_path, client=client)
    result = await server.call_tool("wiki_status", {})
    assert result
    assert "page_count" in result[0].text


@pytest.mark.asyncio
async def test_mcp_server_unknown_tool_raises(tmp_path):
    from llm_wiki.mcp.server import MCPServer

    client = MagicMock(spec=DaemonClient)
    server = MCPServer(vault_path=tmp_path, client=client)
    with pytest.raises(KeyError):
        await server.call_tool("nonexistent", {})


def test_mcp_server_connection_id_is_stable_across_tool_calls(tmp_path):
    """One UUID per MCP server instance, threaded into every tool call."""
    from llm_wiki.mcp.server import MCPServer

    client = MagicMock(spec=DaemonClient)
    server = MCPServer(vault_path=tmp_path, client=client)
    # Same instance, same connection_id
    assert server._connection_id == server._ctx.connection_id
    # Different instance, different connection_id
    server2 = MCPServer(vault_path=tmp_path, client=client)
    assert server._connection_id != server2._connection_id


@pytest.mark.asyncio
async def test_mcp_server_threads_connection_id_into_write_calls(tmp_path):
    """A write tool dispatched via call_tool sees the server's connection_id
    in the daemon request."""
    from llm_wiki.mcp.server import MCPServer

    client = MagicMock(spec=DaemonClient)
    client.arequest.return_value = {
        "status": "ok",
        "page_path": "wiki/foo.md",
        "journal_id": "1",
        "session_id": "abc",
        "content_hash": "sha256:x",
    }
    server = MCPServer(vault_path=tmp_path, client=client)
    await server.call_tool("wiki_create", {
        "title": "Foo",
        "body": "body [[raw/x.pdf]]",
        "citations": ["raw/x.pdf"],
        "author": "alice",
    })
    sent = client.arequest.call_args[0][0]
    assert sent["connection_id"] == server._connection_id
