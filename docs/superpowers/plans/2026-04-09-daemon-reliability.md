# Daemon Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three categories of urgent daemon bugs — startup crashes and silent failures, missing mutual exclusion, and background worker resilience/observability.

**Architecture:** Two groups in order: startup/init code first (vault.py, cli/main.py, daemon/__main__.py), then runtime code (llm_client.py, scheduler.py, server.py). Each task is TDD: write the failing test, run it, implement the fix, run again.

**Tech Stack:** Python 3.11+, asyncio, click, litellm, httpx (transitive), pytest-asyncio

---

## File Map

| File | What changes |
|------|-------------|
| `src/llm_wiki/vault.py` | Skip non-file *.md; validate vault structure guard |
| `src/llm_wiki/cli/main.py` | Default vault path order; stderr capture on daemon spawn |
| `src/llm_wiki/daemon/__main__.py` | Refuse startup if live daemon already running |
| `src/llm_wiki/traverse/llm_client.py` | Retry with exponential backoff |
| `src/llm_wiki/config.py` | Add `failure_escalation_threshold` to `MaintenanceConfig` |
| `src/llm_wiki/daemon/scheduler.py` | `ScheduledWorker.health_probe_url`; failure tracking; health probe; escalation |
| `src/llm_wiki/daemon/server.py` | Pass probe URLs + issue_queue to scheduler; extend status responses |
| `tests/test_vault.py` | Tests for scan fixes |
| `tests/test_cli/test_commands.py` | Tests for vault path + error visibility |
| `tests/test_daemon/test_lifecycle.py` | Test for mutual exclusion guard |
| `tests/test_traverse/test_llm_client.py` | Tests for retry logic |
| `tests/test_daemon/test_scheduler.py` | Tests for failure tracking, probe, escalation, health_info |

---

## Task 1: vault.py — Skip non-file *.md paths

**Files:**
- Modify: `src/llm_wiki/vault.py:65` (the `for md_file in md_files:` loop body)
- Test: `tests/test_vault.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_vault.py

def test_scan_skips_directory_named_dot_md(tmp_path):
    """Vault.scan() must not crash when a directory is named something.md."""
    # Create vault structure
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "normal-page.md").write_text(
        "---\ntitle: Normal\ntags: []\ncitations: []\n---\n\n# Normal\n"
    )
    # A directory named *.md — triggers IsADirectoryError without the fix
    dir_md = wiki_dir / "weird-dir.md"
    dir_md.mkdir()
    (dir_md / "some_file.txt").write_text("inside the directory")

    # Should not raise
    vault = Vault.scan(tmp_path)
    assert vault.page_count == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_vault.py::test_scan_skips_directory_named_dot_md -v
```

Expected: `FAILED` — `IsADirectoryError` or similar when `Page.parse()` tries to read a directory.

- [ ] **Step 3: Implement the fix**

In `src/llm_wiki/vault.py`, inside the `for md_file in md_files:` loop (around line 65), add `is_file()` guard:

```python
    for md_file in md_files:
        if not md_file.is_file():
            continue
        page = Page.parse(md_file)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_vault.py::test_scan_skips_directory_named_dot_md -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/vault.py tests/test_vault.py
git commit -m "fix: vault.scan() skips directory paths matching *.md"
```

---

## Task 2: vault.py — Validate vault structure before walking

**Files:**
- Modify: `src/llm_wiki/vault.py` (start of `Vault.scan()`)
- Test: `tests/test_vault.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_vault.py

def test_scan_rejects_non_vault_directory(tmp_path):
    """Vault.scan() raises ValueError with a clear message for non-vault paths."""
    # tmp_path has no schema/, raw/, or wiki/ — looks like a home directory
    (tmp_path / "downloads").mkdir()
    (tmp_path / "documents").mkdir()

    with pytest.raises(ValueError, match="does not appear to be an llm-wiki vault"):
        Vault.scan(tmp_path)


def test_scan_accepts_directory_with_schema_config(tmp_path):
    """Vault.scan() accepts a directory that has schema/config.yaml."""
    (tmp_path / "schema").mkdir()
    (tmp_path / "schema" / "config.yaml").write_text("vault:\n  mode: managed\n")

    # Should not raise, even if empty
    vault = Vault.scan(tmp_path)
    assert vault.page_count == 0


def test_scan_accepts_directory_with_wiki_dir(tmp_path):
    """Vault.scan() accepts a directory that has a wiki/ subdirectory."""
    (tmp_path / "wiki").mkdir()

    vault = Vault.scan(tmp_path)
    assert vault.page_count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_vault.py::test_scan_rejects_non_vault_directory tests/test_vault.py::test_scan_accepts_directory_with_schema_config tests/test_vault.py::test_scan_accepts_directory_with_wiki_dir -v
```

Expected: `FAILED` — no guard exists yet; rejection test passes through, acceptance tests succeed trivially but the rejection is wrong.

- [ ] **Step 3: Implement the fix**

Add at the top of `Vault.scan()`, before the `md_files = sorted(root.rglob("*.md"))` line:

```python
    # Validate that this directory looks like a vault before walking.
    has_config = (root / "schema" / "config.yaml").exists()
    has_vault_dir = (root / "raw").exists() or (root / "wiki").exists()
    if not has_config and not has_vault_dir:
        raise ValueError(
            f"Path '{root}' does not appear to be an llm-wiki vault. "
            "Pass --vault <path> or run from inside a vault directory."
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_vault.py::test_scan_rejects_non_vault_directory tests/test_vault.py::test_scan_accepts_directory_with_schema_config tests/test_vault.py::test_scan_accepts_directory_with_wiki_dir -v
```

Expected: all three `PASSED`

- [ ] **Step 5: Run the full vault test suite to check for regressions**

```bash
pytest tests/test_vault.py -v
```

Expected: all pass (the `sample_vault` fixture creates a vault with markdown files but no `schema/` or `wiki/` dir — add a `wiki/` or `raw/` dir to the `sample_vault` fixture in `tests/conftest.py` if needed).

> **Note:** If `sample_vault` tests now fail, the fixture in `tests/conftest.py` needs `(tmp_path / "wiki").mkdir()` added so it looks like a vault. Check and patch accordingly.

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/vault.py tests/test_vault.py tests/conftest.py
git commit -m "fix: vault.scan() rejects non-vault directories with a clear error"
```

---

## Task 3: cli/main.py — Default vault path resolution order

**Files:**
- Modify: `src/llm_wiki/cli/main.py`
- Test: `tests/test_cli/test_commands.py`

- [ ] **Step 1: Write the failing tests**

```python
# Add to tests/test_cli/test_commands.py (or create it if it doesn't exist)
import os
from pathlib import Path
from click.testing import CliRunner
from llm_wiki.cli.main import cli


def test_default_vault_uses_env_var(tmp_path, monkeypatch):
    """--vault defaults to LLM_WIKI_VAULT env var when set."""
    # Create a minimal vault so the path exists
    (tmp_path / "wiki").mkdir()
    monkeypatch.setenv("LLM_WIKI_VAULT", str(tmp_path))

    from llm_wiki.cli.main import _default_vault_path
    result = _default_vault_path()
    assert result == str(tmp_path)


def test_default_vault_falls_back_to_home_wiki(tmp_path, monkeypatch):
    """--vault defaults to ~/wiki when LLM_WIKI_VAULT is unset and ~/wiki exists."""
    monkeypatch.delenv("LLM_WIKI_VAULT", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / "wiki").mkdir()

    from llm_wiki.cli.main import _default_vault_path
    result = _default_vault_path()
    assert result == str(tmp_path / "wiki")


def test_default_vault_falls_back_to_dot(tmp_path, monkeypatch):
    """--vault defaults to '.' when neither LLM_WIKI_VAULT nor ~/wiki is set."""
    monkeypatch.delenv("LLM_WIKI_VAULT", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    # Do NOT create tmp_path/wiki

    from llm_wiki.cli.main import _default_vault_path
    result = _default_vault_path()
    assert result == "."
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_cli/test_commands.py::test_default_vault_uses_env_var tests/test_cli/test_commands.py::test_default_vault_falls_back_to_home_wiki tests/test_cli/test_commands.py::test_default_vault_falls_back_to_dot -v
```

Expected: `FAILED` — `_default_vault_path` doesn't exist yet.

- [ ] **Step 3: Implement the fix**

Add at the top of `src/llm_wiki/cli/main.py`, after the existing imports:

```python
import os


def _default_vault_path() -> str:
    """Resolve vault path: LLM_WIKI_VAULT env → ~/wiki → '.'"""
    env = os.environ.get("LLM_WIKI_VAULT", "").strip()
    if env:
        return env
    home_wiki = Path.home() / "wiki"
    if home_wiki.is_dir():
        return str(home_wiki)
    return "."
```

Then change every `--vault` option in the file from `default="."` to `default=_default_vault_path`:

```python
# Before (appears ~10 times):
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=".", help="Path to vault",
)

# After:
@click.option(
    "--vault", "vault_path", type=click.Path(exists=True, path_type=Path),
    default=_default_vault_path, help="Path to vault",
)
```

There are approximately 10 commands with `--vault` options. Change all of them.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_cli/test_commands.py::test_default_vault_uses_env_var tests/test_cli/test_commands.py::test_default_vault_falls_back_to_home_wiki tests/test_cli/test_commands.py::test_default_vault_falls_back_to_dot -v
```

Expected: all three `PASSED`

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/cli/main.py tests/test_cli/test_commands.py
git commit -m "fix: cli --vault defaults to LLM_WIKI_VAULT -> ~/wiki -> ."
```

---

## Task 4: cli/main.py — Daemon startup error visibility

**Files:**
- Modify: `src/llm_wiki/cli/main.py` (`_get_client()` function)
- Test: `tests/test_cli/test_commands.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_cli/test_commands.py

import subprocess
import sys

def test_get_client_reports_daemon_exit_immediately(tmp_path, monkeypatch):
    """_get_client() reports the daemon's stderr immediately when it exits, not after 30s."""
    # Create a minimal vault
    (tmp_path / "wiki").mkdir()
    (tmp_path / "schema").mkdir()

    # Patch subprocess.Popen to spawn a process that exits immediately with an error
    exit_script = tmp_path / "bad_daemon.py"
    exit_script.write_text('import sys; print("vault config missing", file=sys.stderr); sys.exit(1)')

    from llm_wiki.daemon.client import DaemonClient
    from llm_wiki.daemon.lifecycle import socket_path_for

    calls = []

    class FakePopen:
        def __init__(self, *args, **kwargs):
            import subprocess as _sp
            self._proc = _sp.Popen(
                [sys.executable, str(exit_script)],
                stderr=kwargs.get("stderr", _sp.DEVNULL),
                stdout=_sp.DEVNULL,
            )
            calls.append(True)

        def poll(self):
            return self._proc.poll()

    monkeypatch.setattr("llm_wiki.cli.main.subprocess.Popen", FakePopen)

    # Patch DaemonClient.is_running to always return False
    monkeypatch.setattr(DaemonClient, "is_running", lambda self: False)

    from llm_wiki.cli.main import _get_client
    import click

    with pytest.raises(click.ClickException) as exc_info:
        _get_client(tmp_path)

    # Must fail fast (not after 30s) and include the error output
    assert "vault config missing" in str(exc_info.value.format_message())
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_cli/test_commands.py::test_get_client_reports_daemon_exit_immediately -v
```

Expected: `FAILED` — test times out (30 seconds) or the error message is not included.

- [ ] **Step 3: Implement the fix**

Replace `_get_client()` in `src/llm_wiki/cli/main.py`:

```python
import os
import tempfile


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

    # Capture stderr so startup errors are visible immediately
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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_cli/test_commands.py::test_get_client_reports_daemon_exit_immediately -v
```

Expected: `PASSED` (fast — no 30-second wait)

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/cli/main.py tests/test_cli/test_commands.py
git commit -m "fix: _get_client() captures daemon stderr and reports failure immediately"
```

---

## Task 5: daemon/__main__.py — Mutual exclusion via alive-daemon check

**Files:**
- Modify: `src/llm_wiki/daemon/__main__.py`
- Test: `tests/test_daemon/test_lifecycle.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_daemon/test_lifecycle.py

import os
import pytest
from pathlib import Path
from llm_wiki.daemon.lifecycle import (
    pidfile_path_for, write_pidfile, is_process_alive
)


@pytest.mark.asyncio
async def test_run_refuses_to_start_when_daemon_already_running(tmp_path):
    """daemon.__main__.run() exits with a clear error if a live daemon holds the pidfile."""
    (tmp_path / "wiki").mkdir()
    (tmp_path / "schema").mkdir()
    (tmp_path / "schema" / "config.yaml").write_text("")

    pid_path = pidfile_path_for(tmp_path)
    # Write our own PID — we are definitely alive
    write_pidfile(pid_path, os.getpid())

    try:
        from llm_wiki.daemon.__main__ import run
        with pytest.raises(SystemExit) as exc_info:
            await run(tmp_path)
        assert exc_info.value.code != 0
        assert "already running" in str(exc_info.value).lower() or exc_info.value.code == 1
    finally:
        pid_path.unlink(missing_ok=True)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_daemon/test_lifecycle.py::test_run_refuses_to_start_when_daemon_already_running -v
```

Expected: `FAILED` — no guard exists; `run()` calls `cleanup_stale()` which removes the pidfile and proceeds.

- [ ] **Step 3: Implement the fix**

In `src/llm_wiki/daemon/__main__.py`, add the live-daemon check at the start of `run()`, before `cleanup_stale()`:

```python
from llm_wiki.daemon.lifecycle import (
    cleanup_stale,
    is_process_alive,
    pidfile_path_for,
    read_pidfile,
    socket_path_for,
    write_pidfile,
)


async def run(vault_root: Path) -> None:
    sock_path = socket_path_for(vault_root)
    pid_path = pidfile_path_for(vault_root)

    # Mutual exclusion: refuse to start if a live daemon already holds the pidfile
    existing_pid = read_pidfile(pid_path)
    if existing_pid is not None and is_process_alive(existing_pid):
        raise SystemExit(
            f"Daemon already running for this vault (PID {existing_pid}). "
            "Use 'llm-wiki stop' or kill the process first."
        )

    cleanup_stale(sock_path, pid_path)
    # ... rest of run() unchanged
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_daemon/test_lifecycle.py::test_run_refuses_to_start_when_daemon_already_running -v
```

Expected: `PASSED`

- [ ] **Step 5: Run the full lifecycle test suite**

```bash
pytest tests/test_daemon/test_lifecycle.py -v
```

Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/daemon/__main__.py tests/test_daemon/test_lifecycle.py
git commit -m "fix: daemon refuses to start when another live daemon holds the pidfile"
```

---

## Task 6: traverse/llm_client.py — Retry with exponential backoff

**Files:**
- Modify: `src/llm_wiki/traverse/llm_client.py`
- Test: `tests/test_traverse/test_llm_client.py`

- [ ] **Step 1: Write the failing tests**

```python
# Add to tests/test_traverse/test_llm_client.py

import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from llm_wiki.traverse.llm_client import LLMClient, _should_retry
from llm_wiki.daemon.llm_queue import LLMQueue  # adjust import if needed


def _make_client():
    queue = MagicMock()
    queue.submit = AsyncMock(side_effect=lambda fn, **kw: fn())
    queue.record_tokens = MagicMock()
    return LLMClient(queue=queue, model="openai/test", api_base="http://localhost:4000/v1")


def test_should_retry_connection_error():
    """ConnectionError (no status_code) should be retried."""
    assert _should_retry(ConnectionError("refused")) is True


def test_should_retry_503():
    """HTTP 503 should be retried."""
    exc = Exception("service unavailable")
    exc.status_code = 503
    assert _should_retry(exc) is True


def test_should_not_retry_401():
    """HTTP 401 (auth failure) should not be retried."""
    exc = Exception("unauthorized")
    exc.status_code = 401
    assert _should_retry(exc) is False


def test_should_not_retry_400():
    """HTTP 400 (bad request) should not be retried."""
    exc = Exception("bad request")
    exc.status_code = 400
    assert _should_retry(exc) is False


@pytest.mark.asyncio
async def test_complete_retries_on_transient_error():
    """complete() retries up to 3 times on transient failures then succeeds."""
    call_count = {"n": 0}

    async def fake_completion(**kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise ConnectionError("connection refused")
        resp = MagicMock()
        resp.choices[0].message.content = "answer"
        resp.usage.total_tokens = 10
        return resp

    client = _make_client()
    with patch("litellm.acompletion", side_effect=fake_completion):
        with patch("asyncio.sleep", new_callable=AsyncMock):  # skip real delays
            result = await client.complete([{"role": "user", "content": "hi"}])

    assert result.content == "answer"
    assert call_count["n"] == 3


@pytest.mark.asyncio
async def test_complete_does_not_retry_permanent_error():
    """complete() raises immediately on a permanent error (401)."""
    async def fake_completion(**kwargs):
        exc = Exception("unauthorized")
        exc.status_code = 401
        raise exc

    client = _make_client()
    with patch("litellm.acompletion", side_effect=fake_completion):
        with pytest.raises(Exception, match="unauthorized"):
            await client.complete([{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_complete_raises_after_max_retries():
    """complete() raises after exhausting all 3 retries."""
    async def fake_completion(**kwargs):
        raise ConnectionError("always fails")

    client = _make_client()
    with patch("litellm.acompletion", side_effect=fake_completion):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ConnectionError):
                await client.complete([{"role": "user", "content": "hi"}])
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_traverse/test_llm_client.py::test_should_retry_connection_error tests/test_traverse/test_llm_client.py::test_complete_retries_on_transient_error tests/test_traverse/test_llm_client.py::test_complete_does_not_retry_permanent_error -v
```

Expected: `FAILED` — `_should_retry` doesn't exist; `complete()` has no retry logic.

- [ ] **Step 3: Implement the fix**

At the top of `src/llm_wiki/traverse/llm_client.py`, add after existing imports:

```python
import logging

logger = logging.getLogger(__name__)

_TRANSIENT_HTTP_CODES = {408, 429, 500, 502, 503, 504}
_RETRY_DELAYS = [5.0, 15.0, 45.0]


def _should_retry(exc: Exception) -> bool:
    """Return True for transient errors that should be retried."""
    status = getattr(exc, "status_code", None)
    if status is None:
        return True  # Connection/timeout errors have no status_code
    return status in _TRANSIENT_HTTP_CODES
```

Replace the `_call()` inner function inside `complete()` with the retry version:

```python
    async def _call() -> LLMResponse:
        last_exc: Exception | None = None
        for attempt, delay in enumerate([*_RETRY_DELAYS, None]):
            try:
                kwargs: dict[str, Any] = {
                    "model": self._model,
                    "messages": messages,
                    "temperature": temperature,
                }
                if self._api_base is not None:
                    kwargs["api_base"] = self._api_base
                if self._api_key is not None:
                    kwargs["api_key"] = self._api_key

                response = await litellm.acompletion(**kwargs)
                content = response.choices[0].message.content
                tokens = response.usage.total_tokens if response.usage else 0
                self._queue.record_tokens(tokens)
                return LLMResponse(content=content, tokens_used=tokens)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not _should_retry(exc) or delay is None:
                    raise
                last_exc = exc
                logger.warning(
                    "LLM call failed (attempt %d/%d, retrying in %.0fs): %s",
                    attempt + 1, len(_RETRY_DELAYS) + 1, delay, exc,
                )
                await asyncio.sleep(delay)
        raise last_exc  # type: ignore[misc]  # unreachable
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_traverse/test_llm_client.py -v
```

Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/traverse/llm_client.py tests/test_traverse/test_llm_client.py
git commit -m "fix: LLMClient retries transient failures with exponential backoff (5s/15s/45s)"
```

---

## Task 7: scheduler.py — Track last_attempt and consecutive_failures

**Files:**
- Modify: `src/llm_wiki/daemon/scheduler.py`
- Test: `tests/test_daemon/test_scheduler.py`

- [ ] **Step 1: Write the failing tests**

```python
# Add to tests/test_daemon/test_scheduler.py

@pytest.mark.asyncio
async def test_scheduler_tracks_last_attempt_on_failure():
    """last_attempt is set even when the worker fails; last_run is not."""
    async def failing():
        raise RuntimeError("oops")

    scheduler = IntervalScheduler()
    scheduler.register(ScheduledWorker("fail", 100.0, failing))
    await scheduler.start()
    await asyncio.sleep(0.05)
    await scheduler.stop()

    assert scheduler.last_attempt_iso("fail") is not None
    assert scheduler.last_run_iso("fail") is None  # never succeeded


@pytest.mark.asyncio
async def test_scheduler_increments_consecutive_failures():
    """consecutive_failures increments on each failed run."""
    call_count = {"n": 0}

    async def failing():
        call_count["n"] += 1
        raise RuntimeError("oops")

    scheduler = IntervalScheduler()
    scheduler.register(ScheduledWorker("fail", 0.05, failing))
    await scheduler.start()
    await asyncio.sleep(0.2)
    await scheduler.stop()

    assert scheduler.consecutive_failures("fail") >= 2


@pytest.mark.asyncio
async def test_scheduler_resets_consecutive_failures_on_success():
    """consecutive_failures resets to 0 after a successful run."""
    fail_until = {"n": 2}

    async def sometimes_fails():
        if fail_until["n"] > 0:
            fail_until["n"] -= 1
            raise RuntimeError("not yet")

    scheduler = IntervalScheduler()
    scheduler.register(ScheduledWorker("maybe", 0.05, sometimes_fails))
    await scheduler.start()
    await asyncio.sleep(0.4)
    await scheduler.stop()

    # Eventually succeeds and resets
    assert scheduler.consecutive_failures("maybe") == 0
    assert scheduler.last_run_iso("maybe") is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_daemon/test_scheduler.py::test_scheduler_tracks_last_attempt_on_failure tests/test_daemon/test_scheduler.py::test_scheduler_increments_consecutive_failures tests/test_daemon/test_scheduler.py::test_scheduler_resets_consecutive_failures_on_success -v
```

Expected: `FAILED` — `last_attempt_iso`, `consecutive_failures` methods don't exist.

- [ ] **Step 3: Implement the fix**

In `src/llm_wiki/daemon/scheduler.py`, extend `IntervalScheduler`:

Add to `__init__()`:
```python
        self._last_attempt: dict[str, str] = {}
        self._consecutive_failures: dict[str, int] = {}
```

Add new public accessors after `last_run_iso()`:
```python
    def last_attempt_iso(self, name: str) -> str | None:
        return self._last_attempt.get(name)

    def consecutive_failures(self, name: str) -> int:
        return self._consecutive_failures.get(name, 0)
```

Replace `_run_once()`:
```python
    async def _run_once(self, worker: ScheduledWorker) -> None:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._last_attempt[worker.name] = now
        try:
            await worker.coro_factory()
            self._last_run[worker.name] = now
            self._consecutive_failures[worker.name] = 0
        except asyncio.CancelledError:
            raise
        except Exception:
            failures = self._consecutive_failures.get(worker.name, 0) + 1
            self._consecutive_failures[worker.name] = failures
            logger.exception(
                "Worker %r raised (consecutive_failures=%d); will retry on next interval",
                worker.name, failures,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_daemon/test_scheduler.py -v
```

Expected: all pass (including previously passing tests)

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/scheduler.py tests/test_daemon/test_scheduler.py
git commit -m "feat: scheduler tracks last_attempt and consecutive_failures per worker"
```

---

## Task 8: scheduler.py — Health probe before each worker run

**Files:**
- Modify: `src/llm_wiki/daemon/scheduler.py` (add `health_probe_url` to `ScheduledWorker` + probe logic)
- Modify: `src/llm_wiki/daemon/server.py` (pass probe URLs when registering workers)
- Test: `tests/test_daemon/test_scheduler.py`

- [ ] **Step 1: Write the failing tests**

```python
# Add to tests/test_daemon/test_scheduler.py

@pytest.mark.asyncio
async def test_scheduler_skips_worker_when_backend_unreachable():
    """Worker is skipped (not failed) when health probe fails."""
    call_count = {"n": 0}

    async def worker_fn():
        call_count["n"] += 1

    scheduler = IntervalScheduler()
    scheduler.register(ScheduledWorker(
        name="probe-test",
        interval_seconds=100.0,
        coro_factory=worker_fn,
        health_probe_url="http://127.0.0.1:19999/v1",  # nothing listening here
    ))
    await scheduler.start()
    await asyncio.sleep(0.1)
    await scheduler.stop()

    # Worker should NOT have been called — backend unreachable
    assert call_count["n"] == 0
    # Not a failure — consecutive_failures stays 0
    assert scheduler.consecutive_failures("probe-test") == 0
    # last_attempt NOT set for a skipped run
    assert scheduler.last_attempt_iso("probe-test") is None


@pytest.mark.asyncio
async def test_scheduler_runs_worker_when_no_probe_url():
    """Worker without a health_probe_url runs normally."""
    call_count = {"n": 0}

    async def worker_fn():
        call_count["n"] += 1

    scheduler = IntervalScheduler()
    scheduler.register(ScheduledWorker(
        name="no-probe",
        interval_seconds=100.0,
        coro_factory=worker_fn,
        health_probe_url=None,
    ))
    await scheduler.start()
    await asyncio.sleep(0.05)
    await scheduler.stop()

    assert call_count["n"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_daemon/test_scheduler.py::test_scheduler_skips_worker_when_backend_unreachable tests/test_daemon/test_scheduler.py::test_scheduler_runs_worker_when_no_probe_url -v
```

Expected: `FAILED` — `ScheduledWorker` doesn't have `health_probe_url`; no probe logic exists.

- [ ] **Step 3: Implement the fix in scheduler.py**

Add `health_probe_url` to `ScheduledWorker`:

```python
from typing import Awaitable, Callable

@dataclass
class ScheduledWorker:
    """One named worker the scheduler runs on an interval."""
    name: str
    interval_seconds: float
    coro_factory: Callable[[], Awaitable[None]]
    health_probe_url: str | None = None
```

Add the probe helper function (module-level, after imports):

```python
async def _probe_backend(url: str) -> bool:
    """Return True if the backend at url is reachable. False on any error."""
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{url}/models", timeout=5.0)
            return r.status_code < 500
    except Exception:
        return False
```

Add `_backend_reachable` dict to `__init__()`:
```python
        self._backend_reachable: dict[str, bool | None] = {}
```

Update `_run_once()` to probe before running (insert at the top of the method, before the `now = ...` line):

```python
    async def _run_once(self, worker: ScheduledWorker) -> None:
        # Health probe — skip run (not fail) if backend is unreachable
        if worker.health_probe_url is not None:
            reachable = await _probe_backend(worker.health_probe_url)
            self._backend_reachable[worker.name] = reachable
            if not reachable:
                logger.info(
                    "[%s] backend unreachable at %s, skipping run",
                    worker.name, worker.health_probe_url,
                )
                return

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._last_attempt[worker.name] = now
        try:
            await worker.coro_factory()
            self._last_run[worker.name] = now
            self._consecutive_failures[worker.name] = 0
        except asyncio.CancelledError:
            raise
        except Exception:
            failures = self._consecutive_failures.get(worker.name, 0) + 1
            self._consecutive_failures[worker.name] = failures
            logger.exception(
                "Worker %r raised (consecutive_failures=%d); will retry on next interval",
                worker.name, failures,
            )
```

Add `backend_reachable()` accessor:

```python
    def backend_reachable(self, name: str) -> bool | None:
        """Last probe result for worker. None if probe has not run yet."""
        return self._backend_reachable.get(name)
```

- [ ] **Step 4: Update server.py worker registrations**

In `src/llm_wiki/daemon/server.py`, in `_register_maintenance_workers()`, add `health_probe_url` to LLM-dependent workers. Find the registrations and extend them:

```python
# Helper at top of _register_maintenance_workers():
def _probe_url(role: str) -> str | None:
    try:
        backend = self._config.llm.resolve(role)
        return backend.api_base  # e.g. "http://localhost:4000/v1"
    except Exception:
        return None

# Example for librarian worker (same pattern for adversary, talk_summary):
self._scheduler.register(ScheduledWorker(
    name="librarian",
    interval_seconds=parse_interval(self._config.maintenance.librarian_interval),
    coro_factory=run_librarian,
    health_probe_url=_probe_url("librarian"),
))

# Auditor is pure Python — no probe needed:
self._scheduler.register(ScheduledWorker(
    name="auditor",
    interval_seconds=parse_interval(self._config.maintenance.auditor_interval),
    coro_factory=run_auditor,
    health_probe_url=None,
))
```

Apply this pattern to all LLM-dependent workers: `librarian`, `adversary`, `talk_summary`, `compliance`. Leave `auditor` and `authority_recalc` with `health_probe_url=None`.

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_daemon/test_scheduler.py -v
```

Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/llm_wiki/daemon/scheduler.py src/llm_wiki/daemon/server.py tests/test_daemon/test_scheduler.py
git commit -m "feat: scheduler health probe skips LLM workers when backend is unreachable"
```

---

## Task 9: config.py + scheduler.py — Escalation on repeated failure

**Files:**
- Modify: `src/llm_wiki/config.py`
- Modify: `src/llm_wiki/daemon/scheduler.py`
- Modify: `src/llm_wiki/daemon/server.py`
- Test: `tests/test_daemon/test_scheduler.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_daemon/test_scheduler.py

@pytest.mark.asyncio
async def test_scheduler_files_issue_on_threshold_crossing(tmp_path):
    """After N consecutive failures, an issue is filed in the wiki issue queue."""
    from llm_wiki.issues.queue import IssueQueue

    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    issue_queue = IssueQueue(wiki_dir)

    async def always_fails():
        raise RuntimeError("backend down")

    scheduler = IntervalScheduler(
        issue_queue=issue_queue,
        escalation_threshold=2,
    )
    scheduler.register(ScheduledWorker("bad-worker", 0.05, always_fails))
    await scheduler.start()
    await asyncio.sleep(0.3)  # enough for >= 2 failures
    await scheduler.stop()

    issues = issue_queue.list(status="open", type="worker-failure")
    assert len(issues) == 1
    assert "bad-worker" in issues[0].title
    assert issues[0].severity == "moderate"


@pytest.mark.asyncio
async def test_scheduler_resolves_issue_on_recovery(tmp_path):
    """Issue is auto-resolved when the worker recovers after threshold crossing."""
    from llm_wiki.issues.queue import IssueQueue

    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    issue_queue = IssueQueue(wiki_dir)

    fail_count = {"n": 0}

    async def fails_then_recovers():
        if fail_count["n"] < 2:
            fail_count["n"] += 1
            raise RuntimeError("temporary failure")

    scheduler = IntervalScheduler(
        issue_queue=issue_queue,
        escalation_threshold=2,
    )
    scheduler.register(ScheduledWorker("recover-worker", 0.05, fails_then_recovers))
    await scheduler.start()
    await asyncio.sleep(0.5)
    await scheduler.stop()

    # Issue should exist but be resolved
    issues_open = issue_queue.list(status="open", type="worker-failure")
    issues_resolved = issue_queue.list(status="resolved", type="worker-failure")
    assert len(issues_open) == 0
    assert len(issues_resolved) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_daemon/test_scheduler.py::test_scheduler_files_issue_on_threshold_crossing tests/test_daemon/test_scheduler.py::test_scheduler_resolves_issue_on_recovery -v
```

Expected: `FAILED` — `IntervalScheduler` doesn't accept `issue_queue` or `escalation_threshold`.

- [ ] **Step 3: Add escalation_threshold to config.py**

In `src/llm_wiki/config.py`, add to `MaintenanceConfig`:

```python
@dataclass
class MaintenanceConfig:
    librarian_interval: str = "6h"
    adversary_interval: str = "12h"
    adversary_claims_per_run: int = 5
    auditor_interval: str = "24h"
    authority_recalc: str = "12h"
    compliance_debounce_secs: float = 30.0
    talk_pages_enabled: bool = True
    talk_summary_min_new_entries: int = 5
    talk_summary_min_interval_seconds: int = 3600
    failure_escalation_threshold: int = 3  # <- add this
```

- [ ] **Step 4: Implement escalation in scheduler.py**

Add imports at the top of `scheduler.py`:
```python
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from llm_wiki.issues.queue import IssueQueue
```

Change `__init__()` signature and body:
```python
    def __init__(
        self,
        issue_queue: IssueQueue | None = None,
        escalation_threshold: int = 3,
    ) -> None:
        self._workers: list[ScheduledWorker] = []
        self._tasks: dict[str, asyncio.Task] = {}
        self._last_run: dict[str, str] = {}
        self._last_attempt: dict[str, str] = {}
        self._consecutive_failures: dict[str, int] = {}
        self._backend_reachable: dict[str, bool | None] = {}
        self._issue_queue = issue_queue
        self._escalation_threshold = escalation_threshold
        self._escalation_issue_ids: dict[str, str] = {}  # worker_name -> open issue id
        self._stopping = False
```

Add `_maybe_escalate()` method:
```python
    async def _maybe_escalate(
        self, worker: ScheduledWorker, failures: int, exc: Exception
    ) -> None:
        """File or resolve an issue based on consecutive_failures crossing the threshold."""
        if self._issue_queue is None:
            return

        from llm_wiki.issues.queue import Issue, IssueQueue
        import datetime as _dt

        if failures == self._escalation_threshold:
            # Threshold just crossed: file a new issue
            key = f"{worker.name}-{_dt.datetime.now(_dt.timezone.utc).strftime('%Y%m%dT%H%M%S')}"
            issue = Issue(
                id=Issue.make_id("worker-failure", None, key),
                type="worker-failure",
                status="open",
                title=f"[{worker.name}] has failed {failures} consecutive runs",
                page=None,
                body=(
                    f"Last error type: {type(exc).__name__}\n"
                    f"Last error: {exc}\n\n"
                    f"The worker will retry on its next interval. "
                    f"Check that the configured LLM backend is reachable."
                ),
                created=Issue.now_iso(),
                detected_by="scheduler",
                severity="moderate",
            )
            _, was_new = self._issue_queue.add(issue)
            if was_new:
                self._escalation_issue_ids[worker.name] = issue.id
                logger.warning(
                    "Worker %r: filed issue %s after %d consecutive failures",
                    worker.name, issue.id, failures,
                )
```

Update `_run_once()` to call `_maybe_escalate` on failure and auto-resolve on success:
```python
    async def _run_once(self, worker: ScheduledWorker) -> None:
        # Health probe — skip run (not fail) if backend is unreachable
        if worker.health_probe_url is not None:
            reachable = await _probe_backend(worker.health_probe_url)
            self._backend_reachable[worker.name] = reachable
            if not reachable:
                logger.info(
                    "[%s] backend unreachable at %s, skipping run",
                    worker.name, worker.health_probe_url,
                )
                return

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._last_attempt[worker.name] = now
        try:
            await worker.coro_factory()
            self._last_run[worker.name] = now
            prev_failures = self._consecutive_failures.get(worker.name, 0)
            self._consecutive_failures[worker.name] = 0
            # Auto-resolve any open escalation issue for this worker
            if prev_failures >= self._escalation_threshold and self._issue_queue is not None:
                issue_id = self._escalation_issue_ids.pop(worker.name, None)
                if issue_id:
                    self._issue_queue.update_status(issue_id, "resolved")
                    logger.info("Worker %r recovered; resolved issue %s", worker.name, issue_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failures = self._consecutive_failures.get(worker.name, 0) + 1
            self._consecutive_failures[worker.name] = failures
            logger.exception(
                "Worker %r raised (consecutive_failures=%d); will retry on next interval",
                worker.name, failures,
            )
            await self._maybe_escalate(worker, failures, exc)
```

- [ ] **Step 5: Pass issue_queue and threshold from server.py**

In `src/llm_wiki/daemon/server.py`, find where `self._scheduler = IntervalScheduler()` is called (in `start()` or `_register_maintenance_workers()`). Change it to:

```python
from llm_wiki.issues.queue import IssueQueue

wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
issue_queue = IssueQueue(wiki_dir)
self._scheduler = IntervalScheduler(
    issue_queue=issue_queue,
    escalation_threshold=self._config.maintenance.failure_escalation_threshold,
)
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
pytest tests/test_daemon/test_scheduler.py -v
```

Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/llm_wiki/config.py src/llm_wiki/daemon/scheduler.py src/llm_wiki/daemon/server.py tests/test_daemon/test_scheduler.py
git commit -m "feat: scheduler escalates to wiki issue after N consecutive worker failures"
```

---

## Task 10: scheduler.py + server.py — Health field in status responses

**Files:**
- Modify: `src/llm_wiki/daemon/scheduler.py`
- Modify: `src/llm_wiki/daemon/server.py`
- Modify: `src/llm_wiki/cli/main.py` (`maintenance_status` display)
- Test: `tests/test_daemon/test_scheduler.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_daemon/test_scheduler.py

def test_scheduler_health_info_structure():
    """health_info() returns per-worker dict with expected keys."""
    scheduler = IntervalScheduler()

    async def noop():
        pass

    scheduler.register(ScheduledWorker("worker-a", 1.0, noop))
    scheduler.register(ScheduledWorker("worker-b", 2.0, noop, health_probe_url="http://x/v1"))

    info = scheduler.health_info()
    assert "worker-a" in info
    assert "worker-b" in info

    for name in ("worker-a", "worker-b"):
        entry = info[name]
        assert "last_run" in entry
        assert "last_attempt" in entry
        assert "consecutive_failures" in entry
        assert "backend_reachable" in entry

    assert info["worker-a"]["last_run"] is None  # never ran
    assert info["worker-a"]["consecutive_failures"] == 0


@pytest.mark.asyncio
async def test_scheduler_status_route_includes_health(sample_vault, tmp_path):
    """The scheduler-status daemon route includes health fields per worker."""
    from llm_wiki.config import MaintenanceConfig, WikiConfig
    from llm_wiki.daemon.client import DaemonClient
    from llm_wiki.daemon.server import DaemonServer

    sock_path = tmp_path / "health-test.sock"
    config = WikiConfig(maintenance=MaintenanceConfig(auditor_interval="1s"))
    server = DaemonServer(sample_vault, sock_path, config=config)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    try:
        await asyncio.sleep(0.2)
        client = DaemonClient(sock_path)
        resp = client.request({"type": "scheduler-status"})

        assert resp["status"] == "ok"
        auditor = next(w for w in resp["workers"] if w["name"] == "auditor")
        assert "last_attempt" in auditor
        assert "consecutive_failures" in auditor
        assert "backend_reachable" in auditor
    finally:
        server._server.close()
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        await server.stop()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_daemon/test_scheduler.py::test_scheduler_health_info_structure tests/test_daemon/test_scheduler.py::test_scheduler_status_route_includes_health -v
```

Expected: `FAILED` — `health_info()` doesn't exist.

- [ ] **Step 3: Add health_info() to IntervalScheduler**

```python
    def health_info(self) -> dict[str, dict]:
        """Return health snapshot for all registered workers."""
        return {
            worker.name: {
                "last_run": self._last_run.get(worker.name),
                "last_attempt": self._last_attempt.get(worker.name),
                "consecutive_failures": self._consecutive_failures.get(worker.name, 0),
                "backend_reachable": self._backend_reachable.get(worker.name),
            }
            for worker in self._workers
        }
```

- [ ] **Step 4: Extend _handle_scheduler_status() in server.py**

Find `_handle_scheduler_status()` in `src/llm_wiki/daemon/server.py` (currently around line 689) and extend the per-worker dict:

```python
    def _handle_scheduler_status(self) -> dict:
        if self._scheduler is None:
            return {"status": "ok", "workers": []}
        health = self._scheduler.health_info()
        workers = [
            {
                "name": name,
                "interval_seconds": interval_seconds,
                "last_run": last_run,
                "last_attempt": health.get(name, {}).get("last_attempt"),
                "consecutive_failures": health.get(name, {}).get("consecutive_failures", 0),
                "backend_reachable": health.get(name, {}).get("backend_reachable"),
            }
            for name, interval_seconds, last_run in self._scheduler.workers_info()
        ]
        return {"status": "ok", "workers": workers}
```

- [ ] **Step 5: Update maintenance_status CLI display**

In `src/llm_wiki/cli/main.py`, find `maintenance_status` and extend the table output:

```python
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
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
pytest tests/test_daemon/test_scheduler.py -v
```

Expected: all pass

- [ ] **Step 7: Run the full test suite**

```bash
pytest -x -q
```

Expected: all pass. Fix any regressions before proceeding.

- [ ] **Step 8: Commit**

```bash
git add src/llm_wiki/daemon/scheduler.py src/llm_wiki/daemon/server.py src/llm_wiki/cli/main.py tests/test_daemon/test_scheduler.py
git commit -m "feat: scheduler-status and maintenance status expose health fields per worker"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ Vault validation guard (Tasks 1, 2)
- ✅ CLI default vault path (Task 3)
- ✅ Daemon startup error visibility (Task 4)
- ✅ Mutual exclusion via alive-daemon check (Task 5)
- ✅ Retry with backoff (Task 6)
- ✅ last_attempt + consecutive_failures tracking (Task 7)
- ✅ Health probe per worker (Task 8)
- ✅ Escalation on repeated failure with auto-resolve (Task 9)
- ✅ health field in status responses (Task 10)
- ✅ failure_escalation_threshold in MaintenanceConfig (Task 9)
- ✅ No circuit breaker (excluded per spec)

**Type consistency:**
- `IntervalScheduler` constructor: `issue_queue: IssueQueue | None`, `escalation_threshold: int` — consistent across Tasks 7-10
- `ScheduledWorker.health_probe_url: str | None` — added in Task 8, used in Tasks 8-10
- `_should_retry(exc: Exception) -> bool` — defined in Task 6, used in Task 6 only
- `health_info() -> dict[str, dict]` — defined in Task 10, used in Task 10

**Note on sample_vault fixture:** Tasks 2 and 10 use `sample_vault`. If the fixture doesn't have a `wiki/` or `raw/` directory, Task 2's vault validation guard will reject it. Check `tests/conftest.py` after Task 2 and add the directory if needed.
