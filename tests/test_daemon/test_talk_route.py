from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from llm_wiki.config import VaultConfig, WikiConfig
from llm_wiki.daemon.client import DaemonClient
from llm_wiki.daemon.server import DaemonServer


def _vault_with_page_and_talk(tmp_path: Path) -> Path:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "test-page.md").write_text("---\ntitle: T\n---\n\nBody.\n")
    (wiki / "test-page.talk.md").write_text(
        "---\npage: test-page\n---\n\n"
        "**2026-04-08T10:00:00+00:00 — @adversary**\nVerified the k=10 claim.\n"
    )
    return tmp_path


@pytest.mark.asyncio
async def test_talk_read_returns_entries(tmp_path: Path):
    vault_root = _vault_with_page_and_talk(tmp_path)
    sock = tmp_path / "talk.sock"
    config = WikiConfig(vault=VaultConfig(wiki_dir="wiki/"))
    server = DaemonServer(vault_root, sock, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        client = DaemonClient(sock)
        resp = client.request({"type": "talk-read", "page": "test-page"})
        assert resp["status"] == "ok"
        assert len(resp["entries"]) == 1
        assert resp["entries"][0]["author"] == "@adversary"
        assert "k=10" in resp["entries"][0]["body"]
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()


@pytest.mark.asyncio
async def test_talk_read_missing_page_returns_empty(tmp_path: Path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "test-page.md").write_text("---\ntitle: T\n---\n")
    sock = tmp_path / "talk.sock"
    config = WikiConfig(vault=VaultConfig(wiki_dir="wiki/"))
    server = DaemonServer(tmp_path, sock, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        client = DaemonClient(sock)
        resp = client.request({"type": "talk-read", "page": "test-page"})
        assert resp["status"] == "ok"
        assert resp["entries"] == []
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()


@pytest.mark.asyncio
async def test_talk_append_creates_entry(tmp_path: Path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "p.md").write_text("---\ntitle: P\n---\n\nBody.\n")
    sock = tmp_path / "talk.sock"
    config = WikiConfig(vault=VaultConfig(wiki_dir="wiki/"))
    server = DaemonServer(tmp_path, sock, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        client = DaemonClient(sock)
        resp = client.request({
            "type": "talk-append",
            "page": "p",
            "author": "@human",
            "body": "Looks good to me.",
        })
        assert resp["status"] == "ok"

        read_resp = client.request({"type": "talk-read", "page": "p"})
        assert len(read_resp["entries"]) == 1
        assert read_resp["entries"][0]["author"] == "@human"
        assert "Looks good" in read_resp["entries"][0]["body"]
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()


@pytest.mark.asyncio
async def test_talk_list_returns_pages_with_talk_files(tmp_path: Path):
    vault_root = _vault_with_page_and_talk(tmp_path)
    (vault_root / "wiki" / "without-talk.md").write_text("---\ntitle: W\n---\n")
    sock = tmp_path / "talk.sock"
    config = WikiConfig(vault=VaultConfig(wiki_dir="wiki/"))
    server = DaemonServer(vault_root, sock, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        client = DaemonClient(sock)
        resp = client.request({"type": "talk-list"})
        assert resp["status"] == "ok"
        assert "test-page" in resp["pages"]
        assert "without-talk" not in resp["pages"]
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()


@pytest.mark.asyncio
async def test_talk_append_accepts_severity_field(tmp_path: Path):
    """The talk-append route persists a non-default severity."""
    from llm_wiki.talk.page import TalkPage

    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "p.md").write_text("---\ntitle: P\n---\n\nBody.\n")

    sock = tmp_path / "talk.sock"
    config = WikiConfig(vault=VaultConfig(wiki_dir="wiki/"))
    server = DaemonServer(tmp_path, sock, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        client = DaemonClient(sock)
        resp = client.request({
            "type": "talk-append",
            "page": "p",
            "author": "@adversary",
            "body": "A critical contradiction.",
            "severity": "critical",
        })
        assert resp["status"] == "ok"

        talk = TalkPage.for_page(wiki / "p.md")
        entries = talk.load()
        assert len(entries) == 1
        assert entries[0].severity == "critical"
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()


@pytest.mark.asyncio
async def test_talk_append_accepts_resolves_field(tmp_path: Path):
    """The talk-append route persists a `resolves` list; compute_open_set
    excludes the closed entries."""
    from llm_wiki.talk.page import TalkPage, compute_open_set

    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "p.md").write_text("---\ntitle: P\n---\n\nBody.\n")

    sock = tmp_path / "talk.sock"
    config = WikiConfig(vault=VaultConfig(wiki_dir="wiki/"))
    server = DaemonServer(tmp_path, sock, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        client = DaemonClient(sock)
        for body in ("first", "second"):
            resp = client.request({
                "type": "talk-append",
                "page": "p",
                "author": "@a",
                "body": body,
            })
            assert resp["status"] == "ok"

        resp = client.request({
            "type": "talk-append",
            "page": "p",
            "author": "@b",
            "body": "closes 1",
            "resolves": [1],
        })
        assert resp["status"] == "ok"

        talk = TalkPage.for_page(wiki / "p.md")
        entries = talk.load()
        open_set = compute_open_set(entries)
        open_indices = [e.index for e in open_set]
        assert 1 not in open_indices
        assert 2 in open_indices
        assert 3 in open_indices  # the closing entry itself stays open
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()


@pytest.mark.asyncio
async def test_talk_append_rejects_non_integer_resolves(tmp_path: Path):
    """A `resolves` value that isn't a list of ints returns a clean error."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "p.md").write_text("---\ntitle: P\n---\n\nBody.\n")

    sock = tmp_path / "talk.sock"
    config = WikiConfig(vault=VaultConfig(wiki_dir="wiki/"))
    server = DaemonServer(tmp_path, sock, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        client = DaemonClient(sock)
        resp = client.request({
            "type": "talk-append",
            "page": "p",
            "author": "@a",
            "body": "bad",
            "resolves": ["not-an-int"],
        })
        assert resp["status"] == "error"
        assert "resolves" in resp["message"]
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()
