"""End-to-end: edit page -> debounced compliance review -> issue filed."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from llm_wiki.config import MaintenanceConfig, VaultConfig, WikiConfig
from llm_wiki.daemon.client import DaemonClient
from llm_wiki.daemon.server import DaemonServer
from llm_wiki.daemon.watcher import FileWatcher


@pytest.mark.asyncio
async def test_compliance_review_fires_after_edit(tmp_path: Path):
    """Edit a page -> wait for debounce -> assert compliance issue exists."""
    # Build a tiny vault from scratch (the sample_vault fixture sample doesn't
    # have a wiki_dir layout we control, and we want to set wiki_dir cleanly).
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    page = wiki_dir / "test-page.md"
    page.write_text(
        "---\ntitle: Test Page\n---\n\n"
        "%% section: overview %%\n## Overview\n\nOriginal text [[raw/source.pdf]].\n"
    )

    config = WikiConfig(
        maintenance=MaintenanceConfig(
            compliance_debounce_secs=0.5,
            auditor_interval="1h",
        ),
        vault=VaultConfig(wiki_dir="wiki/"),
    )

    sock_path = tmp_path / "compliance-int.sock"
    server = DaemonServer(tmp_path, sock_path, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    watcher = FileWatcher(tmp_path, server.handle_file_changes, poll_interval=0.1)
    await watcher.start()

    try:
        client = DaemonClient(sock_path)

        # Seed the snapshot store by triggering one settled change with the
        # original content. We do this by writing the file again with identical
        # content (mtime changes), waiting the debounce, and assuming the
        # reviewer treats it as a "creation" review.
        page.write_text(page.read_text(encoding="utf-8"))
        await asyncio.sleep(1.0)

        # Now make an uncited edit
        page.write_text(
            "---\ntitle: Test Page\n---\n\n"
            "%% section: overview %%\n## Overview\n\nOriginal text [[raw/source.pdf]].\n"
            "We added a brand new uncited claim that the reviewer should flag.\n"
        )

        # Wait debounce + slack
        await asyncio.sleep(1.0)

        # Query the issue queue — there should be a compliance issue
        listing = client.request({"type": "issues-list", "type_filter": "compliance"})
        assert listing["status"] == "ok"
        compliance_titles = [i["title"] for i in listing["issues"]]
        assert any("test-page" in t.lower() for t in compliance_titles), (
            f"expected a compliance issue for test-page, got: {compliance_titles}"
        )
    finally:
        await watcher.stop()
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()
