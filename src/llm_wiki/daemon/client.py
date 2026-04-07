from __future__ import annotations

import asyncio
import asyncio.tasks
import socket
from pathlib import Path

from llm_wiki.daemon.protocol import read_message, read_message_sync, write_message, write_message_sync


class DaemonClient:
    """Synchronous client for the daemon Unix socket."""

    def __init__(self, socket_path: Path) -> None:
        self._socket_path = socket_path

    def request(self, msg: dict) -> dict:
        """Send a request and return the response.

        Works both from synchronous code and from within a running asyncio
        event loop (e.g. an async test).  In the latter case the event loop
        is pumped manually so the server-side coroutine can run concurrently.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None:
            return _run_coroutine_in_running_loop(loop, self._async_request(msg))
        else:
            return self._sync_request(msg)

    def _sync_request(self, msg: dict) -> dict:
        """Blocking socket request — safe when no event loop is running."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(30.0)
        try:
            sock.connect(str(self._socket_path))
            write_message_sync(sock, msg)
            return read_message_sync(sock)
        finally:
            sock.close()

    async def _async_request(self, msg: dict) -> dict:
        """Async request — used from within a running event loop."""
        reader, writer = await asyncio.open_unix_connection(str(self._socket_path))
        try:
            await write_message(writer, msg)
            return await read_message(reader)
        finally:
            writer.close()
            await writer.wait_closed()

    def is_running(self) -> bool:
        """Return True if the daemon is reachable via its socket."""
        if not self._socket_path.exists():
            return False
        try:
            resp = self.request({"type": "status"})
            return resp.get("status") == "ok"
        except OSError:
            return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_coroutine_in_running_loop(loop: asyncio.AbstractEventLoop, coro) -> object:
    """Run *coro* to completion while the event loop is already running.

    This is a minimal re-implementation of the technique used by nest_asyncio:
    manually pump the loop with ``_run_once()`` until the future is done,
    temporarily suspending the currently-executing task so that other
    tasks (including the server's ``_handle_client``) can run in between.
    """
    from heapq import heappop

    future = asyncio.ensure_future(coro, loop=loop)

    # _current_tasks maps loop → currently-executing Task.
    # We must temporarily remove the outer task so that the inner coroutine
    # can be stepped without hitting "Cannot enter into task while another
    # task is being executed".
    curr_tasks = asyncio.tasks._current_tasks  # type: ignore[attr-defined]

    while not future.done():
        outer_task = curr_tasks.pop(loop, None)
        try:
            # Drive the loop one iteration at a time (mirrors _run_once).
            ready = loop._ready  # type: ignore[attr-defined]
            scheduled = loop._scheduled  # type: ignore[attr-defined]

            while scheduled and scheduled[0]._cancelled:
                heappop(scheduled)

            timeout = (
                0 if ready or loop._stopping  # type: ignore[attr-defined]
                else min(max(scheduled[0]._when - loop.time(), 0), 86400)
                if scheduled
                else None
            )
            event_list = loop._selector.select(timeout)  # type: ignore[attr-defined]
            loop._process_events(event_list)  # type: ignore[attr-defined]

            end_time = loop.time() + loop._clock_resolution  # type: ignore[attr-defined]
            while scheduled and scheduled[0]._when < end_time:
                handle = heappop(scheduled)
                ready.append(handle)

            for _ in range(len(ready)):
                if not ready:
                    break
                handle = ready.popleft()
                if not handle._cancelled:
                    handle._run()
        finally:
            if outer_task is not None:
                curr_tasks[loop] = outer_task

    return future.result()
