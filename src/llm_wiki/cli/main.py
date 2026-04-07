from pathlib import Path

import click

from llm_wiki.vault import Vault


@click.group()
def cli() -> None:
    """llm-wiki — Agent-first knowledge base tool."""
    pass


@cli.command()
@click.argument("vault_path", type=click.Path(exists=True, path_type=Path))
def init(vault_path: Path) -> None:
    """Scan and index a vault directory."""
    vault = Vault.scan(vault_path)
    click.echo(
        f"Indexed {vault.page_count} pages "
        f"in {vault.cluster_count} clusters."
    )
    click.echo(f"Index stored in {vault_path / '.llm-wiki' / 'index'}")


@cli.command()
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=".", help="Path to vault (default: current directory)",
)
def status(vault_path: Path) -> None:
    """Show vault status."""
    vault = Vault.scan(vault_path)
    info = vault.status()
    click.echo(f"Vault: {info['vault_root']}")
    click.echo(f"Pages: {info['page_count']}")
    click.echo(f"Clusters: {info['cluster_count']}")
    for cluster_text in info["clusters"]:
        click.echo(f"  {cluster_text}")
    click.echo(f"Index: {info['index_path']}")
    click.echo(f"Index entries: {info['index_entries']}")


@cli.command()
@click.argument("query")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=".", help="Path to vault",
)
@click.option("--limit", default=10, help="Max results")
@click.option("--budget", default=16000, help="Token budget for manifest output")
def search(query: str, vault_path: Path, limit: int, budget: int) -> None:
    """Search the wiki index."""
    vault = Vault.scan(vault_path)
    results = vault.search(query, limit=limit)

    if not results:
        click.echo("No results found.")
        return

    click.echo(f"Found {len(results)} result(s):\n")
    for r in results:
        entry = r.entry
        click.echo(entry.to_manifest_text())
        click.echo(f"  score: {r.score:.3f}")
        click.echo()


@cli.command()
@click.argument("page_name")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=".", help="Path to vault",
)
@click.option("--viewport", default="top", type=click.Choice(["top", "full"]))
@click.option("--section", default=None, help="Read specific section by name")
@click.option("--grep", default=None, help="Search within page")
@click.option("--budget", default=None, type=int, help="Token budget")
def read(
    page_name: str,
    vault_path: Path,
    viewport: str,
    section: str | None,
    grep: str | None,
    budget: int | None,
) -> None:
    """Read a wiki page with viewport support."""
    vault = Vault.scan(vault_path)

    content = vault.read_viewport(
        page_name,
        viewport=viewport,
        section=section,
        grep=grep,
        budget=budget,
    )

    if content is None:
        click.echo(f"Page not found: {page_name}", err=True)
        raise SystemExit(1)

    click.echo(content)


@cli.command()
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=".", help="Path to vault",
)
@click.option("--budget", default=16000, help="Token budget for manifest output")
def manifest(vault_path: Path, budget: int) -> None:
    """Show the hierarchical manifest (budget-aware)."""
    vault = Vault.scan(vault_path)
    click.echo(vault.manifest_text(budget=budget))
