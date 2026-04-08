from __future__ import annotations

from pathlib import Path

from llm_wiki.talk.discovery import ensure_talk_marker


def test_ensure_talk_marker_inserts_when_missing(tmp_path: Path):
    page = tmp_path / "srna-embeddings.md"
    page.write_text("---\ntitle: sRNA\n---\n\nContent.\n")

    inserted = ensure_talk_marker(page)
    assert inserted is True

    text = page.read_text(encoding="utf-8")
    assert "%% talk: [[srna-embeddings.talk]] %%" in text
    # Marker is at the end
    assert text.rstrip().endswith("%% talk: [[srna-embeddings.talk]] %%")


def test_ensure_talk_marker_idempotent(tmp_path: Path):
    page = tmp_path / "p.md"
    page.write_text("---\ntitle: P\n---\n\nContent.\n")

    assert ensure_talk_marker(page) is True
    assert ensure_talk_marker(page) is False  # already present
    text = page.read_text(encoding="utf-8")
    # Marker only appears once
    assert text.count("%% talk: [[p.talk]] %%") == 1


def test_ensure_talk_marker_preserves_existing_content(tmp_path: Path):
    page = tmp_path / "p.md"
    original = "---\ntitle: P\n---\n\n## Overview\n\nImportant content [[raw/x.pdf]].\n"
    page.write_text(original)

    ensure_talk_marker(page)
    text = page.read_text(encoding="utf-8")
    assert "## Overview" in text
    assert "Important content [[raw/x.pdf]]" in text
    assert "title: P" in text
