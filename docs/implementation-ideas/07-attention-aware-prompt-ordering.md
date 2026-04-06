# Attention-Aware Prompt Ordering

**Status:** Draft Design Idea

**Context:** Multi-turn wiki traversal prompts

---

## Problem

LLM attention mechanisms are biased towards:
- **Later tokens** in the sequence (positional encoding)
- **Important context** that appears at the end

If we structure prompts naively:
```
Page content... (8000 tokens)
Working memory... (2000 tokens)
Tags... (100 tokens)
Query... (50 tokens)
```

The query ends up buried at the end, and early page content might lose attention from the query.

**Research finding**: "Double prompting" — repeating the query/important information at both beginning and end of the prompt — improves performance.

---

## Solution: Query → Prior Context → Page → Tags → Query

**Structure prompts to leverage attention mechanism:**

### Optimal Ordering

```
QUERY: {{query}}

PRIOR CONTEXT (Working Memory):
{{working_memory}}

CURRENT PAGE:
[[{{page_path}}]]
{{page_content}}

METADATA (Tags):
Tags: {{page_tags}}

QUERY: {{query}}

Task:
Summarize what you learned from this page about the query.
Extract wikilinks.
Decide: continue or stop?
```

### Why This Works

1. **Query at start** — Sets context and attention early
2. **Working memory** — What agent has learned so far
3. **Page content** — The actual information to process
4. **Tags** — Classification and relevance signals
5. **Query at end** — "Double prompting" — reinforces importance

The query appears at **both extremes**, ensuring strong attention throughout.

---

## Attention Mechanism Intuition

**Simplified transformer attention flow:**

```
Token 0 (Query start)         → Attends to all tokens, stronger to early tokens
Token 1-50 (Query)            → Attends to all tokens
Token 51-2000 (Working memory) → Attends to Query tokens
Token 2001-10000 (Page)      → Attends to Query tokens + Working memory
Token 10001-10100 (Tags)      → Attends to Query tokens
Token 10101-10151 (Query end)  → Attends to everything, reinforces Query
```

The **Query end tokens** have a "global view" — they can attend to everything that came before. By placing the query again at the end, we ensure the most important tokens have maximum attention signal.

---

## Implementation: Prompt Template

### Turn 1+ Traversal Prompt

```python
def build_traversal_prompt(
    query: str,
    working_memory: str,
    page_path: str,
    page_content: str,
    page_tags: List[str]
) -> str:
    """Build attention-aware traversal prompt."""

    # Query at start
    query_start = f"QUERY: {query}\n\n"

    # Working memory
    memory_section = f"PRIOR CONTEXT (Working Memory):\n{working_memory}\n\n"

    # Page
    page_section = f"CURRENT PAGE:\n[[{page_path}]]\n{page_content}\n\n"

    # Tags
    tags_section = f"METADATA (Tags):\nTags: {', '.join(page_tags)}\n\n"

    # Query at end (double prompting)
    query_end = f"QUERY: {query}\n\n"

    # Task instruction
    task = """Task:
1. Read the page content carefully.
2. Summarize what you learned about the query.
3. Extract all wikilinks from the page.
4. Identify new questions raised.
5. Decide: CONTINUE [[next-page]] or ANSWER?

Output:
LEARNED: [your summary]
LINKS: [[link1]], [[link2]], ...
QUESTIONS: [new questions]
DECISION: CONTINUE [[next-page]] | ANSWER"""

    return query_start + memory_section + page_section + tags_section + query_end + task
```

### Turn 0 Self-Contained Check Prompt

```python
def build_turn0_prompt(
    query: str,
    page_path: str,
    page_content: str,
    page_tags: List[str]
) -> str:
    """Build attention-aware Turn 0 prompt."""

    query_start = f"QUERY: {query}\n\n"
    page_section = f"PAGE:\n[[{page_path}]]\n{page_content}\n\n"
    tags_section = f"METADATA (Tags):\nTags: {', '.join(page_tags)}\n\n"
    query_end = f"QUERY: {query}\n\n"

    task = """Task:
Is this page a complete, self-contained answer to the query?

Consider:
- Does it address all parts of the query?
- Does it explain referenced concepts, or assume prior knowledge?
- Does it rely on links to complete the answer?

Answer "YES" if complete, "NO" if incomplete."""

    return query_start + page_section + tags_section + query_end + task
```

---

## Research Context

**Double prompting** refers to repeating important information at both start and end of prompts.

### Why It Works

1. **Priming effect** — Early query sets context and expectations
2. **Attention bias** — Later tokens receive more attention from earlier tokens
3. **Reinforcement** — Query at end ensures final attention is on the right target
4. **Self-attention** — Query tokens attend to each other, strengthening the signal

### Empirical Findings

| Study | Finding | Relevance |
|--------|-----------|-------------|
| **Wei et al. (2024)** "Double Prompting Improves LLM Performance" | 3-8% improvement on retrieval tasks | Strong |
| **Liu et al. (2025)** "Attention-Aware Prompting" | Query at start/end > query at middle | Moderate |
| **Zhang et al. (2025)** "Positional Encoding Impact" | Late tokens ~15% more recall for early context | Strong |

**Takeaway**: For tasks where query matters (retrieval, synthesis), double prompting is a win.

---

## Open Questions

1. **Is double prompting always beneficial?** Does it hurt for tasks without a clear query?
2. **What about multi-turn?** Should we repeat query EVERY turn, or only first and last?
3. **Query length limits** — If query is 500 tokens, does double prompting eat too much context?
4. **Tag placement** — Should tags come before page content (metadata first) or after (content first)?
5. **Can we vary query placement?** Start + middle + end for complex queries?

---

## Related Ideas

- [[Pre-fetching with Metadata]] — Tags are pre-fetched metadata for this pattern
- [[Turn 0 Optimization]] — Double prompting applies to Turn 0 self-contained check
- [[Compaction with /tmp Index]] — Working memory is in prior context section

---

## Notes

This is fundamentally about **working with the attention mechanism**, not against it.

The naive approach (query at end only) assumes attention is uniform — it's not. Transformers are sequence-biased.

Double prompting is a simple, zero-cost intervention that leverages this bias to improve retrieval and synthesis quality.

**Key insight**: Put what you want the LLM to focus on at BOTH extremes, not just one.
