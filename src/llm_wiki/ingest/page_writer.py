from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class PageSection:
    """One section of a wiki page."""
    name: str      # slug: "overview"
    heading: str   # display text: "Overview"
    content: str   # markdown body


@dataclass
class WrittenPage:
    """Result of writing a page."""
    path: Path
    was_update: bool


def write_page(
    wiki_dir: Path,
    concept_name: str,
    title: str,
    sections: list[PageSection],
    source_ref: str,
) -> WrittenPage:
    """Create a new wiki page or append new-source sections to an existing one.

    Args:
        wiki_dir:     Directory to write the page into.
        concept_name: URL-safe slug, used as the filename (without .md).
        title:        Human-readable page title (written to frontmatter).
        sections:     Sections to write (for new pages) or append (for updates).
        source_ref:   Source citation string, e.g. "raw/paper.pdf".
                      Used in frontmatter and to name the appended section.

    Returns:
        WrittenPage with .path and .was_update flag.
    """
    page_path = wiki_dir / f"{concept_name}.md"

    if not page_path.exists():
        return _create_page(page_path, title, sections, source_ref)
    else:
        return _append_source(page_path, sections, source_ref)


def _create_page(
    page_path: Path,
    title: str,
    sections: list[PageSection],
    source_ref: str,
) -> WrittenPage:
    """Write a brand-new wiki page with frontmatter and %% markers."""
    fm = {
        "title": title,
        "source": f"[[{source_ref}]]",
        "created_by": "ingest",
    }
    frontmatter = "---\n" + yaml.dump(fm, default_flow_style=False).strip() + "\n---"

    body_parts = []
    for section in sections:
        body_parts.append(f"%% section: {section.name} %%")
        body_parts.append(f"## {section.heading}")
        body_parts.append("")
        body_parts.append(section.content)
        body_parts.append("")

    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text(frontmatter + "\n\n" + "\n".join(body_parts).strip() + "\n")
    return WrittenPage(path=page_path, was_update=False)


def _append_source(
    page_path: Path,
    sections: list[PageSection],
    source_ref: str,
) -> WrittenPage:
    """Append a 'from-{source-slug}' section to an existing page.

    Does nothing if a section from this source already exists (idempotent).
    """
    source_slug = Path(source_ref).stem  # "raw/paper.pdf" → "paper"
    section_marker = f"%% section: from-{source_slug} %%"

    existing = page_path.read_text(encoding="utf-8")
    if section_marker in existing:
        return WrittenPage(path=page_path, was_update=True)

    appended_parts = [f"\n{section_marker}", f"## From {source_slug}", ""]
    for section in sections:
        appended_parts.append(section.content)
        appended_parts.append("")

    page_path.write_text(existing.rstrip() + "\n" + "\n".join(appended_parts))
    return WrittenPage(path=page_path, was_update=True)
