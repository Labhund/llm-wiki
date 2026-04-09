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
    assert "- [ ] Alpha" in content
    assert "- [ ] Beta" in content


def test_render_plan_file_empty_claims():
    from llm_wiki.ingest.plan import render_plan_file
    content = render_plan_file("raw/p.pdf", "T", [], "2026-04-10")
    assert "## Claims / Ideas" in content
    assert "- [ ]" not in content


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
    content = "- [ ] A\n- [x] B\n- [ ] C\n"
    assert count_unchecked_claims(content) == 2


def test_count_unchecked_claims_none():
    from llm_wiki.ingest.plan import count_unchecked_claims
    assert count_unchecked_claims("- [x] A\n- [x] B\n") == 0
