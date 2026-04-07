from __future__ import annotations

from dataclasses import dataclass, field

from llm_wiki.page import Page, Section as PageSection
from llm_wiki.tokens import count_tokens


@dataclass
class SectionInfo:
    name: str
    tokens: int


@dataclass
class ManifestEntry:
    name: str
    title: str
    summary: str
    tags: list[str]
    cluster: str
    tokens: int
    sections: list[SectionInfo]
    links_to: list[str]
    links_from: list[str]
    # Usage stats — initialized to defaults, updated by librarian (Phase 5)
    read_count: int = 0
    usefulness: float = 0.0
    authority: float = 0.0
    last_corroborated: str | None = None

    def to_manifest_text(self) -> str:
        """Compact text representation for agent consumption."""
        sec_info = ", ".join(
            f"{s.name}({s.tokens}t)" for s in self.sections
        )
        tags_str = ", ".join(self.tags) if self.tags else "none"
        links_str = ", ".join(self.links_to) if self.links_to else "none"
        return (
            f"{self.name}: {self.summary}\n"
            f"  tags: [{tags_str}] | tokens: {self.tokens} | "
            f"authority: {self.authority:.2f}\n"
            f"  sections: [{sec_info}]\n"
            f"  links: [{links_str}]"
        )


@dataclass
class ClusterSummary:
    name: str
    page_count: int
    total_tokens: int
    page_names: list[str]

    @classmethod
    def from_entries(cls, name: str, entries: list[ManifestEntry]) -> ClusterSummary:
        return cls(
            name=name,
            page_count=len(entries),
            total_tokens=sum(e.tokens for e in entries),
            page_names=[e.name for e in entries],
        )

    def to_summary_text(self) -> str:
        return f"{self.name} ({self.page_count} pages, {self.total_tokens} tokens)"


def build_entry(page: Page, cluster: str) -> ManifestEntry:
    """Build a manifest entry from a parsed page."""
    # Summary: first section content, truncated
    summary = ""
    if page.sections:
        first_content = page.sections[0].content
        # Take first sentence or first 120 chars
        dot = first_content.find(".")
        if 0 < dot < 120:
            summary = first_content[: dot + 1]
        else:
            summary = first_content[:120].rsplit(" ", 1)[0] + "..."

    sections = [
        SectionInfo(name=s.name, tokens=s.tokens)
        for s in page.sections
    ]

    return ManifestEntry(
        name=page.path.stem,
        title=page.title,
        summary=summary,
        tags=[],  # Tags added by librarian in Phase 5
        cluster=cluster,
        tokens=page.total_tokens,
        sections=sections,
        links_to=page.wikilinks,
        links_from=[],  # Computed after all pages indexed
    )


@dataclass
class ManifestPage:
    """A paginated slice of manifest entries."""
    entries: list[ManifestEntry]
    has_more: bool
    next_cursor: int | None


class ManifestStore:
    """Hierarchical manifest with budget-aware pagination."""

    def __init__(self, entries: list[ManifestEntry]) -> None:
        self._entries: dict[str, ManifestEntry] = {e.name: e for e in entries}
        self._clusters: dict[str, list[ManifestEntry]] = {}
        for entry in entries:
            self._clusters.setdefault(entry.cluster, []).append(entry)
        self._compute_links_from()

    def _compute_links_from(self) -> None:
        """Compute reverse links (links_from) across all entries."""
        for entry in self._entries.values():
            entry.links_from = []
        for entry in self._entries.values():
            for target in entry.links_to:
                if target in self._entries:
                    if entry.name not in self._entries[target].links_from:
                        self._entries[target].links_from.append(entry.name)

    def level0(self) -> list[ClusterSummary]:
        """Level 0: cluster summaries."""
        return [
            ClusterSummary.from_entries(name, entries)
            for name, entries in sorted(self._clusters.items())
        ]

    def level1(
        self, cluster: str, page_size: int = 20, cursor: int = 0
    ) -> ManifestPage:
        """Level 1: paginated entries within a cluster."""
        entries = self._clusters.get(cluster, [])
        page = entries[cursor : cursor + page_size]
        has_more = cursor + page_size < len(entries)
        next_cursor = cursor + page_size if has_more else None
        return ManifestPage(entries=page, has_more=has_more, next_cursor=next_cursor)

    def level2(self, name: str) -> ManifestEntry | None:
        """Level 2: single entry by name."""
        return self._entries.get(name)

    def manifest_text(self, budget: int = 16000) -> str:
        """Budget-aware text representation of the manifest.

        Starts with level 0 (clusters). If budget allows, adds level 1 entries
        for each cluster until budget is exhausted.
        """
        lines: list[str] = []
        running_tokens = 0

        # Always include level 0
        for cluster in self.level0():
            line = cluster.to_summary_text()
            running_tokens += count_tokens(line)
            lines.append(line)

        if running_tokens >= budget:
            return "\n".join(lines)

        # Add level 1 entries until budget exhausted
        for cluster_name in sorted(self._clusters):
            page = self.level1(cluster_name, page_size=100)
            for entry in page.entries:
                entry_text = entry.to_manifest_text()
                entry_tokens = count_tokens(entry_text)
                if running_tokens + entry_tokens > budget:
                    lines.append(f"  ... ({cluster_name}: more pages available)")
                    return "\n".join(lines)
                running_tokens += entry_tokens
                lines.append(entry_text)

        return "\n".join(lines)

    @property
    def total_entries(self) -> int:
        return len(self._entries)

    @property
    def total_clusters(self) -> int:
        return len(self._clusters)
