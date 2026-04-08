from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from llm_wiki.issues.queue import Issue, IssueQueue


def _make_issue(
    type: str = "broken-link",
    page: str | None = "srna-tquant",
    key: str = "k-means-deep",
    title: str = "Wikilink target does not exist",
    body: str = "The page references [[k-means-deep]] but no such page exists.",
    detected_by: str = "auditor",
    metadata: dict | None = None,
) -> Issue:
    return Issue(
        id=Issue.make_id(type, page, key),
        type=type,
        status="open",
        title=title,
        page=page,
        body=body,
        created=Issue.now_iso(),
        detected_by=detected_by,
        metadata=metadata or {},
    )


def test_make_id_is_deterministic():
    """Same inputs always produce the same id."""
    id1 = Issue.make_id("broken-link", "srna-tquant", "k-means-deep")
    id2 = Issue.make_id("broken-link", "srna-tquant", "k-means-deep")
    assert id1 == id2


def test_make_id_format():
    """Id follows '<type>-<page-or-vault>-<6hex>' format."""
    issue_id = Issue.make_id("broken-link", "srna-tquant", "k-means-deep")
    assert issue_id.startswith("broken-link-srna-tquant-")
    suffix = issue_id.rsplit("-", 1)[-1]
    assert len(suffix) == 6
    assert all(c in "0123456789abcdef" for c in suffix)


def test_make_id_uses_vault_when_page_is_none():
    """Vault-wide issues (page=None) use the literal 'vault' segment."""
    issue_id = Issue.make_id("orphan-cluster", None, "stale")
    assert issue_id.startswith("orphan-cluster-vault-")


def test_make_id_distinguishes_different_inputs():
    """Different type/page/key produce different ids."""
    a = Issue.make_id("broken-link", "page-a", "target-x")
    b = Issue.make_id("broken-link", "page-a", "target-y")
    c = Issue.make_id("broken-link", "page-b", "target-x")
    d = Issue.make_id("orphan", "page-a", "target-x")
    assert len({a, b, c, d}) == 4


def test_queue_add_creates_file(tmp_path: Path):
    """add() writes the issue to <wiki_dir>/.issues/<id>.md."""
    wiki_dir = tmp_path / "wiki"
    queue = IssueQueue(wiki_dir)
    issue = _make_issue()

    path, was_new = queue.add(issue)

    assert was_new is True
    assert path == wiki_dir / ".issues" / f"{issue.id}.md"
    assert path.exists()


def test_queue_add_writes_frontmatter_and_body(tmp_path: Path):
    """The on-disk file has parseable YAML frontmatter and the body."""
    wiki_dir = tmp_path / "wiki"
    queue = IssueQueue(wiki_dir)
    issue = _make_issue(metadata={"target": "k-means-deep"})

    path, _ = queue.add(issue)
    text = path.read_text(encoding="utf-8")

    assert text.startswith("---\n")
    end = text.index("\n---", 4)
    fm = yaml.safe_load(text[4:end])
    assert fm["id"] == issue.id
    assert fm["type"] == "broken-link"
    assert fm["status"] == "open"
    assert fm["page"] == "srna-tquant"
    assert fm["detected_by"] == "auditor"
    assert fm["metadata"] == {"target": "k-means-deep"}

    body = text[end + 4:].strip()
    assert body == issue.body.strip()


def test_queue_add_is_idempotent(tmp_path: Path):
    """Adding the same issue twice does not overwrite — second call returns was_new=False."""
    wiki_dir = tmp_path / "wiki"
    queue = IssueQueue(wiki_dir)
    issue = _make_issue()

    path1, was_new_1 = queue.add(issue)
    original_text = path1.read_text(encoding="utf-8")

    # Second add with the same id — even if body differs, on-disk file is preserved
    issue_again = _make_issue(body="DIFFERENT BODY THAT SHOULD NOT BE WRITTEN")
    assert issue_again.id == issue.id  # ids match
    path2, was_new_2 = queue.add(issue_again)

    assert was_new_2 is False
    assert path2 == path1
    assert path2.read_text(encoding="utf-8") == original_text


def test_queue_exists(tmp_path: Path):
    wiki_dir = tmp_path / "wiki"
    queue = IssueQueue(wiki_dir)
    issue = _make_issue()

    assert queue.exists(issue.id) is False
    queue.add(issue)
    assert queue.exists(issue.id) is True


def test_queue_creates_issues_dir_on_demand(tmp_path: Path):
    """The .issues subdirectory does not need to exist before add()."""
    wiki_dir = tmp_path / "wiki"
    # wiki_dir itself doesn't exist
    queue = IssueQueue(wiki_dir)
    queue.add(_make_issue())

    assert (wiki_dir / ".issues").is_dir()


def test_queue_get_round_trip(tmp_path: Path):
    """get() returns an Issue with all fields preserved."""
    wiki_dir = tmp_path / "wiki"
    queue = IssueQueue(wiki_dir)
    issue = _make_issue(metadata={"target": "k-means-deep", "section": "method"})
    queue.add(issue)

    loaded = queue.get(issue.id)

    assert loaded is not None
    assert loaded.id == issue.id
    assert loaded.type == issue.type
    assert loaded.status == issue.status
    assert loaded.title == issue.title
    assert loaded.page == issue.page
    assert loaded.body == issue.body.strip()
    assert loaded.created == issue.created
    assert loaded.detected_by == issue.detected_by
    assert loaded.metadata == issue.metadata


def test_queue_get_missing_returns_none(tmp_path: Path):
    queue = IssueQueue(tmp_path / "wiki")
    assert queue.get("does-not-exist") is None


def test_queue_list_empty(tmp_path: Path):
    """list() on a queue with no .issues dir returns []."""
    queue = IssueQueue(tmp_path / "wiki")
    assert queue.list() == []


def test_queue_list_returns_all_issues(tmp_path: Path):
    queue = IssueQueue(tmp_path / "wiki")
    a = _make_issue(type="broken-link", page="page-a", key="x")
    b = _make_issue(type="orphan", page="page-b", key="")
    c = _make_issue(type="broken-link", page="page-c", key="y")
    queue.add(a)
    queue.add(b)
    queue.add(c)

    ids = {issue.id for issue in queue.list()}
    assert ids == {a.id, b.id, c.id}


def test_queue_list_filters_by_status(tmp_path: Path):
    queue = IssueQueue(tmp_path / "wiki")
    a = _make_issue(type="orphan", page="page-a", key="")
    b = _make_issue(type="orphan", page="page-b", key="")
    queue.add(a)
    queue.add(b)

    # Manually set b to resolved by rewriting via the helper we'll add in Task 5
    # For now, fake it by rewriting the file's frontmatter
    path_b = queue.issues_dir / f"{b.id}.md"
    path_b.write_text(
        path_b.read_text(encoding="utf-8").replace("status: open", "status: resolved"),
        encoding="utf-8",
    )

    open_issues = queue.list(status="open")
    resolved = queue.list(status="resolved")
    assert {i.id for i in open_issues} == {a.id}
    assert {i.id for i in resolved} == {b.id}


def test_queue_list_filters_by_type(tmp_path: Path):
    queue = IssueQueue(tmp_path / "wiki")
    queue.add(_make_issue(type="broken-link", page="a", key="1"))
    queue.add(_make_issue(type="orphan", page="b", key=""))
    queue.add(_make_issue(type="broken-link", page="c", key="2"))

    broken = queue.list(type="broken-link")
    assert len(broken) == 2
    assert all(i.type == "broken-link" for i in broken)


def test_update_status_changes_status_only(tmp_path: Path):
    """update_status preserves all other fields."""
    queue = IssueQueue(tmp_path / "wiki")
    issue = _make_issue(metadata={"target": "k-means-deep"})
    queue.add(issue)

    ok = queue.update_status(issue.id, "resolved")
    assert ok is True

    loaded = queue.get(issue.id)
    assert loaded is not None
    assert loaded.status == "resolved"
    assert loaded.title == issue.title
    assert loaded.body == issue.body.strip()
    assert loaded.metadata == {"target": "k-means-deep"}
    assert loaded.created == issue.created


def test_update_status_missing_returns_false(tmp_path: Path):
    queue = IssueQueue(tmp_path / "wiki")
    assert queue.update_status("does-not-exist", "resolved") is False


def test_update_status_validates_value(tmp_path: Path):
    queue = IssueQueue(tmp_path / "wiki")
    issue = _make_issue()
    queue.add(issue)

    with pytest.raises(ValueError):
        queue.update_status(issue.id, "invalid-status")


def test_queue_rejects_invalid_id(tmp_path):
    """Path traversal attempts via issue_id are rejected."""
    queue = IssueQueue(tmp_path / "wiki")
    with pytest.raises(ValueError):
        queue.exists("../../etc/passwd")
    with pytest.raises(ValueError):
        queue.get("../../etc/passwd")
    with pytest.raises(ValueError):
        queue.update_status("../../etc/passwd", "resolved")


def test_issue_default_severity_is_minor():
    """Issues without an explicit severity default to 'minor'."""
    issue = _make_issue()
    assert issue.severity == "minor"


def test_queue_round_trips_severity(tmp_path):
    """add() then get() preserves a non-default severity."""
    wiki_dir = tmp_path / "wiki"
    queue = IssueQueue(wiki_dir)
    issue = _make_issue()
    issue.severity = "critical"

    queue.add(issue)
    loaded = queue.get(issue.id)
    assert loaded is not None
    assert loaded.severity == "critical"


def test_queue_writes_severity_to_frontmatter(tmp_path):
    """The on-disk YAML carries the severity field."""
    wiki_dir = tmp_path / "wiki"
    queue = IssueQueue(wiki_dir)
    issue = _make_issue()
    issue.severity = "moderate"

    path, _ = queue.add(issue)
    text = path.read_text(encoding="utf-8")
    end = text.index("\n---", 4)
    fm = yaml.safe_load(text[4:end])
    assert fm["severity"] == "moderate"


def test_queue_legacy_file_without_severity_defaults_to_minor(tmp_path):
    """A 5a-era issue file with no severity field reads as 'minor'."""
    wiki_dir = tmp_path / "wiki"
    issues_dir = wiki_dir / ".issues"
    issues_dir.mkdir(parents=True)
    legacy = issues_dir / "broken-link-foo-abc123.md"
    legacy.write_text(
        "---\n"
        "id: broken-link-foo-abc123\n"
        "type: broken-link\n"
        "status: open\n"
        "title: Wikilink target does not exist\n"
        "page: foo\n"
        "created: 2026-04-01T10:00:00+00:00\n"
        "detected_by: auditor\n"
        "metadata: {}\n"
        "---\n\n"
        "Body text.\n",
        encoding="utf-8",
    )

    queue = IssueQueue(wiki_dir)
    loaded = queue.get("broken-link-foo-abc123")
    assert loaded is not None
    assert loaded.severity == "minor"


def test_queue_legacy_file_with_null_severity_defaults_to_minor(tmp_path):
    """A legacy issue file with `severity: null` reads as 'minor', not None."""
    wiki_dir = tmp_path / "wiki"
    issues_dir = wiki_dir / ".issues"
    issues_dir.mkdir(parents=True)
    legacy = issues_dir / "broken-link-bar-def456.md"
    legacy.write_text(
        "---\n"
        "id: broken-link-bar-def456\n"
        "type: broken-link\n"
        "status: open\n"
        "severity:\n"
        "title: Wikilink target does not exist\n"
        "page: bar\n"
        "created: 2026-04-01T10:00:00+00:00\n"
        "detected_by: auditor\n"
        "metadata: {}\n"
        "---\n\n"
        "Body text.\n",
        encoding="utf-8",
    )

    queue = IssueQueue(wiki_dir)
    loaded = queue.get("broken-link-bar-def456")
    assert loaded is not None
    assert loaded.severity == "minor"
