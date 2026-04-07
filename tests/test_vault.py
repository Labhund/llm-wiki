from pathlib import Path
from llm_wiki.vault import Vault


def test_scan_vault(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    assert vault.page_count == 4  # 3 in subdirs + 1 no-structure.md
    assert vault.cluster_count >= 2  # bioinformatics, machine-learning


def test_search(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    results = vault.search("sRNA embeddings", limit=3)
    assert len(results) >= 1
    assert results[0].name in ("srna-embeddings", "inter-rep-variant-analysis")


def test_read_page(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    page = vault.read_page("srna-embeddings")
    assert page is not None
    assert page.title == "sRNA Embeddings Validation"


def test_read_page_missing(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    assert vault.read_page("nonexistent") is None


def test_read_viewport_top(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    content = vault.read_viewport("srna-embeddings", viewport="top", budget=500)
    assert content is not None
    assert "overview" in content.lower() or "sRNA" in content
    # Should include table of contents of remaining sections
    assert "method" in content.lower()


def test_read_viewport_section(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    content = vault.read_viewport("srna-embeddings", section="method")
    assert content is not None
    assert "PCA" in content


def test_read_viewport_grep(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    content = vault.read_viewport("srna-embeddings", grep="k-means")
    assert content is not None
    assert "k-means" in content


def test_read_viewport_full(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    content = vault.read_viewport("srna-embeddings", viewport="full")
    assert content is not None
    assert "PCA" in content
    assert "k-means" in content


def test_manifest_text(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    text = vault.manifest_text(budget=5000)
    assert "bioinformatics" in text.lower() or "srna" in text.lower()


def test_status(sample_vault: Path):
    vault = Vault.scan(sample_vault)
    status = vault.status()
    assert status["page_count"] == 4
    assert status["cluster_count"] >= 2
    assert "index_path" in status
