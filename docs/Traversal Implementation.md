# Traversal Implementation

Concrete implementation details for multi-turn wiki traversal.

---

## Tool Contract

### read_file

```python
def read_file(path: str) -> dict:
    """
    Read a single wiki page.

    Returns:
    {
        "path": str,           # File path
        "content": str,        # Full markdown content
        "metadata": dict,      # Parsed frontmatter (if any)
        "links": list[str],    # All wikilinks found in the page
        "citations": int,     # Number of citations from this page
        "last_modified": str,  # ISO timestamp
    }
    """
    ...
```

### search_index

```python
def search_index(query: str, limit: int = 10) -> dict:
    """
    Search wiki/index.md for relevant pages.

    Returns:
    {
        "results": [
            {
                "path": str,
                "title": str,
                "summary": str,
                "topic": str,
                "relevance": float,  # 0-1
            }
        ],
        "total_matches": int,
    }
    """
    ...
```

---

## Internal Working Memory Format

The agent's "internal" summary between turns follows a structured format:

```markdown
## Traversal Working Memory (Turn N)

### Pages Read So Far
- [[wiki/topic/article-1.md]] — Learned: X, Y, Z. Links to [[wiki/other/article-2.md]]
- [[wiki/topic/article-2.md]] — Learned: A, B, C. Mentions [[wiki/another/article-3.md]]

### Questions Remaining
- How does X relate to A?
- Need more detail on Y.

### Next Candidate Pages
- [[wiki/another/article-3.md]] — High priority (mentioned by article-2)
- [[wiki/topic/article-4.md]] — Low priority (tangential)

### Hypothesis
X and A are related through mechanism Z. Need to verify in article-3.
```

This structure:
- **Tracks read history** — what was learned from each page
- **Maintains question queue** — what still needs answering
- **Prioritizes next steps** — candidate pages ranked by relevance
- **Hypothesis tracking** — agent's working theory, refined each turn

---

## Decision Logic

### When to Continue Reading

Agent continues traversal when:

| Condition | Reason |
|-----------|--------|
| New citations found | Unexplored paths |
| Internal questions remain | Query not fully answered |
| Hypothesis not verified | Working theory needs evidence |
| Contradiction detected | Need to resolve conflict |
| Ambiguous information | Need more detail |

### When to Stop and Answer

Agent stops traversal when:

| Condition | Reason |
|-----------|--------|
| Query fully answered | All sub-questions resolved |
| All candidates exhausted | No more relevant pages |
| Hypothesis confirmed | Working theory validated |
| Dead end reached | Pages don't contain relevant info |
| Context budget (optional) | Hit token limit |

### Prioritizing Next Page

Agent scores candidate pages:

```python
score = (
    citation_count * 0.4 +      # How many times linked
    relevance_match * 0.3 +       # How relevant to query
    novelty * 0.2 +               # How new (not seen before)
    hypothesis_support * 0.1      # How much it helps test hypothesis
)
```

- **citation_count**: How many times this page is linked by read pages
- **relevance_match**: Semantic similarity to query (can be precomputed via embeddings)
- **novelty**: 1 if unseen, 0 if already read
- **hypothesis_support**: How much this page helps verify/ refute working theory

---

## Traversal State Machine

```
                    ┌─────────────┐
                    │   Search    │
                    │   index.md  │
                    └──────┬──────┘
                           │
                           ▼
              ┌──────────────────────┐
              │   Pick start page   │
              └──────┬─────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────┐
│            READ PAGE LOOP                  │
│  ┌─────────────────────────────────────┐  │
│  │  read_file(page)                    │  │
│  └──────┬──────────────────────────────┘  │
│         │                                   │
│         ▼                                   │
│  ┌─────────────────────────────────────┐  │
│  │  Update working memory:              │  │
│  │  - What learned                      │  │
│  │  - New questions                    │  │
│  │  - New links found                   │  │
│  │  - Update hypothesis                 │  │
│  └──────┬──────────────────────────────┘  │
│         │                                   │
│         ▼                                   │
│  ┌─────────────────────────────────────┐  │
│  │  Decision: Continue or Stop?       │  │
│  └──────┬──────────────────────────────┘  │
│         │                                   │
│    ┌────┴────┐                            │
│    ▼         ▼                            │
│ Continue   Stop                            │
│    │         │                             │
│    ▼         ▼                            │
│ Pick next │ Answer with citations           │
│   page    └───────────────────────────────┘
│    │
│    └───────────────────────┐
                            │
                            ▼
                    ┌─────────────┐
                    │   SYNTHESIZE │
                    └─────────────┘
```

---

## Example Traversal Log

### User Query
"How do we validate sRNA embeddings and what metrics do we use?"

### Turn 1
```
→ search_index("validate sRNA embeddings")
   Found: [[wiki/bioinformatics/srna-embeddings.md]] (relevance: 0.92)

→ read_file("wiki/bioinformatics/srna-embeddings.md")

Working Memory:
## Traversal Working Memory (Turn 1)

### Pages Read So Far
- [[wiki/bioinformatics/srna-embeddings.md]]
  - Learned: sRNA embeddings validated via PCA analysis and clustering
  - Links: [[wiki/bioinformatics/inter-rep-variant-analysis.md]],
           [[wiki/machine-learning/clustering-metrics.md]]

### Questions Remaining
- What clustering algorithm?
- What specific metrics?

### Next Candidate Pages
- [[wiki/bioinformatics/inter-rep-variant-analysis.md]] — High (mentioned for validation)
- [[wiki/machine-learning/clustering-metrics.md]] — High (mentioned for metrics)

### Hypothesis
Clustering uses silhouette scores; need to confirm.

Decision: Continue → read inter-rep-variant-analysis.md
```

### Turn 2
```
→ read_file("wiki/bioinformatics/inter-rep-variant-analysis.md")

Working Memory:
## Traversal Working Memory (Turn 2)

### Pages Read So Far
- [[wiki/bioinformatics/srna-embeddings.md]]
  - Learned: sRNA embeddings validated via PCA analysis and clustering
  - Links: [[wiki/bioinformatics/inter-rep-variant-analysis.md]],
           [[wiki/machine-learning/clustering-metrics.md]]

- [[wiki/bioinformatics/inter-rep-variant-analysis.md]]
  - Learned: Inter-rep variance uses silhouette scores for cluster quality
  - Links: [[wiki/machine-learning/clustering-metrics.md]], [[wiki/bioinformatics/srna-embeddings.md]]
  - New: Clustering = k-means with k=10

### Questions Remaining
- How are silhouette scores calculated?
- What's the silhouette score threshold for "good" clusters?

### Next Candidate Pages
- [[wiki/machine-learning/clustering-metrics.md]] — High (need detailed metric info)

### Hypothesis
Validation uses k-means clustering (k=10) + PCA + silhouette scores.
Threshold not specified; need to check clustering-metrics.md.

Decision: Continue → read clustering-metrics.md
```

### Turn 3
```
→ read_file("wiki/machine-learning/clustering-metrics.md")

Working Memory:
## Traversal Working Memory (Turn 3)

### Pages Read So Far
- [[wiki/bioinformatics/srna-embeddings.md]]
  - Learned: sRNA embeddings validated via PCA analysis and clustering
  - Links: [[wiki/bioinformatics/inter-rep-variant-analysis.md]],
           [[wiki/machine-learning/clustering-metrics.md]]

- [[wiki/bioinformatics/inter-rep-variant-analysis.md]]
  - Learned: Inter-rep variance uses silhouette scores for cluster quality
  - Links: [[wiki/machine-learning/clustering-metrics.md]], [[wiki/bioinformatics/srna-embeddings.md]]
  - New: Clustering = k-means with k=10

- [[wiki/machine-learning/clustering-metrics.md]]
  - Learned: Silhouette score = (-1 to 1), >0.5 good, <0.2 poor
  - Links: [[wiki/machine-learning/pca.md]]

### Questions Remaining
- None (fully answered)

### Next Candidate Pages
- None

### Hypothesis
Validated: k-means clustering (k=10) + PCA + silhouette scores (>0.5 good).

Decision: Stop → Answer
```

### Turn 4 (Answer)
```
→ synthesize answer

Answer:

sRNA embeddings are validated using a three-step process:

1. **PCA Analysis**: Embeddings are projected via PCA to reduce dimensionality
   before clustering [[wiki/bioinformatics/srna-embeddings.md]].

2. **K-means Clustering**: Clustering is performed with k=10 clusters
   [[wiki/bioinformatics/inter-rep-variant-analysis.md]].

3. **Silhouette Score Evaluation**: Cluster quality is measured using silhouette
   scores, where scores > 0.5 indicate well-separated clusters and < 0.2 indicate
   poor structure [[wiki/machine-learning/clustering-metrics.md]].

The validation pipeline is documented in the inter-rep variant analysis workflow,
which measures consistency across different embedding representations.

**Citation Path:**
srna-embeddings.md → inter-rep-variant-analysis.md → clustering-metrics.md
```

---

## Implementation Prototype

### Simple Traversal Loop

```python
class WikiTraversal:
    def __init__(self, wiki_root: str):
        self.wiki_root = Path(wiki_root)
        self.working_memory = []
        self.read_pages = set()
        self.turn_count = 0

    def search_index(self, query: str) -> list[dict]:
        """Search wiki/index.md for relevant pages."""
        # Parse index.md, match summaries against query
        # Return sorted results with relevance scores
        ...

    def read_page(self, path: str) -> dict:
        """Read a single wiki page."""
        full_path = self.wiki_root / path
        content = full_path.read_text()

        # Extract wikilinks
        links = re.findall(r'\[\[(.*?)\]\]', content)

        return {
            "path": path,
            "content": content,
            "links": links,
        }

    def update_working_memory(self, page: dict, learned: str):
        """Update agent's working memory after reading a page."""
        self.turn_count += 1
        self.read_pages.add(page["path"])
        self.working_memory.append({
            "turn": self.turn_count,
            "page": page["path"],
            "learned": learned,
            "links": page["links"],
        })

    def decide_next_action(self) -> str:
        """Decide whether to continue reading or answer."""
        # Check stop conditions
        # Score candidate pages
        # Return "continue:<path>" or "answer"
        ...

    def traverse(self, query: str) -> dict:
        """Full traversal from query to answer."""
        # 1. Search index
        results = self.search_index(query)
        if not results:
            return {"answer": "No relevant pages found.", "citations": []}

        # 2. Read first page
        current_page = results[0]["path"]
        page_data = self.read_page(current_page)

        # 3. Traversal loop
        while True:
            # LLM learns from page and updates working memory
            learned = self.agent_learn_from_page(page_data)
            self.update_working_memory(page_data, learned)

            # Decide next action
            action = self.decide_next_action()

            if action.startswith("answer"):
                # Synthesize final answer
                answer = self.agent_synthesize_answer()
                return {
                    "answer": answer,
                    "citations": list(self.read_pages),
                    "turns": self.turn_count,
                }
            else:
                # Continue to next page
                next_page = action.split(":", 1)[1]
                page_data = self.read_page(next_page)
```

---

## Open Questions

1. **Working memory size** — How many turns of history to keep? Drop oldest?
2. **Cycle detection** — How to prevent agent from re-reading pages?
3. **Dead end handling** — What if agent picks a page that's completely irrelevant?
4. **Parallel reading** — Could we read 2-3 pages in parallel and merge working memory?
5. **Context window** — What if agent runs out of context before finishing?

---

## Related

- [[Multi-Turn Traversal Pattern]] — The parent pattern
- [[LLM Wiki - Knowledge Base Pattern]] — The wiki this traversal operates on
- [[Graph Search Algorithms]] — BFS, DFS, A* applied to wiki graphs
