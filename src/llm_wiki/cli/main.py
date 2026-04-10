from __future__ import annotations

import itertools
import os
import subprocess
import sys
import tempfile
import threading
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


def _default_vault_path() -> str:
    """Resolve vault path: LLM_WIKI_VAULT env → ~/wiki → '.'"""
    env = os.environ.get("LLM_WIKI_VAULT", "").strip()
    if env:
        return env
    home_wiki = Path.home() / "wiki"
    if home_wiki.is_dir():
        return str(home_wiki)
    return "."


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

    # Capture stderr so startup errors are visible immediately instead of after 30s.
    stderr_fd, stderr_path_str = tempfile.mkstemp(suffix=".log", prefix="llm-wiki-start-")
    stderr_path = Path(stderr_path_str)
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "llm_wiki.daemon", str(vault_path.resolve())],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=stderr_fd,
        )
        os.close(stderr_fd)
        stderr_fd = -1

        for _ in range(60):
            time.sleep(0.5)
            if client.is_running():
                return client
            if proc.poll() is not None:
                err_text = stderr_path.read_text().strip()
                raise click.ClickException(
                    f"Daemon failed to start.\n{err_text}" if err_text
                    else "Daemon failed to start (no error output captured)."
                )

        raise click.ClickException("Daemon failed to start within 30 seconds")
    finally:
        if stderr_fd >= 0:
            os.close(stderr_fd)
        stderr_path.unlink(missing_ok=True)


class _Spinner:
    """Braille spinner for TTY progress display.

    Uses a threading.RLock so concept lines can be printed atomically
    without interleaving with spinner writes.
    """

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self) -> None:
        self._label = ""
        self._running = False
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._line_width = 0

    def start(self, label: str = "") -> None:
        self._label = label
        self._running = True
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def update(self, label: str) -> None:
        with self._lock:
            self._label = label

    def print_line(self, line: str) -> None:
        """Clear spinner, print a line, let spinner resume."""
        with self._lock:
            sys.stdout.write("\r" + " " * (self._line_width + 2) + "\r")
            sys.stdout.flush()
            print(line)

    def stop(self) -> None:
        with self._lock:
            self._running = False
        if self._thread:
            self._thread.join(timeout=1)
        with self._lock:
            sys.stdout.write("\r" + " " * (self._line_width + 2) + "\r")
            sys.stdout.flush()

    def _spin(self) -> None:
        for frame in itertools.cycle(self.FRAMES):
            if not self._running:
                break
            with self._lock:
                line = f"{frame}  {self._label}"
                self._line_width = len(line)
                sys.stdout.write(f"\r{line}")
                sys.stdout.flush()
            time.sleep(0.08)


@click.group()
def cli() -> None:
    """llm-wiki — Agent-first knowledge base tool."""
    pass


@cli.command()
@click.argument(
    "vault_path",
    type=click.Path(path_type=Path),
    default=_default_vault_path,
)
def configure(vault_path: Path) -> None:
    """Interactive wizard — configure LLM backends and API keys."""
    from llm_wiki.cli.configure import run_wizard
    run_wizard(vault_path)


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
    default=_default_vault_path, help="Path to vault",
)
def stop(vault_path: Path) -> None:
    """Stop the daemon for a vault."""
    sock = socket_path_for(vault_path)
    client = DaemonClient(sock)
    if not client.is_running():
        click.echo("Daemon is not running.")
        return
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
    default=_default_vault_path, help="Path to vault",
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
    default=_default_vault_path, help="Path to vault",
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
    default=_default_vault_path, help="Path to vault",
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
    default=_default_vault_path, help="Path to vault",
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
    default=_default_vault_path, help="Path to vault",
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


@cli.command()
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=_default_vault_path, help="Path to vault",
)
def lint(vault_path: Path) -> None:
    """Run structural integrity checks on the vault and file issues."""
    client = _get_client(vault_path)
    resp = client.request({"type": "lint"})
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Lint failed"))

    total = resp["total_issues"]
    new_count = len(resp["new_issue_ids"])
    existing_count = len(resp["existing_issue_ids"])

    click.echo(f"Ran {resp['total_checks_run']} checks - {total} issue(s) total")
    click.echo(f"  {new_count} new, {existing_count} already in queue")
    click.echo()

    for check, count in resp["by_check"].items():
        marker = "OK" if count == 0 else "!!"
        click.echo(f"  {marker} {check}: {count}")

    if new_count > 0:
        click.echo()
        click.echo("New issue ids:")
        for issue_id in resp["new_issue_ids"]:
            click.echo(f"  - {issue_id}")


def _is_inside(path: Path, parent: Path) -> bool:
    """Return True if path is inside (or equal to) parent."""
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


@cli.command()
@click.argument("source_path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=_default_vault_path, help="Path to vault",
)
@click.option(
    "--dry-run", "dry_run", is_flag=True, default=False,
    help="Preview: run extraction and generation but skip all writes.",
)
def ingest(source_path: Path, vault_path: Path, dry_run: bool) -> None:
    """Ingest a source document — extracts concepts and creates wiki pages."""
    import shutil
    import uuid as _uuid
    from llm_wiki.config import WikiConfig

    source_path = source_path.resolve()
    vault_path = vault_path.resolve()

    # Auto-copy source to raw/ if it's outside the vault
    if not _is_inside(source_path, vault_path):
        config = WikiConfig.load(vault_path / "schema" / "config.yaml")
        if not config.ingest.auto_copy_to_raw:
            raise click.ClickException(
                f"Source must be inside vault raw/ directory. "
                f"Move it first or set auto_copy_to_raw: true in config."
            )
        raw_dir = vault_path / config.vault.raw_dir.rstrip("/")
        raw_dir.mkdir(parents=True, exist_ok=True)
        dest = raw_dir / source_path.name
        if not dest.exists():
            shutil.copy2(source_path, dest)
            click.echo(f"Copied {source_path.name} → raw/{source_path.name}")
        else:
            click.echo(f"Already in raw/: {source_path.name}")
        source_path = dest

    client = _get_client(vault_path)
    msg: dict = {
        "type": "ingest",
        "source_path": str(source_path),
        "author": "cli",
        "connection_id": _uuid.uuid4().hex,
        "dry_run": dry_run,
        "proposal_mode": True,
    }

    if dry_run:
        resp = client.request(msg)
        if resp["status"] != "ok":
            raise click.ClickException(resp.get("message", "Ingest failed"))
        source_name = Path(resp["source_path"]).name
        source_chars = f"{resp['source_chars']:,}"
        click.echo(f"DRY RUN — {source_name} ({source_chars} chars)")
        if resp.get("extraction_warning"):
            click.echo(f"  Warning: {resp['extraction_warning']}")
        for c in resp.get("concepts", []):
            action = "UPD" if c["action"] == "update" else "NEW"
            click.echo(f"  [{action}] {c['name']}  \"{c['title']}\"  ({c['passage_count']} passages)")
        click.echo(f"  {resp['concepts_found']} concepts total")
        return

    # Live streaming ingest
    msg["stream"] = True
    is_tty = sys.stdout.isatty()
    spinner: _Spinner | None = _Spinner() if is_tty else None
    error_seen: list[str] = []

    def on_frame(frame: dict) -> None:
        ftype = frame.get("type")
        stage = frame.get("stage", "")

        if ftype == "progress":
            if stage == "extracting":
                if spinner:
                    spinner.start("Extracting...")
                # no output line for extracting — spinner alone is enough on TTY
            elif stage == "concepts_found":
                count = frame["count"]
                line = f"[PROGRESS] concepts_found: {count}"
                if spinner:
                    spinner.update(f"Found {count} concept(s) — writing pages...")
                    spinner.print_line(line)
                else:
                    click.echo(line)
            elif stage == "concept_done":
                name = frame["name"]
                action = frame["action"]
                line = f"[DONE] {name} ({action})"
                if spinner:
                    spinner.print_line(line)
                else:
                    click.echo(line)

        elif ftype == "done":
            if spinner:
                spinner.stop()
            created = frame.get("pages_created", 0)
            updated = frame.get("pages_updated", 0)
            click.echo(f"[SUMMARY] {created} created, {updated} updated")
            if frame.get("warnings"):
                for w in frame["warnings"]:
                    click.echo(f"  Warning: {w['message']}")

        elif ftype == "error":
            if spinner:
                spinner.stop()
            msg_text = frame.get("message", "Unknown error")
            written = frame.get("concepts_written", 0)
            error_seen.append(f"{msg_text} ({written} concept(s) written before error)")

    client.stream_ingest_sync(msg, on_frame)

    if error_seen:
        raise click.ClickException(error_seen[0])


@cli.group()
def issues() -> None:
    """Query and manage the issue queue."""
    pass


@issues.command("list")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=_default_vault_path, help="Path to vault",
)
@click.option("--status", default=None, help="Filter by status (open|resolved|wontfix)")
@click.option("--type", "type_filter", default=None, help="Filter by issue type")
def issues_list(vault_path: Path, status: str | None, type_filter: str | None) -> None:
    """List issues in the queue."""
    client = _get_client(vault_path)
    req: dict = {"type": "issues-list"}
    if status:
        req["status_filter"] = status
    if type_filter:
        req["type_filter"] = type_filter
    resp = client.request(req)
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Issues list failed"))

    items = resp["issues"]
    if not items:
        click.echo("No issues found.")
        return

    click.echo(f"Found {len(items)} issue(s):\n")
    for item in items:
        click.echo(f"  {item['id']} — {item['title']}")
        click.echo(f"    type: {item['type']} | status: {item['status']} | page: {item['page']}")


@issues.command("show")
@click.argument("issue_id")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=_default_vault_path, help="Path to vault",
)
def issues_show(issue_id: str, vault_path: Path) -> None:
    """Show full details of a single issue."""
    client = _get_client(vault_path)
    resp = client.request({"type": "issues-get", "id": issue_id})
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Issue not found"))

    issue = resp["issue"]
    click.echo(f"id:          {issue['id']}")
    click.echo(f"type:        {issue['type']}")
    click.echo(f"status:      {issue['status']}")
    click.echo(f"page:        {issue['page']}")
    click.echo(f"detected_by: {issue['detected_by']}")
    click.echo(f"created:     {issue['created']}")
    click.echo()
    click.echo(issue['title'])
    click.echo()
    click.echo(issue['body'])


def _set_status(issue_id: str, vault_path: Path, status: str) -> None:
    client = _get_client(vault_path)
    resp = client.request({"type": "issues-update", "id": issue_id, "status": status})
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Update failed"))
    click.echo(f"{issue_id} -> {status}")


@issues.command("resolve")
@click.argument("issue_id")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=_default_vault_path, help="Path to vault",
)
def issues_resolve(issue_id: str, vault_path: Path) -> None:
    """Mark an issue as resolved."""
    _set_status(issue_id, vault_path, "resolved")


@issues.command("wontfix")
@click.argument("issue_id")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=_default_vault_path, help="Path to vault",
)
def issues_wontfix(issue_id: str, vault_path: Path) -> None:
    """Mark an issue as wontfix."""
    _set_status(issue_id, vault_path, "wontfix")


@cli.group()
def maintenance() -> None:
    """Inspect and manage maintenance workers."""
    pass


@maintenance.command("status")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=_default_vault_path, help="Path to vault",
)
def maintenance_status(vault_path: Path) -> None:
    """Show registered maintenance workers and their last-run times."""
    client = _get_client(vault_path)
    resp = client.request({"type": "scheduler-status"})
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Status query failed"))

    workers = resp["workers"]
    if not workers:
        click.echo("No maintenance workers registered.")
        return

    click.echo(f"{'name':<16} {'interval':<10} {'failures':<10} last_run")
    click.echo("-" * 70)
    for worker in workers:
        interval = f"{worker['interval_seconds']:.0f}s"
        failures = worker.get("consecutive_failures", 0)
        last = worker["last_run"] or "never"
        reachable = worker.get("backend_reachable")
        reachable_str = "" if reachable is None else (" [backend DOWN]" if not reachable else "")
        click.echo(
            f"{worker['name']:<16} {interval:<10} {failures:<10} {last}{reachable_str}"
        )


@cli.group()
def proposals() -> None:
    """List, approve, or reject ingest proposals."""
    pass


@proposals.command("list")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=_default_vault_path, help="Path to vault",
)
def proposals_list(vault_path: Path) -> None:
    """List pending ingest proposals."""
    client = _get_client(vault_path)
    resp = client.request({"type": "proposals-list"})
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Failed"))
    items = resp.get("proposals", [])
    if not items:
        click.echo("No pending proposals.")
        return
    click.echo(f"{len(items)} pending proposal(s):\n")
    for item in items:
        click.echo(f"  {item['path']}")
        click.echo(f"    target: {item['target_page']} | action: {item['action']} | source: {item['source']}")


@proposals.command("approve")
@click.argument("proposal_path")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=_default_vault_path, help="Path to vault",
)
def proposals_approve(proposal_path: str, vault_path: Path) -> None:
    """Approve and merge an ingest proposal."""
    client = _get_client(vault_path)
    resp = client.request({"type": "proposals-approve", "path": proposal_path})
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Approve failed"))
    click.echo(f"Approved and merged: {proposal_path}")


@proposals.command("reject")
@click.argument("proposal_path")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=_default_vault_path, help="Path to vault",
)
def proposals_reject(proposal_path: str, vault_path: Path) -> None:
    """Reject an ingest proposal."""
    client = _get_client(vault_path)
    resp = client.request({"type": "proposals-reject", "path": proposal_path})
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Reject failed"))
    click.echo(f"Rejected: {proposal_path}")


@cli.group()
def talk() -> None:
    """Read, post, and list talk-page entries."""
    pass


@talk.command("read")
@click.argument("page")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=_default_vault_path, help="Path to vault",
)
def talk_read(page: str, vault_path: Path) -> None:
    """Show all talk entries for a page."""
    client = _get_client(vault_path)
    resp = client.request({"type": "talk-read", "page": page})
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Talk read failed"))

    entries = resp["entries"]
    if not entries:
        click.echo(f"No entries on {page}.talk.")
        return

    click.echo(f"{len(entries)} entries on {page}.talk:\n")
    for entry in entries:
        click.echo(f"**{entry['timestamp']} — {entry['author']}**")
        click.echo(entry["body"])
        click.echo()


@talk.command("post")
@click.argument("page")
@click.option("--message", "-m", required=True, help="Message body")
@click.option("--author", default="@human", help="Author tag (defaults to @human)")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=_default_vault_path, help="Path to vault",
)
def talk_post(page: str, message: str, author: str, vault_path: Path) -> None:
    """Append a talk-page entry."""
    client = _get_client(vault_path)
    resp = client.request({
        "type": "talk-append",
        "page": page,
        "author": author,
        "body": message,
    })
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Talk post failed"))
    click.echo(f"Posted to {page}.talk as {author}.")


@talk.command("list")
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=_default_vault_path, help="Path to vault",
)
def talk_list(vault_path: Path) -> None:
    """List all pages that have a talk sidecar."""
    client = _get_client(vault_path)
    resp = client.request({"type": "talk-list"})
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Talk list failed"))

    pages = resp["pages"]
    if not pages:
        click.echo("No talk pages.")
        return

    click.echo(f"{len(pages)} talk page(s):")
    for page in pages:
        click.echo(f"  {page}")


@cli.command(name="mcp")
@click.argument(
    "vault_path",
    type=click.Path(exists=True, path_type=Path),
    required=False,
)
def mcp_command(vault_path: Path | None) -> None:
    """Run the MCP server over stdio for this vault.

    Vault resolution order:
      1. LLM_WIKI_VAULT environment variable
      2. The VAULT_PATH positional argument
      3. Error out

    Auto-starts the daemon if it isn't already running.
    """
    import asyncio
    import os

    env_vault = os.environ.get("LLM_WIKI_VAULT", "").strip()
    resolved: Path | None = None
    if env_vault:
        resolved = Path(env_vault)
    elif vault_path is not None:
        resolved = vault_path

    if resolved is None:
        raise click.ClickException(
            "No vault specified. Set LLM_WIKI_VAULT or pass a vault path: "
            "llm-wiki mcp /path/to/vault"
        )
    if not resolved.exists():
        raise click.ClickException(f"Vault path does not exist: {resolved}")

    client = _get_client(resolved, auto_start=True)

    from llm_wiki.mcp.server import MCPServer
    server = MCPServer(vault_path=resolved, client=client)
    asyncio.run(server.run_stdio())
