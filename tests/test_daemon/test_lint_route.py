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


def _serialize_helper(issue_dict):
    return {
        k: v for k, v in issue_dict.items()
        if k in {"id", "type", "status", "title", "page", "detected_by"}
    }


@pytest.mark.asyncio
async def test_issues_list_route(sample_vault: Path, tmp_path: Path):
    """issues-list returns the issues from the queue, optionally filtered."""
    sock_path = tmp_path / "issues-list.sock"
    server = DaemonServer(sample_vault, sock_path)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        client = DaemonClient(sock_path)
        # Populate the queue
        client.request({"type": "lint"})

        all_resp = client.request({"type": "issues-list"})
        assert all_resp["status"] == "ok"
        assert len(all_resp["issues"]) >= 4

        broken_resp = client.request({"type": "issues-list", "type_filter": "broken-link"})
        assert all(i["type"] == "broken-link" for i in broken_resp["issues"])

        open_resp = client.request({"type": "issues-list", "status_filter": "open"})
        assert all(i["status"] == "open" for i in open_resp["issues"])
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()


@pytest.mark.asyncio
async def test_issues_get_and_update(sample_vault: Path, tmp_path: Path):
    sock_path = tmp_path / "issues-get.sock"
    server = DaemonServer(sample_vault, sock_path)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        client = DaemonClient(sock_path)
        client.request({"type": "lint"})

        listing = client.request({"type": "issues-list"})["issues"]
        target_id = listing[0]["id"]

        get_resp = client.request({"type": "issues-get", "id": target_id})
        assert get_resp["status"] == "ok"
        assert get_resp["issue"]["id"] == target_id
        assert "body" in get_resp["issue"]

        update_resp = client.request(
            {"type": "issues-update", "id": target_id, "status": "wontfix"}
        )
        assert update_resp["status"] == "ok"

        get_after = client.request({"type": "issues-get", "id": target_id})
        assert get_after["issue"]["status"] == "wontfix"

        bad_status = client.request(
            {"type": "issues-update", "id": target_id, "status": "bogus"}
        )
        assert bad_status["status"] == "error"

        missing = client.request({"type": "issues-get", "id": "nope-vault-aaaaaa"})
        assert missing["status"] == "error"
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()
