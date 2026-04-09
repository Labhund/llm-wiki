from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from llm_wiki.config import WikiConfig
from llm_wiki.manifest import ManifestEntry, ManifestStore, build_entry
from llm_wiki.page import Page
from llm_wiki.search.backend import SearchResult, SnippetSearchResult
from llm_wiki.search.tantivy_backend import TantivyBackend
from llm_wiki.tokens import count_tokens


_STATE_ROOT = Path.home() / ".llm-wiki" / "vaults"


def _state_dir_for(vault_root: Path) -> Path:
    """Derive a unique state directory under ~/.llm-wiki/vaults/ for a vault path."""
    # Use resolved absolute path to avoid duplicates from symlinks/relative paths
    resolved = str(vault_root.resolve())
    # Readable prefix + short hash for uniqueness
    slug = resolved.strip("/").replace("/", "-")
    # Truncate slug so the final daemon.sock path stays within the 108-char
    # AF_UNIX limit: prefix (~31) + slug + "-" + 8-char hash + "/daemon.sock" (12)
    short_hash = hashlib.sha256(resolved.encode()).hexdigest()[:8]
    max_slug = 107 - len(str(_STATE_ROOT)) - 1 - 1 - 8 - len("/daemon.sock")
    if len(slug) > max_slug:
        slug = slug[:max_slug]
    return _STATE_ROOT / f"{slug}-{short_hash}"


class Vault:
    """A scanned and indexed wiki vault."""

    def __init__(
        self,
        root: Path,
        pages: dict[str, Page],
        store: ManifestStore,
        backend: TantivyBackend,
    ) -> None:
        self._root = root
        self._pages = pages
        self._store = store
        self._backend = backend

    @classmethod
    def scan(cls, root: Path, config: WikiConfig | None = None) -> Vault:
        """Scan a vault directory, parse all pages, build index."""
        config = config or WikiConfig()
        state_dir = _state_dir_for(root)
        state_dir.mkdir(parents=True, exist_ok=True)

        # Find all markdown files, excluding hidden directories
        md_files = sorted(root.rglob("*.md"))
        md_files = [
            f for f in md_files
            if not any(p.startswith(".") for p in f.relative_to(root).parts)
            and not f.name.endswith(".talk.md")
        ]

        # Parse pages
        pages: dict[str, Page] = {}
        entries: list[ManifestEntry] = []
        for md_file in md_files:
            if not md_file.is_file():
                continue
            page = Page.parse(md_file)
            pages[page.path.stem] = page

            # Cluster from parent directory name, or "root" if top-level
            rel = md_file.relative_to(root)
            cluster = rel.parts[0] if len(rel.parts) > 1 else "root"

            entry = build_entry(page, cluster=cluster)
            entries.append(entry)

        # Build search index
        index_path = state_dir / "index"
        backend = TantivyBackend(index_path)
        backend.index_entries(entries)

        # Phase 5c: apply librarian-managed overrides on top of built entries.
        # Overrides live in a sidecar JSON file at <state_dir>/manifest_overrides.json;
        # page frontmatter is never mutated for this purpose. Missing file → empty store.
        from llm_wiki.librarian.overrides import ManifestOverrides
        overrides = ManifestOverrides.load(state_dir / "manifest_overrides.json")
        _apply_overrides(entries, overrides)
        # Only write the sidecar back when prune actually changed state;
        # with frequent rescans (file watcher) this avoids a write storm.
        if overrides.prune({e.name for e in entries}) > 0:
            overrides.save()

        # Build manifest store
        store = ManifestStore(entries)

        return cls(root=root, pages=pages, store=store, backend=backend)

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        return self._backend.search(query, limit=limit)

    def search_with_snippets(
        self,
        query: str,
        limit: int = 10,
    ) -> list[SnippetSearchResult]:
        """Public search-with-snippets API.

        Delegates to the backend, filling in `vault_root` from the Vault's
        own root so callers don't have to thread it through. This is the
        path production code should use; reaching into `_backend` directly
        is only acceptable in test code.
        """
        return self._backend.search_with_snippets(
            query, limit=limit, vault_root=self._root,
        )

    def read_page(self, name: str) -> Page | None:
        return self._pages.get(name)

    def manifest_entries(self) -> dict[str, ManifestEntry]:
        """Return a copy of the manifest entries dict, keyed by page name.

        The store's links_from values are already populated. Returned as a
        copy so callers (auditor, librarian) can iterate without locking.
        """
        return dict(self._store._entries)

    def read_viewport(
        self,
        name: str,
        viewport: str = "top",
        section: str | None = None,
        grep: str | None = None,
        budget: int | None = None,
    ) -> str | None:
        """Read a page with viewport support."""
        page = self._pages.get(name)
        if page is None:
            return None

        if grep:
            return self._viewport_grep(page, grep, budget)
        if section:
            return self._viewport_section(page, section)
        if viewport == "full":
            return self._viewport_full(page, budget)
        # Default: "top"
        return self._viewport_top(page, budget)

    def manifest_text(self, budget: int = 16000) -> str:
        return self._store.manifest_text(budget=budget)

    def status(self) -> dict:
        return {
            "vault_root": str(self._root),
            "page_count": self.page_count,
            "cluster_count": self._store.total_clusters,
            "clusters": [c.to_summary_text() for c in self._store.level0()],
            "index_path": str(_state_dir_for(self._root) / "index"),
            "index_entries": self._backend.entry_count(),
        }

    @property
    def page_count(self) -> int:
        return len(self._pages)

    @property
    def cluster_count(self) -> int:
        return self._store.total_clusters

    # -- Viewport implementations --

    @staticmethod
    def _viewport_top(page: Page, budget: int | None) -> str:
        if not page.sections:
            return page.raw_content

        # First section content
        first = page.sections[0]
        lines = [f"## {first.name}\n", first.content, ""]

        # Table of contents for remaining sections
        if len(page.sections) > 1:
            lines.append("**Remaining sections:**")
            for s in page.sections[1:]:
                lines.append(f"  - {s.name} ({s.tokens} tokens)")

        text = "\n".join(lines)
        if budget and count_tokens(text) > budget:
            # Truncate first section to fit
            truncated = text[: budget * 4]  # rough char estimate
            return truncated.rsplit("\n", 1)[0] + "\n... (truncated)"
        return text

    @staticmethod
    def _viewport_section(page: Page, section_name: str) -> str | None:
        for s in page.sections:
            if s.name == section_name or s.name == section_name.lower():
                return f"## {s.name}\n\n{s.content}"
        return None

    @staticmethod
    def _viewport_grep(page: Page, pattern: str, budget: int | None) -> str:
        matches = []
        regex = re.compile(re.escape(pattern), re.IGNORECASE)
        for s in page.sections:
            if regex.search(s.content):
                matches.append(f"## {s.name}\n\n{s.content}")
        if not matches:
            return f"No matches for '{pattern}' in {page.path.stem}"
        text = "\n\n---\n\n".join(matches)
        if budget and count_tokens(text) > budget:
            return text[: budget * 4].rsplit("\n", 1)[0] + "\n... (truncated)"
        return text

    @staticmethod
    def _viewport_full(page: Page, budget: int | None) -> str:
        # Return full body (strip frontmatter)
        body = page.raw_content
        if body.startswith("---"):
            end = body.find("\n---", 3)
            if end != -1:
                body = body[end + 4:].strip()
        if budget and count_tokens(body) > budget:
            return body[: budget * 4].rsplit("\n", 1)[0] + "\n... (truncated)"
        return body


def _apply_overrides(
    entries: list[ManifestEntry],
    overrides: "ManifestOverrides",  # noqa: F821 — runtime import in caller
) -> None:
    """Apply librarian-managed metadata to programmatically-built entries.

    Tags, authority, last_corroborated, read_count, and usefulness come
    straight from the override. summary_override (if present) replaces the
    auto-generated summary; otherwise the auto-generated summary stands.
    Empty override tags do NOT blank out auto-built tags.
    """
    for entry in entries:
        override = overrides.get(entry.name)
        if override is None:
            continue
        if override.tags:
            entry.tags = list(override.tags)
        if override.summary_override:
            entry.summary = override.summary_override
        entry.authority = override.authority
        entry.last_corroborated = override.last_corroborated
        entry.read_count = override.read_count
        entry.usefulness = override.usefulness
