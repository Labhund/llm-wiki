from __future__ import annotations

from pathlib import Path


class PageSnapshotStore:
    """Last-known content of each page, used by the compliance reviewer.

    Stored at <state_dir>/snapshots/<slug>.md. The store is updated AFTER a
    successful compliance review so the diff for the next edit is the delta
    from the last-reviewed state, not from whatever the daemon happens to
    see on the next watcher tick.
    """

    def __init__(self, state_dir: Path) -> None:
        self._dir = state_dir / "snapshots"

    def get(self, page_slug: str) -> str | None:
        path = self._path_for(page_slug)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def set(self, page_slug: str, content: str) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path_for(page_slug).write_text(content, encoding="utf-8")

    def remove(self, page_slug: str) -> None:
        path = self._path_for(page_slug)
        if path.exists():
            path.unlink()

    def _path_for(self, page_slug: str) -> Path:
        return self._dir / f"{page_slug}.md"
