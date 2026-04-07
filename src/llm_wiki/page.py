from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from llm_wiki.tokens import count_tokens

# Matches: %% section: name, tokens: 123 %%
# or just: %% section: name %%
_MARKER_RE = re.compile(
    r"^%%\s*section:\s*(?P<name>[^,]+?)(?:\s*,\s*tokens:\s*\d+)?\s*%%$"
)

# Matches: # Heading, ## Heading, or ### Heading
_HEADING_RE = re.compile(r"^(?P<level>#{1,3})\s+(?P<title>.+)$")

# Matches: [[target]] or [[path/to/target]] or [[target|alias]]
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]")

# Matches: # Top Heading (h1 only)
_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


@dataclass
class Section:
    name: str
    content: str
    tokens: int


@dataclass
class Page:
    path: Path
    title: str
    frontmatter: dict
    sections: list[Section]
    wikilinks: list[str]
    raw_content: str
    total_tokens: int

    @classmethod
    def parse(cls, path: Path) -> Page:
        raw = path.read_text(encoding="utf-8")
        frontmatter, body = _split_frontmatter(raw)

        title = (
            frontmatter.get("title")
            or _extract_h1(body)
            or path.stem
        )

        lines = body.splitlines(keepends=True)
        sections = _parse_sections_markers(lines)
        if not sections:
            sections = _parse_sections_headings(lines)
        if not sections:
            sections = [Section(
                name="content",
                content=body.strip(),
                tokens=count_tokens(body),
            )]

        wikilinks = _extract_wikilinks(raw)
        total_tokens = sum(s.tokens for s in sections)

        return cls(
            path=path,
            title=title,
            frontmatter=frontmatter,
            sections=sections,
            wikilinks=wikilinks,
            raw_content=raw,
            total_tokens=total_tokens,
        )


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter from body. Returns ({}, full_text) if none."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_text = text[3:end].strip()
    body = text[end + 4:].strip()
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        return {}, text
    return fm, body


def _extract_h1(text: str) -> str | None:
    m = _H1_RE.search(text)
    return m.group(1).strip() if m else None


def _parse_sections_markers(lines: list[str]) -> list[Section]:
    """Parse sections delimited by %% section: name %% markers."""
    boundaries: list[tuple[str, int]] = []
    for i, line in enumerate(lines):
        m = _MARKER_RE.match(line.strip())
        if m:
            boundaries.append((m.group("name").strip(), i))

    if not boundaries:
        return []

    sections = []
    for idx, (name, start) in enumerate(boundaries):
        end = boundaries[idx + 1][1] if idx + 1 < len(boundaries) else len(lines)
        content_lines = lines[start + 1 : end]
        content = "".join(content_lines).strip()
        sections.append(Section(
            name=name,
            content=content,
            tokens=count_tokens(content),
        ))
    return sections


def _parse_sections_headings(lines: list[str]) -> list[Section]:
    """Fallback: parse sections from ## and ### headings."""
    boundaries: list[tuple[str, int]] = []
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line.strip())
        if m:
            name = _slugify(m.group("title"))
            boundaries.append((name, i))

    if not boundaries:
        return []

    sections = []
    for idx, (name, start) in enumerate(boundaries):
        end = boundaries[idx + 1][1] if idx + 1 < len(boundaries) else len(lines)
        content_lines = lines[start + 1 : end]
        content = "".join(content_lines).strip()
        if content:
            sections.append(Section(
                name=name,
                content=content,
                tokens=count_tokens(content),
            ))
    return sections


def _slugify(text: str) -> str:
    """Convert heading text to a slug: 'Silhouette Score' -> 'silhouette-score'."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


_NON_PAGE_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".zip"}


def _extract_wikilinks(text: str) -> list[str]:
    """Extract wikilink targets, normalized to page name only.

    Skips links to non-page files (PDFs, images, etc.) that appear in
    frontmatter source fields or elsewhere.
    """
    raw_links = _WIKILINK_RE.findall(text)
    result = []
    for link in raw_links:
        # Normalize: strip path prefixes and .md suffix
        name = link.split("/")[-1]
        if name.endswith(".md"):
            name = name[:-3]
        # Skip links to non-page files
        ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
        if ext in _NON_PAGE_EXTENSIONS:
            continue
        if name not in result:
            result.append(name)
    return result
