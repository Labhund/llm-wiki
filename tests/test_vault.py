from pathlib import Path
from llm_wiki.vault import Vault


def test_vault_manifest_entries_returns_dict_keyed_by_name(sample_vault):
    """manifest_entries() exposes the parsed manifest entries by page name."""
    from llm_wiki.vault import Vault
    from llm_wiki.manifest import ManifestEntry

    vault = Vault.scan(sample_vault)
    entries = vault.manifest_entries()

    assert isinstance(entries, dict)
    assert "srna-embeddings" in entries
    assert isinstance(entries["srna-embeddings"], ManifestEntry)
    # links_from is computed by the store; srna-embeddings is referenced by
    # both inter-rep-variant-analysis and clustering-metrics in the fixture.
    assert "inter-rep-variant-analysis" in entries["srna-embeddings"].links_from
    assert "clustering-metrics" in entries["srna-embeddings"].links_from


def test_vault_manifest_entries_is_a_copy(sample_vault):
    """Mutating the returned dict does not affect the underlying store."""
    from llm_wiki.vault import Vault

    vault = Vault.scan(sample_vault)
    entries = vault.manifest_entries()
    entries.clear()

    # Re-fetch to confirm internal state intact
    entries2 = vault.manifest_entries()
    assert "srna-embeddings" in entries2


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


def test_vault_scan_applies_manifest_overrides(sample_vault, tmp_path):
    """Tags, authority, and other librarian-managed fields survive Vault.scan()."""
    from llm_wiki.librarian.overrides import ManifestOverrides, PageOverride
    from llm_wiki.vault import Vault, _state_dir_for

    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)

    overrides_path = state_dir / "manifest_overrides.json"
    store = ManifestOverrides.load(overrides_path)
    store.set("srna-embeddings", PageOverride(
        tags=["bioinformatics", "embeddings", "validation"],
        summary_override="Validates sRNA embeddings via PCA and k-means",
        authority=0.74,
        last_corroborated="2026-04-01T12:00:00+00:00",
        read_count=12,
        usefulness=0.82,
        last_refreshed_read_count=10,
    ))
    store.save()

    vault = Vault.scan(sample_vault)
    entry = vault.manifest_entries()["srna-embeddings"]

    assert entry.tags == ["bioinformatics", "embeddings", "validation"]
    assert entry.summary == "Validates sRNA embeddings via PCA and k-means"
    assert abs(entry.authority - 0.74) < 1e-6
    assert entry.last_corroborated == "2026-04-01T12:00:00+00:00"
    assert entry.read_count == 12
    assert abs(entry.usefulness - 0.82) < 1e-6


def test_vault_scan_prunes_overrides_for_deleted_pages(sample_vault, tmp_path):
    """An override for a page that no longer exists in the vault is removed on scan."""
    from llm_wiki.librarian.overrides import ManifestOverrides, PageOverride
    from llm_wiki.vault import Vault, _state_dir_for

    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)
    overrides_path = state_dir / "manifest_overrides.json"

    store = ManifestOverrides.load(overrides_path)
    store.set("srna-embeddings", PageOverride(authority=0.5))
    store.set("deleted-page", PageOverride(authority=0.9))
    store.save()

    Vault.scan(sample_vault)

    reloaded = ManifestOverrides.load(overrides_path)
    assert reloaded.get("srna-embeddings") is not None
    assert reloaded.get("deleted-page") is None


def test_vault_scan_does_not_rewrite_overrides_when_nothing_pruned(sample_vault):
    """Vault.scan() must not touch the overrides file if prune had nothing to do.

    Frequent rescans (file watcher) would otherwise cause a write-per-scan
    storm even when the override state is already consistent with the vault.
    """
    from llm_wiki.librarian.overrides import ManifestOverrides, PageOverride
    from llm_wiki.vault import Vault, _state_dir_for

    state_dir = _state_dir_for(sample_vault)
    state_dir.mkdir(parents=True, exist_ok=True)
    overrides_path = state_dir / "manifest_overrides.json"

    # Seed with an override for an existing page (nothing to prune)
    store = ManifestOverrides.load(overrides_path)
    store.set("srna-embeddings", PageOverride(authority=0.5))
    store.save()

    # First scan establishes a baseline mtime
    Vault.scan(sample_vault)
    mtime_before = overrides_path.stat().st_mtime_ns

    # Second scan: nothing to prune → file must not be rewritten
    Vault.scan(sample_vault)
    mtime_after = overrides_path.stat().st_mtime_ns

    assert mtime_after == mtime_before, (
        "Vault.scan rewrote manifest_overrides.json even though nothing was pruned"
    )


def test_vault_scan_excludes_talk_pages(sample_vault):
    """*.talk.md files are not indexed as wiki pages."""
    from llm_wiki.vault import Vault

    # Create a talk page sidecar in the fixture vault
    talk = sample_vault / "bioinformatics" / "srna-embeddings.talk.md"
    talk.write_text(
        "---\npage: srna-embeddings\n---\n\n"
        "**2026-04-08T10:00:00+00:00 — @adversary**\nVerified.\n"
    )

    vault = Vault.scan(sample_vault)
    entries = vault.manifest_entries()

    # The wiki page is still indexed
    assert "srna-embeddings" in entries
    # The talk page is NOT
    assert "srna-embeddings.talk" not in entries
    assert not any(name.endswith(".talk") for name in entries)
