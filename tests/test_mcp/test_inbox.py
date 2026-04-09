from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def git_vault(tmp_path: Path) -> Path:
    """Minimal git-initialized vault for commit tests."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki").mkdir()
    (tmp_path / "README.md").write_text("vault\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def _make_server(vault_root: Path):
    """Build a DaemonServer with minimal state — no scheduler, no workers."""
    import asyncio
    from llm_wiki.config import WikiConfig
    from llm_wiki.daemon.server import DaemonServer
    server = DaemonServer.__new__(DaemonServer)
    server._vault_root = vault_root
    server._config = WikiConfig()
    server._commit_lock = asyncio.Lock()
    return server


# ---------------------------------------------------------------------------
# inbox-create
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inbox_create_creates_plan_file(git_vault: Path):
    (git_vault / "raw" / "paper.pdf").write_bytes(b"%PDF-1.4 fake")
    subprocess.run(["git", "add", "."], cwd=git_vault, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add source"], cwd=git_vault, check=True, capture_output=True)

    server = _make_server(git_vault)
    response = await server._handle_inbox_create({
        "source_path": "raw/paper.pdf",
        "title": "My Paper",
        "claims": ["Claim A", "Claim B"],
        "author": "test-researcher",
    })

    assert response["status"] == "ok"
    plan_path = git_vault / response["plan_path"]
    assert plan_path.exists()
    assert "- [ ] Claim A" in plan_path.read_text()


@pytest.mark.asyncio
async def test_inbox_create_rejects_source_outside_raw(git_vault: Path):
    server = _make_server(git_vault)
    response = await server._handle_inbox_create({
        "source_path": "wiki/page.md",
        "title": "T",
        "claims": [],
        "author": "test",
    })
    assert response["status"] == "error"
    assert "raw/" in response["message"]


@pytest.mark.asyncio
async def test_inbox_create_rejects_missing_title(git_vault: Path):
    server = _make_server(git_vault)
    response = await server._handle_inbox_create({
        "source_path": "raw/paper.pdf",
        "title": "",
        "claims": [],
        "author": "test",
    })
    assert response["status"] == "error"
    assert "title" in response["message"]


@pytest.mark.asyncio
async def test_inbox_create_accepts_absolute_source_path(git_vault: Path):
    (git_vault / "raw" / "abs.pdf").write_bytes(b"%PDF-1.4 fake")
    subprocess.run(["git", "add", "."], cwd=git_vault, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add"], cwd=git_vault, check=True, capture_output=True)

    server = _make_server(git_vault)
    response = await server._handle_inbox_create({
        "source_path": str(git_vault / "raw" / "abs.pdf"),
        "title": "Abs Paper",
        "claims": [],
        "author": "test",
    })
    assert response["status"] == "ok"
    assert response["source"] == "raw/abs.pdf"


# ---------------------------------------------------------------------------
# inbox-get
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inbox_get_returns_content(tmp_path: Path):
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()
    plan = inbox_dir / "plan.md"
    plan.write_text("---\nsource: raw/paper.pdf\nstatus: in-progress\n---\n\n# Plan\n")

    server = _make_server(tmp_path)
    response = await server._handle_inbox_get({"plan_path": "inbox/plan.md"})

    assert response["status"] == "ok"
    assert "# Plan" in response["content"]
    assert response["frontmatter"]["source"] == "raw/paper.pdf"


@pytest.mark.asyncio
async def test_inbox_get_returns_error_for_missing_file(tmp_path: Path):
    server = _make_server(tmp_path)
    response = await server._handle_inbox_get({"plan_path": "inbox/nonexistent.md"})
    assert response["status"] == "error"


@pytest.mark.asyncio
async def test_inbox_get_rejects_path_outside_inbox(tmp_path: Path):
    server = _make_server(tmp_path)
    response = await server._handle_inbox_get({"plan_path": "wiki/page.md"})
    assert response["status"] == "error"
    assert "inbox/" in response["message"]


# ---------------------------------------------------------------------------
# inbox-write
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inbox_write_updates_content_and_commits(git_vault: Path):
    inbox_dir = git_vault / "inbox"
    inbox_dir.mkdir()
    plan = inbox_dir / "plan.md"
    plan.write_text("---\nsource: raw/paper.pdf\nstatus: in-progress\n---\n\nOriginal.\n")
    subprocess.run(["git", "add", "."], cwd=git_vault, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add plan"], cwd=git_vault, check=True, capture_output=True)

    server = _make_server(git_vault)
    new_content = "---\nsource: raw/paper.pdf\nstatus: in-progress\n---\n\nUpdated checkpoint.\n"
    response = await server._handle_inbox_write({
        "plan_path": "inbox/plan.md",
        "content": new_content,
        "author": "test-researcher",
    })

    assert response["status"] == "ok"
    assert plan.read_text() == new_content


@pytest.mark.asyncio
async def test_inbox_write_rejects_path_outside_inbox(git_vault: Path):
    server = _make_server(git_vault)
    response = await server._handle_inbox_write({
        "plan_path": "wiki/page.md",
        "content": "# Page",
        "author": "test",
    })
    assert response["status"] == "error"
    assert "inbox/" in response["message"]


@pytest.mark.asyncio
async def test_inbox_write_rejects_empty_content(git_vault: Path):
    inbox_dir = git_vault / "inbox"
    inbox_dir.mkdir()
    server = _make_server(git_vault)
    response = await server._handle_inbox_write({
        "plan_path": "inbox/plan.md",
        "content": "",
        "author": "test",
    })
    assert response["status"] == "error"


# ---------------------------------------------------------------------------
# inbox-list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inbox_list_returns_active_plans(tmp_path: Path):
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()
    (inbox_dir / "2026-04-10-paper-plan.md").write_text(
        "---\nsource: raw/paper.pdf\nstarted: 2026-04-10\nstatus: in-progress\nsessions: 1\n---\n\n"
        "## Claims / Ideas\n- [ ] Alpha\n- [x] Beta\n"
    )

    server = _make_server(tmp_path)
    response = await server._handle_inbox_list({})

    assert response["status"] == "ok"
    assert len(response["plans"]) == 1
    plan = response["plans"][0]
    assert plan["source"] == "raw/paper.pdf"
    assert plan["status"] == "in-progress"
    assert plan["unchecked_claims"] == 1


@pytest.mark.asyncio
async def test_inbox_list_empty_when_no_inbox_dir(tmp_path: Path):
    server = _make_server(tmp_path)
    response = await server._handle_inbox_list({})
    assert response["status"] == "ok"
    assert response["plans"] == []


@pytest.mark.asyncio
async def test_inbox_list_skips_non_md_files(tmp_path: Path):
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()
    (inbox_dir / "notes.txt").write_text("some text")
    server = _make_server(tmp_path)
    response = await server._handle_inbox_list({})
    assert response["plans"] == []
