from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from llm_wiki.daemon.client import DaemonClient
from llm_wiki.daemon.server import DaemonServer


@pytest.mark.asyncio
async def test_lint_route_returns_audit_report(sample_vault: Path, tmp_path: Path):
    """The lint route runs the auditor and returns a serialized AuditReport."""
    sock_path = tmp_path / "lint.sock"
    server = DaemonServer(sample_vault, sock_path)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        client = DaemonClient(sock_path)
        resp = client.request({"type": "lint"})

        assert resp["status"] == "ok"
        assert resp["total_checks_run"] == 4
        assert resp["total_issues"] >= 4
        assert "orphans" in resp["by_check"]
        assert "broken-wikilinks" in resp["by_check"]
        assert "missing-markers" in resp["by_check"]
        assert "broken-citations" in resp["by_check"]
        assert isinstance(resp["new_issue_ids"], list)
        assert isinstance(resp["existing_issue_ids"], list)
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()


@pytest.mark.asyncio
async def test_lint_route_idempotent(sample_vault: Path, tmp_path: Path):
    """Calling lint twice does not re-create issues."""
    sock_path = tmp_path / "lint2.sock"
    server = DaemonServer(sample_vault, sock_path)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        client = DaemonClient(sock_path)
        first = client.request({"type": "lint"})
        second = client.request({"type": "lint"})

        assert second["new_issue_ids"] == []
        assert sorted(second["existing_issue_ids"]) == sorted(first["new_issue_ids"])
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()
