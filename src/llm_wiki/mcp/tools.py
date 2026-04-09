"""MCP tool definitions and handlers — thin shims over DaemonClient.

Each tool is one async function that:
  1. Takes ``(ctx: ToolContext, args: dict)`` — ``ctx`` carries the
     ``DaemonClient`` plus the MCP-session-stable ``connection_id``.
  2. Sends one daemon request (write tools include ``ctx.connection_id``).
  3. Translates the response (raising ``McpToolError`` on daemon errors).
  4. Returns a ``list[TextContent]`` for the MCP SDK.

Adding/removing tools is a one-line edit to ``WIKI_TOOLS`` at the bottom.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Awaitable, Callable

from mcp.types import TextContent

from llm_wiki.daemon.client import DaemonClient
from llm_wiki.mcp.errors import McpToolError, translate_daemon_response  # noqa: F401


@dataclass
class ToolContext:
    """Threaded into every tool handler.

    The ``connection_id`` is generated once at MCP server startup
    (one UUID per stdio session) and stays stable for every tool call
    that this MCP server makes. The daemon's ``SessionRegistry`` keys on
    ``(author, connection_id)`` so all writes from one MCP session group
    into a single daemon session that settles cleanly via
    ``wiki_session_close`` or the inactivity timer.
    """

    client: DaemonClient
    connection_id: str


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[ToolContext, dict], Awaitable[list[TextContent]]]


def _ok(response: dict) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(response, indent=2))]


# ---------------------------------------------------------------------------
# Read-side
# ---------------------------------------------------------------------------

async def handle_wiki_search(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({
        "type": "search",
        "query": args["query"],
        "limit": args.get("limit", 10),
    })
    return _ok(translate_daemon_response(response))


WIKI_SEARCH = ToolDefinition(
    name="wiki_search",
    description=(
        "Keyword-search the wiki and return ranked manifest entries with "
        "line-numbered match snippets. Use this to find which pages might "
        "be relevant before deciding which to read in full."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search terms"},
            "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
        },
        "required": ["query"],
    },
    handler=handle_wiki_search,
)


async def handle_wiki_read(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({
        "type": "read",
        "page_name": args["page_name"],
        "viewport": args.get("viewport", "top"),
        "section": args.get("section"),
        "grep": args.get("grep"),
        "budget": args.get("budget"),
    })
    return _ok(translate_daemon_response(response))


WIKI_READ = ToolDefinition(
    name="wiki_read",
    description=(
        "Read a wiki page with viewport control. The response also folds "
        "in any open issues for the page and a digest of unresolved talk "
        "entries — you cannot read the page without seeing what background "
        "workers and prior sessions have said about it."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "page_name": {"type": "string"},
            "viewport": {
                "type": "string",
                "enum": ["top", "section", "grep", "full"],
                "default": "top",
            },
            "section": {"type": "string"},
            "grep": {"type": "string"},
            "budget": {"type": "integer"},
        },
        "required": ["page_name"],
    },
    handler=handle_wiki_read,
)


async def handle_wiki_manifest(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({
        "type": "manifest",
        "budget": args.get("budget", 16000),
    })
    return _ok(translate_daemon_response(response))


WIKI_MANIFEST = ToolDefinition(
    name="wiki_manifest",
    description=(
        "Return a hierarchical, budget-aware manifest of the whole vault. "
        "Use this to get an overview of what the wiki contains before "
        "diving into specific pages."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "budget": {"type": "integer", "default": 16000, "minimum": 1000},
        },
    },
    handler=handle_wiki_manifest,
)


async def handle_wiki_status(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({"type": "status"})
    return _ok(translate_daemon_response(response))


WIKI_STATUS = ToolDefinition(
    name="wiki_status",
    description=(
        "Return vault stats: page count, cluster count, daemon health, "
        "scheduler workers, last index time."
    ),
    input_schema={"type": "object", "properties": {}},
    handler=handle_wiki_status,
)


# ---------------------------------------------------------------------------
# Registration list
# ---------------------------------------------------------------------------

WIKI_TOOLS: list[ToolDefinition] = [
    WIKI_SEARCH,
    WIKI_READ,
    WIKI_MANIFEST,
    WIKI_STATUS,
    # Tasks 4–6 append more tools here.
]
