from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import click

from llm_wiki.daemon.client import DaemonClient
from llm_wiki.daemon.lifecycle import (
    is_daemon_running,
    pidfile_path_for,
    read_pidfile,
    socket_path_for,
)
from llm_wiki.vault import Vault


def _get_client(vault_path: Path, auto_start: bool = True) -> DaemonClient:
    """Get a daemon client, auto-starting the daemon if needed."""
    sock = socket_path_for(vault_path)
    client = DaemonClient(sock)

    if client.is_running():
        return client

    if not auto_start:
        raise click.ClickException(
            f"Daemon not running for {vault_path}. Run: llm-wiki serve {vault_path}"
        )

    click.echo("Starting daemon...", err=True)
    subprocess.Popen(
        [sys.executable, "-m", "llm_wiki.daemon", str(vault_path.resolve())],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    for _ in range(60):
        time.sleep(0.5)
        if client.is_running():
            return client

    raise click.ClickException("Daemon failed to start within 30 seconds")


@click.group()
def cli() -> None:
    """llm-wiki — Agent-first knowledge base tool."""
    pass


@cli.command()
@click.argument("vault_path", type=click.Path(exists=True, path_type=Path))
def init(vault_path: Path) -> None:
    """Scan and index a vault directory (no daemon needed)."""
    vault = Vault.scan(vault_path)
    click.echo(
        f"Indexed {vault.page_count} pages "
        f"in {vault.cluster_count} clusters."
    )


@cli.command()
@click.argument("vault_path", type=click.Path(exists=True, path_type=Path))
def serve(vault_path: Path) -> None:
    """Start the daemon in the foreground."""
    from llm_wiki.daemon.__main__ import main as daemon_main
    sys.argv = ["llm-wiki-daemon", str(vault_path.resolve())]
    daemon_main()


@cli.command()
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=".", help="Path to vault",
)
def stop(vault_path: Path) -> None:
    """Stop the daemon for a vault."""
    sock = socket_path_for(vault_path)
    client = DaemonClient(sock)
    if not client.is_running():
        click.echo("Daemon is not running.")
        return
    import os
    import signal
    pid = read_pidfile(pidfile_path_for(vault_path))
    if pid:
        os.kill(pid, signal.SIGTERM)
        click.echo(f"Sent stop signal to daemon (PID {pid})")
    else:
        click.echo("Could not find daemon PID")


@cli.command()
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=".", help="Path to vault",
)
def status(vault_path: Path) -> None:
    """Show vault status."""
    client = _get_client(vault_path)
    resp = client.request({"type": "status"})
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Unknown error"))
    click.echo(f"Vault: {resp['vault_root']}")
    click.echo(f"Pages: {resp['page_count']}")
    click.echo(f"Clusters: {resp['cluster_count']}")
    for cluster_text in resp["clusters"]:
        click.echo(f"  {cluster_text}")
    click.echo(f"Index: {resp['index_path']}")


@cli.command()
@click.argument("query")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=".", help="Path to vault",
)
@click.option("--limit", default=10, help="Max results")
def search(query: str, vault_path: Path, limit: int) -> None:
    """Search the wiki index."""
    client = _get_client(vault_path)
    resp = client.request({"type": "search", "query": query, "limit": limit})
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Unknown error"))

    results = resp["results"]
    if not results:
        click.echo("No results found.")
        return

    click.echo(f"Found {len(results)} result(s):\n")
    for r in results:
        click.echo(r["manifest"])
        click.echo(f"  score: {r['score']:.3f}")
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
    client = _get_client(vault_path)
    req = {"type": "read", "page_name": page_name, "viewport": viewport}
    if section:
        req["section"] = section
    if grep:
        req["grep"] = grep
    if budget:
        req["budget"] = budget

    resp = client.request(req)
    if resp["status"] != "ok":
        click.echo(resp.get("message", "Page not found"), err=True)
        raise SystemExit(1)

    click.echo(resp["content"])


@cli.command()
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=".", help="Path to vault",
)
@click.option("--budget", default=16000, help="Token budget for manifest output")
def manifest(vault_path: Path, budget: int) -> None:
    """Show the hierarchical manifest (budget-aware)."""
    client = _get_client(vault_path)
    resp = client.request({"type": "manifest", "budget": budget})
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Unknown error"))
    click.echo(resp["content"])


@cli.command()
@click.argument("question")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=".", help="Path to vault",
)
@click.option("--budget", default=None, type=int, help="Token budget for traversal")
def query(question: str, vault_path: Path, budget: int | None) -> None:
    """Query the wiki — multi-turn LLM traversal with citations."""
    client = _get_client(vault_path)
    req: dict = {"type": "query", "question": question}
    if budget is not None:
        req["budget"] = budget

    resp = client.request(req)
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Query failed"))

    click.echo(resp["answer"])
    click.echo()
    if resp.get("citations"):
        click.echo(f"Citations: {', '.join(resp['citations'])}")
    if resp.get("needs_more_budget"):
        click.echo(
            "\nNote: answer may be incomplete — increase --budget for more detail.",
            err=True,
        )
