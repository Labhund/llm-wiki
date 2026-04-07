from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

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
