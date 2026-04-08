from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki.audit.compliance import ComplianceResult, ComplianceReviewer
from llm_wiki.config import WikiConfig
from llm_wiki.issues.queue import IssueQueue


def _setup(tmp_path: Path) -> tuple[Path, IssueQueue, ComplianceReviewer, Path]:
    """Create a wiki dir + queue + reviewer rooted at tmp_path."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    queue = IssueQueue(wiki_dir)
    config = WikiConfig()
    reviewer = ComplianceReviewer(tmp_path, queue, config)
    page_path = wiki_dir / "test.md"
    return wiki_dir, queue, reviewer, page_path


def test_minor_edit_auto_approves(tmp_path: Path):
    """A small edit with no new wikilinks/headings is auto-approved."""
    _, _, reviewer, page = _setup(tmp_path)
    old = "---\ntitle: Test\n---\n\n%% section: overview %%\n## Overview\n\nOriginal text. [[raw/source.pdf]]\n"
    new = old.replace("Original text", "Origina1 text")  # typo fix style
    page.write_text(new)

    result = reviewer.review_change(page, old, new)

    assert isinstance(result, ComplianceResult)
    assert result.page == "test"
    assert result.auto_approved is True
    assert "minor-edit" in result.reasons
    assert result.issues_filed == []
    assert result.auto_fixed == []


def test_minor_edit_threshold_is_50_chars(tmp_path: Path):
    """Edits >= 50 chars are NOT minor."""
    _, _, reviewer, page = _setup(tmp_path)
    old = "---\ntitle: Test\n---\n\n%% section: overview %%\n## Overview\n\nText. [[raw/source.pdf]]\n"
    addition = "x" * 60  # 60 chars > 50 threshold
    new = old.replace("Text.", f"Text. {addition}")
    page.write_text(new)

    result = reviewer.review_change(page, old, new)
    assert "minor-edit" not in result.reasons


def test_minor_edit_disqualified_by_new_wikilink(tmp_path: Path):
    """A small edit that introduces a new wikilink is NOT minor."""
    _, _, reviewer, page = _setup(tmp_path)
    old = "---\ntitle: Test\n---\n\n%% section: overview %%\n## Overview\n\nText. [[raw/source.pdf]]\n"
    new = old.replace("Text.", "Text. See [[other-page]].")
    page.write_text(new)

    result = reviewer.review_change(page, old, new)
    assert "minor-edit" not in result.reasons


def test_minor_edit_disqualified_by_new_heading(tmp_path: Path):
    """A small edit that introduces a new ## heading is NOT minor."""
    _, _, reviewer, page = _setup(tmp_path)
    old = "---\ntitle: Test\n---\n\n%% section: overview %%\n## Overview\n\nText.\n"
    new = old + "\n## New\n"
    page.write_text(new)

    result = reviewer.review_change(page, old, new)
    assert "minor-edit" not in result.reasons


def test_first_time_seen_page_skips_minor_edit(tmp_path: Path):
    """When old_content is None (new file), minor-edit shortcut does not apply."""
    _, _, reviewer, page = _setup(tmp_path)
    new = "---\ntitle: Test\n---\n\n%% section: overview %%\n## Overview\n\nNew page.\n"
    page.write_text(new)

    result = reviewer.review_change(page, None, new)
    assert "minor-edit" not in result.reasons
