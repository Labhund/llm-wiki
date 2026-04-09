# Attended Ingest Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build the inbox plan file system — a persisted research cursor for multi-session deep ingests — and rewrite the ingest skill to use it.

**Architecture:** A new `ingest/plan.py` module provides pure-Python helpers (render, create, read) for inbox plan files. Four MCP tools (`wiki_inbox_create`, `wiki_inbox_get`, `wiki_inbox_write`, `wiki_inbox_list`) give agents atomic creation, reading, updating, and listing. All inbox commits go directly via subprocess outside the session pipeline (same pattern as `wiki_source_mark`). The ingest skill is then rewritten to reference these tools explicitly.

**Tech Stack:** Python stdlib (`pathlib`, `datetime`, `subprocess`), PyYAML (already a dep), pytest-asyncio. No new dependencies.

**Scope notes:**
- `wiki_source_mark` (referenced in the rewritten skill) is implemented in the `source-reading-status` plan, which runs in a parallel worktree. The skill is written correctly — it will be fully operational once both plans merge.
- `llm-wiki init` creating `inbox/` on vault initialisation is a known gap. `create_plan_file` uses `mkdir(exist_ok=True)` as a runtime rescue, so this is non-blocking. Tracked as a follow-up.
- A `find_inbox_staleness` auditor check (surfacing in-progress plans via `wiki_lint`) is tracked in `docs/superpowers/plans/2026-04-10-inbox-inprogress-check.md` and executes after the source-reading-status plan merges.

---

## File Structure

| File | Change |
|---|---|
| `src/llm_wiki/ingest/plan.py` | **New** — `render_plan_file`, `plan_filename`, `create_plan_file`, `read_plan_frontmatter`, `count_unchecked_claims` |
| `src/llm_wiki/config.py` | Add `inbox_dir: str = "inbox/"` to `VaultConfig` |
| `src/llm_wiki/daemon/server.py` | Add `inbox-create`, `inbox-get`, `inbox-write`, `inbox-list` to `_route`; add four handler methods |
| `src/llm_wiki/mcp/tools.py` | Add `WIKI_INBOX_CREATE`, `WIKI_INBOX_GET`, `WIKI_INBOX_WRITE`, `WIKI_INBOX_LIST` to `WIKI_TOOLS` |
| `tests/test_ingest/test_plan.py` | **New** — unit tests for `plan.py` helpers |
| `tests/test_mcp/test_inbox.py` | **New** — integration tests for inbox daemon routes |
| `skills/llm-wiki/ingest.md` | Rewrite Mode 3 setup and session checkpoint to use the new tools; add reading-status protocol |

---

### Task 1: `ingest/plan.py` + `config.py`

**Files:**
- Create: `src/llm_wiki/ingest/plan.py`
- Modify: `src/llm_wiki/config.py`
- Create: `tests/test_ingest/test_plan.py`

- [x] **Step 1: Write failing tests**

Create `tests/test_ingest/test_plan.py`:

```python
from __future__ import annotations

import datetime
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# render_plan_file
# ---------------------------------------------------------------------------

def test_render_plan_file_frontmatter(tmp_path: Path):
    from llm_wiki.ingest.plan import render_plan_file
    content = render_plan_file(
        source="raw/paper.pdf",
        title="My Paper",
        claims=["Claim A", "Claim B"],
        started="2026-04-10",
    )
    assert "source: raw/paper.pdf" in content
    assert "started: 2026-04-10" in content
    assert "status: in-progress" in content
    assert "sessions: 1" in content


def test_render_plan_file_claims_are_checkboxes(tmp_path: Path):
    from llm_wiki.ingest.plan import render_plan_file
    content = render_plan_file("raw/p.pdf", "T", ["Alpha", "Beta"], "2026-04-10")
    assert "- [x] Alpha" in content
    assert "- [x] Beta" in content


def test_render_plan_file_empty_claims():
    from llm_wiki.ingest.plan import render_plan_file
    content = render_plan_file("raw/p.pdf", "T", [], "2026-04-10")
    assert "## Claims / Ideas" in content
    assert "- [x]" not in content


def test_render_plan_file_has_required_sections():
    from llm_wiki.ingest.plan import render_plan_file
    content = render_plan_file("raw/p.pdf", "T", ["X"], "2026-04-10")
    assert "## Claims / Ideas" in content
    assert "## Decisions" in content
    assert "## Session Notes" in content


# ---------------------------------------------------------------------------
# plan_filename
# ---------------------------------------------------------------------------

def test_plan_filename_format():
    from llm_wiki.ingest.plan import plan_filename
    name = plan_filename("raw/2026-04-09-vaswani.pdf", "2026-04-10")
    # Leading date prefix stripped from source stem — no double-dating
    assert name == "2026-04-10-vaswani-plan.md"


def test_plan_filename_uses_stem_not_extension():
    from llm_wiki.ingest.plan import plan_filename
    name = plan_filename("raw/paper.pdf", "2026-04-10")
    assert name.endswith("-plan.md")
    assert ".pdf" not in name


# ---------------------------------------------------------------------------
# create_plan_file
# ---------------------------------------------------------------------------

def test_create_plan_file_creates_file(tmp_path: Path):
    from llm_wiki.ingest.plan import create_plan_file
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    path = create_plan_file(tmp_path, "raw/paper.pdf", "My Paper", ["Claim A"])
    assert path.exists()
    assert path.parent == tmp_path / "inbox"
    assert path.name.endswith("-plan.md")


def test_create_plan_file_creates_inbox_dir(tmp_path: Path):
    from llm_wiki.ingest.plan import create_plan_file
    # inbox/ does not exist yet
    path = create_plan_file(tmp_path, "raw/paper.pdf", "T", [])
    assert (tmp_path / "inbox").is_dir()


def test_create_plan_file_raises_if_already_exists(tmp_path: Path):
    from llm_wiki.ingest.plan import create_plan_file
    create_plan_file(tmp_path, "raw/paper.pdf", "T", ["A"])
    with pytest.raises(FileExistsError):
        create_plan_file(tmp_path, "raw/paper.pdf", "T", ["A"])


def test_create_plan_file_content_is_valid_yaml_frontmatter(tmp_path: Path):
    from llm_wiki.ingest.plan import create_plan_file, read_plan_frontmatter
    path = create_plan_file(tmp_path, "raw/paper.pdf", "My Paper", ["X"])
    fm = read_plan_frontmatter(path)
    assert fm["source"] == "raw/paper.pdf"
    assert fm["status"] == "in-progress"
    assert fm["sessions"] == 1


# ---------------------------------------------------------------------------
# read_plan_frontmatter
# ---------------------------------------------------------------------------

def test_read_plan_frontmatter_returns_dict(tmp_path: Path):
    f = tmp_path / "plan.md"
    f.write_text("---\nsource: raw/p.pdf\nstatus: in-progress\n---\n\nBody.\n")
    from llm_wiki.ingest.plan import read_plan_frontmatter
    fm = read_plan_frontmatter(f)
    assert fm["source"] == "raw/p.pdf"


def test_read_plan_frontmatter_missing_file_returns_empty(tmp_path: Path):
    from llm_wiki.ingest.plan import read_plan_frontmatter
    assert read_plan_frontmatter(tmp_path / "nonexistent.md") == {}


# ---------------------------------------------------------------------------
# count_unchecked_claims
# ---------------------------------------------------------------------------

def test_count_unchecked_claims():
    from llm_wiki.ingest.plan import count_unchecked_claims
    content = "- [x] A\n- [x] B\n- [x] C\n"
    assert count_unchecked_claims(content) == 2


def test_count_unchecked_claims_none():
    from llm_wiki.ingest.plan import count_unchecked_claims
    assert count_unchecked_claims("- [x] A\n- [x] B\n") == 0
```

- [x] **Step 2: Run tests to confirm they all fail**

```bash
pytest tests/test_ingest/test_plan.py -v 2>&1 | head -15
```

Expected: `ModuleNotFoundError: No module named 'llm_wiki.ingest.plan'`

- [x] **Step 3: Create `src/llm_wiki/ingest/plan.py`**

```python
from __future__ import annotations

import datetime
from pathlib import Path

import yaml


def render_plan_file(
    source: str,
    title: str,
    claims: list[str],
    started: str,
) -> str:
    """Return the full text of a new inbox plan file (frontmatter + body)."""
    claims_md = "\n".join(f"- [x] {c}" for c in claims)
    return (
        f"---\n"
        f"source: {source}\n"
        f"started: {started}\n"
        f"status: in-progress\n"
        f"sessions: 1\n"
        f"---\n\n"
        f"# {title} — Research Plan\n\n"
        f"## Claims / Ideas\n"
        f"{claims_md}\n\n"
        f"## Decisions\n\n"
        f"## Session Notes\n\n"
        f"### {started} (Session 1)\n"
    )


def plan_filename(source_path: str, started: str) -> str:
    """Derive the inbox plan filename from the source path and start date.

    Strips any leading ``YYYY-MM-DD-`` date prefix from the source stem so
    that ``raw/2026-04-09-vaswani.pdf`` with ``started='2026-04-10'``
    → ``2026-04-10-vaswani-plan.md`` (not double-dated).
    """
    import re as _re
    stem = Path(source_path).stem
    stem = _re.sub(r"^\d{4}-\d{2}-\d{2}-", "", stem)
    return f"{started}-{stem}-plan.md"


def create_plan_file(
    vault_root: Path,
    source: str,
    title: str,
    claims: list[str],
) -> Path:
    """Create a scaffolded plan file in inbox/.

    Creates ``inbox/`` if it does not exist. Does NOT git-commit — the
    daemon handler is responsible for the commit.

    Raises ``FileExistsError`` if the plan file already exists (same
    source ingested on the same day).
    """
    inbox_dir = vault_root / "inbox"
    inbox_dir.mkdir(exist_ok=True)

    started = datetime.date.today().isoformat()
    filename = plan_filename(source, started)
    plan_path = inbox_dir / filename

    if plan_path.exists():
        raise FileExistsError(
            f"Plan file already exists: {plan_path.relative_to(vault_root)}"
        )

    content = render_plan_file(source, title, claims, started)
    plan_path.write_text(content, encoding="utf-8")
    return plan_path


def read_plan_frontmatter(path: Path) -> dict:
    """Read YAML frontmatter from a plan file.

    Returns ``{}`` on any error (missing file, no frontmatter block,
    YAML parse failure).
    """
    try:
        with path.open(encoding="utf-8") as f:
            if f.readline().strip() != "---":
                return {}
            lines: list[str] = []
            for _ in range(20):
                line = f.readline()
                if not line or line.strip() == "---":
                    break
                lines.append(line)
        return yaml.safe_load("".join(lines)) or {}
    except (OSError, yaml.YAMLError):
        return {}


def count_unchecked_claims(content: str) -> int:
    """Count unchecked ``- [x]`` items in a plan file's body."""
    return content.count("- [x]")
```

- [x] **Step 4: Run tests to confirm they all pass**

```bash
pytest tests/test_ingest/test_plan.py -v
```

Expected: all PASS

- [x] **Step 5: Add `inbox_dir` to `VaultConfig` in `config.py`**

In `src/llm_wiki/config.py`, update `VaultConfig`:

```python
@dataclass
class VaultConfig:
    mode: str = "vault"
    raw_dir: str = "raw/"
    wiki_dir: str = "wiki/"
    inbox_dir: str = "inbox/"    # ← new
    watch: bool = True
```

- [x] **Step 6: Verify config parses correctly**

```bash
python -c "from llm_wiki.config import WikiConfig; c = WikiConfig(); print(c.vault.inbox_dir)"
```

Expected: `inbox/`

- [x] **Step 7: Commit**

```bash
git add src/llm_wiki/ingest/plan.py src/llm_wiki/config.py tests/test_ingest/test_plan.py
git commit -m "feat: inbox plan file helpers (render, create, read) + VaultConfig.inbox_dir"
```

---

### Task 2: daemon inbox routes + MCP tools + tests

**Files:**
- Modify: `src/llm_wiki/daemon/server.py`
- Modify: `src/llm_wiki/mcp/tools.py`
- Create: `tests/test_mcp/test_inbox.py`

- [x] **Step 1: Write failing tests**

Create `tests/test_mcp/test_inbox.py`:

```python
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
    assert "- [x] Claim A" in plan_path.read_text()


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
        "## Claims / Ideas\n- [x] Alpha\n- [x] Beta\n"
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
```

- [x] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_mcp/test_inbox.py -v 2>&1 | head -15
```

Expected: `AttributeError: '_handle_inbox_create'`

- [x] **Step 3: Add inbox route handlers to `DaemonServer`**

In `src/llm_wiki/daemon/server.py`, add four cases to the `match` in `_route()`:

```python
            case "inbox-create":
                return await self._handle_inbox_create(request)
            case "inbox-get":
                return await self._handle_inbox_get(request)
            case "inbox-write":
                return await self._handle_inbox_write(request)
            case "inbox-list":
                return await self._handle_inbox_list(request)
```

Add these four handler methods to `DaemonServer`:

```python
    async def _handle_inbox_create(self, request: dict) -> dict:
        source_path_str = request.get("source_path", "")
        title = request.get("title", "")
        claims = request.get("claims", [])
        author = request.get("author", "cli")

        if not title:
            return {"status": "error", "message": "Missing required field: title"}
        if not source_path_str:
            return {"status": "error", "message": "Missing required field: source_path"}

        # Normalize to relative raw/<filename>
        source_path = Path(source_path_str)
        if source_path.is_absolute():
            try:
                source_path_str = str(source_path.relative_to(self._vault_root))
            except ValueError:
                return {"status": "error", "message": "source_path must be under the vault root"}
        if not source_path_str.startswith("raw/"):
            return {"status": "error", "message": "source_path must be under raw/"}

        from llm_wiki.ingest.plan import create_plan_file
        try:
            plan_path = create_plan_file(
                self._vault_root, source_path_str, title, claims
            )
        except FileExistsError as e:
            return {"status": "error", "message": str(e)}

        rel_path = str(plan_path.relative_to(self._vault_root))
        commit_msg = (
            f"plan: create inbox plan for {Path(source_path_str).name}\n\n"
            f"Agent: {author}"
        )
        import subprocess as _sp
        async with self._commit_lock:
            _sp.run(["git", "add", rel_path], cwd=self._vault_root, check=True, capture_output=True)
            _sp.run(["git", "commit", "-m", commit_msg], cwd=self._vault_root, check=True, capture_output=True)

        return {
            "status": "ok",
            "plan_path": rel_path,
            "source": source_path_str,
        }

    async def _handle_inbox_get(self, request: dict) -> dict:
        plan_path_str = request.get("plan_path", "")
        if not plan_path_str:
            return {"status": "error", "message": "Missing required field: plan_path"}

        inbox_dir = self._vault_root / self._config.vault.inbox_dir.rstrip("/")
        plan_path = (self._vault_root / plan_path_str).resolve()
        try:
            plan_path.relative_to(inbox_dir.resolve())
        except ValueError:
            return {"status": "error", "message": "plan_path must be under inbox/"}

        if not plan_path.exists():
            return {"status": "error", "message": f"Plan file not found: {plan_path_str}"}

        content = plan_path.read_text(encoding="utf-8")
        from llm_wiki.ingest.plan import read_plan_frontmatter
        fm = read_plan_frontmatter(plan_path)
        return {"status": "ok", "content": content, "frontmatter": fm}

    async def _handle_inbox_write(self, request: dict) -> dict:
        plan_path_str = request.get("plan_path", "")
        content = request.get("content", "")
        author = request.get("author", "cli")

        if not plan_path_str:
            return {"status": "error", "message": "Missing required field: plan_path"}
        if not content:
            return {"status": "error", "message": "content must not be empty"}

        inbox_dir = self._vault_root / self._config.vault.inbox_dir.rstrip("/")
        plan_path = (self._vault_root / plan_path_str).resolve()
        try:
            plan_path.relative_to(inbox_dir.resolve())
        except ValueError:
            return {"status": "error", "message": "plan_path must be under inbox/"}

        plan_path.write_text(content, encoding="utf-8")

        rel_path = str(plan_path.relative_to(self._vault_root))
        commit_msg = (
            f"plan: checkpoint {plan_path.name}\n\n"
            f"Agent: {author}"
        )
        import subprocess as _sp
        async with self._commit_lock:
            _sp.run(["git", "add", rel_path], cwd=self._vault_root, check=True, capture_output=True)
            _sp.run(["git", "commit", "-m", commit_msg], cwd=self._vault_root, check=True, capture_output=True)

        return {"status": "ok", "plan_path": rel_path}

    async def _handle_inbox_list(self, request: dict) -> dict:
        inbox_dir = self._vault_root / self._config.vault.inbox_dir.rstrip("/")
        if not inbox_dir.is_dir():
            return {"status": "ok", "plans": []}

        from llm_wiki.ingest.plan import read_plan_frontmatter, count_unchecked_claims
        plans = []
        for f in sorted(inbox_dir.iterdir()):
            if not f.is_file() or f.suffix.lower() not in (".md", ".markdown"):
                continue
            fm = read_plan_frontmatter(f)
            content = f.read_text(encoding="utf-8")
            plans.append({
                "path": f"inbox/{f.name}",
                "source": fm.get("source", ""),
                "started": fm.get("started", ""),
                "status": fm.get("status", ""),
                "sessions": fm.get("sessions", 0),
                "unchecked_claims": count_unchecked_claims(content),
            })
        return {"status": "ok", "plans": plans}
```

- [x] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_mcp/test_inbox.py -v
```

Expected: all PASS

- [x] **Step 5: Add MCP tool definitions to `mcp/tools.py`**

Add four handler functions and definitions, then append all four to `WIKI_TOOLS`:

```python
# ---------------------------------------------------------------------------
# Inbox plan files
# ---------------------------------------------------------------------------

async def handle_wiki_inbox_create(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({
        "type": "inbox-create",
        "source_path": args["source_path"],
        "title": args["title"],
        "claims": args.get("claims", []),
        "author": args["author"],
    })
    return _ok(translate_daemon_response(response))


WIKI_INBOX_CREATE = ToolDefinition(
    name="wiki_inbox_create",
    description=(
        "Create a scaffolded inbox plan file for a Deep ingest session. "
        "Call this before any wiki write in Mode 3 — the plan file is the "
        "persistent cursor that lets you resume across sessions. Commits "
        "the plan file directly to git (outside the write session). "
        "Returns the plan_path to use with wiki_inbox_get, wiki_inbox_write."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source_path": {
                "type": "string",
                "description": "Path to the source file (e.g. 'raw/2026-04-09-paper.pdf' or absolute path)",
            },
            "title": {
                "type": "string",
                "description": "Human-readable title for the research plan (usually the source title)",
            },
            "claims": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Initial claim list — one-line scope per claim. Presented to user for approval before the loop starts.",
            },
            "author": {
                "type": "string",
                "description": "Your agent identifier",
            },
        },
        "required": ["source_path", "title", "author"],
    },
    handler=handle_wiki_inbox_create,
)


async def handle_wiki_inbox_get(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({
        "type": "inbox-get",
        "plan_path": args["plan_path"],
    })
    return _ok(translate_daemon_response(response))


WIKI_INBOX_GET = ToolDefinition(
    name="wiki_inbox_get",
    description=(
        "Read the current content and frontmatter of an inbox plan file. "
        "Use this when resuming a Deep ingest session to reconstruct the "
        "task list from unchecked claims."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "plan_path": {
                "type": "string",
                "description": "Relative path to the plan file (e.g. 'inbox/2026-04-09-paper-plan.md')",
            },
        },
        "required": ["plan_path"],
    },
    handler=handle_wiki_inbox_get,
)


async def handle_wiki_inbox_write(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({
        "type": "inbox-write",
        "plan_path": args["plan_path"],
        "content": args["content"],
        "author": args["author"],
    })
    return _ok(translate_daemon_response(response))


WIKI_INBOX_WRITE = ToolDefinition(
    name="wiki_inbox_write",
    description=(
        "Write the full content of an inbox plan file and commit it to git. "
        "Use this at session checkpoints: read the current content with "
        "wiki_inbox_get, update checkboxes and append session notes, then "
        "call this to persist and commit. Always call before wiki_session_close."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "plan_path": {
                "type": "string",
                "description": "Relative path to the plan file (from wiki_inbox_create response)",
            },
            "content": {
                "type": "string",
                "description": "Full file content (frontmatter + body). Preserve the frontmatter from wiki_inbox_get.",
            },
            "author": {
                "type": "string",
                "description": "Your agent identifier",
            },
        },
        "required": ["plan_path", "content", "author"],
    },
    handler=handle_wiki_inbox_write,
)


async def handle_wiki_inbox_list(ctx: ToolContext, args: dict) -> list[TextContent]:
    response = await ctx.client.arequest({"type": "inbox-list"})
    return _ok(translate_daemon_response(response))


WIKI_INBOX_LIST = ToolDefinition(
    name="wiki_inbox_list",
    description=(
        "List all inbox plan files with their status and unchecked claim count. "
        "Use this when resuming work to find the right plan file, or to "
        "surface in-progress ingests for the researcher."
    ),
    input_schema={"type": "object", "properties": {}},
    handler=handle_wiki_inbox_list,
)
```

Add the four tools to `WIKI_TOOLS`:

```python
WIKI_TOOLS: list[ToolDefinition] = [
    WIKI_SEARCH,
    WIKI_READ,
    WIKI_MANIFEST,
    WIKI_STATUS,
    WIKI_QUERY,
    WIKI_INGEST,
    WIKI_LINT,
    WIKI_CREATE,
    WIKI_UPDATE,
    WIKI_APPEND,
    WIKI_ISSUES_LIST,
    WIKI_ISSUES_GET,
    WIKI_ISSUES_RESOLVE,
    WIKI_TALK_READ,
    WIKI_TALK_POST,
    WIKI_TALK_LIST,
    WIKI_SESSION_CLOSE,
    WIKI_INBOX_CREATE,   # ← new
    WIKI_INBOX_GET,      # ← new
    WIKI_INBOX_WRITE,    # ← new
    WIKI_INBOX_LIST,     # ← new
]
```

- [x] **Step 6: Verify tool count**

```bash
python -c "from llm_wiki.mcp.tools import WIKI_TOOLS; print(len(WIKI_TOOLS), [t.name for t in WIKI_TOOLS])"
```

Expected: `21` tools, with `wiki_inbox_create`, `wiki_inbox_get`, `wiki_inbox_write`, `wiki_inbox_list` in the list.

- [x] **Step 7: Run full test suite — no regressions**

```bash
pytest tests/ -q 2>&1 | tail -10
```

Expected: all PASS.

- [x] **Step 8: Commit**

```bash
git add src/llm_wiki/daemon/server.py src/llm_wiki/mcp/tools.py tests/test_mcp/test_inbox.py
git commit -m "feat: wiki_inbox_create/get/write/list — inbox plan file MCP tools"
```

---

### Task 3: `skills/llm-wiki/ingest.md` — skill rewrite

**Files:**
- Modify: `skills/llm-wiki/ingest.md`

The existing skill's structure (three modes, synthesis principle, page threshold) is sound. The rewrite focuses on two things: (1) replacing manual plan file construction with the new MCP tools in Mode 3, and (2) making the reading-status protocol explicit throughout. The skill must be self-contained — an agent that has never seen the old version should use it correctly.

- [x] **Step 1: Rewrite `skills/llm-wiki/ingest.md`**

Replace the file contents entirely:

```markdown
---
name: llm-wiki/ingest
description: Use when incorporating an external source (paper, PDF, document) into
  an llm-wiki vault. Three modes — queue (background extraction), brief (briefing
  with your context), deep (claim-by-claim research). Attended mode.
---

# LLM-Wiki Ingest — Attended Source Intake

The attended agent's unique value in ingest is what it knows about you: your context, your memory, your prior work, your entire wiki. These three modes let you decide how much of that to use.

## Before Any Mode — Copy the Source

Copy the source into `raw/` before touching any wiki tool:

- **PDFs:** store the original as `raw/YYYY-MM-DD-slug.pdf` (immutable). The daemon
  creates `raw/YYYY-MM-DD-slug.md` alongside it automatically on `wiki_ingest`
  with `reading_status: unread` and extracted text.
- **Markdown / text:** copy verbatim to `raw/YYYY-MM-DD-slug.md`. Body is immutable;
  frontmatter is metadata.
- **Flat** — no subdirectories inside `raw/`.

All `source_ref` values in wiki citations must point here. `wiki_lint` flags broken citations.

**PDF extraction quality varies.** Check extracted text before writing — mangled output
(captions bleeding into body, garbled tables, watermarks repeating) degrades everything
written from it. The vault config's `pdf_extractor` controls which tool is used:
`pdftotext` (default, poor layout), `local-ocr` (vision model via llama.cpp, handles
tables/figures), `marker`/`nougat` (high quality, GPU required). Flag bad extraction
to the user before proceeding.

## Reading Status Protocol

`reading_status` in `raw/` frontmatter tracks whether the researcher has engaged
with a source. The daemon sets it; you update it with `wiki_source_mark`. Never
edit `raw/` frontmatter manually.

| Moment | Call |
|---|---|
| Brief mode — start reading | `wiki_source_mark(source_path, "in_progress", author)` |
| Brief mode — done, no deep session planned | `wiki_source_mark(source_path, "read", author)` |
| Deep mode — session start | `wiki_source_mark(source_path, "in_progress", author)` |
| Deep mode — plan file complete | `wiki_source_mark(source_path, "read", author)` |
| Queue mode (autonomous ingest) | Do not call — daemon sets `unread` only |

`source_path` is the path to either the binary (`raw/foo.pdf`) or its companion
(`raw/foo.md`) — both accepted.

## Choose a Mode

> "I have [source]. How do you want to handle it?
>
> **Queue** — background extraction, I'll report what was created
> **Brief** — I read it with your full context and wiki loaded, tell you what matters, you decide what to do next
> **Deep** — claim-by-claim analysis together; builds a persistent plan we can resume across sessions"

If no response: default to **Brief**. One conversation turn, always produces something useful, even if the user queues everything afterward.

---

## Mode 1: Queue

Background extraction. No analysis.

1. Confirm source is in `raw/`
2. `wiki_ingest` — daemon handles concept extraction and page creation
3. Report: pages created, pages updated, errors
4. `wiki_session_close`

The daemon sets `reading_status: unread` on ingest. Only attended engagement promotes a source.

---

## Mode 2: Brief

Read with the user's full context loaded. The output is a briefing, not pages.

1. Confirm source is in `raw/`
2. Read the source — abstract and intro at minimum, full document if short
3. `wiki_manifest` + `wiki_search` for key concepts — know what's already covered
4. `wiki_source_mark(source_path, "in_progress", author)`
5. Produce the briefing:

```
**New to your work:** [what this adds not already in your wiki or prior work — be specific]
**Already covered:** [concepts with existing pages — link them]
**Contradictions:** [specific claims conflicting with existing pages — name both sides]
**Worth reading yourself:** [sections needing your judgment, not just extraction]
**Scope if queued:** ~N pages
```

6. Wait. User decides:
   - "Queue it" → run Mode 1; `wiki_source_mark(source_path, "read", author)` if the brief is sufficient engagement
   - "Go deeper on X" → continue into Mode 3 for those claims
   - "I'll read it myself" → leave at `in_progress`, close session
   - "Nothing for now" → leave at `in_progress`, close session

7. `wiki_session_close`

The briefing is the value. Page creation is optional and user-directed.

---

## Mode 3: Deep

Claim-by-claim iterative analysis. The compounding is a byproduct; the research is the point.

### Setup

1. Confirm source is in `raw/`
2. `wiki_source_mark(source_path, "in_progress", author)`
3. Read the source fully — form a claim list before creating the plan
4. Create the inbox plan file **before any wiki write:**

```
wiki_inbox_create(
  source_path="raw/YYYY-MM-DD-slug.pdf",
  title="[Source Title]",
  claims=["Claim 1 — one-line scope", "Claim 2", ...],
  author=your_identifier
)
```

   Save the returned `plan_path` — you will need it for checkpoints and resuming.

5. Present the claim list to the user. Get approval — merge, drop, reorder — before starting the loop.

### Per-Claim Loop

For each claim:

1. **Agent presents:** what the claim is, what's genuinely new vs already covered, any contradiction with existing wiki pages, what it would write and why
2. **Human reacts** — push back, add their reading, redirect
3. **Decide together:**
   - Write now → `wiki_create` / `wiki_update` / `wiki_append`; link aggressively
   - Defer → note reason, move on
   - Talk post only → `wiki_talk_post` on the relevant concept page
   - Skip → note in plan file
4. Tick the claim in the plan file (tracked locally — written at checkpoint, not after every claim)

### Session Checkpoint

When `session-cap-approaching` fires or at a natural stopping point:

1. Read the current plan file: `wiki_inbox_get(plan_path)`
2. Update the content: tick completed claims, add decisions, append session notes section
3. Commit: `wiki_inbox_write(plan_path, updated_content, author)`
4. `wiki_session_close`

The plan file is the full context. No prior session memory needed to resume.

### Resuming

1. `wiki_inbox_list` — find the active plan if you don't have the path
2. `wiki_inbox_get(plan_path)` — reconstruct task list from unchecked `- [x]` items
3. `wiki_source_mark(source_path, "in_progress", author)` — re-assert status
4. Continue the per-claim loop from the first unchecked item

### Completion

1. `wiki_source_mark(source_path, "read", author)`
2. Read plan file, mark `status: completed`, increment `sessions` count
3. `wiki_inbox_write(plan_path, updated_content, author)` — final commit
4. **Cascade:** scan same topic area for pages that should cross-reference new content, then adjacent clusters. Part of the work, not optional cleanup.
5. `wiki_session_close`

---

## Key Synthesis Principle

For any mode that writes pages:

Situate claims, don't extract them. For each concept, how does it connect to what is already there?
- Contradictions → `wiki_talk_post` on the relevant page
- Extensions → page body with citation
- Confirmations → note in relevant claim's context

**Page threshold:** create a page when a concept is central to this source OR appears in 2+ sources. Passing mentions → link to an existing page; do not create stubs.

**Wikilinks:** every salient noun, technical term, and named entity on first mention. Writing habit, not a checklist.

**Scope:** if 10+ pages estimated, flag before committing — large ingests benefit from Deep mode so synthesis doesn't get buried in bulk creation.
```

- [x] **Step 2: Verify the file structure is intact**

```bash
grep "^## " skills/llm-wiki/ingest.md
```

Expected:
```
## Before Any Mode — Copy the Source
## Reading Status Protocol
## Choose a Mode
## Mode 1: Queue
## Mode 2: Brief
## Mode 3: Deep
## Key Synthesis Principle
```

- [x] **Step 3: Verify all four inbox tools are referenced**

```bash
grep "wiki_inbox" skills/llm-wiki/ingest.md
```

Expected: `wiki_inbox_create`, `wiki_inbox_get`, `wiki_inbox_write`, `wiki_inbox_list` all present.

- [x] **Step 4: Commit**

```bash
git add skills/llm-wiki/ingest.md
git commit -m "docs: rewrite ingest skill — wiki_inbox tools, reading-status protocol"
```

---

### Task 4: Full test run

- [x] **Step 1: Run the complete test suite**

```bash
pytest tests/ -q 2>&1 | tail -15
```

Expected: all PASS, no regressions.

- [x] **Step 2: Verify inbox tool count in MCP surface**

```bash
python -c "
from llm_wiki.mcp.tools import WIKI_TOOLS
inbox = [t.name for t in WIKI_TOOLS if 'inbox' in t.name]
print('inbox tools:', inbox)
print('total tools:', len(WIKI_TOOLS))
"
```

Expected:
```
inbox tools: ['wiki_inbox_create', 'wiki_inbox_get', 'wiki_inbox_write', 'wiki_inbox_list']
total tools: 21
```

- [x] **Step 3: Smoke-check inbox create + list round-trip**

```bash
python -c "
import asyncio, tempfile, subprocess, datetime
from pathlib import Path

async def main():
    import sys
    with tempfile.TemporaryDirectory() as d:
        vr = Path(d)
        (vr / 'raw').mkdir()
        (vr / 'wiki').mkdir()
        (vr / 'raw' / 'paper.pdf').write_bytes(b'%PDF-1.4 fake')
        subprocess.run(['git', 'init'], cwd=vr, capture_output=True)
        subprocess.run(['git', 'config', 'user.email', 'test@test.com'], cwd=vr, capture_output=True)
        subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=vr, capture_output=True)
        subprocess.run(['git', 'add', '.'], cwd=vr, capture_output=True)
        subprocess.run(['git', 'commit', '-m', 'init'], cwd=vr, capture_output=True)

        import asyncio as aio
        from llm_wiki.config import WikiConfig
        from llm_wiki.daemon.server import DaemonServer
        s = DaemonServer.__new__(DaemonServer)
        s._vault_root = vr
        s._config = WikiConfig()
        s._commit_lock = aio.Lock()

        r = await s._handle_inbox_create({'source_path': 'raw/paper.pdf', 'title': 'Test', 'claims': ['A', 'B'], 'author': 'smoke'})
        print('create:', r)
        r2 = await s._handle_inbox_list({})
        print('list:', r2)

asyncio.run(main())
"
```

Expected: `create: {'status': 'ok', ...}` and `list: {'status': 'ok', 'plans': [{'source': 'raw/paper.pdf', ...}]}`

---

## Self-Review

**Spec coverage:**

- [x] inbox/ directory recognized in VaultConfig — `inbox_dir` field, Task 1 Step 5
- [x] Plan file format (frontmatter: source, started, status, sessions; body: Claims, Decisions, Session Notes) — `render_plan_file`, Task 1
- [x] `wiki_inbox_create` scaffolds and commits — Task 2
- [x] `wiki_inbox_get` reads content + frontmatter — Task 2
- [x] `wiki_inbox_write` replaces content + commits (session checkpoint path) — Task 2
- [x] `wiki_inbox_list` surfaces active plans — Task 2
- [x] All four tools added to WIKI_TOOLS — Task 2 Step 5
- [x] Skill rewrite: Mode 3 uses new tools — Task 3
- [x] Skill rewrite: reading-status protocol documented — Task 3
- [x] Skill rewrite: resuming via wiki_inbox_list + wiki_inbox_get — Task 3
- [x] `in-progress-no-plan` auditor compatibility — `source:` frontmatter field matches what `_canonical_source` produces in `find_source_gaps` (e.g. `raw/paper.pdf`)

**Placeholder scan:** No TBD, no TODO, no placeholder content. All code steps contain actual runnable code.

**Type consistency check:**
- `create_plan_file` returns `Path` — handler stores it and calls `.relative_to()` ✓
- `read_plan_frontmatter` returns `dict` — handler and list both use `.get()` ✓
- `count_unchecked_claims` takes `str` content — list handler reads text first ✓
- `_handle_inbox_get` returns `frontmatter` as dict — tests assert `response["frontmatter"]["source"]` ✓
- All handlers use `self._commit_lock` (not `_git_lock`) ✓
- MCP tool handlers match daemon request field names (e.g. `plan_path`, `content`, `source_path`) ✓
