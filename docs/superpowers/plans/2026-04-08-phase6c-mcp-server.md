# Phase 6c: MCP Server — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Spec reference:** `docs/superpowers/specs/2026-04-08-phase6-mcp-server-design.md`. Read §"MCP tool surface" (the tool tables + "Tools deliberately not in the surface") and §"Vault binding" before starting Task 1. The error code table is the contract for Task 11.
>
> **Prerequisites:** Phase 6a (visibility & severity) and Phase 6b (write surface) must both be merged. Phase 6c is the **thinnest** of the three sub-phases — every tool is a pass-through to a daemon route that already exists. The hard work is in 6b.

**Goal:** Ship the MCP server that lets MCP-capable clients (Claude Code, Claude Desktop, Cursor, agent frameworks) read, query, and write through the daemon. Wrap every existing daemon route as an MCP tool with a clear description, route the response shapes through, and translate error codes into structured MCP tool errors. Add a CLI entry point (`llm-wiki mcp`) that auto-starts the daemon if it isn't already running, mirroring the existing `llm-wiki search` / `llm-wiki query` ergonomics.

**Architecture:** A new `src/llm_wiki/mcp/` package with three modules: `server.py` (the MCP server entry point and tool definitions), `tools.py` (one async function per tool that calls into the daemon via `DaemonClient`), and `errors.py` (translation from daemon `code` strings into MCP-flavored error responses). The CLI gains one new subcommand `llm-wiki mcp [vault]` that resolves the vault path from `LLM_WIKI_VAULT` env var or the positional arg, ensures the daemon is running (auto-starting it via the existing `lifecycle.py` helpers), and runs the MCP server's stdio transport. Tests use the official Python MCP SDK's in-process test client to exercise each tool through the protocol.

**Tech Stack:** Python 3.11+, the official `mcp` Python SDK (new dependency), existing `DaemonClient` for IPC. No new test infrastructure beyond the SDK's test client.

---

## File Structure

```
src/llm_wiki/
  mcp/
    __init__.py        # NEW: package marker
    server.py          # NEW: MCPServer class, stdio entry point, tool registration
    tools.py           # NEW: one async def per tool, each calling DaemonClient
    errors.py          # NEW: code → MCP error translation, response shape helpers
  cli/
    main.py            # MODIFIED: add `llm-wiki mcp` subcommand

tests/
  test_mcp/
    __init__.py        # NEW
    test_server.py     # NEW: server lifecycle + tool registration
    test_tools.py      # NEW: each tool through the MCP test client
    test_errors.py     # NEW: code translation
    test_cli.py        # NEW: `llm-wiki mcp` command (vault binding, env var)

pyproject.toml         # MODIFIED: add `mcp` to dependencies
```

**Type flow across tasks:**

- `mcp.server.MCPServer(vault_path: Path, client: DaemonClient)` is the entry point. It constructs an `mcp.server.Server` (from the SDK), generates **a single `connection_id` UUID at startup** (one per MCP stdio session), registers every tool from `tools.py`, and runs `stdio_server()` as its event loop. The `connection_id` is held as a server attribute and threaded into every tool handler call. Construction is sync; running is async.
- `mcp.tools.WIKI_TOOLS: list[ToolDefinition]` is the registration list. Each `ToolDefinition` is a small dataclass `(name: str, description: str, input_schema: dict, handler: Callable[[ToolContext, dict], Awaitable[list[TextContent]]])`. The MCP SDK takes a list-of-tools and a per-tool handler — `WIKI_TOOLS` is the source of truth so adding/removing a tool is a one-line edit.
- `mcp.tools.ToolContext` is a small dataclass `(client: DaemonClient, connection_id: str)` passed into every handler. This is what threads the MCP-session-stable `connection_id` from `MCPServer` down into individual tool handlers without each handler needing to know how it's stored. Read tools ignore the `connection_id`; write tools include it in their daemon request payload.
- Each tool handler in `tools.py` follows this shape:
  ```python
  async def handle_wiki_search(ctx: ToolContext, args: dict) -> list[TextContent]:
      # Read tools don't need connection_id
      response = await ctx.client.arequest({"type": "search", **args})
      if response.get("status") == "error":
          raise McpToolError(response.get("code"), response.get("message"), response)
      return [TextContent(type="text", text=json.dumps(response, indent=2))]

  async def handle_wiki_create(ctx: ToolContext, args: dict) -> list[TextContent]:
      # Write tools MUST include connection_id from the MCP session
      response = await ctx.client.arequest({
          "type": "page-create",
          "connection_id": ctx.connection_id,
          **args,
      })
      ...
  ```
  Handlers `await ctx.client.arequest(...)` rather than calling the synchronous `client.request(...)` so the MCP server's event loop never depends on `DaemonClient`'s `_run_coroutine_in_running_loop` helper (which reaches into private asyncio internals like `loop._ready` / `loop._scheduled` / `_current_tasks`). The `arequest` async method is added to `DaemonClient` in Task 2.
- `mcp.errors.McpToolError(code: str | None, message: str, details: dict)` is the exception the handlers raise on daemon errors. `mcp.errors.format_error(exc) -> str` builds the structured error message that the MCP SDK surfaces to the agent.
- `cli.main` gains a `mcp` subcommand:
  ```python
  @cli.command(name="mcp")
  @click.argument("vault_path", type=click.Path(exists=True, path_type=Path), required=False)
  def mcp_command(vault_path: Path | None) -> None: ...
  ```
- The CLI resolves the vault in the priority order from the spec: `LLM_WIKI_VAULT` env var first, then the positional arg. If neither is set, exits with a clear error. Auto-starts the daemon via the existing `_get_client(vault_path, auto_start=True)` helper from the same file.

**Cross-cutting reminders:**
- Phase 6c is a thin wrapper. **Do not add business logic to the MCP layer** — every decision (validation, routing, journaling) lives in the daemon. The MCP layer's job is exactly two things: argument shape and error translation.
- Tool descriptions tell the agent **what and why**, never **how**. The "how" lives in the daemon. (PHILOSOPHY.md Principle 5.)
- Every write tool (`wiki_create`, `wiki_update`, `wiki_append`, `wiki_talk_post`, `wiki_issues_resolve`, `wiki_ingest`, `wiki_session_close`) requires `author` in its input schema. The MCP SDK enforces required arguments at the protocol level, so an agent that omits `author` gets a schema error before the daemon ever sees the request.
- **The `connection_id` is generated once at MCP server startup** (one UUID per stdio session) and threaded into every write tool's daemon request payload via `ToolContext`. This is load-bearing: the daemon's protocol is one-message-per-Unix-socket-connection, so without an explicit `connection_id` from the MCP layer, every write would land in its own session and session grouping would be useless. The `wiki_session_close` tool uses the same `connection_id` so the daemon's `get_active(author, connection_id)` lookup finds exactly the session this MCP client owns.
- Read tools (`wiki_search`, `wiki_read`, `wiki_manifest`, `wiki_status`, `wiki_query`, `wiki_lint`, `wiki_issues_*`, `wiki_talk_read`, `wiki_talk_list`) do **not** require `author` — they don't open sessions.

---

### Task 1: Add the `mcp` SDK dependency

**Files:**
- Modify: `pyproject.toml` (add `mcp` to `dependencies`)

This is the smallest task — just declares the new runtime dep so the rest of the plan can `import mcp`.

- [ ] **Step 1: Edit `pyproject.toml`**

Edit the `dependencies` list:

```toml
[project]
name = "llm-wiki"
version = "0.1.0"
description = "Agent-first knowledge base tool — wiki over RAG"
requires-python = ">=3.11"
dependencies = [
    "pyyaml>=6.0",
    "tantivy>=0.22.0",
    "click>=8.0",
    "litellm>=1.0.0",
    "liteparse>=0.2.0",
    "mcp>=1.0.0",
]
```

- [ ] **Step 2: Sync the environment**

Run: `pip install -e .`
Expected: Installs the `mcp` package (and its dependencies) into the editable environment.

- [ ] **Step 3: Verify the import works**

Run: `python -c "import mcp; import mcp.server; print(mcp.__version__)"`
Expected: A version string is printed without `ModuleNotFoundError`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "feat: phase 6c — add mcp SDK dependency"
```

---

### Task 2: Package skeleton + async client helper

**Files:**
- Create: `src/llm_wiki/mcp/__init__.py`
- Create: `src/llm_wiki/mcp/errors.py` (skeleton)
- Modify: `src/llm_wiki/daemon/client.py` (add `arequest` async method)
- Create: `tests/test_mcp/__init__.py`
- Create: `tests/test_mcp/test_errors.py`
- Modify: `tests/test_daemon/test_client.py` (add `arequest` test) — create the file if it doesn't exist

The `errors.py` module is the smallest piece on the MCP side and gets landed first because every other MCP module imports it. Alongside it we add a small async helper to `DaemonClient` (`arequest`) so that the MCP tool handlers in Tasks 3–6 can `await` daemon calls directly instead of going through the synchronous `request()` path. The synchronous path uses `_run_coroutine_in_running_loop` to drive the loop manually via private asyncio internals (`asyncio.tasks._current_tasks`, `loop._ready`, `loop._scheduled`, `loop._selector`); that helper exists for compatibility with sync callers inside async tests, but the MCP server is async top to bottom and shouldn't depend on it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mcp/test_errors.py`:

```python
from __future__ import annotations

import pytest


def test_mcp_tool_error_carries_code_and_details():
    from llm_wiki.mcp.errors import McpToolError
    exc = McpToolError(
        code="missing-citations",
        message="wiki_create requires at least one citation",
        details={"page": "foo"},
    )
    assert exc.code == "missing-citations"
    assert "citation" in str(exc)
    assert exc.details == {"page": "foo"}


def test_format_error_includes_code_and_message():
    from llm_wiki.mcp.errors import McpToolError, format_error
    exc = McpToolError(
        code="patch-conflict",
        message="context drift",
        details={"current_excerpt": "actual content"},
    )
    formatted = format_error(exc)
    assert "patch-conflict" in formatted
    assert "context drift" in formatted
    assert "actual content" in formatted


def test_format_error_handles_missing_code():
    from llm_wiki.mcp.errors import McpToolError, format_error
    exc = McpToolError(code=None, message="something failed", details={})
    formatted = format_error(exc)
    assert "something failed" in formatted


def test_translate_daemon_response_passes_through_ok():
    from llm_wiki.mcp.errors import translate_daemon_response
    response = {"status": "ok", "page_path": "wiki/foo.md"}
    # No exception raised
    result = translate_daemon_response(response)
    assert result == response


def test_translate_daemon_response_raises_on_error():
    from llm_wiki.mcp.errors import McpToolError, translate_daemon_response
    response = {
        "status": "error",
        "code": "missing-citations",
        "message": "no citations",
        "page": "foo",
    }
    with pytest.raises(McpToolError) as exc_info:
        translate_daemon_response(response)
    assert exc_info.value.code == "missing-citations"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_mcp/test_errors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'llm_wiki.mcp'`.

- [ ] **Step 3: Create the package skeletons**

Create empty `src/llm_wiki/mcp/__init__.py` and `tests/test_mcp/__init__.py`.

- [ ] **Step 4: Create `errors.py`**

Create `src/llm_wiki/mcp/errors.py`:

```python
"""Error translation between daemon responses and MCP tool errors.

The daemon returns `{"status": "error", "code": "...", "message": "..."}`.
The MCP SDK surfaces tool errors as exceptions raised by the tool handler.
This module bridges the two.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class McpToolError(Exception):
    """Raised by an MCP tool handler when the daemon returned an error.

    The MCP SDK turns this into a structured error response. The `code`
    field is the daemon's error code (e.g. 'patch-conflict', 'missing-citations')
    so the agent can act on it programmatically.
    """
    code: str | None
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(self.message)


def format_error(exc: McpToolError) -> str:
    """Build a human-readable error message that carries the code + details.

    The MCP SDK doesn't have a structured-error type per se — errors are
    rendered as strings. We pack everything into a JSON blob inside the
    string so the agent can parse it back if it wants the details.
    """
    payload = {"message": exc.message}
    if exc.code is not None:
        payload["code"] = exc.code
    if exc.details:
        payload["details"] = exc.details
    return json.dumps(payload, indent=2)


def translate_daemon_response(response: dict) -> dict:
    """Pass through ok responses; raise McpToolError on daemon errors.

    The daemon's error responses carry `status="error"`, `code` (sometimes),
    `message`, and arbitrary additional fields. We pack the additional
    fields into `details` for the agent.
    """
    if response.get("status") == "error":
        code = response.get("code")
        message = response.get("message", "Unknown daemon error")
        details = {
            k: v for k, v in response.items()
            if k not in ("status", "code", "message")
        }
        raise McpToolError(code=code, message=message, details=details)
    return response
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_mcp/test_errors.py -v`
Expected: PASS for all five tests.

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/mcp/__init__.py src/llm_wiki/mcp/errors.py \
        tests/test_mcp/__init__.py tests/test_mcp/test_errors.py
git commit -m "feat: phase 6c — mcp package skeleton + error translation"
```

- [ ] **Step 7: Write a failing test for `DaemonClient.arequest`**

Append to `tests/test_daemon/test_client.py` (create the file if it doesn't exist):

```python
import asyncio

import pytest

from llm_wiki.daemon.client import DaemonClient


@pytest.mark.asyncio
async def test_arequest_round_trips_through_async_path(tmp_path):
    """`arequest` is the async public entry point used by the MCP server.

    It must NOT depend on the `_run_coroutine_in_running_loop` helper that
    `request()` falls back to when called from inside an event loop. We
    verify this indirectly by exercising the path against a real Unix
    socket — if `arequest` is just a thin `await self._async_request(msg)`
    wrapper, this round-trips cleanly.
    """
    sock_path = tmp_path / "echo.sock"

    async def echo_server(reader, writer):
        from llm_wiki.daemon.protocol import read_message, write_message
        msg = await read_message(reader)
        await write_message(writer, {"status": "ok", "echo": msg})
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(echo_server, path=str(sock_path))
    try:
        client = DaemonClient(sock_path)
        resp = await client.arequest({"type": "ping", "n": 1})
        assert resp["status"] == "ok"
        assert resp["echo"] == {"type": "ping", "n": 1}
    finally:
        server.close()
        await server.wait_closed()
```

- [ ] **Step 8: Run the test to verify it fails**

Run: `pytest tests/test_daemon/test_client.py::test_arequest_round_trips_through_async_path -v`
Expected: FAIL with `AttributeError: 'DaemonClient' object has no attribute 'arequest'`.

- [ ] **Step 9: Add `arequest` to `DaemonClient`**

Edit `src/llm_wiki/daemon/client.py`. Just below the existing `_async_request` method, add a public async wrapper:

```python
    async def arequest(self, msg: dict) -> dict:
        """Async public entry point — `await client.arequest(msg)`.

        Use this from any code that already runs inside an event loop
        (the MCP server, async test code, future async daemons). It
        bypasses the synchronous `request()` path entirely, so it
        does NOT touch `_run_coroutine_in_running_loop`'s private
        asyncio internals. Functionally identical to `request()` but
        type-honest about being async.
        """
        return await self._async_request(msg)
```

The existing `request()` method, `_sync_request()`, `_async_request()`, and `_run_coroutine_in_running_loop()` are unchanged — sync callers (e.g. `cli/main.py`'s `_get_client(...).is_running()`) still work exactly as before.

- [ ] **Step 10: Run the test to verify it passes**

Run: `pytest tests/test_daemon/test_client.py::test_arequest_round_trips_through_async_path -v`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add src/llm_wiki/daemon/client.py tests/test_daemon/test_client.py
git commit -m "feat: phase 6c — DaemonClient.arequest async helper for MCP handlers"
```

---

### Task 3: Tool definitions and handlers — read-side

**Files:**
- Create: `src/llm_wiki/mcp/tools.py` (with the read-side tools: `wiki_search`, `wiki_read`, `wiki_manifest`, `wiki_status`)
- Create: `tests/test_mcp/test_tools.py` (with read-side tests)

The read-side tools are the simplest — no `author`, no session, no error codes beyond "page not found." Each handler is ~5 lines.

The tool input schemas follow the JSON Schema dialect that the MCP SDK uses. Required arguments go in the `required` array; the SDK rejects calls missing required fields before the handler runs.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mcp/test_tools.py`:

```python
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from llm_wiki.config import VaultConfig, WikiConfig
from llm_wiki.daemon.client import DaemonClient
from llm_wiki.daemon.server import DaemonServer


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True)
    (path / ".gitignore").write_text("# placeholder\n")
    subprocess.run(["git", "-C", str(path), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "initial"], check=True)


@pytest.fixture
def mock_client():
    """A DaemonClient stub whose .arequest() returns canned responses.

    `MagicMock(spec=DaemonClient)` introspects the class so `arequest`
    (declared `async def`) is auto-created as an `AsyncMock`. Tests set
    `mock_client.arequest.return_value = {...}` and the value is what
    `await client.arequest(...)` resolves to.
    """
    client = MagicMock(spec=DaemonClient)
    return client


@pytest.fixture
def mock_ctx(mock_client):
    """A ToolContext wrapping the mock client + a stable test connection_id.

    Use this fixture for handler invocations. The underlying `mock_client`
    fixture (which `mock_ctx` depends on, and which tests can also request
    directly) is where you set return values and inspect `call_args` —
    e.g. `mock_client.arequest.return_value = {...}` and
    `mock_client.arequest.call_args[0][0]`. Because `MagicMock(spec=DaemonClient)`
    introspects the class, `arequest` is auto-created as an `AsyncMock`,
    so `await client.arequest(...)` resolves to whatever `return_value` is set.
    """
    from llm_wiki.mcp.tools import ToolContext
    return ToolContext(client=mock_client, connection_id="test-mcp-conn")


@pytest.mark.asyncio
async def test_wiki_search_tool_passes_query_to_daemon(mock_client, mock_ctx):
    from llm_wiki.mcp.tools import handle_wiki_search

    mock_client.arequest.return_value = {
        "status": "ok",
        "results": [
            {"name": "foo", "score": 0.9, "manifest": "...", "matches": []}
        ],
    }
    result = await handle_wiki_search(mock_ctx, {"query": "k-means", "limit": 5})
    mock_client.arequest.assert_called_once()
    sent = mock_client.arequest.call_args[0][0]
    assert sent["type"] == "search"
    assert sent["query"] == "k-means"
    assert sent["limit"] == 5
    assert "k-means" not in result[0].text or "foo" in result[0].text


@pytest.mark.asyncio
async def test_wiki_search_tool_raises_on_daemon_error(mock_client, mock_ctx):
    from llm_wiki.mcp.errors import McpToolError
    from llm_wiki.mcp.tools import handle_wiki_search

    mock_client.arequest.return_value = {
        "status": "error",
        "message": "boom",
    }
    with pytest.raises(McpToolError):
        await handle_wiki_search(mock_ctx, {"query": "x"})


@pytest.mark.asyncio
async def test_wiki_read_tool_passes_viewport(mock_client, mock_ctx):
    from llm_wiki.mcp.tools import handle_wiki_read
    mock_client.arequest.return_value = {
        "status": "ok",
        "content": "page content",
        "issues": {"open_count": 0, "by_severity": {}, "items": []},
        "talk": {
            "entry_count": 0, "open_count": 0, "by_severity": {},
            "summary": "", "recent_critical": [], "recent_moderate": [],
        },
    }
    await handle_wiki_read(mock_ctx, {
        "page_name": "foo", "viewport": "section", "section": "Methods",
    })
    sent = mock_client.arequest.call_args[0][0]
    assert sent["type"] == "read"
    assert sent["page_name"] == "foo"
    assert sent["viewport"] == "section"
    assert sent["section"] == "Methods"


@pytest.mark.asyncio
async def test_wiki_manifest_tool_passes_budget(mock_client, mock_ctx):
    from llm_wiki.mcp.tools import handle_wiki_manifest
    mock_client.arequest.return_value = {"status": "ok", "content": "manifest text"}
    await handle_wiki_manifest(mock_ctx, {"budget": 8000})
    sent = mock_client.arequest.call_args[0][0]
    assert sent["type"] == "manifest"
    assert sent["budget"] == 8000


@pytest.mark.asyncio
async def test_wiki_status_tool(mock_client, mock_ctx):
    from llm_wiki.mcp.tools import handle_wiki_status
    mock_client.arequest.return_value = {"status": "ok", "page_count": 4}
    result = await handle_wiki_status(mock_ctx, {})
    assert result
    assert "page_count" in result[0].text


def test_wiki_tools_includes_read_side():
    """The WIKI_TOOLS registration list includes the read-side tools."""
    from llm_wiki.mcp.tools import WIKI_TOOLS
    names = {t.name for t in WIKI_TOOLS}
    assert "wiki_search" in names
    assert "wiki_read" in names
    assert "wiki_manifest" in names
    assert "wiki_status" in names
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_mcp/test_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'llm_wiki.mcp.tools'`.

- [ ] **Step 3: Implement `tools.py` with the read-side tools**

Create `src/llm_wiki/mcp/tools.py`:

```python
"""MCP tool definitions and handlers — thin shims over DaemonClient.

Each tool is one async function that:
  1. Takes (ctx: ToolContext, args: dict) — ctx carries the DaemonClient
     plus the MCP-session-stable connection_id
  2. Sends one daemon request (write tools include ctx.connection_id)
  3. Translates the response (raising McpToolError on daemon errors)
  4. Returns a list[TextContent] for the MCP SDK

Adding/removing tools is a one-line edit to WIKI_TOOLS at the bottom.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from mcp.types import TextContent

from llm_wiki.daemon.client import DaemonClient
from llm_wiki.mcp.errors import McpToolError, translate_daemon_response


@dataclass
class ToolContext:
    """Threaded into every tool handler.

    The `connection_id` is generated once at MCP server startup
    (one UUID per stdio session) and stays stable for every tool call
    that this MCP server makes. The daemon's `SessionRegistry` keys on
    `(author, connection_id)` so all writes from one MCP session group
    into a single daemon session that settles cleanly via
    `wiki_session_close` or the inactivity timer.
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_mcp/test_tools.py -v`
Expected: PASS for all six read-side tests.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/mcp/tools.py tests/test_mcp/test_tools.py
git commit -m "feat: phase 6c — read-side tools (search, read, manifest, status)"
```

---

### Task 4: Tool handlers — query-side (`query`, `ingest`, `lint`)

**Files:**
- Modify: `src/llm_wiki/mcp/tools.py` (add three query-side handlers)
- Modify: `tests/test_mcp/test_tools.py` (add tests)

These tools are identical in shape to the read-side ones — just different daemon route names and slightly richer input schemas. `wiki_ingest` requires `author` because Phase 6b made the underlying route session-aware.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mcp/test_tools.py`:

```python
@pytest.mark.asyncio
async def test_wiki_query_tool(mock_client, mock_ctx):
    from llm_wiki.mcp.tools import handle_wiki_query
    mock_client.arequest.return_value = {
        "status": "ok",
        "answer": "answer text",
        "citations": ["foo"],
        "outcome": "complete",
        "needs_more_budget": False,
        "log": {},
    }
    await handle_wiki_query(mock_ctx, {"question": "What is k-means?"})
    sent = mock_client.arequest.call_args[0][0]
    assert sent["type"] == "query"
    assert sent["question"] == "What is k-means?"


@pytest.mark.asyncio
async def test_wiki_ingest_tool_passes_author(mock_client, mock_ctx):
    from llm_wiki.mcp.tools import handle_wiki_ingest
    mock_client.arequest.return_value = {
        "status": "ok", "pages_created": 2, "pages_updated": 0,
        "created": ["a", "b"], "updated": [], "concepts_found": 2,
    }
    await handle_wiki_ingest(mock_ctx, {
        "source_path": "/raw/paper.pdf",
        "author": "alice",
    })
    sent = mock_client.arequest.call_args[0][0]
    assert sent["type"] == "ingest"
    assert sent["source_path"] == "/raw/paper.pdf"
    assert sent["author"] == "alice"


@pytest.mark.asyncio
async def test_wiki_lint_tool(mock_client, mock_ctx):
    from llm_wiki.mcp.tools import handle_wiki_lint
    mock_client.arequest.return_value = {
        "status": "ok",
        "structural": {},
        "attention_map": {"pages_needing_attention": [], "totals": {}, "by_page": {}},
    }
    await handle_wiki_lint(mock_ctx, {})
    sent = mock_client.arequest.call_args[0][0]
    assert sent["type"] == "lint"


def test_wiki_tools_includes_query_side():
    from llm_wiki.mcp.tools import WIKI_TOOLS
    names = {t.name for t in WIKI_TOOLS}
    assert "wiki_query" in names
    assert "wiki_ingest" in names
    assert "wiki_lint" in names
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_mcp/test_tools.py -k "query or ingest or lint or query_side" -v`
Expected: FAIL — handlers don't exist yet.

- [ ] **Step 3: Add the query-side handlers**

Append to `src/llm_wiki/mcp/tools.py` (before the `WIKI_TOOLS` registration):

```python
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
    })
    return _ok(translate_daemon_response(response))


WIKI_INGEST = ToolDefinition(
    name="wiki_ingest",
    description=(
        "Ingest a source file (PDF, DOCX, markdown, URL, image) into the "
        "wiki. The daemon runs extraction, identifies concepts, and creates "
        "or updates pages. Every internal write journals under your session "
        "so the whole ingest produces one git commit attributed to you."
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
```

Update the `WIKI_TOOLS` list to include the new entries:

```python
WIKI_TOOLS: list[ToolDefinition] = [
    WIKI_SEARCH,
    WIKI_READ,
    WIKI_MANIFEST,
    WIKI_STATUS,
    WIKI_QUERY,
    WIKI_INGEST,
    WIKI_LINT,
]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_mcp/test_tools.py -v`
Expected: PASS for all read-side and query-side tests.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/mcp/tools.py tests/test_mcp/test_tools.py
git commit -m "feat: phase 6c — query-side tools (query, ingest, lint)"
```

---

### Task 5: Tool handlers — write-side (`create`, `update`, `append`)

**Files:**
- Modify: `src/llm_wiki/mcp/tools.py`
- Modify: `tests/test_mcp/test_tools.py`

The three write tools require `author` and translate every Phase 6b error code into a structured `McpToolError`. Each handler also includes `"connection_id": ctx.connection_id` in the daemon request payload — this is the load-bearing piece that lets all writes from one MCP stdio session group into one daemon session. The handlers themselves are still ~6 lines each — the daemon does all the work.

> **Note on `wiki_talk_post` and `wiki_issues_resolve`:** these are also write-side tools and the spec at §"The journal" lists them as session-aware. However, the underlying daemon routes (`talk-append`, `issues-update`) are pre-Phase-6b routes that don't yet accept `connection_id` and don't yet flow through `PageWriteService`. The handlers in this plan deliberately do **not** include `connection_id` for those tools — adding it would have no effect until those daemon routes are session-aware-ified in a follow-up phase. Track this gap in `docs/codebase-audit/` (or open an issue) so it isn't lost.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mcp/test_tools.py`:

```python
@pytest.mark.asyncio
async def test_wiki_create_tool_passes_all_fields(mock_client, mock_ctx):
    from llm_wiki.mcp.tools import handle_wiki_create
    mock_client.arequest.return_value = {
        "status": "ok",
        "page_path": "wiki/foo.md",
        "journal_id": "1",
        "session_id": "abc",
        "content_hash": "sha256:x",
    }
    await handle_wiki_create(mock_ctx, {
        "title": "Foo",
        "body": "body [[raw/x.pdf]]",
        "citations": ["raw/x.pdf"],
        "author": "alice",
        "intent": "test",
        "tags": ["a", "b"],
    })
    sent = mock_client.arequest.call_args[0][0]
    assert sent["type"] == "page-create"
    assert sent["title"] == "Foo"
    assert sent["citations"] == ["raw/x.pdf"]
    assert sent["author"] == "alice"
    assert sent["tags"] == ["a", "b"]
    # Connection_id from ToolContext is threaded into the daemon request
    assert sent["connection_id"] == "test-mcp-conn"


@pytest.mark.asyncio
async def test_wiki_create_tool_raises_missing_citations(mock_client, mock_ctx):
    from llm_wiki.mcp.errors import McpToolError
    from llm_wiki.mcp.tools import handle_wiki_create
    mock_client.arequest.return_value = {
        "status": "error",
        "code": "missing-citations",
        "message": "no citations",
    }
    with pytest.raises(McpToolError) as exc_info:
        await handle_wiki_create(mock_ctx, {
            "title": "Foo", "body": "body", "citations": [], "author": "alice",
        })
    assert exc_info.value.code == "missing-citations"


@pytest.mark.asyncio
async def test_wiki_update_tool_passes_patch(mock_client, mock_ctx):
    from llm_wiki.mcp.tools import handle_wiki_update
    mock_client.arequest.return_value = {
        "status": "ok", "page_path": "wiki/foo.md",
        "journal_id": "1", "session_id": "s", "content_hash": "h",
        "diff_summary": "+1 -1",
    }
    await handle_wiki_update(mock_ctx, {
        "page": "foo",
        "patch": "*** Begin Patch\n*** Update File: wiki/foo.md\n@@ @@\n+x\n*** End Patch",
        "author": "alice",
    })
    sent = mock_client.arequest.call_args[0][0]
    assert sent["type"] == "page-update"
    assert sent["page"] == "foo"
    assert "Begin Patch" in sent["patch"]


@pytest.mark.asyncio
async def test_wiki_update_tool_raises_patch_conflict(mock_client, mock_ctx):
    from llm_wiki.mcp.errors import McpToolError
    from llm_wiki.mcp.tools import handle_wiki_update
    mock_client.arequest.return_value = {
        "status": "error",
        "code": "patch-conflict",
        "message": "context drift",
        "current_excerpt": "actual content",
    }
    with pytest.raises(McpToolError) as exc_info:
        await handle_wiki_update(mock_ctx, {
            "page": "foo", "patch": "x", "author": "alice",
        })
    assert exc_info.value.code == "patch-conflict"
    assert "actual content" in exc_info.value.details["current_excerpt"]


@pytest.mark.asyncio
async def test_wiki_append_tool(mock_client, mock_ctx):
    from llm_wiki.mcp.tools import handle_wiki_append
    mock_client.arequest.return_value = {
        "status": "ok", "page_path": "wiki/foo.md",
        "journal_id": "1", "session_id": "s", "content_hash": "h",
    }
    await handle_wiki_append(mock_ctx, {
        "page": "foo",
        "section_heading": "New",
        "body": "content [[raw/x.pdf]]",
        "citations": ["raw/x.pdf"],
        "after_heading": "Methods",
        "author": "alice",
    })
    sent = mock_client.arequest.call_args[0][0]
    assert sent["type"] == "page-append"
    assert sent["section_heading"] == "New"
    assert sent["after_heading"] == "Methods"


def test_wiki_tools_includes_write_side():
    from llm_wiki.mcp.tools import WIKI_TOOLS
    names = {t.name for t in WIKI_TOOLS}
    assert "wiki_create" in names
    assert "wiki_update" in names
    assert "wiki_append" in names
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_mcp/test_tools.py -k "create or update or append or write_side" -v`
Expected: FAIL.

- [ ] **Step 3: Add the write-side handlers**

Append to `src/llm_wiki/mcp/tools.py`:

```python
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
```

Update the registration list:

```python
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
]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_mcp/test_tools.py -v`
Expected: PASS for every test so far.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/mcp/tools.py tests/test_mcp/test_tools.py
git commit -m "feat: phase 6c — write-side tools (create, update, append)"
```

---

### Task 6: Tool handlers — maintenance-side (`issues_*`, `talk_*`, `session_close`)

**Files:**
- Modify: `src/llm_wiki/mcp/tools.py`
- Modify: `tests/test_mcp/test_tools.py`

Seven more tools, all thin shims:
- `wiki_issues_list`, `wiki_issues_get`, `wiki_issues_resolve`
- `wiki_talk_read`, `wiki_talk_post` (with `severity` and `resolves`), `wiki_talk_list`
- `wiki_session_close`

`wiki_issues_resolve`, `wiki_talk_post`, and `wiki_session_close` require `author`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mcp/test_tools.py`:

```python
@pytest.mark.asyncio
async def test_wiki_issues_list_tool(mock_client, mock_ctx):
    from llm_wiki.mcp.tools import handle_wiki_issues_list
    mock_client.arequest.return_value = {"status": "ok", "issues": []}
    await handle_wiki_issues_list(mock_ctx, {"status_filter": "open"})
    sent = mock_client.arequest.call_args[0][0]
    assert sent["type"] == "issues-list"
    assert sent["status_filter"] == "open"


@pytest.mark.asyncio
async def test_wiki_issues_get_tool(mock_client, mock_ctx):
    from llm_wiki.mcp.tools import handle_wiki_issues_get
    mock_client.arequest.return_value = {"status": "ok", "issue": {}}
    await handle_wiki_issues_get(mock_ctx, {"id": "broken-link-foo-abc"})
    sent = mock_client.arequest.call_args[0][0]
    assert sent["type"] == "issues-get"
    assert sent["id"] == "broken-link-foo-abc"


@pytest.mark.asyncio
async def test_wiki_issues_resolve_tool_requires_author(mock_client, mock_ctx):
    from llm_wiki.mcp.tools import handle_wiki_issues_resolve
    mock_client.arequest.return_value = {"status": "ok"}
    await handle_wiki_issues_resolve(mock_ctx, {
        "id": "broken-link-foo-abc",
        "author": "alice",
    })
    sent = mock_client.arequest.call_args[0][0]
    assert sent["type"] == "issues-update"
    assert sent["status"] == "resolved"


@pytest.mark.asyncio
async def test_wiki_talk_post_tool_passes_severity_and_resolves(mock_client, mock_ctx):
    from llm_wiki.mcp.tools import handle_wiki_talk_post
    mock_client.arequest.return_value = {"status": "ok"}
    await handle_wiki_talk_post(mock_ctx, {
        "page": "foo",
        "body": "x",
        "author": "alice",
        "severity": "critical",
        "resolves": [3],
    })
    sent = mock_client.arequest.call_args[0][0]
    assert sent["type"] == "talk-append"
    assert sent["severity"] == "critical"
    assert sent["resolves"] == [3]


@pytest.mark.asyncio
async def test_wiki_session_close_tool(mock_client, mock_ctx):
    from llm_wiki.mcp.tools import handle_wiki_session_close
    mock_client.arequest.return_value = {
        "status": "ok", "settled": True, "commit_sha": "abc",
    }
    await handle_wiki_session_close(mock_ctx, {"author": "alice"})
    sent = mock_client.arequest.call_args[0][0]
    assert sent["type"] == "session-close"
    assert sent["author"] == "alice"
    # Critical: session-close uses the same connection_id as the writes
    # that opened the session, so the daemon's get_active() finds it.
    assert sent["connection_id"] == "test-mcp-conn"


def test_wiki_tools_includes_maintenance_side():
    from llm_wiki.mcp.tools import WIKI_TOOLS
    names = {t.name for t in WIKI_TOOLS}
    assert "wiki_issues_list" in names
    assert "wiki_issues_get" in names
    assert "wiki_issues_resolve" in names
    assert "wiki_talk_read" in names
    assert "wiki_talk_post" in names
    assert "wiki_talk_list" in names
    assert "wiki_session_close" in names


def test_wiki_tools_does_not_include_delete_or_commit():
    """Per the spec's 'Tools deliberately not in the surface' section."""
    from llm_wiki.mcp.tools import WIKI_TOOLS
    names = {t.name for t in WIKI_TOOLS}
    assert "wiki_delete" not in names
    assert "wiki_write" not in names
    assert "wiki_commit" not in names
    assert "wiki_revert" not in names
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_mcp/test_tools.py -v`
Expected: FAIL on the seven new tests.

- [ ] **Step 3: Add the maintenance-side handlers**

Append to `src/llm_wiki/mcp/tools.py`:

```python
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
```

Update the registration list:

```python
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
    WIKI_SESSION_CLOSE,
]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_mcp/test_tools.py -v`
Expected: PASS for all tests including the eight new ones.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/mcp/tools.py tests/test_mcp/test_tools.py
git commit -m "feat: phase 6c — maintenance-side tools (issues, talk, session_close)"
```

---

### Task 7: MCP server entry point

**Files:**
- Create: `src/llm_wiki/mcp/server.py`
- Create: `tests/test_mcp/test_server.py`

The `MCPServer` class wraps an `mcp.server.Server` from the SDK, registers every tool from `WIKI_TOOLS`, and exposes a `run_stdio()` async method that runs the stdio transport. The `DaemonClient` is constructed from the resolved vault path.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mcp/test_server.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from llm_wiki.mcp.tools import WIKI_TOOLS


def test_mcp_server_registers_all_tools(tmp_path):
    """Constructing an MCPServer registers every tool in WIKI_TOOLS."""
    from llm_wiki.mcp.server import MCPServer

    client = MagicMock()
    server = MCPServer(vault_path=tmp_path, client=client)
    registered = server.list_tools()
    registered_names = {t.name for t in registered}
    expected_names = {t.name for t in WIKI_TOOLS}
    assert registered_names == expected_names


def test_mcp_server_tool_descriptions_present(tmp_path):
    """Every registered tool has a non-empty description (agent-facing)."""
    from llm_wiki.mcp.server import MCPServer

    client = MagicMock()
    server = MCPServer(vault_path=tmp_path, client=client)
    for tool in server.list_tools():
        assert tool.description, f"Tool {tool.name} has no description"
        assert len(tool.description) > 20  # not just a placeholder


@pytest.mark.asyncio
async def test_mcp_server_dispatches_tool_call(tmp_path):
    """A call_tool invocation routes to the corresponding handler."""
    from llm_wiki.mcp.server import MCPServer

    client = MagicMock()
    client.request.return_value = {"status": "ok", "page_count": 4}

    server = MCPServer(vault_path=tmp_path, client=client)
    result = await server.call_tool("wiki_status", {})
    assert result
    assert "page_count" in result[0].text


@pytest.mark.asyncio
async def test_mcp_server_unknown_tool_raises(tmp_path):
    from llm_wiki.mcp.server import MCPServer

    client = MagicMock()
    server = MCPServer(vault_path=tmp_path, client=client)
    with pytest.raises(KeyError):
        await server.call_tool("nonexistent", {})


def test_mcp_server_connection_id_is_stable_across_tool_calls(tmp_path):
    """One UUID per MCP server instance, threaded into every tool call."""
    from llm_wiki.mcp.server import MCPServer

    client = MagicMock()
    server = MCPServer(vault_path=tmp_path, client=client)
    # Same instance, same connection_id
    assert server._connection_id == server._ctx.connection_id
    # Different instance, different connection_id
    server2 = MCPServer(vault_path=tmp_path, client=client)
    assert server._connection_id != server2._connection_id


@pytest.mark.asyncio
async def test_mcp_server_threads_connection_id_into_write_calls(tmp_path):
    """A write tool dispatched via call_tool sees the server's connection_id in the daemon request."""
    from llm_wiki.mcp.server import MCPServer

    client = MagicMock()
    client.request.return_value = {
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
    sent = client.request.call_args[0][0]
    assert sent["connection_id"] == server._connection_id
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_mcp/test_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'llm_wiki.mcp.server'`.

- [ ] **Step 3: Implement `server.py`**

Create `src/llm_wiki/mcp/server.py`:

```python
"""MCP server entry point — wraps an mcp.server.Server with our tool list.

This is intentionally a thin layer. Construction registers every tool in
WIKI_TOOLS. The class exposes:
  - list_tools() → the registered ToolDefinition list
  - call_tool(name, args) → dispatches to the matching handler
  - run_stdio() → runs the SDK's stdio transport (the actual entry point
    used by `llm-wiki mcp`)
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_mcp/test_server.py -v`
Expected: PASS for all four server tests.

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/mcp/server.py tests/test_mcp/test_server.py
git commit -m "feat: phase 6c — MCPServer entry point with stdio transport"
```

---

### Task 8: CLI subcommand `llm-wiki mcp`

**Files:**
- Modify: `src/llm_wiki/cli/main.py` (add the `mcp` subcommand)
- Create: `tests/test_mcp/test_cli.py`

The CLI command resolves the vault path in priority order: `LLM_WIKI_VAULT` env var, then the positional argument. If neither is set, exits with a clear error. Reuses the existing `_get_client(vault_path, auto_start=True)` helper to ensure the daemon is running before constructing the MCP server.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mcp/test_cli.py`:

```python
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner


def test_mcp_cli_resolves_vault_from_arg(tmp_path):
    """A positional vault path is used when LLM_WIKI_VAULT is not set."""
    from llm_wiki.cli.main import cli

    # Make tmp_path look like a vault
    (tmp_path / "wiki").mkdir()

    runner = CliRunner(env={"LLM_WIKI_VAULT": ""})
    with patch("llm_wiki.cli.main._get_client") as mock_get_client, \
         patch("llm_wiki.mcp.server.MCPServer") as mock_server_cls:
        mock_get_client.return_value = MagicMock()
        mock_instance = MagicMock()

        async def fake_run():
            return None
        mock_instance.run_stdio = fake_run
        mock_server_cls.return_value = mock_instance

        result = runner.invoke(cli, ["mcp", str(tmp_path)])
        assert result.exit_code == 0, result.output
        mock_get_client.assert_called_once()
        called_with = mock_get_client.call_args[0][0]
        assert Path(called_with).resolve() == tmp_path.resolve()


def test_mcp_cli_resolves_vault_from_env(tmp_path):
    """LLM_WIKI_VAULT takes priority over the positional arg."""
    from llm_wiki.cli.main import cli

    (tmp_path / "wiki").mkdir()

    runner = CliRunner(env={"LLM_WIKI_VAULT": str(tmp_path)})
    with patch("llm_wiki.cli.main._get_client") as mock_get_client, \
         patch("llm_wiki.mcp.server.MCPServer") as mock_server_cls:
        mock_get_client.return_value = MagicMock()
        mock_instance = MagicMock()
        async def fake_run():
            return None
        mock_instance.run_stdio = fake_run
        mock_server_cls.return_value = mock_instance

        result = runner.invoke(cli, ["mcp"])
        assert result.exit_code == 0, result.output
        called_with = mock_get_client.call_args[0][0]
        assert Path(called_with).resolve() == tmp_path.resolve()


def test_mcp_cli_errors_when_no_vault():
    """Exits with a clear error if neither env var nor positional arg is set."""
    from llm_wiki.cli.main import cli

    runner = CliRunner(env={"LLM_WIKI_VAULT": ""})
    result = runner.invoke(cli, ["mcp"])
    assert result.exit_code != 0
    assert "vault" in result.output.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_mcp/test_cli.py -v`
Expected: FAIL — `cli` does not have an `mcp` subcommand.

- [ ] **Step 3: Add the `mcp` subcommand to `cli/main.py`**

Append to `src/llm_wiki/cli/main.py`:

```python
@cli.command(name="mcp")
@click.argument(
    "vault_path",
    type=click.Path(exists=True, path_type=Path),
    required=False,
)
def mcp_command(vault_path: Path | None) -> None:
    """Run the MCP server over stdio for this vault.

    Vault resolution order:
      1. LLM_WIKI_VAULT environment variable
      2. The VAULT_PATH positional argument
      3. Error out

    Auto-starts the daemon if it isn't already running.
    """
    import asyncio
    import os

    env_vault = os.environ.get("LLM_WIKI_VAULT", "").strip()
    resolved: Path | None = None
    if env_vault:
        resolved = Path(env_vault)
    elif vault_path is not None:
        resolved = vault_path

    if resolved is None:
        raise click.ClickException(
            "No vault specified. Set LLM_WIKI_VAULT or pass a vault path: "
            "llm-wiki mcp /path/to/vault"
        )
    if not resolved.exists():
        raise click.ClickException(f"Vault path does not exist: {resolved}")

    client = _get_client(resolved, auto_start=True)

    from llm_wiki.mcp.server import MCPServer
    server = MCPServer(vault_path=resolved, client=client)
    asyncio.run(server.run_stdio())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_mcp/test_cli.py -v`
Expected: PASS for all three CLI tests.

- [ ] **Step 5: Run the full MCP + CLI test suite**

Run: `pytest tests/test_mcp tests/test_cli -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/cli/main.py tests/test_mcp/test_cli.py
git commit -m "feat: phase 6c — llm-wiki mcp CLI subcommand"
```

---

### Task 9: End-to-end MCP smoke test

**Files:**
- Create: `tests/test_mcp/test_smoke_e2e.py`

This is the test that catches "the daemon route works but the MCP wrapper is wrong." It spins up a real `DaemonServer` against a tmp vault, constructs an `MCPServer` against the resulting `DaemonClient`, and exercises one tool from each family (`wiki_status`, `wiki_create`, `wiki_session_close`) through the `MCPServer.call_tool` interface (skipping the stdio transport — the SDK's stdio transport is hard to test in-process and is exercised manually in the smoke test in Task 11).

- [ ] **Step 1: Write the test**

Create `tests/test_mcp/test_smoke_e2e.py`:

```python
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio

from llm_wiki.config import WikiConfig
from llm_wiki.daemon.client import DaemonClient
from llm_wiki.daemon.server import DaemonServer
from llm_wiki.mcp.server import MCPServer


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True)
    (path / ".gitignore").write_text("# placeholder\n")
    subprocess.run(["git", "-C", str(path), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "initial"], check=True)


@pytest_asyncio.fixture
async def mcp_e2e(tmp_path):
    _init_git_repo(tmp_path)
    sock_path = tmp_path / "e2e.sock"
    # IMPORTANT: use the production-default WikiConfig() so that
    # `wiki_dir = "wiki"` and the daemon's PageWriteService writes pages
    # to `tmp_path/wiki/<slug>.md`. Phase 6a's `phase6a_daemon_server`
    # fixture uses `VaultConfig(wiki_dir="")` to align with the
    # `sample_vault` layout — but here we're creating pages from scratch
    # via `wiki_create`, and the assertions below expect them under
    # `tmp_path/wiki/...`. This is exactly the trap Plan 6b's prerequisites
    # note (top of plan 6b) explicitly warned against; do not regress it.
    config = WikiConfig()
    server = DaemonServer(tmp_path, sock_path, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    client = DaemonClient(sock_path)
    mcp_server = MCPServer(vault_path=tmp_path, client=client)

    yield mcp_server, tmp_path

    server._server.close()
    serve_task.cancel()
    try:
        await serve_task
    except asyncio.CancelledError:
        pass
    await server.stop()


@pytest.mark.asyncio
async def test_e2e_status_through_mcp(mcp_e2e):
    mcp_server, _ = mcp_e2e
    result = await mcp_server.call_tool("wiki_status", {})
    payload = json.loads(result[0].text)
    assert payload["status"] == "ok"
    assert "page_count" in payload


@pytest.mark.asyncio
async def test_e2e_create_then_session_close(mcp_e2e):
    """A wiki_create followed by wiki_session_close produces a git commit."""
    mcp_server, vault_root = mcp_e2e

    create_result = await mcp_server.call_tool("wiki_create", {
        "title": "Test Page",
        "body": "body [[raw/x.pdf]]",
        "citations": ["raw/x.pdf"],
        "author": "alice",
        "intent": "smoke test",
    })
    create_payload = json.loads(create_result[0].text)
    assert create_payload["status"] == "ok"
    assert (vault_root / "wiki" / "test-page.md").exists()

    close_result = await mcp_server.call_tool("wiki_session_close", {
        "author": "alice",
    })
    close_payload = json.loads(close_result[0].text)
    assert close_payload["status"] == "ok"
    assert close_payload["settled"] is True
    assert close_payload["commit_sha"]

    # Verify the commit landed in git
    log = subprocess.run(
        ["git", "-C", str(vault_root), "log", "-1", "--format=%B"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "alice" in log


@pytest.mark.asyncio
async def test_e2e_create_missing_citations_raises(mcp_e2e):
    from llm_wiki.mcp.errors import McpToolError
    mcp_server, _ = mcp_e2e

    with pytest.raises(McpToolError) as exc_info:
        await mcp_server.call_tool("wiki_create", {
            "title": "Foo",
            "body": "body",
            "citations": [],
            "author": "alice",
        })
    assert exc_info.value.code == "missing-citations"


@pytest.mark.asyncio
async def test_e2e_lint_returns_attention_map(mcp_e2e):
    mcp_server, _ = mcp_e2e
    result = await mcp_server.call_tool("wiki_lint", {})
    payload = json.loads(result[0].text)
    assert payload["status"] == "ok"
    assert "attention_map" in payload
```

- [ ] **Step 2: Run the smoke tests**

Run: `pytest tests/test_mcp/test_smoke_e2e.py -v`
Expected: PASS for all four end-to-end tests.

- [ ] **Step 3: Run the full MCP test suite**

Run: `pytest tests/test_mcp -v`
Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_mcp/test_smoke_e2e.py
git commit -m "test: phase 6c — end-to-end MCP smoke test"
```

---

### Task 10: AST hard-rule sanity check (no MCP imports in background workers)

**Files:**
- Modify: `tests/test_daemon/test_ast_hard_rule.py` (extend the forbidden-symbol set)

The Phase 6b AST hard-rule test enforces that background workers never reach `PageWriteService`. Phase 6c adds a small extension: background workers must also never reach the MCP server itself (importing `mcp.server`, `mcp.tools`, etc. from inside `audit/`, `librarian/`, `adversary/`, `talk/` is a hard-rule violation — the MCP layer is for the supervised path only).

This is a one-line update to the existing test plus a confirmatory run.

- [ ] **Step 1: Extend the forbidden-name set**

Edit `tests/test_daemon/test_ast_hard_rule.py`. Update the `FORBIDDEN_NAMES` set:

```python
FORBIDDEN_NAMES = {
    "PageWriteService",
    "_handle_page_create",
    "_handle_page_update",
    "_handle_page_append",
    "_handle_session_close",
    # Phase 6c: the MCP layer is for supervised paths only
    "MCPServer",
    "WIKI_TOOLS",
}
```

And add a string check for the MCP module itself:

```python
FORBIDDEN_IMPORT_MODULES = {
    "llm_wiki.mcp",
    "llm_wiki.mcp.server",
    "llm_wiki.mcp.tools",
    "llm_wiki.mcp.errors",
}
```

Add to `_violations_in_file`:

```python
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in FORBIDDEN_NAMES:
                    violations.append(
                        f"{path}:{node.lineno}: imports forbidden symbol {alias.name!r}"
                    )
            if node.module in FORBIDDEN_IMPORT_MODULES:
                violations.append(
                    f"{path}:{node.lineno}: imports from forbidden module {node.module!r}"
                )
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in FORBIDDEN_IMPORT_MODULES:
                    violations.append(
                        f"{path}:{node.lineno}: imports forbidden module {alias.name!r}"
                    )
```

**Mirror the same extension into `_violations_in_function`** (the surgical walker added in Phase 6b Task 18 that checks `_register_maintenance_workers` inside `daemon/server.py`). Without this, a future regression that adds `from llm_wiki.mcp.tools import WIKI_TOOLS` inside `run_auditor` (or any of the other nested closures) would slip past the surgical walker because the directory walk exempts `server.py`. Edit `_violations_in_function` to add the same `node.module in FORBIDDEN_IMPORT_MODULES` and `ast.Import` checks alongside the existing `alias.name in FORBIDDEN_NAMES` check:

```python
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in FORBIDDEN_NAMES:
                    violations.append(
                        f"{source_label}:{node.lineno}: imports forbidden symbol {alias.name!r}"
                    )
            if node.module in FORBIDDEN_IMPORT_MODULES:
                violations.append(
                    f"{source_label}:{node.lineno}: imports from forbidden module {node.module!r}"
                )
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in FORBIDDEN_IMPORT_MODULES:
                    violations.append(
                        f"{source_label}:{node.lineno}: imports forbidden module {alias.name!r}"
                    )
```

- [ ] **Step 2: Run the test to confirm no violations**

Run: `pytest tests/test_daemon/test_ast_hard_rule.py -v`
Expected: PASS — Phase 6c added new code only under `mcp/` and `cli/`, neither of which is in the background-worker subtree.

- [ ] **Step 3: Sanity-check by introducing two violations (directory walk + surgical walker)**

**3a. Directory walk catches an audit/ MCP import:**
Temporarily add `from llm_wiki.mcp.tools import WIKI_TOOLS` to `src/llm_wiki/audit/auditor.py`. Run `pytest tests/test_daemon/test_ast_hard_rule.py::test_background_workers_never_reference_write_surface -v`. Expected: FAIL with the file path and line number. **Revert the change.**

**3b. Surgical walker catches a server.py background-worker MCP import:**
Temporarily add `from llm_wiki.mcp.tools import WIKI_TOOLS` inside the body of `run_auditor` in `src/llm_wiki/daemon/server.py`. Run `pytest tests/test_daemon/test_ast_hard_rule.py::test_register_maintenance_workers_never_reach_write_surface -v`. Expected: FAIL with the file path, line number, and `'llm_wiki.mcp.tools'` cited as the forbidden module. **Revert the change.** This confirms the Phase 6c extension to `_violations_in_function` actually fires — without it, the surgical walker would silently let MCP imports through inside background-worker closures.

- [ ] **Step 4: Commit**

```bash
git add tests/test_daemon/test_ast_hard_rule.py
git commit -m "test: phase 6c — extend AST hard-rule to MCP imports"
```

---

### Task 11: Update README with quick-start MCP config

**Files:**
- Modify: `README.md` (add the MCP setup snippet from the spec)

A short section in the README that tells a Claude Code / Claude Desktop user how to add this server to their client config.

- [ ] **Step 1: Read the current README to find the right insertion point**

Run: `grep -n "^## " README.md`
Expected: A list of section headings. Pick one near the top (e.g. between the project intro and the development docs) for the MCP setup section.

- [ ] **Step 2: Add the MCP setup section**

Insert under an appropriate heading (e.g. after the "Quick start" or "Installation" section):

````markdown
## Connecting from an MCP client

After `pip install -e .`, register the server in your MCP client's config:

```json
{
  "mcpServers": {
    "llm-wiki": {
      "command": "llm-wiki",
      "args": ["mcp"],
      "env": { "LLM_WIKI_VAULT": "/path/to/your/vault" }
    }
  }
}
```

The MCP server auto-starts the daemon on first connect. Every supervised
write produces a git commit attributed to the calling agent via the
`Agent:` trailer.

Tools available: `wiki_search`, `wiki_read`, `wiki_manifest`, `wiki_status`,
`wiki_query`, `wiki_ingest`, `wiki_lint`, `wiki_create`, `wiki_update`,
`wiki_append`, `wiki_issues_list`, `wiki_issues_get`, `wiki_issues_resolve`,
`wiki_talk_read`, `wiki_talk_post`, `wiki_talk_list`, `wiki_session_close`.
````

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: phase 6c — README MCP quick-start section"
```

---

### Task 12: Final regression sweep + Phase 6c tag

**Files:**
- None (verification + tag)

- [ ] **Step 1: Run the full test suite**

Run: `pytest -q`
Expected: All tests pass — Phases 6a, 6b, 6c.

- [ ] **Step 2: Run the AST hard-rule test specifically**

Run: `pytest tests/test_daemon/test_ast_hard_rule.py -v`
Expected: PASS. The test now covers both `PageWriteService` (Phase 6b) and the MCP layer (Phase 6c).

- [ ] **Step 3: Manual stdio smoke test**

The in-process `MCPServer.call_tool` is exercised by Task 9; this step verifies the actual stdio transport launches and accepts MCP protocol messages.

```bash
mkdir -p /tmp/p6c-vault
cd /tmp/p6c-vault
git init -q
git config user.email "test@test"
git config user.name "test"
echo "# placeholder" > .gitignore
git add .gitignore
git commit -q -m "initial"

# Start the MCP server in one terminal:
LLM_WIKI_VAULT=$PWD llm-wiki mcp

# In another terminal, send an initialize request via the SDK's test client:
python -c "
import asyncio
import subprocess
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(
        command='llm-wiki', args=['mcp'], env={'LLM_WIKI_VAULT': '/tmp/p6c-vault'},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print('Tools:', [t.name for t in tools.tools])
            result = await session.call_tool('wiki_status', {})
            print('Status:', result.content[0].text)

asyncio.run(main())
"
```

Expected: A list of 17 tool names and a `wiki_status` response showing the vault stats. Kill the daemon when done.

- [ ] **Step 4: Tag the phase complete**

```bash
git tag phase-6c-complete
git tag phase-6-complete  # the umbrella tag for the whole phase
git log --oneline | head -30
```

- [ ] **Step 5: Update the spec status line**

Edit `docs/superpowers/specs/2026-04-08-phase6-mcp-server-design.md`. Update the status line to:

```
> Status: Phase 6 complete (6a + 6b + 6c implemented and tagged)
```

Commit:

```bash
git add docs/superpowers/specs/2026-04-08-phase6-mcp-server-design.md
git commit -m "docs: phase 6 complete — spec status updated"
```

---

## Phase 6 complete

When Task 12 is done, llm-wiki has:
- Severity-aware issues + talk entries with append-only closure (Phase 6a)
- Librarian-refreshed talk-page summaries with threshold + rate limit (Phase 6a)
- Enriched `read` / `search` / `lint` daemon responses (Phase 6a)
- A V4A patch parser/applier with exact + fuzzy match (Phase 6b)
- Three new write routes (`page-create`, `page-update`, `page-append`) backed by a session/journal/commit pipeline (Phase 6b)
- An AST hard-rule test that mechanically prevents background workers from reaching the write surface OR the MCP layer (Phase 6b + 6c)
- Session-aware ingest (Phase 6b)
- A complete MCP server exposing 17 tools over stdio, registerable in any MCP-capable client (Phase 6c)

The wiki is now usable by any MCP-capable agent. Every supervised write is a git commit. Every commit is attributed to its agent via the `Agent:` trailer. The asynchronous channel between background workers and active agents has both ends — agents see issues and talk-page digests inline in `wiki_read`, and they can post back via `wiki_talk_post`. The write surface is small, the daemon does the boring work, and the wiki gets cared for between sessions because the daemon is always running.

The original Phase 6 promise is delivered. From here, the next questions are: cost ceilings (the future `cost_control:` config section noted in the spec's out-of-scope section), the brainstorming companion tool (a sibling to the wiki, not a child), and `wiki_extract` for collaborative ingest. None of those are blocking — the wiki works, end to end, today.
