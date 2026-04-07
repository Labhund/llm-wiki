import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from llm_wiki.daemon.client import DaemonClient
from llm_wiki.daemon.server import DaemonServer


@pytest_asyncio.fixture
async def running_daemon(sample_vault: Path, tmp_path: Path):
    """Start daemon, yield client, stop daemon."""
    sock_path = tmp_path / "test.sock"
    server = DaemonServer(sample_vault, sock_path)
    await server.start()

    serve_task = asyncio.create_task(server.serve_forever())

    client = DaemonClient(sock_path)
    yield client

    server._server.close()
    serve_task.cancel()
    try:
        await serve_task
    except asyncio.CancelledError:
        pass
    await server.stop()


@pytest.mark.asyncio
async def test_client_search(running_daemon):
    client = running_daemon
    resp = client.request({"type": "search", "query": "sRNA", "limit": 5})
    assert resp["status"] == "ok"
    assert len(resp["results"]) >= 1


@pytest.mark.asyncio
async def test_client_status(running_daemon):
    client = running_daemon
    resp = client.request({"type": "status"})
    assert resp["status"] == "ok"
    assert resp["page_count"] == 4


@pytest.mark.asyncio
async def test_client_read(running_daemon):
    client = running_daemon
    resp = client.request({"type": "read", "page_name": "srna-embeddings"})
    assert resp["status"] == "ok"
    assert "overview" in resp["content"].lower()


@pytest.mark.asyncio
async def test_client_is_running(running_daemon, tmp_path: Path):
    client = running_daemon
    assert client.is_running()

    dead_client = DaemonClient(tmp_path / "nonexistent.sock")
    assert not dead_client.is_running()
