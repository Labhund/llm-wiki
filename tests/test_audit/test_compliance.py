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


def test_missing_citation_files_issue(tmp_path: Path):
    """A new sentence without a citation produces a compliance issue."""
    _, queue, reviewer, page = _setup(tmp_path)
    old = "---\ntitle: Test\n---\n\n%% section: overview %%\n## Overview\n\nWe used PCA [[raw/paper.pdf]].\n"
    new = old + "\nWe also computed silhouette scores using k=10.\n"
    page.write_text(new)

    result = reviewer.review_change(page, old, new)

    assert "missing-citation" in result.reasons
    assert len(result.issues_filed) >= 1
    issue = queue.get(result.issues_filed[0])
    assert issue is not None
    assert issue.type == "compliance"
    assert issue.detected_by == "compliance"


def test_new_sentences_with_citations_pass(tmp_path: Path):
    """A new sentence ending in [[...]] does NOT file a missing-citation issue."""
    _, _, reviewer, page = _setup(tmp_path)
    old = "---\ntitle: Test\n---\n\n%% section: overview %%\n## Overview\n\nText [[raw/a.pdf]].\n"
    new = old + "\nMore text [[raw/b.pdf]].\n"
    page.write_text(new)

    result = reviewer.review_change(page, old, new)
    assert "missing-citation" not in result.reasons


def test_missing_citation_first_time_seen_page(tmp_path: Path):
    """A new file with uncited sentences is also flagged."""
    _, _, reviewer, page = _setup(tmp_path)
    new = "---\ntitle: Test\n---\n\n%% section: overview %%\n## Overview\n\nThis is an uncited claim.\n"
    page.write_text(new)

    result = reviewer.review_change(page, None, new)
    assert "missing-citation" in result.reasons


def test_structural_drift_auto_inserts_marker(tmp_path: Path):
    """A new ## heading without a preceding marker is auto-fixed in place."""
    _, _, reviewer, page = _setup(tmp_path)
    old = (
        "---\ntitle: Test\n---\n\n"
        "%% section: overview %%\n## Overview\n\nText [[raw/a.pdf]].\n"
    )
    new = old + "\n## New Section\n\nMore text [[raw/b.pdf]].\n"
    page.write_text(new)

    result = reviewer.review_change(page, old, new)

    assert "structural-drift" in result.reasons
    assert "inserted-marker:new-section" in result.auto_fixed

    updated = page.read_text(encoding="utf-8")
    # Marker may have been patched with a token count by patch_token_estimates,
    # so match on prefix rather than exact string.
    assert "%% section: new-section" in updated
    # Original heading still present
    assert "## New Section" in updated
    # Marker appears before the heading
    marker_pos = updated.index("%% section: new-section")
    heading_pos = updated.index("## New Section")
    assert marker_pos < heading_pos


def test_structural_drift_skipped_when_marker_present(tmp_path: Path):
    """A new heading WITH its marker is not flagged."""
    _, _, reviewer, page = _setup(tmp_path)
    old = (
        "---\ntitle: Test\n---\n\n"
        "%% section: overview %%\n## Overview\n\nText [[raw/a.pdf]].\n"
    )
    new = (
        old
        + "\n%% section: method %%\n## Method\n\nDetails [[raw/a.pdf]].\n"
    )
    page.write_text(new)

    result = reviewer.review_change(page, old, new)
    assert "structural-drift" not in result.reasons
    assert result.auto_fixed == []


def test_structural_drift_handles_h3(tmp_path: Path):
    _, _, reviewer, page = _setup(tmp_path)
    old = "---\ntitle: Test\n---\n\n%% section: overview %%\n## Overview\n\nText [[raw/a.pdf]].\n"
    new = old + "\n### Sub Heading\n\nDetail [[raw/a.pdf]].\n"
    page.write_text(new)

    result = reviewer.review_change(page, old, new)
    assert "structural-drift" in result.reasons
    assert "inserted-marker:sub-heading" in result.auto_fixed
    assert "%% section: sub-heading" in page.read_text(encoding="utf-8")


def test_structural_drift_first_time_seen_page(tmp_path: Path):
    """A brand-new file with headings but no markers is auto-fixed."""
    _, _, reviewer, page = _setup(tmp_path)
    new = (
        "---\ntitle: Test\n---\n\n"
        "## Overview\n\nText [[raw/a.pdf]].\n"
        "## Method\n\nMore [[raw/a.pdf]].\n"
    )
    page.write_text(new)

    result = reviewer.review_change(page, None, new)
    assert "structural-drift" in result.reasons
    assert "inserted-marker:overview" in result.auto_fixed
    assert "inserted-marker:method" in result.auto_fixed

    updated = page.read_text(encoding="utf-8")
    assert "%% section: overview" in updated
    assert "%% section: method" in updated


def test_new_idea_files_issue_for_large_addition(tmp_path: Path):
    """A new paragraph >= 200 chars is flagged as new-idea."""
    _, queue, reviewer, page = _setup(tmp_path)
    old = (
        "---\ntitle: Test\n---\n\n"
        "%% section: overview %%\n## Overview\n\nOriginal text [[raw/a.pdf]].\n"
    )
    big = (
        "This is a substantial new paragraph that introduces a fresh idea about "
        "the topic. It has enough content to clear the 200-character threshold "
        "and trip the new-idea heuristic so the librarian can take a look later "
        "and decide what to do with it [[raw/a.pdf]]."
    )
    new = old + "\n" + big + "\n"
    page.write_text(new)

    result = reviewer.review_change(page, old, new)

    assert "new-idea" in result.reasons
    new_idea_issues = [
        i for i in result.issues_filed
        if (issue := queue.get(i)) is not None and issue.type == "new-idea"
    ]
    assert len(new_idea_issues) >= 1


def test_small_addition_does_not_trigger_new_idea(tmp_path: Path):
    """A short addition (< 200 chars) does not trigger new-idea."""
    _, _, reviewer, page = _setup(tmp_path)
    old = "---\ntitle: Test\n---\n\n%% section: overview %%\n## Overview\n\nText [[raw/a.pdf]].\n"
    new = old + "\nA brief addition with citation [[raw/a.pdf]].\n"
    page.write_text(new)

    result = reviewer.review_change(page, old, new)
    assert "new-idea" not in result.reasons


def test_structural_drift_ignores_code_fence_headings(tmp_path: Path):
    """A ``## Heading`` that appears inside a fenced code block is NOT a real
    heading. The reviewer must not inject a %% section marker mid-block, and
    the code-fence bytes must survive untouched.
    """
    _, _, reviewer, page = _setup(tmp_path)
    code_fence_body = (
        "```python\n"
        "## This is a code comment that looks like a heading\n"
        "def foo():\n"
        "    pass\n"
        "```"
    )
    new = (
        "---\ntitle: Topic\n---\n\n"
        "%% section: intro %%\n"
        "## Intro\n"
        "\n"
        "Some text [[raw/a.pdf]].\n"
        "\n"
        f"{code_fence_body}\n"
        "\n"
        "More text after the fence [[raw/a.pdf]].\n"
    )
    page.write_text(new, encoding="utf-8")

    result = reviewer.review_change(page, old_content=None, new_content=new)

    # No marker should have been inserted for the code-comment "heading".
    assert "inserted-marker:this-is-a-code-comment-that-looks-like-a-heading" not in result.auto_fixed
    # And more generally: no auto_fixed entry should match the code-comment slug.
    assert not any("code-comment" in fix for fix in result.auto_fixed)

    updated = page.read_text(encoding="utf-8")
    # The injected marker line must not appear anywhere.
    assert "%% section: this-is-a-code-comment" not in updated
    # The original code-fence bytes must be present verbatim.
    assert code_fence_body in updated


def test_structural_drift_ignores_tilde_fence_headings(tmp_path: Path):
    """Same rule as triple-backtick fences, but for ~~~-style fences."""
    _, _, reviewer, page = _setup(tmp_path)
    code_fence_body = (
        "~~~markdown\n"
        "## Fake Heading Inside Tilde Fence\n"
        "not a real section\n"
        "~~~"
    )
    new = (
        "---\ntitle: Topic\n---\n\n"
        "%% section: intro %%\n"
        "## Intro\n"
        "\n"
        "Some text [[raw/a.pdf]].\n"
        "\n"
        f"{code_fence_body}\n"
        "\n"
        "More text after the fence [[raw/a.pdf]].\n"
    )
    page.write_text(new, encoding="utf-8")

    result = reviewer.review_change(page, old_content=None, new_content=new)

    assert not any("fake-heading-inside-tilde-fence" in fix for fix in result.auto_fixed)

    updated = page.read_text(encoding="utf-8")
    assert "%% section: fake-heading-inside-tilde-fence" not in updated
    # The original code-fence bytes must be present verbatim.
    assert code_fence_body in updated


def test_new_idea_skipped_for_first_time_seen_page(tmp_path: Path):
    """A brand-new file is not flagged as new-idea (the whole file is 'new' by definition)."""
    _, _, reviewer, page = _setup(tmp_path)
    big = "x" * 300
    new = (
        "---\ntitle: Test\n---\n\n"
        f"%% section: overview %%\n## Overview\n\n{big} [[raw/a.pdf]].\n"
    )
    page.write_text(new)

    result = reviewer.review_change(page, None, new)
    assert "new-idea" not in result.reasons


def test_compliance_new_idea_issue_is_moderate(tmp_path: Path):
    """A new-idea issue filed by the compliance reviewer has severity='moderate'."""
    _, queue, reviewer, page = _setup(tmp_path)
    old = "---\ntitle: P\n---\n\n## Body\n\nShort intro.\n"
    new_paragraph = "x" * 250  # > 200 chars triggers new-idea
    new = (
        "---\ntitle: P\n---\n\n## Body\n\nShort intro.\n\n" + new_paragraph + "\n"
    )
    page.write_text(old)

    result = reviewer.review_change(page, old, new)

    new_idea_issues = [
        i for i in result.issues_filed
        if (issue := queue.get(i)) is not None and issue.type == "new-idea"
    ]
    assert new_idea_issues, "expected a new-idea issue"
    for issue_id in new_idea_issues:
        issue = queue.get(issue_id)
        assert issue is not None
        assert issue.severity == "moderate"


def test_compliance_missing_citation_issue_is_moderate(tmp_path: Path):
    """A missing-citation issue filed by the compliance reviewer has severity='moderate'."""
    _, queue, reviewer, page = _setup(tmp_path)
    old = "---\ntitle: P\n---\n\n## Body\n\nFirst sentence [[raw/a.pdf]].\n"
    new = (
        "---\ntitle: P\n---\n\n## Body\n\nFirst sentence [[raw/a.pdf]].\n\n"
        "An uncited sentence with no citation at all.\n"
    )
    page.write_text(old)

    result = reviewer.review_change(page, old, new)

    compliance_issues = [
        i for i in result.issues_filed
        if (issue := queue.get(i)) is not None
        and issue.type == "compliance"
        and issue.metadata.get("subtype") == "missing-citation"
    ]
    assert compliance_issues, "expected a missing-citation issue"
    for issue_id in compliance_issues:
        issue = queue.get(issue_id)
        assert issue is not None
        assert issue.severity == "moderate"


def test_has_citation_recognises_numbered_raw_citations():
    from llm_wiki.audit.compliance import ComplianceReviewer
    assert ComplianceReviewer._has_citation("Claim [[raw/paper.pdf|1]].")
    assert ComplianceReviewer._has_citation("Claim [[raw/paper.pdf|12]].")
    assert ComplianceReviewer._has_citation("Claim [[raw/paper.pdf]].")


def test_has_citation_rejects_uncited_sentences():
    from llm_wiki.audit.compliance import ComplianceReviewer
    assert not ComplianceReviewer._has_citation("Boltz-2 is a model.")
    assert not ComplianceReviewer._has_citation("See [^1] for details.")
