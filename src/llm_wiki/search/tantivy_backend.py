from __future__ import annotations

import json
from pathlib import Path

import tantivy

from llm_wiki.manifest import ManifestEntry, SectionInfo
from llm_wiki.search.backend import SearchResult


class TantivyBackend:
    def __init__(self, index_path: Path) -> None:
        self._path = index_path
        self._schema = self._build_schema()
        self._path.mkdir(parents=True, exist_ok=True)
        self._index = tantivy.Index(self._schema, path=str(self._path))
        self._entries: dict[str, ManifestEntry] = {}

    @staticmethod
    def _build_schema() -> tantivy.Schema:
        builder = tantivy.SchemaBuilder()
        builder.add_text_field("name", stored=True, tokenizer_name="default")
        builder.add_text_field("title", stored=True, tokenizer_name="default")
        builder.add_text_field("summary", stored=True, tokenizer_name="default")
        builder.add_text_field("body", stored=False, tokenizer_name="default")
        builder.add_text_field("tags", stored=True, tokenizer_name="default")
        builder.add_text_field("entry_json", stored=True, tokenizer_name="raw")
        return builder.build()

    def index_entries(self, entries: list[ManifestEntry]) -> None:
        writer = self._index.writer(heap_size=50_000_000)

        # Clear existing documents
        writer.delete_all_documents()

        for entry in entries:
            self._entries[entry.name] = entry
            body = f"{entry.title} {entry.summary} {' '.join(entry.tags)}"
            writer.add_document(tantivy.Document(
                name=entry.name,
                title=entry.title,
                summary=entry.summary,
                body=body,
                tags=" ".join(entry.tags),
                entry_json=json.dumps(_entry_to_dict(entry)),
            ))

        writer.commit()
        self._index.reload()

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        if self.entry_count() == 0:
            return []

        self._index.reload()
        searcher = self._index.searcher()
        parsed = self._index.parse_query(query, ["name", "title", "summary", "body"])

        results = []
        search_result = searcher.search(parsed, limit)
        for score, doc_address in search_result.hits:
            doc = searcher.doc(doc_address)
            entry_data = json.loads(doc["entry_json"][0])
            entry = _entry_from_dict(entry_data)
            results.append(SearchResult(
                name=entry.name,
                score=score,
                entry=entry,
            ))
        return results

    def entry_count(self) -> int:
        self._index.reload()
        return self._index.searcher().num_docs

    # Default cap on bytes read per page during snippet extraction.
    # P6A-M2: prevents O(file_size) reads on multi-MB pages. 64 KB is
    # comfortably more than any human-curated wiki page and still cheap.
    SNIPPET_READ_MAX_BYTES = 64 * 1024

    def search_with_snippets(
        self,
        query: str,
        limit: int,
        vault_root: Path,
    ) -> list:
        """Run a search and attach line-level snippet matches per result.

        Tokenizes the query into terms (whitespace split, lowercased), then
        for each result reads the page file from `vault_root` and finds the
        first few lines that contain any term. The `before` field is the
        nearest preceding `^##` or `^###` heading; `after` is the next
        non-blank line. Snippet count per result is capped at 3, and per
        the `max_bytes` cap on each page read (default 64 KB) we don't pay
        O(file_size) per query for adversarially large pages.
        """
        from llm_wiki.search.backend import SnippetMatch, SnippetSearchResult

        base_results = self.search(query, limit=limit)
        if not base_results:
            return []

        terms = [t.lower() for t in query.split() if t.strip()]
        if not terms:
            return [
                SnippetSearchResult(
                    name=r.name, score=r.score, entry=r.entry, matches=[],
                )
                for r in base_results
            ]

        out: list = []
        for r in base_results:
            matches = self._extract_snippets(r.name, terms, vault_root, max_matches=3)
            out.append(SnippetSearchResult(
                name=r.name, score=r.score, entry=r.entry, matches=matches,
            ))
        return out

    def _extract_snippets(
        self,
        page_name: str,
        terms: list[str],
        vault_root: Path,
        max_matches: int,
        max_bytes: int | None = None,
    ) -> list:
        """Read the page file and find lines containing any of the query terms.

        `max_bytes` caps the number of bytes read from each page (default
        ``SNIPPET_READ_MAX_BYTES``, 64 KB). Lines beyond the cap are not
        searched. Setting `max_bytes=0` is treated as "use the default";
        pass an explicit positive integer to override.
        """
        from llm_wiki.search.backend import SnippetMatch

        if max_bytes is None or max_bytes <= 0:
            max_bytes = self.SNIPPET_READ_MAX_BYTES

        # Find the page file by name (may be nested under cluster directories)
        page_file = None
        for candidate in vault_root.rglob(f"{page_name}.md"):
            rel = candidate.relative_to(vault_root)
            if any(p.startswith(".") for p in rel.parts):
                continue
            if candidate.name.endswith(".talk.md"):
                continue
            page_file = candidate
            break
        if page_file is None:
            return []

        try:
            with page_file.open(encoding="utf-8", errors="replace") as f:
                text = f.read(max_bytes)
        except OSError:
            return []
        lines = text.splitlines()

        matches: list = []
        last_heading = ""
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("## ") or stripped.startswith("### "):
                last_heading = stripped
                continue
            lower = line.lower()
            if any(term in lower for term in terms):
                # Find the next non-blank line for `after`
                after = ""
                for j in range(i + 1, min(i + 5, len(lines))):
                    if lines[j].strip():
                        after = lines[j].strip()
                        break
                matches.append(SnippetMatch(
                    line=i + 1,
                    before=last_heading,
                    match=line.strip(),
                    after=after,
                ))
                if len(matches) >= max_matches:
                    break
        return matches


def _entry_to_dict(entry: ManifestEntry) -> dict:
    return {
        "name": entry.name,
        "title": entry.title,
        "summary": entry.summary,
        "tags": entry.tags,
        "cluster": entry.cluster,
        "tokens": entry.tokens,
        "sections": [{"name": s.name, "tokens": s.tokens} for s in entry.sections],
        "links_to": entry.links_to,
        "links_from": entry.links_from,
        "read_count": entry.read_count,
        "usefulness": entry.usefulness,
        "authority": entry.authority,
        "last_corroborated": entry.last_corroborated,
    }


def _entry_from_dict(data: dict) -> ManifestEntry:
    return ManifestEntry(
        name=data["name"],
        title=data["title"],
        summary=data["summary"],
        tags=data["tags"],
        cluster=data["cluster"],
        tokens=data["tokens"],
        sections=[SectionInfo(**s) for s in data["sections"]],
        links_to=data["links_to"],
        links_from=data["links_from"],
        read_count=data.get("read_count", 0),
        usefulness=data.get("usefulness", 0.0),
        authority=data.get("authority", 0.0),
        last_corroborated=data.get("last_corroborated"),
    )
