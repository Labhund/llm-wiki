from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from llm_wiki.issues.queue import Issue
from llm_wiki.vault import Vault

# Page names that should never be flagged as orphans even if nothing links to them.
_ENTRY_POINT_NAMES = {"index", "readme", "home"}


@dataclass
class CheckResult:
    """Result of one structural check."""
    check: str
    issues: list[Issue]


def find_orphans(vault: Vault) -> CheckResult:
    """Pages with zero inlinks (excluding entry-point names).

    Each orphan becomes one Issue with the page slug as the affected page
    and an empty key (since the page itself is the unique identifier).
    """
    issues: list[Issue] = []
    for name, entry in vault.manifest_entries().items():
        if name.lower() in _ENTRY_POINT_NAMES:
            continue
        if entry.links_from:
            continue
        issues.append(
            Issue(
                id=Issue.make_id("orphan", name, ""),
                type="orphan",
                status="open",
                title=f"Page '{name}' has no inbound links",
                page=name,
                body=(
                    f"The page [[{name}]] is not referenced by any other page in the vault. "
                    f"Either link to it from a related page or remove it if obsolete."
                ),
                created=Issue.now_iso(),
                detected_by="auditor",
                metadata={},
            )
        )
    return CheckResult(check="orphans", issues=issues)


def find_broken_wikilinks(vault: Vault) -> CheckResult:
    """For each page, every wikilink target must resolve to a known page.

    The page parser already strips wikilinks pointing at non-page files
    (PDFs, images — see llm_wiki/page.py:_NON_PAGE_EXTENSIONS), so this
    check only sees candidate page references.
    """
    entries = vault.manifest_entries()
    known_pages = set(entries)
    issues: list[Issue] = []
    for name, entry in entries.items():
        for target in entry.links_to:
            if target in known_pages:
                continue
            issues.append(
                Issue(
                    id=Issue.make_id("broken-link", name, target),
                    type="broken-link",
                    status="open",
                    title=f"Wikilink target '{target}' does not exist",
                    page=name,
                    body=(
                        f"The page [[{name}]] references [[{target}]], "
                        f"but no such page exists in the vault. "
                        f"Either create the page or remove the link."
                    ),
                    created=Issue.now_iso(),
                    detected_by="auditor",
                    metadata={"target": target},
                )
            )
    return CheckResult(check="broken-wikilinks", issues=issues)


# Detects ## or ### headings at line start (not inside code blocks — naive but adequate
# for v1; the librarian's retrofit pass uses the same heuristic).
_HEADING_LINE_RE = re.compile(r"^(##|###)\s+\S", re.MULTILINE)
_MARKER_LINE_RE = re.compile(r"^%%\s*section:", re.MULTILINE)


def find_missing_markers(vault: Vault) -> CheckResult:
    """Pages with ## headings but no %% section markers.

    Reads page.raw_content directly so we see what was on disk, not what
    the parser fell back to. The page is flagged exactly when:
      - it contains at least one ##/### heading at line start, AND
      - it contains zero `%% section: ... %%` markers.
    """
    issues: list[Issue] = []
    for name, entry in vault.manifest_entries().items():
        page = vault.read_page(name)
        if page is None:
            continue
        raw = page.raw_content
        if _MARKER_LINE_RE.search(raw):
            continue
        if not _HEADING_LINE_RE.search(raw):
            continue
        issues.append(
            Issue(
                id=Issue.make_id("missing-markers", name, ""),
                type="missing-markers",
                status="open",
                title=f"Page '{name}' has headings but no %% section markers",
                page=name,
                body=(
                    f"The page [[{name}]] uses ## headings without `%% section: ... %%` "
                    f"markers. Markers are required so the daemon can slice the page by "
                    f"section. The librarian will retrofit them on its next run."
                ),
                created=Issue.now_iso(),
                detected_by="auditor",
                metadata={},
            )
        )
    return CheckResult(check="missing-markers", issues=issues)
