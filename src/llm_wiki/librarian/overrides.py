from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class PageOverride:
    """Librarian-managed metadata that survives Vault.scan()."""
    tags: list[str] = field(default_factory=list)
    summary_override: str | None = None
    authority: float = 0.0
    last_corroborated: str | None = None
    read_count: int = 0
    usefulness: float = 0.0
    last_refreshed_read_count: int = 0


class ManifestOverrides:
    """JSON-backed sidecar of librarian-managed page metadata.

    Atomic writes via temp-file-and-rename so concurrent workers
    (librarian + authority_recalc) cannot corrupt the file. Last
    writer wins; both operations are idempotent in steady state.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._entries: dict[str, PageOverride] = {}

    @classmethod
    def load(cls, path: Path) -> "ManifestOverrides":
        store = cls(path)
        if not path.exists():
            return store
        try:
            data = json.loads(path.read_text(encoding="utf-8")) or {}
        except (json.JSONDecodeError, OSError):
            return store
        for name, raw in data.items():
            if not isinstance(raw, dict):
                continue
            store._entries[name] = PageOverride(
                tags=list(raw.get("tags") or []),
                summary_override=raw.get("summary_override"),
                authority=float(raw.get("authority", 0.0) or 0.0),
                last_corroborated=raw.get("last_corroborated"),
                read_count=int(raw.get("read_count", 0) or 0),
                usefulness=float(raw.get("usefulness", 0.0) or 0.0),
                last_refreshed_read_count=int(raw.get("last_refreshed_read_count", 0) or 0),
            )
        return store

    def get(self, page_name: str) -> PageOverride | None:
        return self._entries.get(page_name)

    def set(self, page_name: str, override: PageOverride) -> None:
        self._entries[page_name] = override

    def delete(self, page_name: str) -> None:
        self._entries.pop(page_name, None)

    def prune(self, valid_names: set[str]) -> None:
        for name in list(self._entries):
            if name not in valid_names:
                del self._entries[name]

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {name: asdict(override) for name, override in self._entries.items()}
        # Use a unique-per-writer temp file so concurrent writers
        # (librarian worker + authority_recalc worker) cannot interleave
        # their writes on a shared .tmp path and corrupt it. os.replace
        # is atomic on POSIX, giving us last-writer-wins semantics.
        tmp_fd, tmp_name = tempfile.mkstemp(
            prefix=self._path.name + ".",
            suffix=".tmp",
            dir=str(self._path.parent),
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, indent=2, sort_keys=True))
            os.replace(tmp_path, self._path)
        except Exception:
            # Clean up the temp file on any failure before re-raising
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
            raise

    def __len__(self) -> int:
        return len(self._entries)

    def names(self) -> list[str]:
        return list(self._entries)
