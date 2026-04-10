from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from llm_wiki.daemon.protocol import read_message, write_message
from llm_wiki.daemon.server import DaemonServer


@pytest_asyncio.fixture
async def server_with_ingest(sample_vault: Path, tmp_path: Path):
    """Daemon server for testing ingest route."""
    sock_path = tmp_path / "test.sock"
    server = DaemonServer(sample_vault, sock_path)
    await server.start()
    yield server, sock_path
    await server.stop()


async def _request(sock_path: Path, msg: dict) -> dict:
    reader, writer = await asyncio.open_unix_connection(str(sock_path))
    try:
        await write_message(writer, msg)
        return await read_message(reader)
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_ingest_route_missing_file_returns_ok(server_with_ingest):
    """Ingest with non-existent file returns ok with zero pages (extraction fails gracefully)."""
    server, sock_path = server_with_ingest
    resp = await _request(sock_path, {
        "type": "ingest",
        "source_path": "/nonexistent/file.md",
        "author": "cli",
        "connection_id": "test-conn",
    })
    assert resp["status"] == "ok"
    assert "pages_created" in resp
    # Phase 6b: pages_created is now a count, not a list
    assert resp["pages_created"] == 0


@pytest.mark.asyncio
async def test_ingest_route_missing_source_path(server_with_ingest):
    """Missing source_path field returns an error."""
    server, sock_path = server_with_ingest
    resp = await _request(sock_path, {"type": "ingest"})
    assert resp["status"] == "error"
    assert "source_path" in resp["message"]


@pytest.mark.asyncio
async def test_ingest_route_missing_connection_id_returns_error(server_with_ingest):
    """Phase 6b: connection_id is required so the ingest can join a session."""
    server, sock_path = server_with_ingest
    resp = await _request(sock_path, {
        "type": "ingest",
        "source_path": "/nonexistent/file.md",
        "author": "cli",
    })
    assert resp["status"] == "error"
    assert "connection_id" in resp["message"]


async def _stream_request(sock_path: Path, msg: dict) -> list[dict]:
    """Read all frames from a streaming ingest request."""
    reader, writer = await asyncio.open_unix_connection(str(sock_path))
    frames = []
    try:
        await write_message(writer, msg)
        while True:
            frame = await read_message(reader)
            frames.append(frame)
            if frame.get("type") in ("done", "error"):
                break
    finally:
        writer.close()
        await writer.wait_closed()
    return frames


@pytest.mark.asyncio
async def test_ingest_stream_route_sends_progress_and_done(server_with_ingest, monkeypatch):
    """Streaming ingest sends progress frames then a done frame."""
    server, sock_path = server_with_ingest

    async def fake_ingest(self_agent, source_path, vault_root, *, on_progress=None, **kwargs):
        if on_progress:
            await on_progress({"stage": "extracting"})
            await on_progress({"stage": "concepts_found", "count": 2})
            await on_progress({
                "stage": "concept_done", "name": "foo", "title": "Foo",
                "action": "created", "num": 1, "total": 2,
            })
            await on_progress({
                "stage": "concept_done", "name": "bar", "title": "Bar",
                "action": "updated", "num": 2, "total": 2,
            })
        from llm_wiki.ingest.agent import IngestResult
        from pathlib import Path as _Path
        result = IngestResult(
            source_path=_Path(source_path),
            pages_created=["foo"],
            pages_updated=["bar"],
        )
        return result

    monkeypatch.setattr("llm_wiki.ingest.agent.IngestAgent.ingest", fake_ingest)

    frames = await _stream_request(sock_path, {
        "type": "ingest",
        "source_path": "/any/path.md",
        "author": "test",
        "connection_id": "test-conn",
        "stream": True,
    })

    types = [f["type"] for f in frames]
    assert types == ["progress", "progress", "progress", "progress", "done"]
    assert frames[0]["stage"] == "extracting"
    assert frames[1]["stage"] == "concepts_found"
    assert frames[1]["count"] == 2
    assert frames[2]["stage"] == "concept_done"
    assert frames[2]["name"] == "foo"
    assert frames[4]["status"] == "ok"
    assert frames[4]["pages_created"] == 1
    assert frames[4]["pages_updated"] == 1


@pytest.mark.asyncio
async def test_ingest_stream_trace_flag_emits_trace_frames(server_with_ingest, monkeypatch):
    """When trace=True, the daemon wires trace_fn into LLMClient so that each
    LLM call produces a ``type: "trace"`` frame on the stream."""
    server, sock_path = server_with_ingest

    from llm_wiki.ingest.agent import IngestResult, IngestAgent
    from llm_wiki.traverse.llm_client import LLMClient

    captured_trace_fns: list = []

    # Intercept LLMClient construction to grab the trace_fn that gets wired in.
    original_init = LLMClient.__init__

    def patched_init(self, queue, model, *, trace_fn=None, **kwargs):
        captured_trace_fns.append(trace_fn)
        original_init(self, queue, model, trace_fn=trace_fn, **kwargs)

    monkeypatch.setattr(LLMClient, "__init__", patched_init)

    # Make ingest return immediately without doing real LLM work.
    async def fake_ingest(self_agent, source_path, vault_root, *, on_progress=None, **kwargs):
        from pathlib import Path as _Path
        return IngestResult(
            source_path=_Path(source_path),
            pages_created=["x"],
            pages_updated=[],
        )

    monkeypatch.setattr(IngestAgent, "ingest", fake_ingest)

    frames = await _stream_request(sock_path, {
        "type": "ingest",
        "source_path": "/any/path.md",
        "author": "test",
        "connection_id": "test-conn",
        "stream": True,
        "trace": True,
    })

    # Verify done frame arrives normally
    done_frames = [f for f in frames if f.get("type") == "done"]
    assert done_frames, "expected at least one done frame"
    # Verify a non-None trace_fn was passed to LLMClient
    assert any(fn is not None for fn in captured_trace_fns), (
        "trace=True must wire a trace_fn into LLMClient"
    )


@pytest.mark.asyncio
async def test_ingest_stream_no_trace_flag_passes_no_trace_fn(server_with_ingest, monkeypatch):
    """Without trace=True, trace_fn must be None — no overhead on normal ingests."""
    server, sock_path = server_with_ingest

    from llm_wiki.ingest.agent import IngestResult, IngestAgent
    from llm_wiki.traverse.llm_client import LLMClient

    captured_trace_fns: list = []

    original_init = LLMClient.__init__

    def patched_init(self, queue, model, *, trace_fn=None, **kwargs):
        captured_trace_fns.append(trace_fn)
        original_init(self, queue, model, trace_fn=trace_fn, **kwargs)

    monkeypatch.setattr(LLMClient, "__init__", patched_init)

    async def fake_ingest(self_agent, source_path, vault_root, *, on_progress=None, **kwargs):
        from pathlib import Path as _Path
        return IngestResult(source_path=_Path(source_path))

    monkeypatch.setattr(IngestAgent, "ingest", fake_ingest)

    frames = await _stream_request(sock_path, {
        "type": "ingest",
        "source_path": "/any/path.md",
        "author": "test",
        "connection_id": "test-conn",
        "stream": True,
        # no trace key
    })

    done_frames = [f for f in frames if f.get("type") == "done"]
    assert done_frames
    assert all(fn is None for fn in captured_trace_fns), (
        "without trace=True, trace_fn must be None"
    )


@pytest.mark.asyncio
async def test_ingest_stream_route_missing_source_path_returns_error(server_with_ingest):
    """Streaming ingest validates required fields, sends error frame."""
    server, sock_path = server_with_ingest

    frames = await _stream_request(sock_path, {
        "type": "ingest",
        "connection_id": "test-conn",
        "stream": True,
        # source_path missing
    })

    assert len(frames) == 1
    assert frames[0]["status"] == "error"
    assert "source_path" in frames[0]["message"]
