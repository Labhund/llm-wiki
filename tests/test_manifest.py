from pathlib import Path
from llm_wiki.manifest import ManifestEntry, ClusterSummary, build_entry
from llm_wiki.page import Page
from conftest import SAMPLE_PAGE_WITH_MARKERS


def test_build_entry_from_page(tmp_path: Path):
    p = tmp_path / "srna-embeddings.md"
    p.write_text(SAMPLE_PAGE_WITH_MARKERS)
    page = Page.parse(p)
    entry = build_entry(page, cluster="bioinformatics")
    assert entry.name == "srna-embeddings"
    assert entry.title == "sRNA Embeddings Validation"
    assert entry.cluster == "bioinformatics"
    assert entry.tokens == page.total_tokens
    assert len(entry.sections) == 4
    assert entry.sections[0].name == "overview"
    assert entry.links_to == ["clustering-metrics", "inter-rep-variant-analysis"]
    assert entry.read_count == 0
    assert entry.usefulness == 0.0
    assert entry.authority == 0.0


def test_entry_summary_tokens():
    """Manifest entry itself should serialize to predictable size."""
    entry = ManifestEntry(
        name="test",
        title="Test Page",
        summary="A short summary.",
        tags=["a", "b"],
        cluster="test-cluster",
        tokens=500,
        sections=[],
        links_to=["other"],
        links_from=[],
        read_count=0,
        usefulness=0.0,
        authority=0.0,
    )
    text = entry.to_manifest_text()
    assert "test" in text
    assert "A short summary" in text
    assert "500" in text


def test_cluster_summary():
    entries = [
        ManifestEntry(
            name=f"page-{i}", title=f"Page {i}", summary=f"Summary {i}",
            tags=[], cluster="bio", tokens=100 * (i + 1), sections=[],
            links_to=[], links_from=[], read_count=0,
            usefulness=0.0, authority=0.0,
        )
        for i in range(5)
    ]
    cluster = ClusterSummary.from_entries("bio", entries)
    assert cluster.name == "bio"
    assert cluster.page_count == 5
    assert cluster.total_tokens == 100 + 200 + 300 + 400 + 500
