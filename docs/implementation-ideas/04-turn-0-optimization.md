# Turn 0 Optimization

**Status:** Draft Design Idea

**Context:** Multi-turn wiki traversal

---

## Problem

Most queries are simple: "What is k-means?" "Who discovered X?" "When was Y published?"

These don't need multi-turn traversal. A single page read should suffice.

**But**: The current traversal pattern assumes multi-turn always. It:
- Starts with search_index()
- Invokes traversal skill
- Builds working memory
- Summarizes (even for single page)

Overhead for no reason.

---

## Solution: Turn 0 = Direct Page Read

**Two paths:**

### Path A: Turn 0 (Simple Query)
```
User: "What is k-means?"
→ search_index("k-means")
→ Top result: [[machine-learning/k-means.md]]
→ Direct read: page.content
→ Is answer self-contained? Yes
→ Emit page content directly
→ NO working memory, NO summarization, NO multi-turn
```

### Path B: Turn 1+ (Complex Query)
```
User: "How do we validate sRNA embeddings?"
→ search_index("validate sRNA embeddings")
→ Top result: [[bioinformatics/srna-embeddings.md]]
→ Direct read: page.content
→ Is answer self-contained? No (mentions clustering-metrics.md without explaining)
→ INVOKE traversal skill
→ Multi-turn traversal with working memory
→ Summarize → synthesize answer
```

---

## Self-Contained Check

How does the system know if a page is self-contained?

### Heuristics

| Heuristic | Implementation | False Positive? | False Negative? |
|-----------|-----------------|------------------|-----------------|
| **No outbound links** | Count wikilinks = 0 | Yes (page has no links but doesn't answer query) | Rare |
| **Link count ≤ 2** | wikilinks ≤ 2 | Yes (links to tangential topics) | Common |
| **Query keyword density** | Query words appear in page | Maybe (page mentions query but doesn't answer) | No |
| **Answer keyword detection** | Look for "X is Y", "X refers to", etc. | No | Common |
| **LLM judgment** | Call LLM: "Is this page a complete answer?" | Expensive | No |

### LLM Judgment (Best)

```
Prompt:

You are evaluating whether a wiki page is a complete answer to a query.

Query: "How do we validate sRNA embeddings?"

Page content: [insert page content]

Question: Is this page a complete, self-contained answer to the query?

Consider:
- Does it address all parts of the query?
- Does it explain referenced concepts, or assume prior knowledge?
- Does it end with "see [[other-page.md]]" (incomplete)?

Answer "YES" if complete, "NO" if incomplete.
```

**Cost:** One LLM call per Turn 0 attempt.
**Trade-off:** Expensive, but accurate. Most queries will be Turn 0, so cost is amortized.

---

## Summarization Harness

**Key insight**: Summarization is a harness prompt that happens when traversal skill is invoked, not during traversal loop itself.

### Flow

```
Turn 0:
  → Read page
  → Check: self-contained?
  → YES: Emit page directly (no summarization)
  → NO: Invoke traversal skill

Turn 1+ (traversal skill):
  → Read page
  → HARNESS: "Summarize what you learned from this page"
  → Update working memory
  → Continue
```

### Harness Prompt Template

```
You are on Turn {{turn}} of a wiki traversal.

Page: [[{{page_path}}]]

Content:
{{page_content}}

Working memory so far:
{{working_memory}}

Task:
1. Read the page content carefully.
2. Summarize what you learned from this page (max 200 words).
3. Extract all wikilinks from the page.
4. Identify any new questions raised.
5. Decide: do you need to read more pages, or do you have enough to answer?

Output:
LEARNED: [your summary]
LINKS: [[link1]], [[link2]], ...
QUESTIONS: [new questions]
DECISION: CONTINUE [[next-page]] | ANSWER
```

---

## Implementation Sketch

```python
@dataclass
class Turn0Result:
    page: str
    content: str
    is_complete: bool

class Turn0Handler:
    def __init__(self, wiki_root: str):
        self.wiki_root = Path(wiki_root)

    def try_turn0(self, query: str) -> Optional[str]:
        """
        Attempt Turn 0 (direct page read).

        Returns:
            Page content if complete answer
            None if traversal needed
        """
        # 1. Search index
        results = self.search_index(query)
        if not results:
            return None

        # 2. Read top page
        top_page = results[0].path
        page_content = self.read_page(top_page)

        # 3. Check if self-contained
        is_complete = self.check_self_contained(query, page_content)

        if is_complete:
            return page_content
        else:
            return None

    def check_self_contained(self, query: str, content: str) -> bool:
        """Use LLM to check if page is complete answer."""
        prompt = f"""
        Query: {query}

        Page content: {content[:4000]}  # Truncate if needed

        Is this page a complete, self-contained answer to the query?
        Answer "YES" or "NO".
        """

        response = llm_call(prompt)
        return response.strip().upper() == "YES"
```

---

## Performance Impact

Assumptions:
- 70% of queries are Turn 0 (simple)
- 30% require multi-turn (complex)

| Approach | Turn 0 queries | Multi-turn queries | Avg LLM calls |
|-----------|-----------------|-------------------|----------------|
| **Always multi-turn** | 5-10 calls | 5-10 calls | 7-8 calls |
| **Turn 0 + multi-turn** | 2 calls (search + check) | 2 + (5-10) = 7-12 calls | 3.5 calls |

**~50% reduction** in LLM calls for simple queries.

---

## Edge Cases

### Case 1: Page has links but still complete
```
Query: "What is the boiling point of water?"
Page: "Water boils at 100°C at sea level. [[See Also]]: [[states-of-matter.md]]"
```
**Decision:** Complete. Links are "See Also", not "see this for answer".

**LLM judgment** handles this nuance.

### Case 2: Page has no links but incomplete
```
Query: "How do we validate sRNA embeddings?"
Page: "sRNA embeddings are validated using silhouette scores."
```
**Decision:** Incomplete. Doesn't explain silhouette scores.

**LLM judgment** catches this.

### Case 3: Multiple candidate pages
```
Query: "How do we validate sRNA embeddings?"
Results:
- [[srna-embeddings.md]] - Summary of validation pipeline
- [[inter-rep-variant-analysis.md]] - Detailed variance analysis
```
**Decision:** Turn 0 tries top result (srna-embeddings.md). If incomplete, fall back to multi-turn.

**Optimization:** Could pre-check both candidates (parallel), but latency trade-off.

---

## Open Questions

1. **What's the right self-contained prompt?** Balance accuracy vs. false positives?
2. **Should we check multiple candidates on Turn 0?** Top 2? Top 3? Parallel?
3. **How to handle page truncation?** If page is 20k tokens, can't fit in prompt for self-contained check.
4. **Can we cache self-contained results?** If page X is complete for query Y, maybe reuse for similar Y'?
5. **Should Turn 0 be logged?** Add special entry to wiki/log.md? (Maybe for analytics)

---

## Related Ideas

- [[Pre-fetching with Metadata]] — Pre-fetch not needed if Turn 0 succeeds
- [[Compaction with /tmp Index]] — Compaction not needed for Turn 0
- [[Multi-Turn Traversal]] — This is the fallback for complex queries

---

## Notes

Turn 0 optimization is about **fast path for common cases**. Most queries don't need graph traversal — they just need the right page.

The key distinction is between:
- **Document retrieval** (Turn 0): Find the page, read it, done.
- **Knowledge synthesis** (Turn 1+): Read multiple pages, synthesize connections.

The self-contained check is the discriminator. LLM judgment is best, but we could explore hybrid approaches (heuristics + LLM fallback).
