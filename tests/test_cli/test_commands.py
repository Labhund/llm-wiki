from pathlib import Path
from click.testing import CliRunner
from llm_wiki.cli.main import cli


def test_init_command(sample_vault: Path):
    runner = CliRunner()
    result = runner.invoke(cli, ["init", str(sample_vault)])
    assert result.exit_code == 0
    assert "Indexed" in result.output
    assert (sample_vault / ".llm-wiki" / "index").exists()


def test_init_nonexistent():
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "/nonexistent/path"])
    assert result.exit_code != 0


def test_status_command(sample_vault: Path):
    runner = CliRunner()
    runner.invoke(cli, ["init", str(sample_vault)])
    result = runner.invoke(cli, ["status", "--vault", str(sample_vault)])
    assert result.exit_code == 0
    assert "page" in result.output.lower()
    assert "cluster" in result.output.lower()


def test_search_command(sample_vault: Path):
    runner = CliRunner()
    runner.invoke(cli, ["init", str(sample_vault)])
    result = runner.invoke(
        cli, ["search", "sRNA embeddings", "--vault", str(sample_vault)]
    )
    assert result.exit_code == 0
    assert "srna" in result.output.lower()


def test_search_with_limit(sample_vault: Path):
    runner = CliRunner()
    runner.invoke(cli, ["init", str(sample_vault)])
    result = runner.invoke(
        cli, ["search", "clustering", "--vault", str(sample_vault), "--limit", "1"]
    )
    assert result.exit_code == 0


def test_search_no_results(sample_vault: Path):
    runner = CliRunner()
    runner.invoke(cli, ["init", str(sample_vault)])
    result = runner.invoke(
        cli, ["search", "quantum physics", "--vault", str(sample_vault)]
    )
    assert result.exit_code == 0
    assert "no results" in result.output.lower()


def test_read_top(sample_vault: Path):
    runner = CliRunner()
    runner.invoke(cli, ["init", str(sample_vault)])
    result = runner.invoke(
        cli, ["read", "srna-embeddings", "--vault", str(sample_vault)]
    )
    assert result.exit_code == 0
    assert "overview" in result.output.lower()
    assert "Remaining sections" in result.output or "method" in result.output.lower()


def test_read_section(sample_vault: Path):
    runner = CliRunner()
    runner.invoke(cli, ["init", str(sample_vault)])
    result = runner.invoke(
        cli, ["read", "srna-embeddings", "--section", "method",
              "--vault", str(sample_vault)]
    )
    assert result.exit_code == 0
    assert "PCA" in result.output


def test_read_grep(sample_vault: Path):
    runner = CliRunner()
    runner.invoke(cli, ["init", str(sample_vault)])
    result = runner.invoke(
        cli, ["read", "srna-embeddings", "--grep", "k-means",
              "--vault", str(sample_vault)]
    )
    assert result.exit_code == 0
    assert "k-means" in result.output


def test_read_full(sample_vault: Path):
    runner = CliRunner()
    runner.invoke(cli, ["init", str(sample_vault)])
    result = runner.invoke(
        cli, ["read", "srna-embeddings", "--viewport", "full",
              "--vault", str(sample_vault)]
    )
    assert result.exit_code == 0
    assert "PCA" in result.output
    assert "k-means" in result.output


def test_read_missing_page(sample_vault: Path):
    runner = CliRunner()
    runner.invoke(cli, ["init", str(sample_vault)])
    result = runner.invoke(
        cli, ["read", "nonexistent", "--vault", str(sample_vault)]
    )
    assert result.exit_code != 0 or "not found" in result.output.lower()


def test_manifest_command(sample_vault: Path):
    runner = CliRunner()
    runner.invoke(cli, ["init", str(sample_vault)])
    result = runner.invoke(
        cli, ["manifest", "--vault", str(sample_vault)]
    )
    assert result.exit_code == 0
    assert "bioinformatics" in result.output.lower() or "srna" in result.output.lower()


def test_manifest_with_budget(sample_vault: Path):
    runner = CliRunner()
    runner.invoke(cli, ["init", str(sample_vault)])
    small = runner.invoke(
        cli, ["manifest", "--vault", str(sample_vault), "--budget", "50"]
    )
    large = runner.invoke(
        cli, ["manifest", "--vault", str(sample_vault), "--budget", "5000"]
    )
    assert small.exit_code == 0
    assert large.exit_code == 0
    assert len(large.output) >= len(small.output)
