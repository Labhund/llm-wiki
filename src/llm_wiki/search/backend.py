from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from llm_wiki.manifest import ManifestEntry


@dataclass
class SearchResult:
    name: str
    score: float
    entry: ManifestEntry


@dataclass
class SnippetMatch:
    """One per-line search hit inside a page file."""
    line: int          # 1-based line number in the file
    before: str        # nearest preceding ## heading text (or "" if none)
    match: str         # the matching line itself
    after: str         # the next non-blank line after the match (or "")


@dataclass
class SnippetSearchResult:
    """A SearchResult enriched with line-level snippet matches."""
    name: str
    score: float
    entry: ManifestEntry
    matches: list[SnippetMatch]


class SearchBackend(Protocol):
    def index_entries(self, entries: list[ManifestEntry]) -> None: ...
    def search(self, query: str, limit: int = 10) -> list[SearchResult]: ...
    def entry_count(self) -> int: ...
