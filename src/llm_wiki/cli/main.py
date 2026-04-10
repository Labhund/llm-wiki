from __future__ import annotations

import itertools
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import click

from llm_wiki.daemon.client import DaemonClient
from llm_wiki.daemon.lifecycle import (
    cleanup_stale,
    is_daemon_running,
    is_process_alive,
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


def _relative_time(timestamp_str: str) -> tuple[str, str]:
    """Convert ISO timestamp to (relative, next_scheduled) strings.
    
    Returns:
        Tuple of (relative_time_str, next_run_str) where:
        - relative_time_str: "Xs/minutes/hours/days ago" or "never"
        - next_run_str: "in Xs/minutes/hours/days" or "— (never)"
    """
    if not timestamp_str or timestamp_str == "never":
        return "never", "— (never)"
    
    try:
        # Parse ISO timestamp
        ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - ts
        total_seconds = delta.total_seconds()
        
        # Convert to relative time
        if total_seconds < 60:
            ago = f"{int(total_seconds)}s ago"
        elif total_seconds < 3600:
            minutes = int(total_seconds / 60)
            ago = f"{minutes}m ago"
        elif total_seconds < 86400:
            hours = int(total_seconds / 3600)
            ago = f"{hours}h ago"
        else:
            days = int(total_seconds / 86400)
            ago = f"{days}d ago"
        
        return ago, None  # next_run calculated by caller
    except (ValueError, TypeError):
        return str(timestamp_str), "—"


def _worker_display_action(worker_name: str, jobs: list[dict]) -> str:
    """Extract display string for a running worker from active jobs.

    Finds the first job whose label starts with the worker name, strips the
    source prefix, joins remaining parts with spaces, truncates at 30 chars.
    Returns empty string if no matching job.
    """
    for job in jobs:
        label = job.get("label", "")
        parts = label.split(":", 2)
        if parts and parts[0] == worker_name:
            action_detail = " ".join(parts[1:]) if len(parts) > 1 else ""
            if len(action_detail) > 30:
                action_detail = action_detail[:29] + "…"
            return action_detail
    return ""


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

    has_config = (vault_path / "schema" / "config.yaml").exists()
    has_vault_dir = (vault_path / "raw").is_dir() or (vault_path / "wiki").is_dir()
    if not has_config and not has_vault_dir:
        raise click.ClickException(
            f"'{vault_path}' is not an initialised vault. "
            "Run 'llm-wiki init <vault_path>' to initialise it, "
            "or pass --vault to point at an existing one."
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
    import signal as _signal
    from llm_wiki.cli.configure import run_wizard

    run_wizard(vault_path)

    # Restart daemon if it was running so the new config is picked up immediately.
    pid_path = pidfile_path_for(vault_path)
    sock_path = socket_path_for(vault_path)
    pid = read_pidfile(pid_path)
    if pid is not None and is_process_alive(pid):
        os.kill(pid, _signal.SIGTERM)
        for _ in range(30):
            time.sleep(0.1)
            if not is_process_alive(pid):
                break
        else:
            os.kill(pid, _signal.SIGKILL)
        cleanup_stale(sock_path, pid_path)
        _get_client(vault_path)
        click.echo("Daemon restarted with new config.")


@cli.command()
@click.argument("vault_path", type=click.Path(exists=True, path_type=Path))
def init(vault_path: Path) -> None:
    """Scan and index a vault directory (no daemon needed)."""
    has_config = (vault_path / "schema" / "config.yaml").exists()
    has_vault_dir = (vault_path / "raw").is_dir() or (vault_path / "wiki").is_dir()
    if not has_config and not has_vault_dir:
        (vault_path / "wiki").mkdir(exist_ok=True)
        (vault_path / "raw").mkdir(exist_ok=True)
        (vault_path / "inbox").mkdir(exist_ok=True)
        click.echo(f"Initialised new vault at {vault_path}.")
    # Create markers before Vault.scan — scan validates their presence before creating wiki/ internally
    vault = Vault.scan(vault_path)
    click.echo(
        f"Indexed {vault.page_count} pages "
        f"in {vault.cluster_count} clusters."
    )


@cli.command()
@click.argument("vault_path", type=click.Path(exists=True, path_type=Path), required=False, default=None)
@click.option(
    "--vault", "vault_opt", type=click.Path(exists=True, path_type=Path),
    default=_default_vault_path, help="Path to vault",
)
def serve(vault_path: Path | None, vault_opt: Path) -> None:
    """Start the daemon in the foreground."""
    effective = vault_path or vault_opt
    from llm_wiki.daemon.__main__ import main as daemon_main
    sys.argv = ["llm-wiki-daemon", str(effective.resolve())]
    daemon_main()


@cli.command()
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=_default_vault_path, help="Path to vault",
)
def stop(vault_path: Path) -> None:
    """Stop the daemon for a vault."""
    import signal as _signal
    pid_path = pidfile_path_for(vault_path)
    sock_path = socket_path_for(vault_path)
    pid = read_pidfile(pid_path)
    if pid is None or not is_process_alive(pid):
        cleanup_stale(sock_path, pid_path)
        click.echo("Daemon is not running.")
        return
    os.kill(pid, _signal.SIGTERM)
    # Wait up to 3s for graceful shutdown; escalate to SIGKILL if stuck.
    for _ in range(30):
        time.sleep(0.1)
        if not is_process_alive(pid):
            break
    else:
        os.kill(pid, _signal.SIGKILL)
        click.echo(f"Daemon (PID {pid}) did not stop gracefully; sent SIGKILL")
    cleanup_stale(sock_path, pid_path)
    click.echo(f"Daemon stopped (PID {pid})")


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
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=_default_vault_path, help="Path to vault",
)
def ps(vault_path: Path) -> None:
    """Show active LLM processes and background worker state."""
    try:
        client = _get_client(vault_path, auto_start=False)
    except click.ClickException:
        click.echo("Daemon not running.", err=True)
        raise SystemExit(1)

    resp = client.request({"type": "process-list"})
    if resp["status"] != "ok":
        raise click.ClickException(resp.get("message", "Failed"))

    jobs: list[dict] = resp.get("jobs", [])
    pending: int = resp.get("pending", 0)
    tokens_used: int = resp.get("tokens_used", 0)
    slots_total: int = resp.get("slots_total", 0)
    workers: list[dict] = resp.get("workers", [])
    active = len(jobs)

    # Header
    click.echo(f"PROCESSES  {active} active · {pending} pending · {tokens_used:,} tokens used")
    click.echo()

    # Workers section
    if workers:
        click.echo("WORKERS")
        for w in workers:
            name: str = w["name"]
            state: str = w.get("state", "idle")
            last_run: str | None = w.get("last_run")
            elapsed_s: float | None = w.get("running_elapsed_s")

            if state == "running":
                action = _worker_display_action(name, jobs)
                elapsed_str = f"{int(elapsed_s)}s" if elapsed_s is not None else "—"
                click.echo(f"  {name:<14} running   {action:<32} {elapsed_str}")
            else:
                last_str = (
                    f"last run {_relative_time(last_run)[0]}" if last_run else "never run"
                )
                failures: int = w.get("consecutive_failures", 0)
                fail_str = f" [{failures} failures]" if failures > 0 else ""
                click.echo(f"  {name:<14} idle      {last_str}{fail_str}")
        click.echo()

    # LLM Queue section
    click.echo(
        f"LLM QUEUE  ({active}/{slots_total} slots, {pending} pending)"
        if active or pending
        else "LLM QUEUE"
    )
    if jobs:
        for job in jobs:
            raw_label: str = job.get("label", "unknown")
            label = " · ".join(raw_label.split(":"))
            priority: str = job.get("priority", "")
            elapsed: int = int(job.get("elapsed_s", 0))
            click.echo(f"  [{job['id']}]  {label:<42} {priority:<14} {elapsed}s")
    else:
        click.echo("  No active LLM calls.")


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
@click.option(
    "--timeout", default=300, type=int, show_default=True,
    help="Socket timeout in seconds (increase for slow/local models).",
)
@click.option(
    "--trace", "trace", is_flag=True, default=False,
    help="Show per-call LLM trace and write a full trace file.",
)
def query(question: str, vault_path: Path, budget: int | None, timeout: int, trace: bool) -> None:
    """Query the wiki — multi-turn LLM traversal with citations."""
    client = _get_client(vault_path)
    req: dict = {"type": "query", "question": question}
    if budget is not None:
        req["budget"] = budget
    if trace:
        req["trace"] = True

    resp = client.request(req, timeout=timeout)
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

    # Render trace if requested
    if trace and resp.get("trace_events"):
        events = resp["trace_events"]
        click.echo()
        click.echo(f"─── Trace: {len(events)} LLM call(s) ───────────────────────────")
        for ev in events:
            label = ev.get("label", "?")
            model = ev.get("model", "?").split("/")[-1]
            tin = ev.get("input_tokens", 0)
            tout = ev.get("output_tokens", 0)
            cached = ev.get("cached_tokens", 0)
            latency = ev.get("latency_s", 0)
            cache_info = f" | {cached:,} cached" if cached else ""
            click.echo(f"  {label}  [{model} | {tin:,}→{tout:,} tok{cache_info} | {latency}s]")

        import datetime as _dt
        import tempfile as _tmpfile
        ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = question[:40].lower().replace(" ", "-").replace("/", "-")
        trace_path = Path(_tmpfile.gettempdir()) / f"llm-wiki-query-{slug}-{ts}.trace.md"
        _write_trace_file(trace_path, Path(f"query: {question[:60]}"), events)
        click.echo(f"  Full trace → {trace_path}")


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


def _write_trace_file(trace_path: Path, source: Path, events: list[dict]) -> None:
    """Write a human-readable markdown trace file from collected LLM call events."""
    import datetime as _dt

    lines: list[str] = [
        f"# LLM Trace — {source.name}",
        f"**Date**: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
        f"**Calls**: {len(events)}",
        "",
    ]

    for i, ev in enumerate(events, 1):
        label = ev.get("label", "unknown")
        model = ev.get("model", "?")
        temp = ev.get("temperature", "?")
        tin = ev.get("input_tokens", 0)
        tout = ev.get("output_tokens", 0)
        cached = ev.get("cached_tokens", 0)
        latency = ev.get("latency_s", 0)
        token_str = f"{tin:,} in / {tout:,} out"
        if cached:
            token_str += f" / {cached:,} cached"

        lines += [
            "---",
            "",
            f"## [{i}] `{label}`",
            "",
            f"| Field | Value |",
            f"|-------|-------|",
            f"| Model | `{model}` |",
            f"| Temperature | {temp} |",
            f"| Tokens | {token_str} |",
            f"| Latency | {latency}s |",
            "",
        ]

        messages = ev.get("messages", [])
        for msg in messages:
            role = msg.get("role", "?").upper()
            content = msg.get("content", "")
            lines += [
                f"### {role}",
                "",
                "```",
                content,
                "```",
                "",
            ]

        response = ev.get("response", "")
        lines += [
            "### RESPONSE",
            "",
            "```",
            response,
            "```",
            "",
        ]

    trace_path.write_text("\n".join(lines), encoding="utf-8")


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
@click.option(
    "--trace", "trace", is_flag=True, default=False,
    help="Show per-call LLM trace and write a full trace file.",
)
def ingest(source_path: Path, vault_path: Path, dry_run: bool, trace: bool) -> None:
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
    if trace:
        msg["trace"] = True

    if dry_run:
        resp = client.request(msg, timeout=300)
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

    # Trace file state — populated from "trace" frames when --trace is set
    trace_events: list[dict] = []
    trace_file_path: Path | None = None
    if trace:
        import datetime as _dt
        import tempfile as _tmpfile
        ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        stem = source_path.stem[:40]  # cap to avoid ridiculous filenames
        trace_file_path = Path(_tmpfile.gettempdir()) / f"llm-wiki-{stem}-{ts}.trace.md"

    def on_frame(frame: dict) -> None:
        ftype = frame.get("type")
        stage = frame.get("stage", "")

        if ftype == "trace":
            # Compact inline summary — full content goes to the trace file
            label = frame.get("label", "?")
            model = frame.get("model", "?")
            tin = frame.get("input_tokens", 0)
            tout = frame.get("output_tokens", 0)
            cached = frame.get("cached_tokens", 0)
            latency = frame.get("latency_s", 0)
            cache_info = f" ({cached:,} cached)" if cached else ""
            line = (
                f"[TRACE] {label}  "
                f"model={model.split('/')[-1]}  "
                f"{tin:,}→{tout:,} tok{cache_info}  {latency}s"
            )
            if spinner:
                spinner.print_line(line)
            else:
                click.echo(line)
            trace_events.append(frame)
            return

        if ftype == "progress":
            if stage == "extracting":
                if spinner:
                    spinner.start("Extracting...")
                # no output line for extracting — spinner alone is enough on TTY
            elif stage == "concepts_found":
                count = frame["count"]
                line = f"[PROGRESS] concepts_found: {count}"
                if spinner:
                    spinner.update(f"Found {count} concept(s) — building context...")
                    spinner.print_line(line)
                else:
                    click.echo(line)
            elif stage == "building_context":
                total = frame.get("total_chunks", "?")
                if spinner:
                    spinner.update(f"Building paper context ({total} chunk(s))...")
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
            # Write trace file now that the run is complete
            if trace_file_path and trace_events:
                _write_trace_file(trace_file_path, source_path, trace_events)
                click.echo(f"[TRACE] {len(trace_events)} calls recorded → {trace_file_path}")

        elif ftype == "error":
            if spinner:
                spinner.stop()
            msg_text = frame.get("message", "Unknown error")
            written = frame.get("concepts_written", 0)
            error_seen.append(f"{msg_text} ({written} concept(s) written before error)")
            if trace_file_path and trace_events:
                _write_trace_file(trace_file_path, source_path, trace_events)
                click.echo(f"[TRACE] partial trace ({len(trace_events)} calls) → {trace_file_path}")

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

    click.echo(f"{'name':<16} {'interval':<10} {'failures':<10} last_run        next_scheduled")
    click.echo("-" * 85)
    for worker in workers:
        interval = worker["interval_seconds"]
        interval_str = f"{interval:.0f}s"
        if interval >= 3600:
            interval_str = f"{interval/3600:.1f}h"
        elif interval >= 60:
            interval_str = f"{interval/60:.1f}m"
        
        failures = worker.get("consecutive_failures", 0)
        last = worker["last_run"] or "never"
        last_attempt = worker.get("last_attempt")
        reachable = worker.get("backend_reachable")
        reachable_str = "" if reachable is None else (" [backend DOWN]" if not reachable else "")

        ago, next_calc = _relative_time(last)
        if next_calc is None:
            # Worker has run — calculate next from interval
            next_seconds = interval
            next_str = "—"
            if next_seconds < 60:
                next_str = f"in {next_seconds}s"
            elif next_seconds < 3600:
                next_str = f"in {int(next_seconds/60)}m"
            elif next_seconds < 86400:
                next_str = f"in {int(next_seconds/3600)}h"
            else:
                next_str = f"in {int(next_seconds/86400)}d"
        elif reachable is False:
            next_str = "skipped"   # health probe failing — backend down
        elif failures > 0:
            # Failed at least once; waiting for next interval retry
            next_seconds = interval
            if next_seconds < 60:
                next_str = f"in {int(next_seconds)}s"
            elif next_seconds < 3600:
                next_str = f"in {int(next_seconds/60)}m"
            elif next_seconds < 86400:
                next_str = f"in {int(next_seconds/3600)}h"
            else:
                next_str = f"in {int(next_seconds/86400)}d"
        elif last_attempt:
            next_str = "running"   # first run in progress (no successful completion yet)
        else:
            next_str = "pending"   # task created but hasn't started yet
        
        click.echo(
            f"{worker['name']:<16} {interval_str:<10} {failures:<10} {ago:<16} {next_str:<12}{reachable_str}".rstrip()
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
