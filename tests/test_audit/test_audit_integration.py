"""End-to-end: vault → daemon → lint → query → resolve → re-lint."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from llm_wiki.daemon.client import DaemonClient
from llm_wiki.daemon.server import DaemonServer


@pytest.mark.asyncio
async def test_full_lint_lifecycle(sample_vault: Path, tmp_path: Path):
    sock_path = tmp_path / "audit-int.sock"
    server = DaemonServer(sample_vault, sock_path)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        client = DaemonClient(sock_path)

        # 1. First lint — populates the queue. The scheduled auditor worker
        # may have already filed some issues at daemon start time, so total
        # = new + existing, not new alone.
        first = client.request({"type": "lint"})
        assert first["status"] == "ok"
        assert first["total_issues"] >= 4
        combined_first = set(first["new_issue_ids"]) | set(first["existing_issue_ids"])
        assert len(combined_first) == first["total_issues"]

        # 2. List the issues
        listing = client.request({"type": "issues-list"})
        assert listing["status"] == "ok"
        assert len(listing["issues"]) >= 4

        # 3. Pick an issue and resolve it
        target_id = listing["issues"][0]["id"]
        update = client.request(
            {"type": "issues-update", "id": target_id, "status": "resolved"}
        )
        assert update["status"] == "ok"

        # 4. Verify status changed via get
        got = client.request({"type": "issues-get", "id": target_id})
        assert got["issue"]["status"] == "resolved"

        # 5. Filter open issues — resolved one should not appear
        open_only = client.request({"type": "issues-list", "status_filter": "open"})
        open_ids = {i["id"] for i in open_only["issues"]}
        assert target_id not in open_ids

        # 6. Re-lint — should produce zero new issues; resolved one is preserved
        second = client.request({"type": "lint"})
        assert second["new_issue_ids"] == []

        # 7. The resolved one is still resolved (not re-opened by the auditor)
        got2 = client.request({"type": "issues-get", "id": target_id})
        assert got2["issue"]["status"] == "resolved"
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()
