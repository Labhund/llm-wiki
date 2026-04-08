from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

OnSettledCallback = Callable[[Path], Awaitable[None]]


class ChangeDispatcher:
    """Per-path debouncer for file change events.

    submit(path) (re)starts a debounce timer for the path. After the timer
    elapses without further submissions for that path, on_settled(path) is
    called. Errors raised by on_settled are logged but do not affect future
    dispatches.
    """

    def __init__(
        self,
        debounce_secs: float,
        on_settled: OnSettledCallback,
    ) -> None:
        self._debounce = debounce_secs
        self._on_settled = on_settled
        self._pending: dict[Path, asyncio.Task] = {}

    def submit(self, path: Path) -> None:
        existing = self._pending.get(path)
        if existing is not None and not existing.done():
            existing.cancel()
        self._pending[path] = asyncio.create_task(self._wait_and_dispatch(path))

    async def _wait_and_dispatch(self, path: Path) -> None:
        try:
            await asyncio.sleep(self._debounce)
            try:
                await self._on_settled(path)
            except Exception:
                logger.exception("Error in dispatch callback for %s", path)
        except asyncio.CancelledError:
            return
        finally:
            # Only clear our own entry — a later submit() may have replaced us.
            current = self._pending.get(path)
            if current is asyncio.current_task():
                self._pending.pop(path, None)

    async def stop(self) -> None:
        for task in list(self._pending.values()):
            task.cancel()
        for task in list(self._pending.values()):
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Error during dispatcher shutdown")
        self._pending.clear()

    @property
    def pending_count(self) -> int:
        return sum(1 for t in self._pending.values() if not t.done())
