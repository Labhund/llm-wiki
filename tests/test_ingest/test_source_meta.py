from __future__ import annotations

import datetime
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# read_frontmatter
# ---------------------------------------------------------------------------

def test_read_frontmatter_returns_dict(tmp_path: Path):
    f = tmp_path / "source.md"
    f.write_text("---\nreading_status: unread\ningested: 2026-04-10\n---\n")
    from llm_wiki.ingest.source_meta import read_frontmatter
    result = read_frontmatter(f)
    assert result["reading_status"] == "unread"


def test_read_frontmatter_stops_at_closing_dashes(tmp_path: Path):
    """Body content must never be parsed — even if it looks like YAML."""
    large_body = "key: value\n" * 5000
    f = tmp_path / "large.md"
    f.write_text(f"---\nreading_status: unread\n---\n{large_body}")
    from llm_wiki.ingest.source_meta import read_frontmatter
    result = read_frontmatter(f)
    assert list(result.keys()) == ["reading_status"]


def test_read_frontmatter_no_frontmatter_returns_empty(tmp_path: Path):
    f = tmp_path / "plain.md"
    f.write_text("Just plain text, no frontmatter.\n")
    from llm_wiki.ingest.source_meta import read_frontmatter
    assert read_frontmatter(f) == {}


def test_read_frontmatter_missing_file_returns_empty(tmp_path: Path):
    from llm_wiki.ingest.source_meta import read_frontmatter
    assert read_frontmatter(tmp_path / "nonexistent.md") == {}


# ---------------------------------------------------------------------------
# write_frontmatter
# ---------------------------------------------------------------------------

def test_write_frontmatter_merges_single_field(tmp_path: Path):
    f = tmp_path / "source.md"
    f.write_text("---\nreading_status: unread\ningested: 2026-04-10\n---\n")
    from llm_wiki.ingest.source_meta import write_frontmatter, read_frontmatter
    write_frontmatter(f, {"reading_status": "in_progress"})
    result = read_frontmatter(f)
    assert result["reading_status"] == "in_progress"
    assert "ingested" in result  # untouched


def test_write_frontmatter_preserves_body(tmp_path: Path):
    body = "\nExtracted text body.\nSecond line.\n"
    f = tmp_path / "source.md"
    f.write_text(f"---\nreading_status: unread\n---{body}")
    from llm_wiki.ingest.source_meta import write_frontmatter
    write_frontmatter(f, {"reading_status": "read"})
    content = f.read_text()
    assert "Extracted text body." in content
    assert "Second line." in content


def test_write_frontmatter_adds_frontmatter_to_plain_file(tmp_path: Path):
    f = tmp_path / "plain.md"
    f.write_text("Plain content here.\n")
    from llm_wiki.ingest.source_meta import write_frontmatter, read_frontmatter
    write_frontmatter(f, {"reading_status": "unread"})
    assert read_frontmatter(f)["reading_status"] == "unread"
    assert "Plain content here." in f.read_text()


# ---------------------------------------------------------------------------
# init_companion
# ---------------------------------------------------------------------------

def test_init_companion_creates_companion_for_pdf(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    pdf = raw_dir / "2026-04-10-paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    from llm_wiki.ingest.source_meta import init_companion, read_frontmatter
    companion = init_companion(pdf, tmp_path)
    assert companion is not None
    assert companion == raw_dir / "2026-04-10-paper.md"
    assert companion.exists()
    fm = read_frontmatter(companion)
    assert fm["reading_status"] == "unread"
    assert fm["source_type"] == "paper"
    assert "ingested" in fm


def test_init_companion_returns_none_if_already_exists(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    pdf = raw_dir / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    (raw_dir / "paper.md").write_text("---\nreading_status: in_progress\n---\n")
    from llm_wiki.ingest.source_meta import init_companion
    assert init_companion(pdf, tmp_path) is None


def test_init_companion_returns_none_outside_raw(tmp_path: Path):
    other_dir = tmp_path / "elsewhere"
    other_dir.mkdir()
    pdf = other_dir / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    from llm_wiki.ingest.source_meta import init_companion
    assert init_companion(pdf, tmp_path) is None


def test_init_companion_returns_none_for_markdown_source(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    md = raw_dir / "article.md"
    md.write_text("# Article\n\nContent.\n")
    from llm_wiki.ingest.source_meta import init_companion
    assert init_companion(md, tmp_path) is None


def test_init_companion_idempotent_no_overwrite(tmp_path: Path):
    """Calling init_companion twice must not change the companion written first."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    pdf = raw_dir / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    from llm_wiki.ingest.source_meta import init_companion, write_frontmatter
    companion = init_companion(pdf, tmp_path)
    assert companion is not None
    write_frontmatter(companion, {"reading_status": "in_progress"})
    second = init_companion(pdf, tmp_path)
    assert second is None
    from llm_wiki.ingest.source_meta import read_frontmatter
    assert read_frontmatter(companion)["reading_status"] == "in_progress"


# ---------------------------------------------------------------------------
# write_companion_body
# ---------------------------------------------------------------------------

def test_write_companion_body_appends_after_frontmatter(tmp_path: Path):
    f = tmp_path / "companion.md"
    f.write_text("---\nreading_status: unread\n---\n")
    from llm_wiki.ingest.source_meta import write_companion_body, read_frontmatter
    write_companion_body(f, "Extracted paper text.")
    content = f.read_text()
    assert "Extracted paper text." in content
    # Frontmatter still readable
    assert read_frontmatter(f)["reading_status"] == "unread"
