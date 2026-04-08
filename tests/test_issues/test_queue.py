from __future__ import annotations

from llm_wiki.issues.queue import Issue


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
