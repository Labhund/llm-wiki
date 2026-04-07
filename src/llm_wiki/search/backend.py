from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from llm_wiki.manifest import ManifestEntry


@dataclass
class SearchResult:
    name: str
    score: float
    entry: ManifestEntry


class SearchBackend(Protocol):
    def index_entries(self, entries: list[ManifestEntry]) -> None: ...
    def search(self, query: str, limit: int = 10) -> list[SearchResult]: ...
    def entry_count(self) -> int: ...
