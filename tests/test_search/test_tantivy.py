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
