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
