"""Tests for synthesis page write dispatch in _handle_query."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.traverse.engine import TraversalResult
from llm_wiki.traverse.log import TraversalLog


def _make_log():
    log = MagicMock(spec=TraversalLog)
    log.to_dict.return_value = {}
    return log


_SENTINEL = object()


def _make_result(action=None, answer="Answer [[foo]].", citations=_SENTINEL):
    return TraversalResult(
        answer=answer,
        citations=["foo"] if citations is _SENTINEL else citations,
        outcome="complete",
        needs_more_budget=False,
        log=_make_log(),
        synthesis_action=action,
    )


@pytest.fixture
def wiki_dir(tmp_path):
    (tmp_path / "wiki").mkdir()
    (tmp_path / "raw").mkdir()
    return tmp_path


@pytest.fixture
def server(wiki_dir):
    """Minimal DaemonServer with _vault_root and _config set."""
    from llm_wiki.daemon.server import DaemonServer
    cfg = WikiConfig()
    srv = object.__new__(DaemonServer)
    srv._vault_root = wiki_dir
    srv._config = cfg
    vault_mock = MagicMock()
    vault_mock.page_count = 0
    vault_mock.manifest_entries.return_value = {}
    vault_mock.read_page.return_value = None
    srv._vault = vault_mock
    srv._title_to_slug = {}
    return srv


@pytest.mark.asyncio
async def test_write_synthesis_page_creates_file(server, wiki_dir):
    """_write_synthesis_page writes a type: synthesis page to wiki/."""
    with patch.object(type(server), "rescan", new_callable=AsyncMock):
        await server._write_synthesis_page(
            query="how does foo work?",
            title="Foo",
            answer="Foo uses bar [[foo]].",
            sources=["wiki/foo.md"],
        )
    pages = list((wiki_dir / "wiki").glob("*.md"))
    assert len(pages) == 1
    content = pages[0].read_text()
    assert "type: synthesis" in content
    assert "Foo uses bar [[foo]]." in content
    assert "wiki/foo.md" in content


@pytest.mark.asyncio
async def test_update_synthesis_page_overwrites(server, wiki_dir):
    """_update_synthesis_page overwrites existing synthesis page."""
    existing = wiki_dir / "wiki" / "foo.md"
    existing.write_text(
        '---\ntitle: "Foo"\ntype: synthesis\nquery: "foo"\n'
        "created_by: query\ncreated_at: 2026-01-01T00:00:00Z\n"
        "updated_at: 2026-01-01T00:00:00Z\nsources: []\n---\n\n"
        "%% section: answer %%\n\nOld answer.\n",
        encoding="utf-8",
    )
    with patch.object(type(server), "rescan", new_callable=AsyncMock):
        await server._update_synthesis_page(
            slug="foo",
            query="how does foo work?",
            title="Foo",
            answer="New extended answer [[foo]].",
            sources=["wiki/foo.md"],
            created_at="2026-01-01T00:00:00Z",
        )
    content = existing.read_text()
    assert "New extended answer" in content
    assert "Old answer" not in content
    assert "created_at: 2026-01-01T00:00:00Z" in content  # preserved


@pytest.mark.asyncio
async def test_dispatch_synthesis_action_create(server, wiki_dir):
    """create action writes a new synthesis page."""
    result = _make_result(
        action={"action": "create", "title": "Foo", "sources": ["wiki/foo.md"]},
        answer="Foo uses bar [[foo]].",
        citations=["foo"],
    )
    resp = {"answer": result.answer, "citations": result.citations}
    with patch.object(type(server), "rescan", new_callable=AsyncMock):
        await server._dispatch_synthesis_action("how does foo work?", result, resp)
    pages = list((wiki_dir / "wiki").glob("*.md"))
    assert len(pages) == 1


@pytest.mark.asyncio
async def test_dispatch_synthesis_action_accept_returns_existing(server, wiki_dir):
    """accept action reads existing page and sets resp['answer']."""
    existing = wiki_dir / "wiki" / "foo.md"
    existing.write_text(
        '---\ntitle: "Foo"\ntype: synthesis\nquery: "foo"\n'
        "created_by: query\ncreated_at: 2026-01-01T00:00:00Z\n"
        "updated_at: 2026-01-01T00:00:00Z\nsources: []\n---\n\n"
        "%% section: answer %%\n\nCached answer text.\n",
        encoding="utf-8",
    )
    from llm_wiki.page import Page
    page = Page.parse(existing)
    server._vault.read_page.return_value = page

    result = _make_result(
        action={"action": "accept", "page": "foo"},
        answer="",
        citations=[],
    )
    resp = {"answer": "", "citations": []}
    await server._dispatch_synthesis_action("how does foo work?", result, resp)
    assert resp["answer"] == "Cached answer text."
    assert resp.get("synthesis_cache_hit") == "foo"


@pytest.mark.asyncio
async def test_dispatch_synthesis_no_action_skips_write(server, wiki_dir):
    """No action → no synthesis page written."""
    result = _make_result(action=None)
    resp = {"answer": result.answer}
    await server._dispatch_synthesis_action("q?", result, resp)
    assert not list((wiki_dir / "wiki").glob("*.md"))


@pytest.mark.asyncio
async def test_dispatch_synthesis_no_citations_skips_write(server, wiki_dir):
    """create action with no citations → no write."""
    result = _make_result(
        action={"action": "create", "title": "Foo", "sources": []},
        answer="No citations here.",
        citations=[],
    )
    resp = {"answer": result.answer}
    with patch.object(type(server), "rescan", new_callable=AsyncMock):
        await server._dispatch_synthesis_action("q?", result, resp)
    assert not list((wiki_dir / "wiki").glob("*.md"))


@pytest.mark.asyncio
async def test_dispatch_synthesis_action_update(server, wiki_dir):
    """update action overwrites existing synthesis page via dispatch."""
    existing = wiki_dir / "wiki" / "foo.md"
    existing.write_text(
        '---\ntitle: "Foo"\ntype: synthesis\nquery: "foo"\n'
        "created_by: query\ncreated_at: 2026-01-01T00:00:00Z\n"
        "updated_at: 2026-01-01T00:00:00Z\nsources: []\n---\n\n"
        "%% section: answer %%\n\nOld content.\n",
        encoding="utf-8",
    )
    from llm_wiki.page import Page
    page = Page.parse(existing)
    server._vault.read_page.return_value = page

    result = _make_result(
        action={"action": "update", "page": "foo", "title": "Foo", "sources": ["wiki/foo.md"]},
        answer="New extended content [[foo]].",
        citations=["foo"],
    )
    resp = {"answer": result.answer}
    with patch.object(type(server), "rescan", new_callable=AsyncMock):
        await server._dispatch_synthesis_action("how does foo work?", result, resp)
    content = existing.read_text()
    assert "New extended content" in content
    assert "Old content" not in content
    assert "created_at: 2026-01-01" in content  # preserved


@pytest.mark.asyncio
async def test_dispatch_synthesis_action_update_skips_when_no_citations(server, wiki_dir):
    """update action with no citations → no write (parallel to create guard)."""
    result = _make_result(
        action={"action": "update", "page": "foo", "title": "Foo", "sources": []},
        answer="No citations.",
        citations=[],
    )
    resp = {"answer": result.answer}
    await server._dispatch_synthesis_action("q?", result, resp)
    assert not list((wiki_dir / "wiki").glob("*.md"))


@pytest.mark.asyncio
async def test_dispatch_synthesis_action_update_fallback_creates(server, wiki_dir):
    """update action falls back to create if target page was deleted."""
    # read_page returns None (page was deleted since search)
    server._vault.read_page.return_value = None

    result = _make_result(
        action={"action": "update", "page": "foo", "title": "Foo", "sources": ["wiki/foo.md"]},
        answer="Content [[foo]].",
        citations=["foo"],
    )
    resp = {"answer": result.answer}
    with patch.object(type(server), "rescan", new_callable=AsyncMock):
        await server._dispatch_synthesis_action("q?", result, resp)
    pages = list((wiki_dir / "wiki").glob("*.md"))
    assert len(pages) == 1  # fallback created the page
