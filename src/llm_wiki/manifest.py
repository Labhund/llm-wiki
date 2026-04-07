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
