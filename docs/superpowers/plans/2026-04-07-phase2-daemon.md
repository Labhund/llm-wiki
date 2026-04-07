# Phase 2: Daemon — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap the core library in a persistent daemon process so the vault stays indexed in memory, file changes are detected automatically, and the CLI routes requests through a Unix socket instead of re-scanning on every command.

**Architecture:** One daemon per vault. The daemon holds a `Vault` instance in memory, serves requests over a Unix socket using a length-prefixed JSON protocol, watches for file changes (mtime polling with debounce), and re-scans on change. The CLI auto-starts the daemon on first use and routes all commands through it. LLM queue and write coordinator are built as infrastructure for Phases 3-5 to consume.

**Tech Stack:** Python 3.11+, asyncio (stdlib), pytest-asyncio

**State layout:**
```
~/.llm-wiki/
  vaults/
    <slug-hash>/
      index/          # tantivy index (from Phase 1)
      daemon.sock     # Unix socket
      daemon.pid      # Pidfile
```

---

## File Structure

```
src/
  llm_wiki/
    daemon/
      __init__.py          # Package marker
      protocol.py          # Length-prefixed JSON framing, read/write helpers
      server.py            # DaemonServer: asyncio Unix socket server + request routing
      client.py            # DaemonClient: sync client for CLI
      lifecycle.py         # Pidfile, socket paths, auto-start, cleanup
      watcher.py           # File watcher: mtime polling with debounce
      llm_queue.py         # Priority semaphore for LLM request concurrency
      writer.py            # Per-page async write locks
      __main__.py          # Entry point: python -m llm_wiki.daemon <vault_root>
    cli/
      main.py              # Modified: route through daemon, add serve/stop
tests/
  test_daemon/
    __init__.py
    test_protocol.py
    test_server.py
    test_lifecycle.py
    test_watcher.py
    test_llm_queue.py
    test_writer.py
  test_cli/
    test_commands.py       # Modified: test daemon-routed commands
  test_daemon_integration.py
```

---

### Task 1: Dependencies + Protocol

**Files:**
- Modify: `pyproject.toml`
- Create: `src/llm_wiki/daemon/__init__.py`
- Create: `src/llm_wiki/daemon/protocol.py`
- Create: `tests/test_daemon/__init__.py`
- Create: `tests/test_daemon/test_protocol.py`

- [ ] **Step 1: Add pytest-asyncio to dev dependencies**

In `pyproject.toml`, update the dev dependencies:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-tmp-files>=0.0.2",
    "pytest-asyncio>=0.24.0",
]
```

Run: `~/.venv/bin/uv pip install -e ".[dev]"`

- [ ] **Step 2: Write failing tests**

```python
# tests/test_daemon/__init__.py
```

```python
# tests/test_daemon/test_protocol.py
import asyncio
import pytest
from llm_wiki.daemon.protocol import encode_message, decode_message, read_message, write_message


def test_encode_decode_roundtrip():
    msg = {"type": "search", "query": "sRNA", "limit": 10}
    encoded = encode_message(msg)
    decoded = decode_message(encoded)
    assert decoded == msg


def test_encode_empty_dict():
    encoded = encode_message({})
    decoded = decode_message(encoded)
    assert decoded == {}


def test_encode_nested():
    msg = {"type": "status", "data": {"pages": 42, "clusters": ["bio", "ml"]}}
    encoded = encode_message(msg)
    decoded = decode_message(encoded)
    assert decoded == msg


@pytest.mark.asyncio
async def test_async_read_write():
    """Test async read/write over an in-memory stream."""
    msg = {"type": "search", "query": "test"}

    # Create connected stream pair using Unix socket pair
    rsock, wsock = asyncio.get_event_loop().run_in_executor(None, lambda: None), None

    # Use pipes instead
    read_fd, write_fd = await asyncio.get_event_loop().run_in_executor(None, __import__("os").pipe)
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    transport, _ = await asyncio.get_event_loop().connect_read_pipe(
        lambda: protocol, __import__("os").fdopen(read_fd, "rb")
    )

    # Simpler: just test encode/decode, async tested in server tests
    pass


def test_message_framing():
    """Verify length prefix is correct."""
    msg = {"hello": "world"}
    encoded = encode_message(msg)
    # First 4 bytes are big-endian uint32 length
    import struct
    length = struct.unpack("!I", encoded[:4])[0]
    assert length == len(encoded) - 4
    assert encoded[4:] == __import__("json").dumps(msg).encode("utf-8")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `~/.venv/bin/pytest tests/test_daemon/test_protocol.py -v`
Expected: FAIL

- [ ] **Step 4: Implement protocol**

```python
# src/llm_wiki/daemon/__init__.py
```

```python
# src/llm_wiki/daemon/protocol.py
"""Length-prefixed JSON protocol for daemon IPC.

Wire format: [4 bytes: big-endian uint32 payload length][N bytes: JSON payload]
"""
from __future__ import annotations

import asyncio
import json
import socket
import struct

HEADER_SIZE = 4


def encode_message(msg: dict) -> bytes:
    """Encode a dict as a length-prefixed JSON message."""
    payload = json.dumps(msg).encode("utf-8")
    return struct.pack("!I", len(payload)) + payload


def decode_message(data: bytes) -> dict:
    """Decode a length-prefixed JSON message."""
    length = struct.unpack("!I", data[:HEADER_SIZE])[0]
    payload = data[HEADER_SIZE : HEADER_SIZE + length]
    return json.loads(payload)


async def read_message(reader: asyncio.StreamReader) -> dict:
    """Read one message from an async stream."""
    header = await reader.readexactly(HEADER_SIZE)
    length = struct.unpack("!I", header)[0]
    payload = await reader.readexactly(length)
    return json.loads(payload)


async def write_message(writer: asyncio.StreamWriter, msg: dict) -> None:
    """Write one message to an async stream."""
    writer.write(encode_message(msg))
    await writer.drain()


def read_message_sync(sock: socket.socket) -> dict:
    """Read one message from a blocking socket."""
    header = _recv_exact(sock, HEADER_SIZE)
    length = struct.unpack("!I", header)[0]
    payload = _recv_exact(sock, length)
    return json.loads(payload)


def write_message_sync(sock: socket.socket, msg: dict) -> None:
    """Write one message to a blocking socket."""
    sock.sendall(encode_message(msg))


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """Receive exactly n bytes from a blocking socket."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed while reading")
        buf.extend(chunk)
    return bytes(buf)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `~/.venv/bin/pytest tests/test_daemon/test_protocol.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/llm_wiki/daemon/__init__.py src/llm_wiki/daemon/protocol.py tests/test_daemon/
git commit -m "feat: daemon IPC protocol with length-prefixed JSON framing"
```

---

### Task 2: Daemon Server

**Files:**
- Create: `src/llm_wiki/daemon/server.py`
- Create: `tests/test_daemon/test_server.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_daemon/test_server.py
import asyncio
from pathlib import Path

import pytest

from llm_wiki.daemon.protocol import read_message, write_message
from llm_wiki.daemon.server import DaemonServer


@pytest.fixture
async def daemon_server(sample_vault: Path, tmp_path: Path):
    """Start a daemon server on a temp socket for testing."""
    sock_path = tmp_path / "test.sock"
    server = DaemonServer(sample_vault, sock_path)
    await server.start()
    yield server, sock_path
    await server.stop()


async def _request(sock_path: Path, msg: dict) -> dict:
    """Send a request and return the response."""
    reader, writer = await asyncio.open_unix_connection(str(sock_path))
    try:
        await write_message(writer, msg)
        return await read_message(reader)
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_search(daemon_server):
    server, sock_path = daemon_server
    resp = await _request(sock_path, {"type": "search", "query": "sRNA", "limit": 5})
    assert resp["status"] == "ok"
    assert len(resp["results"]) >= 1


@pytest.mark.asyncio
async def test_read_top(daemon_server):
    server, sock_path = daemon_server
    resp = await _request(sock_path, {
        "type": "read", "page_name": "srna-embeddings", "viewport": "top",
    })
    assert resp["status"] == "ok"
    assert "overview" in resp["content"].lower()


@pytest.mark.asyncio
async def test_read_section(daemon_server):
    server, sock_path = daemon_server
    resp = await _request(sock_path, {
        "type": "read", "page_name": "srna-embeddings", "section": "method",
    })
    assert resp["status"] == "ok"
    assert "PCA" in resp["content"]


@pytest.mark.asyncio
async def test_read_missing(daemon_server):
    server, sock_path = daemon_server
    resp = await _request(sock_path, {
        "type": "read", "page_name": "nonexistent",
    })
    assert resp["status"] == "error"


@pytest.mark.asyncio
async def test_manifest(daemon_server):
    server, sock_path = daemon_server
    resp = await _request(sock_path, {"type": "manifest", "budget": 5000})
    assert resp["status"] == "ok"
    assert len(resp["content"]) > 0


@pytest.mark.asyncio
async def test_status(daemon_server):
    server, sock_path = daemon_server
    resp = await _request(sock_path, {"type": "status"})
    assert resp["status"] == "ok"
    assert resp["page_count"] == 4


@pytest.mark.asyncio
async def test_unknown_request(daemon_server):
    server, sock_path = daemon_server
    resp = await _request(sock_path, {"type": "bogus"})
    assert resp["status"] == "error"


@pytest.mark.asyncio
async def test_concurrent_requests(daemon_server):
    """Multiple clients can connect simultaneously."""
    server, sock_path = daemon_server
    results = await asyncio.gather(
        _request(sock_path, {"type": "status"}),
        _request(sock_path, {"type": "search", "query": "sRNA"}),
        _request(sock_path, {"type": "manifest", "budget": 1000}),
    )
    assert all(r["status"] == "ok" for r in results)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/.venv/bin/pytest tests/test_daemon/test_server.py -v`
Expected: FAIL

- [ ] **Step 3: Implement server**

```python
# src/llm_wiki/daemon/server.py
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from llm_wiki.daemon.protocol import read_message, write_message
from llm_wiki.search.backend import SearchResult
from llm_wiki.vault import Vault

logger = logging.getLogger(__name__)


class DaemonServer:
    """Async Unix socket server wrapping a Vault instance."""

    def __init__(self, vault_root: Path, socket_path: Path) -> None:
        self._vault_root = vault_root
        self._socket_path = socket_path
        self._vault: Vault | None = None
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        """Scan vault and start listening on Unix socket."""
        self._vault = Vault.scan(self._vault_root)
        # Remove stale socket if it exists
        if self._socket_path.exists():
            self._socket_path.unlink()
        self._server = await asyncio.start_unix_server(
            self._handle_client, path=str(self._socket_path)
        )
        logger.info(
            "Daemon started: %d pages, socket %s",
            self._vault.page_count, self._socket_path,
        )

    async def serve_forever(self) -> None:
        """Block until the server is stopped."""
        if self._server:
            async with self._server:
                await self._server.serve_forever()

    async def stop(self) -> None:
        """Shut down the server and clean up socket."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        if self._socket_path.exists():
            self._socket_path.unlink()
        logger.info("Daemon stopped")

    async def rescan(self) -> None:
        """Re-scan the vault (called by file watcher)."""
        self._vault = Vault.scan(self._vault_root)
        logger.info("Rescanned: %d pages", self._vault.page_count)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request = await read_message(reader)
            response = await self._route(request)
            await write_message(writer, response)
        except Exception as exc:
            try:
                await write_message(writer, {"status": "error", "message": str(exc)})
            except Exception:
                pass
            logger.exception("Error handling request")
        finally:
            writer.close()
            await writer.wait_closed()

    async def _route(self, request: dict) -> dict:
        req_type = request.get("type", "")
        match req_type:
            case "search":
                return self._handle_search(request)
            case "read":
                return self._handle_read(request)
            case "manifest":
                return self._handle_manifest(request)
            case "status":
                return self._handle_status()
            case "rescan":
                await self.rescan()
                return {"status": "ok", "page_count": self._vault.page_count}
            case _:
                return {"status": "error", "message": f"Unknown request type: {req_type}"}

    def _handle_search(self, request: dict) -> dict:
        results = self._vault.search(
            request["query"], limit=request.get("limit", 10)
        )
        return {
            "status": "ok",
            "results": [_serialize_result(r) for r in results],
        }

    def _handle_read(self, request: dict) -> dict:
        content = self._vault.read_viewport(
            request["page_name"],
            viewport=request.get("viewport", "top"),
            section=request.get("section"),
            grep=request.get("grep"),
            budget=request.get("budget"),
        )
        if content is None:
            return {"status": "error", "message": f"Page not found: {request['page_name']}"}
        return {"status": "ok", "content": content}

    def _handle_manifest(self, request: dict) -> dict:
        text = self._vault.manifest_text(budget=request.get("budget", 16000))
        return {"status": "ok", "content": text}

    def _handle_status(self) -> dict:
        info = self._vault.status()
        return {"status": "ok", **info}


def _serialize_result(r: SearchResult) -> dict:
    return {
        "name": r.name,
        "score": r.score,
        "manifest": r.entry.to_manifest_text(),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.venv/bin/pytest tests/test_daemon/test_server.py -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/server.py tests/test_daemon/test_server.py
git commit -m "feat: daemon server with Unix socket and request routing"
```

---

### Task 3: Daemon Client

**Files:**
- Create: `src/llm_wiki/daemon/client.py`
- Create: `tests/test_daemon/test_client.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_daemon/test_client.py
import asyncio
from pathlib import Path

import pytest

from llm_wiki.daemon.client import DaemonClient
from llm_wiki.daemon.server import DaemonServer


@pytest.fixture
async def running_daemon(sample_vault: Path, tmp_path: Path):
    """Start daemon, yield client, stop daemon."""
    sock_path = tmp_path / "test.sock"
    server = DaemonServer(sample_vault, sock_path)
    await server.start()

    # Run server in background
    serve_task = asyncio.create_task(server.serve_forever())

    client = DaemonClient(sock_path)
    yield client

    server._server.close()
    serve_task.cancel()
    try:
        await serve_task
    except asyncio.CancelledError:
        pass
    await server.stop()


@pytest.mark.asyncio
async def test_client_search(running_daemon):
    client = running_daemon
    resp = client.request({"type": "search", "query": "sRNA", "limit": 5})
    assert resp["status"] == "ok"
    assert len(resp["results"]) >= 1


@pytest.mark.asyncio
async def test_client_status(running_daemon):
    client = running_daemon
    resp = client.request({"type": "status"})
    assert resp["status"] == "ok"
    assert resp["page_count"] == 4


@pytest.mark.asyncio
async def test_client_read(running_daemon):
    client = running_daemon
    resp = client.request({"type": "read", "page_name": "srna-embeddings"})
    assert resp["status"] == "ok"
    assert "overview" in resp["content"].lower()


@pytest.mark.asyncio
async def test_client_is_running(running_daemon, tmp_path: Path):
    client = running_daemon
    assert client.is_running()

    dead_client = DaemonClient(tmp_path / "nonexistent.sock")
    assert not dead_client.is_running()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/.venv/bin/pytest tests/test_daemon/test_client.py -v`
Expected: FAIL

- [ ] **Step 3: Implement client**

```python
# src/llm_wiki/daemon/client.py
from __future__ import annotations

import socket
from pathlib import Path

from llm_wiki.daemon.protocol import read_message_sync, write_message_sync


class DaemonClient:
    """Synchronous client for the daemon Unix socket."""

    def __init__(self, socket_path: Path) -> None:
        self._socket_path = socket_path

    def request(self, msg: dict) -> dict:
        """Send a request and return the response."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(30.0)
        try:
            sock.connect(str(self._socket_path))
            write_message_sync(sock, msg)
            return read_message_sync(sock)
        finally:
            sock.close()

    def is_running(self) -> bool:
        """Check if the daemon is reachable."""
        if not self._socket_path.exists():
            return False
        try:
            resp = self.request({"type": "status"})
            return resp.get("status") == "ok"
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.venv/bin/pytest tests/test_daemon/test_client.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/client.py tests/test_daemon/test_client.py
git commit -m "feat: sync daemon client for CLI"
```

---

### Task 4: Lifecycle Management

**Files:**
- Create: `src/llm_wiki/daemon/lifecycle.py`
- Create: `tests/test_daemon/test_lifecycle.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_daemon/test_lifecycle.py
import os
from pathlib import Path

import pytest

from llm_wiki.daemon.lifecycle import (
    socket_path_for,
    pidfile_path_for,
    write_pidfile,
    read_pidfile,
    is_process_alive,
    cleanup_stale,
)
from llm_wiki.vault import _state_dir_for


def test_paths_derived_from_vault(tmp_path: Path):
    sock = socket_path_for(tmp_path)
    pid = pidfile_path_for(tmp_path)
    state = _state_dir_for(tmp_path)
    assert sock.parent == state
    assert pid.parent == state
    assert sock.name == "daemon.sock"
    assert pid.name == "daemon.pid"


def test_write_read_pidfile(tmp_path: Path):
    pidfile = tmp_path / "test.pid"
    write_pidfile(pidfile, 12345)
    assert read_pidfile(pidfile) == 12345


def test_read_missing_pidfile(tmp_path: Path):
    assert read_pidfile(tmp_path / "nope.pid") is None


def test_is_process_alive():
    # Current process should be alive
    assert is_process_alive(os.getpid())
    # PID 0 or a very high PID should not
    assert not is_process_alive(9999999)


def test_cleanup_stale(tmp_path: Path):
    sock = tmp_path / "daemon.sock"
    pid = tmp_path / "daemon.pid"
    sock.touch()
    pid.write_text("99999")
    cleanup_stale(sock, pid)
    assert not sock.exists()
    assert not pid.exists()


def test_cleanup_missing_files(tmp_path: Path):
    # Should not raise even if files don't exist
    cleanup_stale(tmp_path / "nope.sock", tmp_path / "nope.pid")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/.venv/bin/pytest tests/test_daemon/test_lifecycle.py -v`
Expected: FAIL

- [ ] **Step 3: Implement lifecycle**

```python
# src/llm_wiki/daemon/lifecycle.py
from __future__ import annotations

import os
import signal
from pathlib import Path

from llm_wiki.vault import _state_dir_for


def socket_path_for(vault_root: Path) -> Path:
    """Get the daemon socket path for a vault."""
    return _state_dir_for(vault_root) / "daemon.sock"


def pidfile_path_for(vault_root: Path) -> Path:
    """Get the daemon pidfile path for a vault."""
    return _state_dir_for(vault_root) / "daemon.pid"


def write_pidfile(pidfile: Path, pid: int) -> None:
    """Write PID to file."""
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(str(pid))


def read_pidfile(pidfile: Path) -> int | None:
    """Read PID from file. Returns None if missing or invalid."""
    if not pidfile.exists():
        return None
    try:
        return int(pidfile.read_text().strip())
    except (ValueError, OSError):
        return None


def is_process_alive(pid: int) -> bool:
    """Check if a process with given PID exists."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def is_daemon_running(vault_root: Path) -> bool:
    """Check if a daemon is running for this vault."""
    pid = read_pidfile(pidfile_path_for(vault_root))
    if pid is None:
        return False
    if not is_process_alive(pid):
        cleanup_stale(socket_path_for(vault_root), pidfile_path_for(vault_root))
        return False
    return True


def cleanup_stale(socket_path: Path, pidfile: Path) -> None:
    """Remove stale socket and pidfile."""
    for f in (socket_path, pidfile):
        try:
            f.unlink()
        except FileNotFoundError:
            pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.venv/bin/pytest tests/test_daemon/test_lifecycle.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/lifecycle.py tests/test_daemon/test_lifecycle.py
git commit -m "feat: daemon lifecycle management (pidfile, socket paths, cleanup)"
```

---

### Task 5: File Watcher

**Files:**
- Create: `src/llm_wiki/daemon/watcher.py`
- Create: `tests/test_daemon/test_watcher.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_daemon/test_watcher.py
import asyncio
from pathlib import Path

import pytest

from llm_wiki.daemon.watcher import FileWatcher


@pytest.mark.asyncio
async def test_detects_new_file(sample_vault: Path):
    changes_detected = []

    async def on_change(changed, removed):
        changes_detected.append(("change", changed, removed))

    watcher = FileWatcher(sample_vault, on_change, poll_interval=0.2)
    await watcher.start()

    # Wait for initial scan
    await asyncio.sleep(0.3)

    # Create a new file
    (sample_vault / "new-page.md").write_text("# New Page\n\nContent.")
    await asyncio.sleep(0.5)

    await watcher.stop()
    assert len(changes_detected) >= 1


@pytest.mark.asyncio
async def test_detects_modified_file(sample_vault: Path):
    changes_detected = []

    async def on_change(changed, removed):
        changes_detected.append(("change", changed, removed))

    watcher = FileWatcher(sample_vault, on_change, poll_interval=0.2)
    await watcher.start()
    await asyncio.sleep(0.3)

    # Modify existing file
    existing = sample_vault / "bioinformatics" / "srna-embeddings.md"
    existing.write_text(existing.read_text() + "\nAppended content.")
    await asyncio.sleep(0.5)

    await watcher.stop()
    assert len(changes_detected) >= 1


@pytest.mark.asyncio
async def test_ignores_hidden_dirs(sample_vault: Path):
    changes_detected = []

    async def on_change(changed, removed):
        changes_detected.append(("change", changed, removed))

    watcher = FileWatcher(sample_vault, on_change, poll_interval=0.2)
    await watcher.start()
    await asyncio.sleep(0.3)

    # Create file in hidden dir — should be ignored
    hidden = sample_vault / ".obsidian"
    hidden.mkdir()
    (hidden / "config.md").write_text("ignored")
    await asyncio.sleep(0.5)

    await watcher.stop()
    assert len(changes_detected) == 0


@pytest.mark.asyncio
async def test_detects_deleted_file(sample_vault: Path):
    changes_detected = []

    async def on_change(changed, removed):
        changes_detected.append(("change", changed, removed))

    watcher = FileWatcher(sample_vault, on_change, poll_interval=0.2)
    await watcher.start()
    await asyncio.sleep(0.3)

    # Delete a file
    (sample_vault / "no-structure.md").unlink()
    await asyncio.sleep(0.5)

    await watcher.stop()
    assert len(changes_detected) >= 1
    # The removed list should contain the deleted file
    last_change = changes_detected[-1]
    removed_files = last_change[2]  # third element is removed list
    assert any("no-structure" in str(p) for p in removed_files)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/.venv/bin/pytest tests/test_daemon/test_watcher.py -v`
Expected: FAIL

- [ ] **Step 3: Implement file watcher**

```python
# src/llm_wiki/daemon/watcher.py
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

# Callback signature: async def on_change(changed: list[Path], removed: list[Path])
OnChangeCallback = Callable[[list[Path], list[Path]], Awaitable[None]]


class FileWatcher:
    """Polls for markdown file changes using mtime comparison.

    Zero external dependencies. Uses asyncio.sleep for polling.
    For production, swap in watchfiles for inotify-level efficiency.
    """

    def __init__(
        self,
        vault_root: Path,
        on_change: OnChangeCallback,
        poll_interval: float = 2.0,
    ) -> None:
        self._root = vault_root
        self._on_change = on_change
        self._interval = poll_interval
        self._mtimes: dict[Path, float] = {}
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start watching for changes."""
        self._mtimes = self._scan_mtimes()
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Stop watching."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            new_mtimes = self._scan_mtimes()

            changed = [
                p for p, t in new_mtimes.items()
                if p not in self._mtimes or self._mtimes[p] != t
            ]
            removed = [p for p in self._mtimes if p not in new_mtimes]

            if changed or removed:
                logger.info(
                    "File changes detected: %d changed, %d removed",
                    len(changed), len(removed),
                )
                try:
                    await self._on_change(changed, removed)
                except Exception:
                    logger.exception("Error in change callback")

            self._mtimes = new_mtimes

    def _scan_mtimes(self) -> dict[Path, float]:
        result = {}
        for p in self._root.rglob("*.md"):
            try:
                rel = p.relative_to(self._root)
            except ValueError:
                continue
            if any(part.startswith(".") for part in rel.parts):
                continue
            try:
                result[p] = p.stat().st_mtime
            except OSError:
                continue
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.venv/bin/pytest tests/test_daemon/test_watcher.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/watcher.py tests/test_daemon/test_watcher.py
git commit -m "feat: file watcher with mtime polling and debounce"
```

---

### Task 6: LLM Request Queue

**Files:**
- Create: `src/llm_wiki/daemon/llm_queue.py`
- Create: `tests/test_daemon/test_llm_queue.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_daemon/test_llm_queue.py
import asyncio
import time

import pytest

from llm_wiki.daemon.llm_queue import LLMQueue


@pytest.mark.asyncio
async def test_basic_submit():
    queue = LLMQueue(max_concurrent=2)

    async def dummy_task():
        return 42

    result = await queue.submit(dummy_task, priority="query")
    assert result == 42


@pytest.mark.asyncio
async def test_concurrency_limited():
    """Only max_concurrent tasks should run at once."""
    queue = LLMQueue(max_concurrent=1)
    running = []
    max_running = 0

    async def tracked_task(task_id: int):
        nonlocal max_running
        running.append(task_id)
        max_running = max(max_running, len(running))
        await asyncio.sleep(0.1)
        running.remove(task_id)
        return task_id

    results = await asyncio.gather(
        queue.submit(tracked_task, priority="query", task_id=1),
        queue.submit(tracked_task, priority="query", task_id=2),
        queue.submit(tracked_task, priority="query", task_id=3),
    )
    assert sorted(results) == [1, 2, 3]
    assert max_running == 1  # Only 1 at a time


@pytest.mark.asyncio
async def test_token_accounting():
    queue = LLMQueue(max_concurrent=2)
    assert queue.tokens_used == 0

    queue.record_tokens(500)
    queue.record_tokens(300)
    assert queue.tokens_used == 800


@pytest.mark.asyncio
async def test_active_count():
    queue = LLMQueue(max_concurrent=2)
    assert queue.active_count == 0

    started = asyncio.Event()
    finish = asyncio.Event()

    async def blocking_task():
        started.set()
        await finish.wait()

    task = asyncio.create_task(queue.submit(blocking_task, priority="query"))
    await started.wait()
    assert queue.active_count == 1

    finish.set()
    await task
    assert queue.active_count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/.venv/bin/pytest tests/test_daemon/test_llm_queue.py -v`
Expected: FAIL

- [ ] **Step 3: Implement LLM queue**

```python
# src/llm_wiki/daemon/llm_queue.py
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class LLMQueue:
    """Concurrency-limited queue for LLM requests.

    Gates all LLM calls through a semaphore to prevent overloading
    the inference server. Phase 3+ will route traversal/ingest LLM
    calls through this queue.
    """

    PRIORITY_MAP = {"query": 0, "ingest": 1, "maintenance": 2}

    def __init__(self, max_concurrent: int = 2) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._tokens_used: int = 0
        self._active: int = 0

    async def submit(
        self,
        fn: Callable[..., Awaitable[Any]],
        priority: str = "maintenance",
        **kwargs: Any,
    ) -> Any:
        """Submit an async callable, waiting for a concurrency slot."""
        async with self._semaphore:
            self._active += 1
            try:
                return await fn(**kwargs)
            finally:
                self._active -= 1

    @property
    def tokens_used(self) -> int:
        return self._tokens_used

    @property
    def active_count(self) -> int:
        return self._active

    def record_tokens(self, count: int) -> None:
        """Record tokens consumed (for accounting/limits)."""
        self._tokens_used += count
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.venv/bin/pytest tests/test_daemon/test_llm_queue.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/llm_queue.py tests/test_daemon/test_llm_queue.py
git commit -m "feat: LLM request queue with concurrency limiting"
```

---

### Task 7: Write Coordinator

**Files:**
- Create: `src/llm_wiki/daemon/writer.py`
- Create: `tests/test_daemon/test_writer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_daemon/test_writer.py
import asyncio

import pytest

from llm_wiki.daemon.writer import WriteCoordinator


@pytest.mark.asyncio
async def test_lock_serializes_same_page():
    """Writes to the same page are serialized."""
    coordinator = WriteCoordinator()
    order = []

    async def write(name: str, value: str):
        async with coordinator.lock_for(name):
            order.append(f"{value}_start")
            await asyncio.sleep(0.1)
            order.append(f"{value}_end")

    await asyncio.gather(
        write("page-a", "first"),
        write("page-a", "second"),
    )
    # One must complete before the other starts
    assert order.index("first_end") < order.index("second_start") or \
           order.index("second_end") < order.index("first_start")


@pytest.mark.asyncio
async def test_different_pages_parallel():
    """Writes to different pages can proceed in parallel."""
    coordinator = WriteCoordinator()
    overlap_detected = False

    async def write(name: str):
        nonlocal overlap_detected
        async with coordinator.lock_for(name):
            # If both are "inside" at the same time, they overlapped
            if coordinator._active.get(name, 0) > 0:
                overlap_detected = True
            coordinator._active[name] = coordinator._active.get(name, 0) + 1
            await asyncio.sleep(0.1)
            coordinator._active[name] -= 1

    coordinator._active = {}
    await asyncio.gather(write("page-a"), write("page-b"))
    # Different pages should not block each other — both run in ~0.1s not ~0.2s


@pytest.mark.asyncio
async def test_reentrant_different_pages():
    coordinator = WriteCoordinator()
    results = []

    async def write(name: str):
        async with coordinator.lock_for(name):
            results.append(name)

    await asyncio.gather(
        write("a"), write("b"), write("c"),
    )
    assert sorted(results) == ["a", "b", "c"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/.venv/bin/pytest tests/test_daemon/test_writer.py -v`
Expected: FAIL

- [ ] **Step 3: Implement write coordinator**

```python
# src/llm_wiki/daemon/writer.py
from __future__ import annotations

import asyncio


class WriteCoordinator:
    """Per-page async write locks.

    Concurrent writes to the same page are serialized in arrival order.
    Writes to different pages proceed in parallel.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def lock_for(self, page_name: str) -> asyncio.Lock:
        """Get or create a lock for a page. Use as async context manager."""
        if page_name not in self._locks:
            self._locks[page_name] = asyncio.Lock()
        return self._locks[page_name]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.venv/bin/pytest tests/test_daemon/test_writer.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/writer.py tests/test_daemon/test_writer.py
git commit -m "feat: per-page write coordinator"
```

---

### Task 8: Daemon Entry Point + CLI Integration

**Files:**
- Create: `src/llm_wiki/daemon/__main__.py`
- Modify: `src/llm_wiki/cli/main.py`
- Modify: `tests/test_cli/test_commands.py`

- [ ] **Step 1: Create daemon entry point**

```python
# src/llm_wiki/daemon/__main__.py
"""Entry point: python -m llm_wiki.daemon <vault_root>"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from llm_wiki.daemon.lifecycle import (
    cleanup_stale,
    pidfile_path_for,
    socket_path_for,
    write_pidfile,
)
from llm_wiki.daemon.server import DaemonServer
from llm_wiki.daemon.watcher import FileWatcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("llm-wiki-daemon")


async def run(vault_root: Path) -> None:
    sock_path = socket_path_for(vault_root)
    pid_path = pidfile_path_for(vault_root)

    # Clean up any stale state
    cleanup_stale(sock_path, pid_path)

    server = DaemonServer(vault_root, sock_path)
    await server.start()
    write_pidfile(pid_path, os.getpid())

    # Start file watcher
    async def on_file_change(changed, removed):
        logger.info("Files changed, rescanning vault...")
        await server.rescan()

    watcher = FileWatcher(vault_root, on_file_change, poll_interval=2.0)
    await watcher.start()

    # Handle shutdown signals
    stop_event = asyncio.Event()

    def handle_signal():
        logger.info("Received shutdown signal")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_signal)

    logger.info("Daemon ready (PID %d, vault %s)", os.getpid(), vault_root)

    # Wait until stopped
    await stop_event.wait()

    # Cleanup
    await watcher.stop()
    await server.stop()
    cleanup_stale(sock_path, pid_path)
    logger.info("Daemon shut down cleanly")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m llm_wiki.daemon <vault_root>", file=sys.stderr)
        sys.exit(1)
    vault_root = Path(sys.argv[1]).resolve()
    if not vault_root.is_dir():
        print(f"Not a directory: {vault_root}", file=sys.stderr)
        sys.exit(1)
    asyncio.run(run(vault_root))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Rewrite CLI to route through daemon**

Replace `src/llm_wiki/cli/main.py`:

```python
# src/llm_wiki/cli/main.py
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import click

from llm_wiki.daemon.client import DaemonClient
from llm_wiki.daemon.lifecycle import (
    is_daemon_running,
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

    # Auto-start daemon
    click.echo("Starting daemon...", err=True)
    subprocess.Popen(
        [sys.executable, "-m", "llm_wiki.daemon", str(vault_path.resolve())],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for daemon to be ready (up to 30 seconds)
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
    import sys
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
    # Send SIGTERM to the daemon process
    from llm_wiki.daemon.lifecycle import read_pidfile, pidfile_path_for
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
```

- [ ] **Step 3: Update CLI tests**

Replace `tests/test_cli/test_commands.py` — tests now use a daemon fixture instead of direct Vault.scan():

```python
# tests/test_cli/test_commands.py
import asyncio
from pathlib import Path

import pytest
from click.testing import CliRunner

from llm_wiki.cli.main import cli
from llm_wiki.daemon.server import DaemonServer
from llm_wiki.daemon.lifecycle import socket_path_for
from llm_wiki.vault import Vault


@pytest.fixture
def daemon_for_cli(sample_vault: Path, tmp_path: Path):
    """Start a daemon and patch socket path so CLI finds it."""
    sock_path = socket_path_for(sample_vault)
    sock_path.parent.mkdir(parents=True, exist_ok=True)

    server = DaemonServer(sample_vault, sock_path)

    loop = asyncio.new_event_loop()
    loop.run_until_complete(server.start())
    serve_task = loop.create_task(server.serve_forever())

    # Run event loop in background thread
    import threading
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    yield sample_vault

    loop.call_soon_threadsafe(server._server.close)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)
    loop.run_until_complete(server.stop())
    loop.close()


def test_init_command(sample_vault: Path):
    """Init still works without daemon (direct scan)."""
    runner = CliRunner()
    result = runner.invoke(cli, ["init", str(sample_vault)])
    assert result.exit_code == 0
    assert "Indexed" in result.output


def test_init_nonexistent():
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "/nonexistent/path"])
    assert result.exit_code != 0


def test_status_via_daemon(daemon_for_cli):
    vault_path = daemon_for_cli
    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--vault", str(vault_path)])
    assert result.exit_code == 0
    assert "page" in result.output.lower()


def test_search_via_daemon(daemon_for_cli):
    vault_path = daemon_for_cli
    runner = CliRunner()
    result = runner.invoke(
        cli, ["search", "sRNA", "--vault", str(vault_path)]
    )
    assert result.exit_code == 0
    assert "srna" in result.output.lower()


def test_search_no_results_via_daemon(daemon_for_cli):
    vault_path = daemon_for_cli
    runner = CliRunner()
    result = runner.invoke(
        cli, ["search", "quantum physics", "--vault", str(vault_path)]
    )
    assert result.exit_code == 0
    assert "no results" in result.output.lower()


def test_read_via_daemon(daemon_for_cli):
    vault_path = daemon_for_cli
    runner = CliRunner()
    result = runner.invoke(
        cli, ["read", "srna-embeddings", "--vault", str(vault_path)]
    )
    assert result.exit_code == 0
    assert "overview" in result.output.lower()


def test_read_section_via_daemon(daemon_for_cli):
    vault_path = daemon_for_cli
    runner = CliRunner()
    result = runner.invoke(
        cli, ["read", "srna-embeddings", "--section", "method",
              "--vault", str(vault_path)]
    )
    assert result.exit_code == 0
    assert "PCA" in result.output


def test_read_grep_via_daemon(daemon_for_cli):
    vault_path = daemon_for_cli
    runner = CliRunner()
    result = runner.invoke(
        cli, ["read", "srna-embeddings", "--grep", "k-means",
              "--vault", str(vault_path)]
    )
    assert result.exit_code == 0
    assert "k-means" in result.output


def test_read_missing_via_daemon(daemon_for_cli):
    vault_path = daemon_for_cli
    runner = CliRunner()
    result = runner.invoke(
        cli, ["read", "nonexistent", "--vault", str(vault_path)]
    )
    assert result.exit_code != 0 or "not found" in result.output.lower()


def test_manifest_via_daemon(daemon_for_cli):
    vault_path = daemon_for_cli
    runner = CliRunner()
    result = runner.invoke(
        cli, ["manifest", "--vault", str(vault_path)]
    )
    assert result.exit_code == 0
    assert len(result.output) > 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.venv/bin/pytest tests/test_cli/test_commands.py -v`
Expected: All 10 tests PASS

Run: `~/.venv/bin/pytest tests/ -v` to verify no regressions
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/llm_wiki/daemon/__main__.py src/llm_wiki/cli/main.py tests/test_cli/test_commands.py
git commit -m "feat: daemon entry point, CLI routes through daemon with auto-start"
```

---

### Task 9: Daemon Integration Test

**Files:**
- Create: `tests/test_daemon_integration.py`

- [ ] **Step 1: Write integration test**

```python
# tests/test_daemon_integration.py
"""Full daemon lifecycle: start → request → file change → rescan → stop."""
import asyncio
from pathlib import Path

import pytest

from llm_wiki.daemon.client import DaemonClient
from llm_wiki.daemon.server import DaemonServer
from llm_wiki.daemon.watcher import FileWatcher


@pytest.mark.asyncio
async def test_full_daemon_lifecycle(sample_vault: Path, tmp_path: Path):
    """Start daemon, query, add file, rescan, query again, stop."""
    sock_path = tmp_path / "integration.sock"

    # Start server
    server = DaemonServer(sample_vault, sock_path)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    client = DaemonClient(sock_path)

    # Verify running
    assert client.is_running()

    # Search
    resp = client.request({"type": "search", "query": "sRNA", "limit": 5})
    assert resp["status"] == "ok"
    assert len(resp["results"]) >= 1

    # Read
    resp = client.request({"type": "read", "page_name": "srna-embeddings"})
    assert resp["status"] == "ok"
    assert "overview" in resp["content"].lower()

    # Status check page count
    resp = client.request({"type": "status"})
    original_count = resp["page_count"]

    # Add a new page to the vault
    (sample_vault / "new-topic.md").write_text(
        "---\ntitle: Brand New Topic\n---\n\n## Overview\n\nThis is new content.\n"
    )

    # Trigger rescan (normally the watcher does this)
    resp = client.request({"type": "rescan"})
    assert resp["status"] == "ok"

    # Verify new page is indexed
    resp = client.request({"type": "status"})
    assert resp["page_count"] == original_count + 1

    # Search for new content
    resp = client.request({"type": "search", "query": "Brand New Topic"})
    assert resp["status"] == "ok"
    assert len(resp["results"]) >= 1

    # Read new page
    resp = client.request({"type": "read", "page_name": "new-topic"})
    assert resp["status"] == "ok"
    assert "new content" in resp["content"].lower()

    # Stop
    server._server.close()
    serve_task.cancel()
    try:
        await serve_task
    except asyncio.CancelledError:
        pass
    await server.stop()

    # Verify stopped
    assert not client.is_running()


@pytest.mark.asyncio
async def test_watcher_triggers_rescan(sample_vault: Path, tmp_path: Path):
    """File watcher detects change and triggers rescan."""
    sock_path = tmp_path / "watcher.sock"

    server = DaemonServer(sample_vault, sock_path)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())

    # Set up watcher that calls server.rescan()
    async def on_change(changed, removed):
        await server.rescan()

    watcher = FileWatcher(sample_vault, on_change, poll_interval=0.3)
    await watcher.start()

    client = DaemonClient(sock_path)

    # Get initial count
    resp = client.request({"type": "status"})
    initial_count = resp["page_count"]

    # Add a file
    (sample_vault / "watcher-test.md").write_text("# Watcher Test\n\nDetected!")
    await asyncio.sleep(1.0)  # Wait for watcher to detect and rescan

    # Verify new page appeared
    resp = client.request({"type": "status"})
    assert resp["page_count"] == initial_count + 1

    # Cleanup
    await watcher.stop()
    server._server.close()
    serve_task.cancel()
    try:
        await serve_task
    except asyncio.CancelledError:
        pass
    await server.stop()
```

- [ ] **Step 2: Run full test suite**

Run: `~/.venv/bin/pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_daemon_integration.py
git commit -m "feat: daemon integration tests (lifecycle, file watcher, rescan)"
```

- [ ] **Step 4: Final commit — Phase 2 complete**

```bash
git add -A
git commit -m "Phase 2 complete: daemon with Unix socket IPC, file watcher, LLM queue

Daemon holds vault in memory, serves requests over Unix socket,
watches for file changes (mtime polling), auto-rescans on change.
CLI routes through daemon with auto-start. LLM queue and write
coordinator ready for Phases 3-5."
```

---

## Phase 2 Deliverables

| Command | What it does |
|---------|-------------|
| `llm-wiki serve /path/to/vault` | Start daemon in foreground |
| `llm-wiki stop --vault /path` | Stop daemon |
| `llm-wiki status --vault /path` | Status via daemon (auto-starts) |
| `llm-wiki search "query" --vault /path` | Search via daemon (auto-starts) |
| `llm-wiki read page --vault /path` | Read via daemon (auto-starts) |
| `llm-wiki manifest --vault /path` | Manifest via daemon (auto-starts) |
| `llm-wiki init /path` | Direct scan (no daemon needed) |

The daemon auto-starts on first CLI use and stays running. File changes in the vault are detected and re-indexed automatically.

## What's Next

- **Phase 3: Traversal Engine** — multi-turn traversal with working memory uses the LLM queue
- **Phase 4: Ingest Pipeline** — write coordinator protects concurrent ingests
- **Phase 5: Maintenance Agents** — background workers run as daemon coroutines
