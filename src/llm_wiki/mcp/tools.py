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
# Query-side
# ---------------------------------------------------------------------------

async def handle_wiki_query(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({
        "type": "query",
        "question": args["question"],
        "budget": args.get("budget"),
    })
    return _ok(translate_daemon_response(response))


WIKI_QUERY = ToolDefinition(
    name="wiki_query",
    description=(
        "Ask the wiki a question. The daemon performs multi-turn traversal "
        "with budget management and returns a synthesized answer plus the "
        "citations it relied on. Your context only sees the final answer — "
        "the navigation log stays on the daemon side."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "budget": {"type": "integer"},
        },
        "required": ["question"],
    },
    handler=handle_wiki_query,
)


async def handle_wiki_ingest(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({
        "type": "ingest",
        "connection_id": ctx.connection_id,
        "source_path": args["source_path"],
        "author": args["author"],
        "dry_run": args.get("dry_run", False),
        "source_type": args.get("source_type", "paper"),
    })
    return _ok(translate_daemon_response(response))


WIKI_INGEST = ToolDefinition(
    name="wiki_ingest",
    description=(
        "Ingest a source file (PDF, DOCX, markdown, URL, image) into the "
        "wiki. The daemon runs extraction, identifies concepts, and creates "
        "or updates pages. Every internal write journals under your session "
        "so the whole ingest produces one git commit attributed to you.\n\n"
        "When dry_run is true, the full pipeline runs (extraction, concept "
        "identification, page content generation) but no pages are written. "
        "Returns a preview of concepts that would be created/updated with "
        "section headings and content previews. Use to inspect before committing."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source_path": {
                "type": "string",
                "description": (
                    "Source to ingest. Accepts a local filesystem path "
                    "(PDF, DOCX, markdown, plain text, image with OCR) "
                    "or a URL the daemon can fetch."
                ),
            },
            "author": {"type": "string", "description": "Your agent identifier"},
            "dry_run": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Preview mode: run extraction and generation but skip "
                    "all filesystem writes. Returns planned concepts with "
                    "section previews instead of creating/updating pages."
                ),
            },
            "source_type": {
                "type": "string",
                "enum": ["paper", "article", "transcript", "book", "other"],
                "default": "paper",
                "description": "Type of source, used to populate reading_status metadata.",
            },
        },
        "required": ["source_path", "author"],
    },
    handler=handle_wiki_ingest,
)


async def handle_wiki_lint(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({"type": "lint"})
    return _ok(translate_daemon_response(response))


WIKI_LINT = ToolDefinition(
    name="wiki_lint",
    description=(
        "Run structural integrity checks AND return the vault-wide attention "
        "map (issue + talk-entry counts per page, by severity). Near-instant, "
        "no LLM. Call this at the start of a session to know exactly where "
        "in the vault to focus."
    ),
    input_schema={"type": "object", "properties": {}},
    handler=handle_wiki_lint,
)


# ---------------------------------------------------------------------------
# Write-side
# ---------------------------------------------------------------------------

async def handle_wiki_create(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({
        "type": "page-create",
        "connection_id": ctx.connection_id,
        "title": args["title"],
        "body": args["body"],
        "citations": args.get("citations", []),
        "tags": args.get("tags", []),
        "author": args["author"],
        "intent": args.get("intent"),
        "force": args.get("force", False),
    })
    return _ok(translate_daemon_response(response))


WIKI_CREATE = ToolDefinition(
    name="wiki_create",
    description=(
        "Create a new wiki page. Requires citations — every claim in the "
        "main wiki must be traceable to a primary source. If you cannot "
        "cite a source, post your idea to the talk page via wiki_talk_post "
        "instead. Pass force=true to override near-match warnings."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "body": {"type": "string"},
            "citations": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            },
            "tags": {"type": "array", "items": {"type": "string"}},
            "author": {"type": "string"},
            "intent": {"type": "string"},
            "force": {"type": "boolean", "default": False},
        },
        "required": ["title", "body", "citations", "author"],
    },
    handler=handle_wiki_create,
)


async def handle_wiki_update(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({
        "type": "page-update",
        "connection_id": ctx.connection_id,
        "page": args["page"],
        "patch": args["patch"],
        "author": args["author"],
        "intent": args.get("intent"),
    })
    return _ok(translate_daemon_response(response))


WIKI_UPDATE = ToolDefinition(
    name="wiki_update",
    description=(
        "Apply a V4A-format patch to an existing page. The patch envelope is "
        "*** Begin Patch / *** Update File: <path> / @@ <context> @@ / "
        "context+/-/space lines / *** End Patch. On context drift, you'll get "
        "patch-conflict with the current file content so you can re-read and retry."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "page": {"type": "string"},
            "patch": {"type": "string", "description": "V4A patch text"},
            "author": {"type": "string"},
            "intent": {"type": "string"},
        },
        "required": ["page", "patch", "author"],
    },
    handler=handle_wiki_update,
)


async def handle_wiki_append(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({
        "type": "page-append",
        "connection_id": ctx.connection_id,
        "page": args["page"],
        "section_heading": args["section_heading"],
        "body": args["body"],
        "citations": args.get("citations", []),
        "after_heading": args.get("after_heading"),
        "author": args["author"],
        "intent": args.get("intent"),
    })
    return _ok(translate_daemon_response(response))


WIKI_APPEND = ToolDefinition(
    name="wiki_append",
    description=(
        "Append a new section to an existing page. Requires citations. "
        "Without after_heading, the section is appended at end of file. "
        "With after_heading, the section is inserted immediately after that "
        "heading's section closes. Multiple matches → uses the first and warns."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "page": {"type": "string"},
            "section_heading": {"type": "string"},
            "body": {"type": "string"},
            "citations": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            },
            "after_heading": {"type": "string"},
            "author": {"type": "string"},
            "intent": {"type": "string"},
        },
        "required": ["page", "section_heading", "body", "citations", "author"],
    },
    handler=handle_wiki_append,
)


# ---------------------------------------------------------------------------
# Maintenance-side
# ---------------------------------------------------------------------------

async def handle_wiki_issues_list(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({
        "type": "issues-list",
        "status_filter": args.get("status_filter"),
        "type_filter": args.get("type_filter"),
    })
    return _ok(translate_daemon_response(response))


WIKI_ISSUES_LIST = ToolDefinition(
    name="wiki_issues_list",
    description=(
        "List issues in the queue. Filter by status (open/resolved/wontfix) "
        "or type (broken-link, broken-citation, missing-markers, orphan, "
        "new-idea, compliance, claim-failed)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "status_filter": {"type": "string"},
            "type_filter": {"type": "string"},
        },
    },
    handler=handle_wiki_issues_list,
)


async def handle_wiki_issues_get(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({"type": "issues-get", "id": args["id"]})
    return _ok(translate_daemon_response(response))


WIKI_ISSUES_GET = ToolDefinition(
    name="wiki_issues_get",
    description="Read the full body of one issue by id.",
    input_schema={
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
    },
    handler=handle_wiki_issues_get,
)


async def handle_wiki_issues_resolve(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({
        "type": "issues-update",
        "id": args["id"],
        "status": "resolved",
        "author": args["author"],
    })
    return _ok(translate_daemon_response(response))


WIKI_ISSUES_RESOLVE = ToolDefinition(
    name="wiki_issues_resolve",
    description=(
        "Mark an issue as resolved. Session-aware: lands in your session "
        "commit. Use this after fixing the underlying problem (e.g. after "
        "wiki_update or wiki_append fixes a broken-link)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "author": {"type": "string"},
        },
        "required": ["id", "author"],
    },
    handler=handle_wiki_issues_resolve,
)


async def handle_wiki_talk_read(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({"type": "talk-read", "page": args["page"]})
    return _ok(translate_daemon_response(response))


WIKI_TALK_READ = ToolDefinition(
    name="wiki_talk_read",
    description=(
        "Read all entries on a page's talk page (full thread, including "
        "resolved entries). For most cases the digest folded into wiki_read "
        "is enough — use this only when you need the full thread history."
    ),
    input_schema={
        "type": "object",
        "properties": {"page": {"type": "string"}},
        "required": ["page"],
    },
    handler=handle_wiki_talk_read,
)


async def handle_wiki_talk_post(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({
        "type": "talk-append",
        "page": args["page"],
        "author": args["author"],
        "body": args["body"],
        "severity": args.get("severity", "suggestion"),
        "resolves": args.get("resolves", []),
    })
    return _ok(translate_daemon_response(response))


WIKI_TALK_POST = ToolDefinition(
    name="wiki_talk_post",
    description=(
        "Post a new entry on a page's talk page. Use this for half-formed "
        "ideas, ambiguous findings, contradictions, or anything you cannot "
        "yet cite to a source. Pass resolves=[N] to close prior entry N. "
        "Severity: critical | moderate | minor | suggestion | new_connection."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "page": {"type": "string"},
            "author": {"type": "string"},
            "body": {"type": "string"},
            "severity": {
                "type": "string",
                "enum": ["critical", "moderate", "minor", "suggestion", "new_connection"],
                "default": "suggestion",
            },
            "resolves": {
                "type": "array",
                "items": {"type": "integer"},
            },
        },
        "required": ["page", "author", "body"],
    },
    handler=handle_wiki_talk_post,
)


async def handle_wiki_talk_list(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({"type": "talk-list"})
    return _ok(translate_daemon_response(response))


WIKI_TALK_LIST = ToolDefinition(
    name="wiki_talk_list",
    description="List all pages that have a talk page (any entries).",
    input_schema={"type": "object", "properties": {}},
    handler=handle_wiki_talk_list,
)


async def handle_wiki_source_mark(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({
        "type": "source-mark",
        "source_path": args["source_path"],
        "status": args["status"],
        "author": args["author"],
    })
    return _ok(translate_daemon_response(response))


WIKI_SOURCE_MARK = ToolDefinition(
    name="wiki_source_mark",
    description=(
        "Update the reading_status of a source file in raw/. Call this to track "
        "your engagement with a source: 'in_progress' when you start reading it, "
        "'read' when you finish. The change is committed to git with a "
        "Source-Status trailer for audit. Valid statuses: unread, in_progress, read.\n\n"
        "Skill protocol:\n"
        "- Brief mode start → in_progress\n"
        "- Brief mode complete (no deep session) → read\n"
        "- Deep mode session start → in_progress\n"
        "- Deep mode plan complete → read"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source_path": {
                "type": "string",
                "description": "Path to the source file or its companion .md (must be under raw/)",
            },
            "status": {
                "type": "string",
                "enum": ["unread", "in_progress", "read"],
            },
            "author": {
                "type": "string",
                "description": "Your agent identifier (e.g. 'claude-researcher')",
            },
        },
        "required": ["source_path", "status", "author"],
    },
    handler=handle_wiki_source_mark,
)


async def handle_wiki_session_close(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({
        "type": "session-close",
        "connection_id": ctx.connection_id,
        "author": args["author"],
    })
    return _ok(translate_daemon_response(response))


WIKI_SESSION_CLOSE = ToolDefinition(
    name="wiki_session_close",
    description=(
        "Explicitly settle your session — commit all pending writes "
        "immediately instead of waiting for the inactivity timeout. "
        "Useful at clean breakpoints or before disconnecting. Idempotent: "
        "closing an already-settled session returns settled=false."
    ),
    input_schema={
        "type": "object",
        "properties": {"author": {"type": "string"}},
        "required": ["author"],
    },
    handler=handle_wiki_session_close,
)


# ---------------------------------------------------------------------------
# Inbox plan files
# ---------------------------------------------------------------------------

async def handle_wiki_inbox_create(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({
        "type": "inbox-create",
        "source_path": args["source_path"],
        "title": args["title"],
        "claims": args.get("claims", []),
        "author": args["author"],
    })
    return _ok(translate_daemon_response(response))


WIKI_INBOX_CREATE = ToolDefinition(
    name="wiki_inbox_create",
    description=(
        "Create a scaffolded inbox plan file for a Deep ingest session. "
        "Call this before any wiki write in Mode 3 — the plan file is the "
        "persistent cursor that lets you resume across sessions. Commits "
        "the plan file directly to git (outside the write session). "
        "Returns the plan_path to use with wiki_inbox_get, wiki_inbox_write."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source_path": {
                "type": "string",
                "description": "Path to the source file (e.g. 'raw/2026-04-09-paper.pdf' or absolute path)",
            },
            "title": {
                "type": "string",
                "description": "Human-readable title for the research plan (usually the source title)",
            },
            "claims": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Initial claim list — one-line scope per claim. Presented to user for approval before the loop starts.",
            },
            "author": {
                "type": "string",
                "description": "Your agent identifier",
            },
        },
        "required": ["source_path", "title", "author"],
    },
    handler=handle_wiki_inbox_create,
)


async def handle_wiki_inbox_get(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({
        "type": "inbox-get",
        "plan_path": args["plan_path"],
    })
    return _ok(translate_daemon_response(response))


WIKI_INBOX_GET = ToolDefinition(
    name="wiki_inbox_get",
    description=(
        "Read the current content and frontmatter of an inbox plan file. "
        "Use this when resuming a Deep ingest session to reconstruct the "
        "task list from unchecked claims."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "plan_path": {
                "type": "string",
                "description": "Relative path to the plan file (e.g. 'inbox/2026-04-09-paper-plan.md')",
            },
        },
        "required": ["plan_path"],
    },
    handler=handle_wiki_inbox_get,
)


async def handle_wiki_inbox_write(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({
        "type": "inbox-write",
        "plan_path": args["plan_path"],
        "content": args["content"],
        "author": args["author"],
    })
    return _ok(translate_daemon_response(response))


WIKI_INBOX_WRITE = ToolDefinition(
    name="wiki_inbox_write",
    description=(
        "Write the full content of an inbox plan file and commit it to git. "
        "Use this at session checkpoints: read the current content with "
        "wiki_inbox_get, update checkboxes and append session notes, then "
        "call this to persist and commit. Always call before wiki_session_close."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "plan_path": {
                "type": "string",
                "description": "Relative path to the plan file (from wiki_inbox_create response)",
            },
            "content": {
                "type": "string",
                "description": "Full file content (frontmatter + body). Preserve the frontmatter from wiki_inbox_get.",
            },
            "author": {
                "type": "string",
                "description": "Your agent identifier",
            },
        },
        "required": ["plan_path", "content", "author"],
    },
    handler=handle_wiki_inbox_write,
)


async def handle_wiki_inbox_list(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({"type": "inbox-list"})
    return _ok(translate_daemon_response(response))


WIKI_INBOX_LIST = ToolDefinition(
    name="wiki_inbox_list",
    description=(
        "List all inbox plan files with their status and unchecked claim count. "
        "Use this when resuming work to find the right plan file, or to "
        "surface in-progress ingests for the researcher."
    ),
    input_schema={"type": "object", "properties": {}},
    handler=handle_wiki_inbox_list,
)


# ---------------------------------------------------------------------------
# Registration list
# ---------------------------------------------------------------------------

WIKI_TOOLS: list[ToolDefinition] = [
    WIKI_SEARCH,
    WIKI_READ,
    WIKI_MANIFEST,
    WIKI_STATUS,
    WIKI_QUERY,
    WIKI_INGEST,
    WIKI_LINT,
    WIKI_CREATE,
    WIKI_UPDATE,
    WIKI_APPEND,
    WIKI_ISSUES_LIST,
    WIKI_ISSUES_GET,
    WIKI_ISSUES_RESOLVE,
    WIKI_TALK_READ,
    WIKI_TALK_POST,
    WIKI_TALK_LIST,
    WIKI_SOURCE_MARK,
    WIKI_SESSION_CLOSE,
    WIKI_INBOX_CREATE,
    WIKI_INBOX_GET,
    WIKI_INBOX_WRITE,
    WIKI_INBOX_LIST,
]
