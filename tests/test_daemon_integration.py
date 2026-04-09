"""Full daemon lifecycle: start → request → file change → rescan → stop."""
import asyncio
from pathlib import Path

import pytest

from llm_wiki.daemon.client import DaemonClient
from llm_wiki.daemon.server import DaemonServer
from llm_wiki.daemon.watcher import FileWatcher


@pytest.mark.asyncio
async def test_full_daemon_lifecycle(sample_vault: Path, tmp_path: Path):
    """Start daemon, query, add file, rescan, query again, stop."""
    sock_path = tmp_path / "integration.sock"

    server = DaemonServer(sample_vault, sock_path)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    client = DaemonClient(sock_path)

    # Verify running
    assert client.is_running()

    # Search
    resp = client.request({"type": "search", "query": "sRNA", "limit": 5})
    assert resp["status"] == "ok"
    assert len(resp["results"]) >= 1

    # Read
    resp = client.request({"type": "read", "page_name": "srna-embeddings"})
    assert resp["status"] == "ok"
    assert "overview" in resp["content"].lower()

    # Status check page count
    resp = client.request({"type": "status"})
    original_count = resp["page_count"]

    # Add a new page to the vault
    (sample_vault / "wiki" / "new-topic.md").write_text(
        "---\ntitle: Brand New Topic\n---\n\n## Overview\n\nThis is new content.\n"
    )

    # Trigger rescan (normally the watcher does this)
    resp = client.request({"type": "rescan"})
    assert resp["status"] == "ok"

    # Verify new page is indexed
    resp = client.request({"type": "status"})
    assert resp["page_count"] == original_count + 1

    # Search for new content
    resp = client.request({"type": "search", "query": "Brand New Topic"})
    assert resp["status"] == "ok"
    assert len(resp["results"]) >= 1

    # Read new page
    resp = client.request({"type": "read", "page_name": "new-topic"})
    assert resp["status"] == "ok"
    assert "new content" in resp["content"].lower()

    # Stop
    server._server.close()
    serve_task.cancel()
    try:
        await serve_task
    except asyncio.CancelledError:
        pass
    await server.stop()

    # Verify stopped
    assert not client.is_running()


@pytest.mark.asyncio
async def test_watcher_triggers_rescan(sample_vault: Path, tmp_path: Path):
    """File watcher detects change and triggers rescan."""
    sock_path = tmp_path / "watcher.sock"

    server = DaemonServer(sample_vault, sock_path)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    async def on_change(changed, removed):
        await server.rescan()

    watcher = FileWatcher(sample_vault, on_change, poll_interval=0.3)
    await watcher.start()

    client = DaemonClient(sock_path)

    # Get initial count
    resp = client.request({"type": "status"})
    initial_count = resp["page_count"]

    # Add a file
    (sample_vault / "wiki" / "watcher-test.md").write_text("# Watcher Test\n\nDetected!")
    await asyncio.sleep(1.0)

    # Verify new page appeared
    resp = client.request({"type": "status"})
    assert resp["page_count"] == initial_count + 1

    # Cleanup
    await watcher.stop()
    server._server.close()
    serve_task.cancel()
    try:
        await serve_task
    except asyncio.CancelledError:
        pass
    await server.stop()
