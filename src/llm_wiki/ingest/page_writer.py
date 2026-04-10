from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from llm_wiki.tokens import count_tokens


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

    Raises:
        ValueError: if concept_name contains path separators or starts with '.'.
    """
    if Path(concept_name).name != concept_name or concept_name.startswith("."):
        raise ValueError(f"Invalid concept slug: {concept_name!r}")
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
    patch_token_estimates(page_path)
    return WrittenPage(path=page_path, was_update=False)


def _append_source(
    page_path: Path,
    sections: list[PageSection],
    source_ref: str,
) -> WrittenPage:
    """Append a 'from-{source-slug}' section to an existing page.

    Returns without modifying the file if a section from this source already
    exists; the caller still records an update (page is up-to-date).
    Deduplicates content: skips section bodies already present in the page.
    """
    raw_stem = Path(source_ref).stem  # "raw/paper.pdf" → "paper"
    source_slug = re.sub(r"[^a-z0-9-]", "-", raw_stem.lower())  # "my.paper.2024" → "my-paper-2024"
    # Use prefix match so it works whether or not tokens have been patched in
    section_marker_prefix = f"%% section: from-{source_slug}"

    existing = page_path.read_text(encoding="utf-8")
    if section_marker_prefix in existing:
        return WrittenPage(path=page_path, was_update=True)

    section_marker = f"%% section: from-{source_slug} %%"

    appended_parts = [f"\n{section_marker}", f"## From {source_slug}", ""]
    for section in sections:
        # Skip empty content and content already present elsewhere in the page
        if section.content and section.content not in existing:
            appended_parts.append(section.content)
            appended_parts.append("")

    page_path.write_text(existing.rstrip() + "\n" + "\n".join(appended_parts))
    patch_token_estimates(page_path)
    return WrittenPage(path=page_path, was_update=True)


# Matches %% section: name %% or %% section: name, tokens: N %%
_SECTION_MARKER_RE = re.compile(
    r"^(%% section:\s*)([^,%]+?)\s*(?:,\s*tokens:\s*\d+\s*)?(%%)$",
    re.MULTILINE,
)


def patch_token_estimates(path: Path) -> None:
    """Rewrite %% section: name %% markers to include token counts.

    Reads the file, counts tokens in each section's content block, then
    rewrites each marker as %% section: name, tokens: N %%. This is a
    pure Python operation — no LLM calls.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Split into segments: each starts with a %% section: ... %% marker line
    segments: list[tuple[str, list[str]]] = []  # (marker_line, content_lines)
    current_marker: str | None = None
    current_lines: list[str] = []

    for line in lines:
        if _SECTION_MARKER_RE.match(line.strip()):
            if current_marker is not None:
                segments.append((current_marker, current_lines))
            current_marker = line.strip()
            current_lines = []
        else:
            if current_marker is not None:
                current_lines.append(line)
            # Lines before the first marker (frontmatter etc.) are preserved
            # by carrying them as the "header" segment with marker=None below

    if current_marker is not None:
        segments.append((current_marker, current_lines))

    if not segments:
        return  # No section markers — nothing to patch

    # Identify the header (everything before the first marker)
    first_marker_line = None
    for i, line in enumerate(lines):
        if _SECTION_MARKER_RE.match(line.strip()):
            first_marker_line = i
            break

    header_lines = lines[:first_marker_line] if first_marker_line is not None else []

    # Rebuild with token counts injected
    output_parts = header_lines[:]
    for marker_line, content_lines in segments:
        m = _SECTION_MARKER_RE.match(marker_line.strip())
        if m:
            section_name = m.group(2).strip()
            section_content = "\n".join(content_lines)
            tokens = count_tokens(section_content)
            new_marker = f"%% section: {section_name}, tokens: {tokens} %%"
        else:
            new_marker = marker_line.strip()
        output_parts.append(new_marker)
        output_parts.extend(content_lines)

    new_text = "\n".join(output_parts)
    if not new_text.endswith("\n"):
        new_text += "\n"
    if new_text != text:
        path.write_text(new_text, encoding="utf-8")
