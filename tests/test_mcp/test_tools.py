from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from llm_wiki.daemon.client import DaemonClient


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
    """A ToolContext wrapping the mock client + a stable test connection_id."""
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
