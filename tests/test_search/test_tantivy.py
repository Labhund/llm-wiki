from pathlib import Path
from llm_wiki.search.tantivy_backend import TantivyBackend
from llm_wiki.page import Page
from llm_wiki.manifest import build_entry
from conftest import SAMPLE_PAGE_WITH_MARKERS, SAMPLE_PAGE_NO_MARKERS


def test_index_and_search(tmp_path: Path):
    index_dir = tmp_path / "index"
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()

    # Create pages
    (vault_dir / "srna.md").write_text(SAMPLE_PAGE_WITH_MARKERS)
    (vault_dir / "clustering.md").write_text(SAMPLE_PAGE_NO_MARKERS)

    page1 = Page.parse(vault_dir / "srna.md")
    page2 = Page.parse(vault_dir / "clustering.md")
    entry1 = build_entry(page1, cluster="bio")
    entry2 = build_entry(page2, cluster="ml")

    backend = TantivyBackend(index_dir)
    backend.index_entries([entry1, entry2])

    results = backend.search("sRNA embeddings", limit=5)
    assert len(results) >= 1
    assert results[0].name == "srna"


def test_search_no_results(tmp_path: Path):
    index_dir = tmp_path / "index"
    backend = TantivyBackend(index_dir)
    backend.index_entries([])
    results = backend.search("nonexistent topic", limit=5)
    assert results == []


def test_search_returns_scores(tmp_path: Path):
    index_dir = tmp_path / "index"
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "srna.md").write_text(SAMPLE_PAGE_WITH_MARKERS)

    page = Page.parse(vault_dir / "srna.md")
    entry = build_entry(page, cluster="bio")

    backend = TantivyBackend(index_dir)
    backend.index_entries([entry])

    results = backend.search("PCA clustering", limit=5)
    assert len(results) >= 1
    assert results[0].score > 0.0


def test_search_with_snippets_returns_matches_with_line_numbers(sample_vault):
    """search_with_snippets attaches a `matches` list to each result."""
    from llm_wiki.vault import Vault

    vault = Vault.scan(sample_vault)
    backend = vault._backend  # access the underlying tantivy backend
    results = backend.search_with_snippets("PCA", limit=5, vault_root=sample_vault)
    assert results, "expected at least one result for 'PCA'"

    for r in results:
        assert hasattr(r, "matches")
        if r.matches:
            for m in r.matches:
                assert isinstance(m.line, int)
                assert isinstance(m.before, str)
                assert isinstance(m.match, str)
                assert isinstance(m.after, str)


def test_search_with_snippets_finds_correct_line(sample_vault):
    """A query token's match line corresponds to the file line that contains it."""
    from llm_wiki.vault import Vault

    vault = Vault.scan(sample_vault)
    results = vault._backend.search_with_snippets("k-means", limit=5, vault_root=sample_vault)
    srna_result = next((r for r in results if r.name == "srna-embeddings"), None)
    assert srna_result is not None

    page_text = (sample_vault / "bioinformatics" / "srna-embeddings.md").read_text()
    page_lines = page_text.splitlines()

    for m in srna_result.matches:
        # The line text on the matched line should contain the search term (case-insensitive)
        assert "k-means" in page_lines[m.line - 1].lower()


def test_search_with_snippets_attaches_nearest_heading(sample_vault):
    """The `before` field is the nearest preceding ## heading text."""
    from llm_wiki.vault import Vault

    vault = Vault.scan(sample_vault)
    results = vault._backend.search_with_snippets("k-means", limit=5, vault_root=sample_vault)
    srna_result = next((r for r in results if r.name == "srna-embeddings"), None)
    assert srna_result is not None

    for m in srna_result.matches:
        # In sample_vault, the k-means content lives in the Clustering section
        assert m.before in ("## Clustering", "## Overview", "## Method", "## Related Pages")


def test_search_with_snippets_empty_results_for_no_match(sample_vault):
    """A query that hits nothing returns an empty list, not a crash."""
    from llm_wiki.vault import Vault

    vault = Vault.scan(sample_vault)
    results = vault._backend.search_with_snippets(
        "absolutelynothingmatchesthistoken", limit=5, vault_root=sample_vault,
    )
    assert results == []
