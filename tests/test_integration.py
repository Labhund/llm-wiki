"""End-to-end test: init vault → search → read viewports → manifest."""
from pathlib import Path
from click.testing import CliRunner
from llm_wiki.cli.main import cli
from llm_wiki.vault import Vault


def test_full_workflow(sample_vault: Path):
    """Simulate a user's first experience with llm-wiki."""
    runner = CliRunner()

    # Step 1: Init
    result = runner.invoke(cli, ["init", str(sample_vault)])
    assert result.exit_code == 0
    assert "Indexed" in result.output

    # Step 2: Status
    result = runner.invoke(cli, ["status", "--vault", str(sample_vault)])
    assert result.exit_code == 0
    assert "4" in result.output  # 4 pages

    # Step 3: Search
    result = runner.invoke(
        cli, ["search", "sRNA embeddings", "--vault", str(sample_vault)]
    )
    assert result.exit_code == 0
    assert "srna" in result.output.lower()

    # Step 4: Read top viewport
    result = runner.invoke(
        cli, ["read", "srna-embeddings", "--vault", str(sample_vault)]
    )
    assert result.exit_code == 0
    assert "overview" in result.output.lower()

    # Step 5: Read specific section
    result = runner.invoke(
        cli, ["read", "srna-embeddings", "--section", "method",
              "--vault", str(sample_vault)]
    )
    assert result.exit_code == 0
    assert "PCA" in result.output

    # Step 6: Grep within page
    result = runner.invoke(
        cli, ["read", "srna-embeddings", "--grep", "k-means",
              "--vault", str(sample_vault)]
    )
    assert result.exit_code == 0
    assert "k-means" in result.output

    # Step 7: Manifest
    result = runner.invoke(
        cli, ["manifest", "--vault", str(sample_vault)]
    )
    assert result.exit_code == 0


def test_vault_api_directly(sample_vault: Path):
    """Test the Vault API for programmatic use (library mode)."""
    vault = Vault.scan(sample_vault)

    # Search — use a term that appears in the indexed summary/title fields
    results = vault.search("clustering metrics")
    assert len(results) >= 1

    # Read viewport — section content is accessible even if not in search index
    content = vault.read_viewport("clustering-metrics", viewport="top")
    assert content is not None
    assert "silhouette" in content.lower()

    # Manifest with tight budget
    manifest_small = vault.manifest_text(budget=100)
    manifest_large = vault.manifest_text(budget=10000)
    assert len(manifest_large) >= len(manifest_small)

    # Page not found
    assert vault.read_viewport("nonexistent") is None

    # Status
    status = vault.status()
    assert status["page_count"] == 4


def test_existing_wiki_directory():
    """Test against the actual vault in the repo (wiki/ lives under the vault root)."""
    vault_root = Path("/home/labhund/repos/llm-wiki")
    if not (vault_root / "wiki").exists():
        return  # Skip if not in the expected location

    vault = Vault.scan(vault_root)
    assert vault.page_count >= 3

    results = vault.search("sRNA")
    assert len(results) >= 1

    content = vault.read_viewport("srna-embeddings", viewport="full")
    assert content is not None
    assert "PCA" in content
