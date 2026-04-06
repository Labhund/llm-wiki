# Pre-fetching with Metadata

**Status:** Draft Design Idea

**Context:** Multi-turn wiki traversal

---

## Problem

When agent reads a page, it sees wikilinks to other pages. It must decide which to read next, but has no information about:
- How big is that page? (500 tokens vs. 8000 tokens)
- How relevant is it? (on-topic vs. tangential)
- What's a preview of the content?

Bad decisions lead to:
- Wasted turns (reading huge irrelevant pages)
- Latency (slow page loads)
- Dead ends (page has nothing useful)

---

## Solution: Pre-Fetch Metadata on Page Read

**Don't just read pages — also fetch metadata for all linked pages** so agent can make informed decisions.

### Metadata to Pre-Fetch

| Metadata | How to get | Why it matters |
|-----------|--------------|-----------------|
| **Token count** | Word count × 1.3 or actual tokenizer | Avoid huge pages |
| **Embedding similarity** | Precomputed page embeddings | Relevance to query |
| **First paragraph** | Parse from page content | Quick preview |
| **Link count** | Parse wikilinks | Hub pages vs. leaf pages |
| **Last modified** | File stat | Freshness |
| **Tags** | Parse from frontmatter / metadata | Page classification, domain, relevance |

### Pre-Fetch Flow

```
Turn1: Agent reads [[srna-embeddings.md]]

Harness:
  1. Read page content
  2. Extract all wikilinks: [[clustering-metrics.md]], [[inter-rep-variant-analysis.md]], [[machine-learning/pca.md]]
  3. Pre-fetch metadata for each:
     - [[clustering-metrics.md]]: 500 tokens, relevance: 0.92, tags: [validation, metrics]
     - [[inter-rep-variant-analysis.md]]: 1200 tokens, relevance: 0.78, tags: [variants, bioinformatics]
     - [[machine-learning/pca.md]]: 8000 tokens, relevance: 0.45, tags: [background, linear-algebra]
  4. Return page + metadata to agent

Agent decision:
  → clustering-metrics.md: Small + highly relevant → read next
  → inter-rep-variant-analysis.md: Moderate + relevant → candidate
  → pca.md: Huge + low relevance → skip unless needed
```

---

## Implementation Sketch

### Pre-Fetch Function

```python
@dataclass
class PageMetadata:
    path: str
    token_count: int
    relevance: float          # Embedding similarity to query
    first_paragraph: str
    link_count: int
    last_modified: str
    tags: List[str]         # Page tags from frontmatter

class MetadataPrefetcher:
    def __init__(self, wiki_root: str):
        self.wiki_root = Path(wiki_root)
        self.embedding_cache = {}  # path → precomputed embedding

    def _estimate_tokens(self, text: str) -> int:
        # Fast estimate: word count × 1.3
        return int(len(text.split()) * 1.3)

    def _load_embedding(self, page_path: str) -> np.ndarray:
        # Precomputed embeddings for all pages
        # Could be stored in .jsonl or .parquet
        if page_path not in self.embedding_cache:
            self.embedding_cache[page_path] = self._load_cached_embedding(page_path)
        return self.embedding_cache[page_path]

    def prefetch_links(self, page_path: str, query: str) -> List[PageMetadata]:
        """Pre-fetch metadata for all links in a page."""
        page_content = (self.wiki_root / page_path).read_text()

        # Extract links
        links = re.findall(r'\[\[(.*?)\]\]', page_content)

        # Fetch metadata for each
        metadata_list = []
        query_embedding = self._compute_query_embedding(query)

        for link in links:
            link_content = (self.wiki_root / link).read_text()

            # Token count
            tokens = self._estimate_tokens(link_content)

            # Relevance (embedding similarity)
            link_embedding = self._load_embedding(link)
            relevance = cosine_similarity(query_embedding, link_embedding)

            # First paragraph
            paragraphs = link_content.split('\n\n')
            first_para = paragraphs[0] if paragraphs else ""

            # Link count
            link_count = len(re.findall(r'\[\[(.*?)\]\]', link_content))

            # Tags (from frontmatter)
            tags = self._parse_tags(link_content)

            # Last modified
            last_modified = datetime.fromtimestamp(
                (self.wiki_root / link).stat().st_mtime
            ).isoformat()

            metadata_list.append(PageMetadata(
                path=link,
                token_count=tokens,
                relevance=relevance,
                first_paragraph=first_para[:200],  # Truncate
                link_count=link_count,
                last_modified=last_modified,
                tags=tags,
            ))

        return metadata_list

    def _parse_tags(self, content: str) -> List[str]:
        """Parse tags from YAML frontmatter."""
        # Format: ---\ntags: [tag1, tag2]\n---\nContent
        match = re.match(r'^---\ntags:\s*\[([^\]]+)\]\n---', content, re.MULTILINE)
        if match:
            tags_str = match.group(1)
            # Split by comma and trim
            return [tag.strip() for tag in tags_str.split(',')]
        return []
```

### Agent Decision-Making with Metadata

```
Agent internal:

"Pre-fetched candidates:
1. [[clustering-metrics.md]] - 500 tokens, relevance: 0.92
   Tags: [validation, metrics]
   Preview: Silhouette scores measure cluster quality, ranging from -1 to 1...

2. [[inter-rep-variant-analysis.md]] - 1200 tokens, relevance: 0.78
   Tags: [variants, bioinformatics]
   Preview: Analysis of variant behavior across different embeddings...

3. [[machine-learning/pca.md]] - 8000 tokens, relevance: 0.45
   Tags: [background, linear-algebra]
   Preview: PCA reduces dimensionality via orthogonal transformation...

Decision:
- clustering-metrics.md: High relevance + 'validation' tag matches query → read next
- inter-rep-variant-analysis.md: Moderate relevance + 'bioinformatics' tag → candidate
- pca.md: Low relevance + 'background' tag → skip unless needed

Next action: read [[clustering-metrics.md]]"

## Programmatic Harness Choices

The pre-fetch behavior is configurable via harness:

```yaml
# ~/.hermes/llm-wiki/config.yaml
traversal:
  prefetch:
    enabled: true

    # Metadata to fetch
    metadata:
      - token_count
      - embedding_similarity_to_query
      - first_paragraph_preview
      - link_count
      - last_modified

    # Decision thresholds (agent can override per-query)
    thresholds:
      max_tokens_per_page: 5000
      min_relevance_threshold: 0.7
      prefer_short_pages: true

    # Pre-fetch limits (don't fetch 50 links)
    limits:
      max_links_to_prefetch: 20
      cap_by_relevance: true  # Only pre-fetch top-N by relevance
```

### Per-Query Override

Agent can tune on the fly:

```
"Skip pre-fetching for [[huge-background-paper.md]] — I know it's 20k tokens
and likely not relevant. Just pre-fetch high-relevance links (>0.8)."
```

---

## Pre-Computing Embeddings

For embedding similarity to work efficiently:

### Build Embedding Cache

```bash
# Run once to pre-compute embeddings for all wiki pages
python3 src/build_embeddings.py --wiki ~/repos/llm-wiki/wiki --output embeddings.jsonl
```

### Embedding File Format

```jsonl
{"path": "wiki/bioinformatics/srna-embeddings.md", "embedding": [0.123, -0.456, ...]}
{"path": "wiki/machine-learning/clustering-metrics.md", "embedding": [0.234, -0.567, ...]}
```

### Load on Startup

```python
class EmbeddingCache:
    def __init__(self, embeddings_file: str):
        self.embeddings = {}
        with open(embeddings_file) as f:
            for line in f:
                data = json.loads(line)
                self.embeddings[data["path"]] = np.array(data["embedding"])

    def get(self, path: str) -> np.ndarray:
        return self.embeddings.get(path)
```

---

## Latency Considerations

| Operation | Cost | Notes |
|-----------|-------|-------|
| Read page | 10-50ms | File I/O |
| Parse links | <1ms | Regex |
| Token count | <1ms | Word count × 1.3 |
| Embedding lookup | <1ms | Array lookup (precomputed) |
| First paragraph | <1ms | Parse + truncate |

**Total pre-fetch per page:** ~50-60ms for 10-20 links.

This is negligible compared to LLM call time (500ms-5s).

---

## Open Questions

1. **What's the right max_links_to_prefetch?** 10? 20? 50? Adaptive based on page link count?
2. **Should relevance be weighted by page size?** A 200-token page with relevance 0.8 is better than an 8000-token page with relevance 0.9.
3. **How to handle non-existent links?** Skip silently? Report to agent?
4. **Should embeddings be recomputed periodically?** When pages change, embeddings get stale.
5. **Can agent request additional metadata on demand?** "I need the full first paragraph, not just 200 chars."
6. **What about circular links?** A → B → A — pre-fetch could get stuck.

---

## Related Ideas

- [[Intelligent Re-Reading]] — Pre-fetch might avoid need to re-read
- [[Compaction with /tmp Index]] — Metadata can inform compaction decisions
- [[Turn 0 Optimization]] — Pre-fetch not needed if answer is on first page

---

## Notes

Pre-fetching turns "what page to read next?" from a blind choice into an informed decision.

The key insight is that metadata is cheap (file stats, simple parsing, precomputed embeddings) but LLM calls are expensive. Pre-fetching a bit of metadata to save a wasted LLM call is a win.

Potential optimization: **predictive pre-fetching**. If agent always reads B after A, pre-fetch B when A is read (before agent even asks).
