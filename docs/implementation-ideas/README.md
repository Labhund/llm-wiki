# Implementation Ideas

**Status:** Open Design Space

---

## Overview

These are draft design ideas for the LLM Wiki multi-turn traversal pattern. None are implemented yet — they're exploration documents for discussion and eventual development.

Each document describes:
- The problem being solved
- A proposed solution
- Implementation sketches
- Open questions for further discussion

---

## Ideas

1. [[01-intelligent-re-reading.md]] — Allow LLM to re-read pages with explicit intent
2. [[02-compaction-with-tmp-index.md]] — Compact working memory to /tmp with link-based retrieval
3. [[03-pre-fetching-with-metadata.md]] — Pre-fetch metadata for linked pages to inform traversal decisions
4. [[04-turn-0-optimization.md]] — Fast path for simple queries (direct page read)
5. [[05-parallel-agents.md]] — Parallel strategies for latency reduction
6. [[06-programmatic-context-tuning.md]] — Profiles + hard budgets for memory management
7. [[07-attention-aware-prompt-ordering.md]] — Double prompting with query at start/end to leverage attention
8. [[08-pre-seeding-with-vector-keyword-lookup.md]] — Hybrid search on tool init, agent chooses starting points
9. [[09-working-agent-context-for-tool-calls.md]] — Platform/agent integration: how much conversation history to pass

---

## How to Use These

### For Discussion

Read these documents and:
- Ask clarifying questions
- Propose alternatives
- Identify edge cases
- Highlight potential issues

### For Implementation

When implementing:
1. Start with the highest-impact ideas (likely Turn 0, Compaction, Pre-fetching)
2. Iterate based on testing
3. Open new design docs for discovered issues
4. Update existing docs with decisions made

---

## Decision Log

As we implement ideas, we'll document decisions here:

| Idea | Decision | Date | Notes |
|------|-----------|--------|--------|
| Intelligent Re-Reading | TBD | - |
| Compaction with /tmp Index | TBD | - |
| Pre-fetching with Metadata | TBD | - |
| Turn 0 Optimization | TBD | - |
| Parallel Agents | TBD | - |
| Programmatic Context Tuning | TBD | - |

---

## Contributing

To add a new idea:
1. Create a new markdown file: `docs/implementation-ideas/07-your-idea.md`
2. Follow the template from existing docs
3. Update this index
4. Commit and open for discussion

---

## Notes

These documents are **living** — they'll evolve as we implement, test, and discover edge cases. Don't treat them as final specs.

The goal is to capture the design space before committing to implementation. Better to hash out trade-offs here than in code.
