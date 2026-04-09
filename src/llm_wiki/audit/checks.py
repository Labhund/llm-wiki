from __future__ import annotations

import datetime
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
                severity="minor",
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
                    severity="moderate",
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
                severity="minor",
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


# Matches [[raw/<anything>]] inside page bodies. Allows | aliases.
_RAW_CITATION_RE = re.compile(r"\[\[(raw/[^\]|]+)(?:\|[^\]]+)?\]\]")
# Frontmatter source values are stored as the literal string "[[raw/...]]".
_FRONTMATTER_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def find_broken_citations(vault: Vault, vault_root: Path) -> CheckResult:
    """References to raw/ source files that don't exist on disk.

    Scans two places:
      1. page.raw_content for inline `[[raw/<path>]]` references
      2. page.frontmatter['source'] (and 'sources' as a list) for raw refs

    Each missing target produces one Issue keyed by (page, target).
    """
    issues: list[Issue] = []
    for name, entry in vault.manifest_entries().items():
        page = vault.read_page(name)
        if page is None:
            continue
        targets: set[str] = set()

        for match in _RAW_CITATION_RE.finditer(page.raw_content):
            targets.add(match.group(1))

        source_field = page.frontmatter.get("source")
        if isinstance(source_field, str):
            for match in _FRONTMATTER_LINK_RE.finditer(source_field):
                inner = match.group(1)
                if inner.startswith("raw/"):
                    targets.add(inner)

        sources_field = page.frontmatter.get("sources")
        if isinstance(sources_field, list):
            for entry_str in sources_field:
                if not isinstance(entry_str, str):
                    continue
                for match in _FRONTMATTER_LINK_RE.finditer(entry_str):
                    inner = match.group(1)
                    if inner.startswith("raw/"):
                        targets.add(inner)

        for target in sorted(targets):
            absolute = vault_root / target
            if absolute.exists():
                continue
            issues.append(
                Issue(
                    id=Issue.make_id("broken-citation", name, target),
                    type="broken-citation",
                    status="open",
                    severity="critical",
                    title=f"Citation '{target}' does not exist on disk",
                    page=name,
                    body=(
                        f"The page [[{name}]] cites `{target}`, but no such file "
                        f"exists at `{absolute}`. Either restore the source file "
                        f"or remove the citation."
                    ),
                    created=Issue.now_iso(),
                    detected_by="auditor",
                    metadata={"target": target},
                )
            )
    return CheckResult(check="broken-citations", issues=issues)


from llm_wiki.config import WikiConfig
from llm_wiki.ingest.source_meta import _SUPPORTED_BINARY, read_frontmatter


def _file_slug(path: Path) -> str:
    """Return a safe lowercase slug from a filename (no extension, no dots)."""
    slug = re.sub(r"[^a-z0-9]+", "-", path.stem.lower()).strip("-")
    return slug or "file"


def _canonical_source(companion: Path, raw_dir: Path) -> str:
    """Return the canonical raw/<filename> path used in plan file source: fields.

    For a companion foo.md, checks whether a sibling binary (foo.pdf etc.) exists.
    If yes, returns raw/<binary_name>. If no, returns raw/<companion_name> (native .md source).
    """
    for ext in _SUPPORTED_BINARY:
        binary = companion.with_suffix(ext)
        if binary.exists():
            return f"raw/{binary.name}"
    return f"raw/{companion.name}"


def find_source_gaps(vault_root: Path, config: WikiConfig) -> CheckResult:
    """Scan raw/ for sources with missing or stale reading_status metadata.

    Four issue types:
      bare-source             (minor)   — binary with no companion .md
      missing-reading-status  (minor)   — .md with no reading_status field
      unread-source           (minor)   — unread for > auditor_unread_source_days
      in-progress-no-plan     (moderate)— in_progress with no matching inbox/ plan
    """
    raw_dir = vault_root / "raw"
    if not raw_dir.is_dir():
        return CheckResult(check="source-gaps", issues=[])

    threshold_days = config.maintenance.auditor_unread_source_days
    today = datetime.date.today()
    issues: list[Issue] = []

    for file in sorted(raw_dir.iterdir()):
        if not file.is_file():
            continue
        suffix = file.suffix.lower()

        # bare-source: binary with no companion .md
        if suffix in _SUPPORTED_BINARY:
            companion = file.with_suffix(".md")
            if not companion.exists():
                issues.append(Issue(
                    id=Issue.make_id("bare-source", _file_slug(file), ""),
                    type="bare-source",
                    status="open",
                    severity="minor",
                    title=f"Source has no metadata companion: raw/{file.name}",
                    page=f"raw/{file.name}",
                    body=(
                        f"`raw/{file.name}` has no companion `.md` file. "
                        f"Run `wiki_ingest` on it, or call `wiki_source_mark` to register it."
                    ),
                    created=Issue.now_iso(),
                    detected_by="auditor",
                    metadata={"path": f"raw/{file.name}"},
                ))
            continue

        # Only process .md files below this point
        if suffix not in (".md", ".markdown"):
            continue

        fm = read_frontmatter(file)

        # missing-reading-status
        if "reading_status" not in fm:
            issues.append(Issue(
                id=Issue.make_id("missing-reading-status", _file_slug(file), ""),
                type="missing-reading-status",
                status="open",
                severity="minor",
                title=f"Source missing reading_status: raw/{file.name}",
                page=f"raw/{file.name}",
                body=(
                    f"`raw/{file.name}` has no `reading_status` field. "
                    f"Call `wiki_source_mark` to set it."
                ),
                created=Issue.now_iso(),
                detected_by="auditor",
                metadata={"path": f"raw/{file.name}"},
            ))
            continue

        reading_status = fm["reading_status"]
        ingested = fm.get("ingested")

        # unread-source: unread for longer than threshold
        if reading_status == "unread" and ingested is not None:
            if isinstance(ingested, datetime.date):
                ingested_date = ingested
            else:
                try:
                    ingested_date = datetime.date.fromisoformat(str(ingested))
                except (ValueError, TypeError):
                    ingested_date = None
            if ingested_date is not None and (today - ingested_date).days > threshold_days:
                issues.append(Issue(
                    id=Issue.make_id("unread-source", _file_slug(file), ""),
                    type="unread-source",
                    status="open",
                    severity="minor",
                    title=f"Unread source: raw/{file.name} (ingested {ingested})",
                    page=f"raw/{file.name}",
                    body=(
                        f"`raw/{file.name}` has been `reading_status: unread` for "
                        f"{(today - ingested_date).days} days (ingested {ingested}). "
                        f"Read it or queue it for ingest."
                    ),
                    created=Issue.now_iso(),
                    detected_by="auditor",
                    metadata={"path": f"raw/{file.name}", "ingested": str(ingested)},
                ))

        # in-progress-no-plan: check inbox/ if it exists
        elif reading_status == "in_progress":
            inbox_dir = vault_root / "inbox"
            if not inbox_dir.is_dir():
                continue  # inbox/ not yet created — skip gracefully
            canonical = _canonical_source(file, raw_dir)
            has_plan = any(
                read_frontmatter(plan).get("source") == canonical
                for plan in inbox_dir.glob("*.md")
                if plan.is_file()
            )
            if not has_plan:
                issues.append(Issue(
                    id=Issue.make_id("in-progress-no-plan", _file_slug(file), ""),
                    type="in-progress-no-plan",
                    status="open",
                    severity="moderate",
                    title=f"In-progress source has no plan file: raw/{file.name}",
                    page=f"raw/{file.name}",
                    body=(
                        f"`raw/{file.name}` is `reading_status: in_progress` but no "
                        f"plan file in `inbox/` has `source: {canonical}`. "
                        f"Create an inbox plan file or mark the source as read."
                    ),
                    created=Issue.now_iso(),
                    detected_by="auditor",
                    metadata={"path": f"raw/{file.name}", "canonical_source": canonical},
                ))

    return CheckResult(check="source-gaps", issues=issues)
