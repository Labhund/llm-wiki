# Parallel Agents

**Status:** Draft Design Idea

**Context:** Multi-turn wiki traversal latency

---

## Problem

Sequential traversal has latency:
- 10 turns = 10 LLM calls
- Each call adds 500ms-5s
- Total: 5-50 seconds for complex queries

For interactive use, this is slow.

But we also don't want to sacrifice traversal quality.

---

## Solution: Parallel Agent Strategies

Multiple approaches, different trade-offs.

---

## Strategy A: Parallel Page Reading

**Idea:** Read multiple pages in parallel within a turn.

```
Turn 1:
  → Subagent 1: Read [[srna-embeddings.md]]
  → Subagent 2: Read [[clustering-metrics.md]]
  → Subagent 3: Read [[inter-rep-variant-analysis.md]]

Turn 2:
  → Main agent receives all 3 summaries
  → Synthesizes answer from all three
```

### Pros
- Faster (3× per turn)
- Diverse exploration
- Can find cross-page patterns immediately

### Cons
- Higher cost per turn (3 LLM calls vs. 1)
- Harder to reason about causality (did A inform B, or are they independent?)
- May read irrelevant pages (waste)

### When to use
- Candidate pages are clearly relevant and diverse
- Exploration phase (early turns)
- When latency matters more than cost

---

## Strategy B: Parallel Traversal Threads

**Idea:** Multiple independent traversal paths, merged at end.

```
Thread 1: A → B → C → D
Thread 2: A → E → F → G
Thread 3: A → H → I → J

All threads complete:
  → Main agent merges all working memories
  → Synthesizes final answer
```

### Pros
- Diverse exploration (doesn't get stuck in one sub-graph)
- Redundancy (if one thread fails, others succeed)
- Can find unexpected connections

### Cons
- Complexity explosion (thread coordination, merging)
- Expensive (3× sequential cost)
- May read overlapping pages (A read 3 times)

### When to use
- High-stakes queries (need comprehensive coverage)
- Exploration of unknown topic space
- Batch queries (overnight, latency doesn't matter)

---

## Strategy C: Parallel Candidate Evaluation

**Idea:** Sequential traversal, but evaluate candidates in parallel.

```
Turn 1: Read [[srna-embeddings.md]]
  → Candidates: [[clustering-metrics.md]], [[inter-rep-variant-analysis.md]], [[pca.md]]

Turn 2 (parallel):
  → Subagent 1: Read [[clustering-metrics.md]], summarize
  → Subagent 2: Read [[inter-rep-variant-analysis.md]], summarize
  → Subagent 3: Read [[pca.md]], summarize

Turn 3:
  → Main agent receives all 3 summaries
  → Decides: which are relevant? which to read next?
  → Continue with best candidate
```

### Pros
- Speeds up candidate evaluation (main bottleneck)
- Maintains sequential decision-making (agent control)
- Cheaper than full parallel traversal (only evaluation, not full traversal)

### Cons
- Still sequential main loop (not 3× faster, just faster per turn)
- May read irrelevant pages (waste)
- Coordination overhead

### When to use
- **Default option** — good balance of speed vs. control
- When candidate pages are diverse and relevance is uncertain
- For interactive queries (latency matters but not critical)

---

## Strategy D: Parallel Subquery Decomposition

**Idea:** Decompose complex query into subqueries, answer in parallel.

```
User: "How do we validate sRNA embeddings and what metrics do we use?"

Decompose:
  - Subquery 1: "How do we validate sRNA embeddings?"
  - Subquery 2: "What metrics do we use for clustering validation?"

Turn 1 (parallel):
  → Agent 1: Answer subquery 1 (traversal)
  → Agent 2: Answer subquery 2 (traversal)

Turn 2:
  → Main agent merges both answers
  → Synthesizes: combined answer
```

### Pros
- True parallelism (independent queries)
- Can use different agents for different subdomains
- Natural fit for complex queries

### Cons
- Requires query decomposition (LLM task)
- May overlap (both agents read same pages)
- Hard to coordinate (contradictions?)

### When to use
- Queries with clear sub-structure ("and", "or", "compare")
- When agents have different specializations
- High-latency acceptable for quality

---

## Implementation Sketch (Strategy C)

```python
from concurrent.futures import ThreadPoolExecutor

class ParallelCandidateEvaluator:
    def __init__(self, wiki_root: str, max_workers: int = 3):
        self.wiki_root = Path(wiki_root)
        self.max_workers = max_workers

    def evaluate_candidate(self, page_path: str, query: str) -> dict:
        """Subagent: read and summarize a candidate page."""
        # Create isolated working directory for subagent
        agent = TraversalAgent(wiki_root=self.wiki_root, fresh_context=True)

        # Read and summarize
        summary = agent.summarize_page(page_path, query)

        return {
            "path": page_path,
            "summary": summary,
            "relevant": summary.relevance_to_query(query),
        }

    def evaluate_parallel(self, candidates: List[str], query: str) -> List[dict]:
        """Evaluate all candidates in parallel."""
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(
                    self.evaluate_candidate, page, query
                ): page
                for page in candidates
            }

            results = []
            for future in as_completed(futures):
                results.append(future.result())

        return results

class TraversalAgent:
    def __init__(self, wiki_root: str):
        self.wiki_root = Path(wiki_root)
        self.parallel_evaluator = ParallelCandidateEvaluator(wiki_root)

    def traverse_with_parallel_eval(self, query: str) -> dict:
        """Sequential traversal with parallel candidate evaluation."""
        # Turn 1: Read first page
        first_page = self.search_and_read(query)
        working_memory = self.summarize_page(first_page, query)

        # Traversal loop
        while True:
            # Get candidates from current page
            candidates = working_memory.candidates

            # Turn 2+: Evaluate candidates in parallel
            if candidates:
                candidate_results = self.parallel_evaluator.evaluate_parallel(
                    candidates[:3],  # Top 3 only
                    query
                )

                # Pick best candidate
                best = max(candidate_results, key=lambda r: r["relevant"])

                # Update working memory
                working_memory.add_summary(best)

                # Continue or stop?
                if working_memory.should_stop():
                    break
            else:
                break

        # Synthesize final answer
        return working_memory.synthesize_answer()
```

---

## Latency Comparison

| Strategy | Turns | Parallel LLM Calls | Sequential LLM Calls | Total Time (assuming 1s/LLM call) |
|-----------|--------|---------------------|---------------------|------------------------------------|
| **Sequential** | 10 | 0 | 10 | 10s |
| **Parallel page reading** | 4 | 3 per turn | 0 | 4s (12 LLM calls) |
| **Parallel threads** | 4 | 3 threads | 0 | 4s (12 LLM calls) |
| **Parallel candidate eval** | 8 | 3 per eval turn | 5 | 8s (8 seq + 12 parallel = 20 LLM calls) |
| **Parallel subqueries** | 4 | 2 per subquery | 0 | 4s (8 LLM calls) |

**Trade-off:** Parallel strategies reduce latency but increase LLM calls (cost).

---

## Open Questions

1. **Which strategy is default?** Strategy C (parallel candidate eval) seems most balanced.
2. **What's the right max_workers?** 3? 5? 10? (Cost vs. latency)
3. **Can we dynamically switch strategies?** Turn 1-2: parallel, later turns: sequential?
4. **How to handle overlapping pages?** Thread 1 and Thread 2 both read page A — cache results?
5. **When does parallel not help?** If candidates are all low relevance, parallel just wastes tokens.
6. **Should parallelism be user-configurable?** "Fast" vs. "Quality" profiles?

---

## Related Ideas

- [[Intelligent Re-Reading]] — Parallel might cause race conditions on same page
- [[Compaction with /tmp Index]] — Each agent has its own compaction cache
- [[Pre-fetching with Metadata]] — Pre-fetch works with parallel strategies

---

## Notes

Parallelism is about **latency vs. cost**. If 1 minute+ is acceptable, sequential is fine (cheaper). If 10-20s is required, parallel is necessary.

The key insight is that **not everything needs to be parallel**. Strategy C (parallel candidate evaluation) parallelizes the bottleneck (deciding what to read next) while keeping the main loop sequential and controlled.

My recommendation: Start with Strategy C. It's the safest bet. Add other strategies as options for specific use cases (batch jobs, high-stakes queries).
