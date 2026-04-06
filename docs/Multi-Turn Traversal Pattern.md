# Multi-Turn Traversal Pattern

How agents navigate the wiki without reading everything at once.

---

## Problem

A query might require synthesizing information from 10+ wiki pages. Reading all of them at once and stuffing them into context is:
- Inefficient (context tokens, latency)
- Inflexible (pre-commits to pages that may not be relevant)
- Wasteful (reads pages that end up being tangential)

## Solution: Adaptive Multi-Turn Traversal

The agent traverses the wiki graph incrementally, building up context through multiple turns. Each turn:
1. Reads one page
2. Summarizes what it learned internally
3. Decides what to read next (or answer)
4. Makes a tool call to read the next page

## Example

```
Turn 1:
  Context: User query "How do we validate sRNA embeddings?"
  → Tool: read_file("wiki/index.md")
  → Internal: "Index shows [[bioinformatics/srna-embeddings.md]] and [[bioinformatics/inter-rep-variant-analysis.md]]"
  → Tool: read_file("wiki/bioinformatics/srna-embeddings.md")

Turn 2:
  Context: [Turn 1 summary] + [[srna-embeddings.md]] content
  → Internal: "Embeddings validated via PCA, clustering. Links to [[machine-learning/clustering-metrics.md]]"
  → Tool: read_file("wiki/bioinformatics/inter-rep-variant-analysis.md")

Turn 3:
  Context: [Turn 2 summary] + [[inter-rep-variant-analysis.md]] content
  → Internal: "Inter-rep variance uses silhouette scores. References [[clustering-metrics.md]] for details. Enough context."
  → No tool call → emit answer with citations:
     "sRNA embeddings are validated using PCA analysis and clustering with silhouette scores (see [[bioinformatics/srna-embeddings.md]], [[bioinformatics/inter-rep-variant-analysis.md]]). Clustering metrics are detailed in [[machine-learning/clustering-metrics.md]]."
```

## Key Components

### 1. Internal Working Memory

The agent's "internal" summary between turns is its working memory. It tracks:
- What has been read
- What was learned from each page
- What connections were found
- What still needs to be investigated

This is NOT written to the wiki — it's ephemeral agent state.

### 2. Tool-Based Pagination

The traversal is driven by tool calls:
- `read_file()` reads one page
- Agent decides what to read next based on what it learned
- Agent decides when to stop (enough context, or dead end)

No fixed N-pages budget. The agent self-terminates.

### 3. Citation Path Construction

Every turn that reads a page adds to the citation chain. The final answer has a full provenance trail:
- Which pages were read
- In what order
- What each page contributed

This makes answers verifiable.

## Why This Works

| Benefit | Explanation |
|---------|-------------|
| **Adaptive** | Agent adjusts traversal based on what it finds, doesn't pre-commit |
| **Efficient** | Only reads relevant pages, doesn't waste context |
| **Transparent** | Each turn shows the decision-making process |
| **Verifiable** | Full citation path from start to finish |
| **Scalable** | Works whether the wiki has 10 pages or 10,000 |

## Comparison

| Approach | Context per Turn | Latency | Flexibility |
|----------|-----------------|---------|-------------|
| **Read all at once** | All pages upfront | One turn, high context | Low (pre-committed) |
| **Multi-turn traversal** | Cumulative summary + current page | Multiple turns, low context per turn | High (adaptive) |

## Implementation Notes

### Tool Contract

The traversal relies on a simple tool contract:
```python
def read_file(path: str) -> str:
    """Read a single wiki page. Returns full markdown content."""
    ...
```

The agent uses wikilinks from page content to discover related pages:
- Internal links: `[[wiki/topic/article.md]]`
- Backlinks: inferred from index or parsed from markdown

### Termination Conditions

Agent should stop when:
- It has synthesized a complete answer
- It hits a dead end (no relevant pages left)
- It reaches a context budget (optional, not required)
- The query is answered to its satisfaction

### Breadcrumbs

Some implementations may want to emit "breadcrumb" messages between turns:
```
→ Read [[bioinformatics/srna-embeddings.md]]
  Learned: Validation uses PCA + clustering
→ Read [[bioinformatics/inter-rep-variant-analysis.md]]
  Learned: Silhouette scores measure cluster quality
→ Synthesizing answer...
```

This gives visibility into the traversal process.

## Connection to Librarian + Worker

In the Librarian + Worker architecture:

**Librarian** (QueryAgent):
- Performs multi-turn traversal
- Maintains internal working memory
- Synthesizes answer with citations
- Verifies citation paths exist

**Worker** (user-facing):
- Receives final answer from Librarian
- Doesn't see traversal process
- Gets clean, cited response

---

## Open Questions

1. **How many turns is too many?** At what point should the agent admit it can't find the answer?
2. **Should traversal be logged?** Save breadcrumbs to wiki/log.md for audit?
3. **Can traversal be cached?** If the same query is asked again, can we reuse the traversal path?
4. **How to handle cycles?** What if the agent loops back to a page it already read?

---

## Related Patterns

- [[LLM Wiki - Knowledge Base Pattern]] — The parent pattern this traversal supports
- [[Graph Search]] — General pattern for graph-based traversal
- [[Incremental Context Building]] — Alternative: accumulate full page content instead of summaries
