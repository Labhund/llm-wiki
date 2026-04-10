from __future__ import annotations

import re


def build_link_pattern(title_to_slug: dict[str, str]) -> re.Pattern | None:
    """Compile an alternation regex from the manifest's title→slug map.

    Titles are sorted longest-first so multi-word titles win over their
    prefixes (e.g. "Boltz Diffusion" beats "Boltz").

    Returns None when the dict is empty (nothing to link).
    """
    if not title_to_slug:
        return None
    titles = sorted(title_to_slug.keys(), key=len, reverse=True)
    alternation = "|".join(re.escape(t) for t in titles)
    return re.compile(rf"\b({alternation})\b", re.IGNORECASE)


def _find_excluded_ranges(text: str) -> list[tuple[int, int]]:
    """Return (start, end) byte ranges that must not be rewritten.

    Covers: YAML frontmatter, fenced code blocks (``` or ~~~),
    inline code spans (`...`), and existing [[...]] wikilinks.
    """
    ranges: list[tuple[int, int]] = []

    # YAML frontmatter (--- block at start of file)
    fm = re.match(r"^---\n.*?\n---\n", text, re.DOTALL)
    if fm:
        ranges.append((0, fm.end()))

    # Fenced code blocks (``` or ~~~, with optional language tag)
    for m in re.finditer(r"(?:```|~~~).*?(?:```|~~~)", text, re.DOTALL):
        ranges.append((m.start(), m.end()))

    # Inline code spans
    for m in re.finditer(r"`[^`\n]+`", text):
        ranges.append((m.start(), m.end()))

    # Existing wikilinks [[...]] — must not double-link
    for m in re.finditer(r"\[\[.*?\]\]", text):
        ranges.append((m.start(), m.end()))

    return ranges


def apply_wikilinks(
    text: str,
    title_to_slug: dict[str, str],
    page_slug: str,
    pattern: re.Pattern,
) -> tuple[str, int]:
    """Replace every unlinked title occurrence with a [[slug|title]] link.

    Exclusions: frontmatter, code fences, inline code, existing wikilinks,
    and the page's own slug (no self-references).

    Returns (new_text, count_added). count_added == 0 means the file should
    not be written.
    """
    excluded = _find_excluded_ranges(text)
    # Case-insensitive canonical lookup: lowercase title → canonical title
    lower_to_canonical: dict[str, str] = {t.lower(): t for t in title_to_slug}
    count = 0

    def _in_excluded(start: int) -> bool:
        return any(ex_start <= start < ex_end for ex_start, ex_end in excluded)

    def replacer(m: re.Match) -> str:
        nonlocal count
        if _in_excluded(m.start()):
            return m.group(0)

        matched_text = m.group(1)
        canonical = lower_to_canonical.get(matched_text.lower(), matched_text)
        slug = title_to_slug.get(canonical, "")
        if not slug or slug == page_slug:
            return m.group(0)

        count += 1
        if matched_text == slug:
            return f"[[{slug}]]"
        return f"[[{slug}|{matched_text}]]"

    new_text = pattern.sub(replacer, text)
    return new_text, count
