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
