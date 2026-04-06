# Pre-Seeding with Vector/Keyword Lookup

**Status:** Draft Design Idea

**Context:** Tool initialization for wiki traversal

---

## Problem

Current traversal:
- Starts with `search_index()` for top 1 result
- Agent reads page, then explores links

**Issue:**
- First page might not be the best starting point
- No opportunity for agent to choose among multiple candidates
- Wasted turns if first page is dead end

**Goal:** Give agent **multiple starting points** to choose from.

---

## Solution: Pre-Seeding on Tool Initialization

**When traversal tool is called:**
1. Perform vector + keyword search over all wiki pages
2. Present top-N candidates to agent
3. Agent chooses:
   - How many to pull into context?
   - Which ones are most relevant?
4. Agent reads selected pages, then continues traversal normally

**Like a "phase 0" traversal** — initial exploration before main loop.

---

## Search Strategy

**Hybrid search:**

```python
class PreSeeder:
    def __init__(self, wiki_root: str):
        self.wiki_root = Path(wiki_root)
        self.keyword_index = KeywordIndex(wiki_root)
        self.vector_index = VectorIndex(wiki_root)

    def search(self, query: str, limit: int = 10) -> List[dict]:
        """Hybrid search: keyword + vector."""
        # 1. Keyword search (BM25)
        keyword_results = self.keyword_index.search(query, limit=limit)
        keyword_dict = {r["path"]: r["score"] for r in keyword_results}

        # 2. Vector search (semantic)
        vector_results = self.vector_index.search(query, limit=limit)
        vector_dict = {r["path"]: r["score"] for r in vector_results}

        # 3. Combine and rank (reciprocal rank fusion)
        all_paths = set(keyword_dict.keys()) | set(vector_dict.keys())
        combined = []

        for path in all_paths:
            keyword_score = keyword_dict.get(path, 0)
            vector_score = vector_dict.get(path, 0)

            # Reciprocal rank fusion
            rr_keyword = 1.0 / (keyword_dict.get(path, 0) + 1)
            rr_vector = 1.0 / (vector_dict.get(path, 0) + 1)
            combined_score = rr_keyword + rr_vector

            combined.append({
                "path": path,
                "keyword_score": keyword_score,
                "vector_score": vector_score,
                "combined_score": combined_score,
            })

        # Sort by combined score
        combined.sort(key=lambda r: r["combined_score"], reverse=True)
        return combined[:limit]
```

---

## Agent Choice: How Many / Which Pages?

**Agent decides based on:**
- Query complexity (single fact vs. synthesis)
- Token budget (how much context fits)
- Candidate diversity (don't pull 10 pages on same topic)

### Decision Prompt

```
You are initializing wiki traversal for a query.

QUERY: {{query}}

TOP 10 SEARCH RESULTS:

1. [[wiki/bioinformatics/srna-embeddings.md]]
   - Keyword score: 0.82
   - Vector score: 0.91
   - Combined: 1.87
   - Tags: [validation, bioinformatics]
   - Preview: sRNA embeddings validated via PCA and clustering...

2. [[wiki/machine-learning/clustering-metrics.md]]
   - Keyword score: 0.78
   - Vector score: 0.88
   - Combined: 1.82
   - Tags: [metrics, evaluation]
   - Preview: Silhouette scores measure cluster quality...

... (8 more)

TASK:
1. Decide how many pages to read initially (0-10).
2. Decide which pages to read (choose by number).

Consider:
- Token budget: {{remaining_tokens}} tokens available
- Query complexity: Is this a simple lookup or complex synthesis?
- Candidate diversity: Don't choose all pages from same topic.

Output:
PAGES_TO_READ: [[page1]], [[page2]], ...
COUNT: N
```

### Example Agent Choices

| Query complexity | Pages chosen | Rationale |
|-----------------|----------------|------------|
| **Simple** ("What is k-means?") | 1-2 pages | Look up specific fact, don't need exploration |
| **Moderate** ("How do we validate sRNA embeddings?") | 3-5 pages | Need synthesis, explore related concepts |
| **Complex** ("Compare validation methods across all sRNA papers") | 7-10 pages | Comprehensive coverage, token budget permitting |

---

## Initialization Flow

```
User: "How do we validate sRNA embeddings?"

Tool invocation: traverse_wiki(query="...")

PHASE 0: Pre-seeding
  → Hybrid search (keyword + vector)
  → Top 10 results presented to agent
  → Agent chooses: "Read [[srna-embeddings.md]], [[clustering-metrics.md]], [[inter-rep-variant-analysis.md]]"

PHASE 1: Initial read
  → Agent reads all 3 pages in parallel
  → Summarizes each
  → Working memory has 3 summaries

PHASE 2: Normal traversal
  → Agent decides: continue or stop?
  → If continue: normal multi-turn traversal
```

---

## Implementation Sketch

```python
class WikiTraversalTool:
    def __init__(self, wiki_root: str):
        self.wiki_root = Path(wiki_root)
        self.pre_seeder = PreSeeder(wiki_root)
        self.max_initial_pages = 10

    def __call__(self, query: str, token_budget: int = 16000) -> dict:
        """Tool entry point."""

        # PHASE 0: Pre-seeding
        candidates = self.pre_seeder.search(query, limit=self.max_initial_pages)

        # Present to agent for choice
        choice_prompt = self._build_choice_prompt(
            query=query,
            candidates=candidates,
            token_budget=token_budget,
        )

        # Agent chooses pages to read
        agent_response = llm_call(choice_prompt)

        # Parse choice
        pages_to_read = self._parse_agent_choice(agent_response)

        # PHASE 1: Initial read (parallel)
        initial_summaries = self._read_pages_parallel(pages_to_read, query)

        # PHASE 2: Normal traversal
        working_memory = WorkingMemory(
            turns=[{**initial_summaries[p], "turn": i}
                   for i, p in enumerate(initial_summaries.values())]
        )

        # Continue traversal loop
        while True:
            if working_memory.should_stop():
                break

            next_page = self._decide_next_page(working_memory)
            summary = self._read_and_summarize(next_page, query, working_memory)
            working_memory.add(summary)

        # Synthesize final answer
        return working_memory.synthesize_answer()

    def _read_pages_parallel(
        self,
        pages: List[str],
        query: str
    ) -> Dict[str, dict]:
        """Read multiple pages in parallel."""
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(
                    self._read_and_summarize, page, query, WorkingMemory(turns=[])
                ): page
                for page in pages
            }

            results = {}
            for future in as_completed(futures):
                page = futures[future]
                results[page] = future.result()

            return results
```

---

## Index Construction

### Keyword Index (BM25)

```python
from rank_bm25 import BM25Okapi

class KeywordIndex:
    def __init__(self, wiki_root: str):
        self.wiki_root = Path(wiki_root)
        self.documents = []
        self._build_index()

    def _build_index(self):
        """Index all wiki pages."""
        for md_file in self.wiki_root.rglob("*.md"):
            if md_file.name in ["index.md", "log.md"]:
                continue

            content = md_file.read_text()
            self.documents.append({
                "path": str(md_file.relative_to(self.wiki_root)),
                "content": content,
            })

        # Build BM25 index
        self.bm25 = BM25Okapi([d["content"] for d in self.documents])

    def search(self, query: str, limit: int = 10) -> List[dict]:
        """Search keyword index."""
        scores = self.bm25.get_scores(query)
        ranked = sorted(
            scores.items(),
            key=lambda x: x[1],
            reverse=True
        )

        results = []
        for i, (doc_idx, score) in enumerate(ranked[:limit]):
            results.append({
                "path": self.documents[doc_idx]["path"],
                "keyword_score": score,
            })

        return results
```

### Vector Index (Semantic Search)

```python
class VectorIndex:
    def __init__(self, wiki_root: str):
        self.wiki_root = Path(wiki_root)
        self.embeddings = []
        self._load_embeddings()

    def _load_embeddings(self):
        """Load precomputed page embeddings."""
        # Assume embeddings.jsonl: {"path": "...", "embedding": [...]}
        with open(self.wiki_root / "embeddings.jsonl") as f:
            for line in f:
                data = json.loads(line)
                self.embeddings.append(data)

    def search(self, query: str, limit: int = 10) -> List[dict]:
        """Search vector index."""
        # Embed query
        query_embedding = embed_query(query)

        # Compute cosine similarity
        similarities = []
        for doc in self.embeddings:
            sim = cosine_similarity(query_embedding, doc["embedding"])
            similarities.append(sim)

        # Rank
        ranked = sorted(
            zip(self.embeddings, similarities),
            key=lambda x: x[1],
            reverse=True
        )

        results = []
        for i, (doc, score) in enumerate(ranked[:limit]):
            results.append({
                "path": doc["path"],
                "vector_score": score,
            })

        return results
```

---

## Open Questions

1. **What's the right limit for pre-seeding?** 10 candidates? 20? Adaptive based on query complexity?
2. **How to combine keyword + vector scores?** Reciprocal rank fusion? Weighted sum? Learned weights?
3. **Should agent see scores?** Or just preview/tags? (Scores might bias choices)
4. **What if agent chooses 0 pages?** Fall back to standard search_index()?
5. **Can we cache pre-seed results?** If query is repeated, reuse candidate list?
6. **Should pre-seeding be optional?** Some tools might want single starting point.

---

## Related Ideas

- [[Turn 0 Optimization]] — Pre-seeding is more aggressive Turn 0
- [[Pre-fetching with Metadata]] — Pre-seeding provides initial metadata
- [[Parallel Agents]] — Initial read can use parallel strategy

---

## Notes

Pre-seeding is about **giving agent choice** at the start, not just following links blindly.

The "phase 0" concept separates initialization (broad search) from traversal (focused exploration). Agent can:
- Choose multiple starting points for complex queries
- Choose single starting point for simple queries
- Skip pre-seeding entirely for direct lookups

This is more flexible than "always start with top-1 result".
