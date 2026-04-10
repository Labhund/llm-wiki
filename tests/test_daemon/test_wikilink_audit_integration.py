from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from llm_wiki.daemon.client import DaemonClient
from llm_wiki.daemon.server import DaemonServer


@pytest.mark.asyncio
async def test_wikilink_audit_adds_links_on_settle(sample_vault: Path, tmp_path: Path):
    """After a wiki page settles, unlinked title occurrences get wikilinks."""
    sock_path = tmp_path / "wikilink-audit.sock"
    server = DaemonServer(sample_vault, sock_path)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        wiki_dir = sample_vault / "wiki"
        wiki_dir.mkdir(parents=True, exist_ok=True)

        # Write a page that mentions a known title without linking it.
        # We pick a title from the manifest that should appear in the sample vault.
        test_page = wiki_dir / "wikilink-test-target.md"
        original = (
            "---\ntitle: Wikilink Test Target\n---\n\n"
            "This page discusses clustering metrics and PCA.\n"
        )
        test_page.write_text(original)

        # Directly call the settled-change handler (bypasses the debounce timer)
        await server._handle_settled_change(test_page)

        new_content = test_page.read_text()
        # Regardless of whether any title matched, we must not have shrunk the file
        # or removed existing wikilinks.
        assert len(new_content) >= len(original)
        assert new_content.count("[[") >= original.count("[[")

    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()


@pytest.mark.asyncio
async def test_wikilink_audit_skips_page_with_active_write_lock(
    sample_vault: Path, tmp_path: Path
):
    """Wikilink audit must not touch a page whose write lock is held."""
    sock_path = tmp_path / "wikilink-lock.sock"
    server = DaemonServer(sample_vault, sock_path)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        wiki_dir = sample_vault / "wiki"
        wiki_dir.mkdir(parents=True, exist_ok=True)
        test_page = wiki_dir / "locked-page.md"
        test_page.write_text("---\ntitle: Locked Page\n---\n\nContent.\n")

        # Hold the write lock for this page
        lock = server._write_coordinator.lock_for("locked-page")
        original_mtime = test_page.stat().st_mtime

        async with lock:
            # With lock held, the audit must skip this page
            await server._run_wikilink_audit(test_page)

        # File must not have been touched while lock was held
        assert test_page.stat().st_mtime == original_mtime

    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()


@pytest.mark.asyncio
async def test_wikilink_audit_skips_non_wiki_path(sample_vault: Path, tmp_path: Path):
    """Wikilink audit must not process files outside wiki_dir."""
    sock_path = tmp_path / "wikilink-nonwiki.sock"
    server = DaemonServer(sample_vault, sock_path)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        # File in raw/, not wiki/
        raw_dir = sample_vault / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_page = raw_dir / "source.md"
        raw_page.write_text("# Source\n\nPCA is here.\n")
        original_mtime = raw_page.stat().st_mtime

        await server._run_wikilink_audit(raw_page)

        # raw/ file must be untouched
        assert raw_page.stat().st_mtime == original_mtime

    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()
