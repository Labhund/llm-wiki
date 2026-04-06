# Compaction with /tmp Index

**Status:** Draft Design Idea

**Context:** Multi-turn wiki traversal with growing working memory

---

## Problem

As traversal progresses, working memory grows. After 10+ turns, it's:
- Expensive (huge token cost per subsequent turn)
- Noisy (early turns may be less relevant)
- Risky (token limits may be hit)

Naive solutions:
- Drop oldest turns → permanent loss
- Hard limit → abrupt truncation

**Goal:** Compaction without permanent loss. Keep ability to retrieve compacted information when needed.

---

## Solution: /tmp Index with Link-Based Retrieval

**Compact turns to /tmp**, keep them retrievable via wikilinks.

### Architecture

```
Active Working Memory (Turn N)
├─ Turn 1: [[srna-embeddings.md]] — Full summary (500 tokens)
├─ Turn 2: [[clustering-metrics.md]] — Full summary (800 tokens)
├─ Turn 3: [[inter-rep-variant-analysis.md]] — Full summary (600 tokens)
├─ ...
└─ Turn 10: [[machine-learning/pca.md]] — Full summary (2000 tokens)

[Token budget hit → compaction]
           ↓
Active Working Memory (Turn N)
├─ Turn 8: [[machine-learning/clustering-metrics.md]] — Full summary
└─ Turn 9: [[machine-learning/pca.md]] — Full summary

/tmp/compacted/2026-04-07_traversal.json
{
  "turns_1_7": [
    {
      "page": "wiki/bioinformatics/srna-embeddings.md",
      "summary": "sRNA embeddings validated via PCA...",
      "self_score": 0.85,
      "rationale": "Core to validation pipeline",
      "links": ["clustering-metrics.md", "inter-rep-variant-analysis.md"],
      "read_at": "2026-04-07T12:35:00Z"
    },
    ...
  ]
}
```

### Retrieval Flow

```
Turn 11: Agent wants to reference [[srna-embeddings.md]]
→ Check active memory: Not there (compacted 3 turns ago)
→ Check /tmp/compacted/: Found in turns_1_7
→ Retrieve compacted summary
→ Inject into active memory with [RETRIEVED] tag:
  "[RETRIEVED] [[srna-embeddings.md]]: sRNA embeddings validated..."
→ Agent uses it without re-reading full page
```

---

## LLM Self-Scoring for Compaction

When compaction triggers, LLM scores each turn:

### Scoring Prompt

```
You are compacting working memory from a wiki traversal.
For each turn, provide:
1. Compact summary (max 100 words)
2. Self-score (0-1): How relevant is this turn to the original query?
3. Rationale: Why this score?
4. Links: All wikilinks mentioned in this turn

Output JSON.
```

### Score Format

```json
{
  "wiki/bioinformatics/srna-embeddings.md": {
    "summary": "sRNA embeddings validated via PCA and k-means clustering (k=10)",
    "self_score": 0.85,
    "rationale": "Core to validation pipeline, directly answers query",
    "links": [
      "wiki/bioinformatics/inter-rep-variant-analysis.md",
      "wiki/machine-learning/clustering-metrics.md"
    ]
  },
  "wiki/machine-learning/pca.md": {
    "summary": "PCA reduces dimensionality via orthogonal transformation",
    "self_score": 0.4,
    "rationale": "Background knowledge, tangential to main query",
    "links": []
  }
}
```

### Score Usage

- **Retrieval by score**: Pull top-N highest-scored turns when needed
- **Retrieval by link**: Pull specific page (regardless of score) if agent requests it
- **Threshold filtering**: Only retrieve turns with score > X (e.g., 0.7)

---

## Compaction Trigger Conditions

When to compact?

| Trigger | Description |
|----------|-------------|
| **Token budget** | Active memory exceeds N tokens (e.g., 16k) |
| **Turn count** | Active memory exceeds N turns (e.g., 10) |
| **Time since last** | Oldest turn is > M minutes old (e.g., 5 min) |
| **Manual** | Agent explicitly requests compaction (rare) |

**Config:**
```yaml
compaction:
  triggers:
    max_tokens: 16000
    max_turns: 10
    max_age_minutes: 5
  keep_recent: 3  # Always keep last 3 turns in active memory
```

---

## /tmp Expiration Policy

When to delete compacted entries?

| Policy | Trade-off |
|---------|------------|
| **Delete on traversal end** | Clean, but lose if same query asked again |
| **Time-based** (e.g., 1 hour) | Balances persistence vs. storage |
| **Never** | Persistent cache, but unbounded growth |

**Recommendation:** Time-based with LRU eviction.

```python
class CompactionCache:
    def __init__(self, ttl_seconds: int = 3600):
        self.ttl = ttl_seconds
        self.cache = {}  # traversal_id → compacted_data

    def evict_expired(self):
        now = time.time()
        expired = [
            tid for tid, data in self.cache.items()
            if now - data["created_at"] > self.ttl
        ]
        for tid in expired:
            del self.cache[tid]
```

---

## Implementation Sketch

```python
@dataclass
class CompactedTurn:
    page: str
    summary: str          # Compact summary (max 100 words)
    self_score: float     # LLM self-score (0-1)
    rationale: str        # Why this score?
    links: List[str]      # Wikilinks mentioned
    read_at: str         # ISO timestamp

class WorkingMemoryCompactor:
    def __init__(self, tmp_dir: str = "/tmp/wiki-compaction"):
        self.tmp_dir = Path(tmp_dir)
        self.tmp_dir.mkdir(exist_ok=True)
        self.active_cache = {}  # page → CompactedTurn

    def compact(self, turns: List[dict]) -> List[dict]:
        """Compact turns, keep recent ones, save rest to /tmp."""
        # 1. Call LLM to score and summarize
        scored = self._llm_score_and_summarize(turns)

        # 2. Keep recent turns in active memory
        recent_turns = turns[-3:]  # Configurable
        compacted_turns = turns[:-3]

        # 3. Save compacted to /tmp
        traversal_id = str(uuid.uuid4())
        compact_file = self.tmp_dir / f"{traversal_id}.json"

        with open(compact_file, 'w') as f:
            json.dump({
                "traversal_id": traversal_id,
                "created_at": datetime.now().isoformat(),
                "turns": compacted_turns,
            }, f)

        # 4. Update active cache for retrieval
        for turn in compacted_turns:
            page = turn["page"]
            self.active_cache[page] = CompactedTurn(
                page=turn["page"],
                summary=turn["summary"],
                self_score=turn["self_score"],
                rationale=turn["rationale"],
                links=turn["links"],
                read_at=turn["read_at"],
            )

        return recent_turns

    def retrieve(self, page: str) -> Optional[CompactedTurn]:
        """Retrieve compacted turn by page."""
        return self.active_cache.get(page)

    def retrieve_top_n(self, n: int, min_score: float = 0.0) -> List[CompactedTurn]:
        """Retrieve top N compacted turns by score."""
        sorted_turns = sorted(
            self.active_cache.values(),
            key=lambda t: t.self_score,
            reverse=True
        )
        filtered = [t for t in sorted_turns if t.self_score >= min_score]
        return filtered[:n]
```

---

## Open Questions

1. **What's the right compaction trigger?** Token budget? Turn count? Both?
2. **How many recent turns to keep?** 2? 3? 5? Adaptive based on query complexity?
3. **What's the right summary length?** 50 words? 100 words? Dynamic based on score?
4. **How does agent decide to retrieve vs. re-read?** Always use compacted if available? Only if score > threshold?
5. **Can we pre-compact aggressively?** If first turn is clearly background, compact immediately?
6. **Should compacted summaries be logged?** Add to wiki/log.md for audit?
7. **Multi-traversal sharing?** If query B uses same pages as query A, can we reuse compacted cache?

---

## Related Ideas

- [[Intelligent Re-Reading]] — Re-reading can pull from compaction cache
- [[Pre-fetching with Metadata]] — Metadata might inform compaction decisions
- [[Working Memory Management]] — Compaction is one aspect of memory management

---

## Notes

This turns compaction from "lossy" to "retrievable". The key insight is that links are natural retrieval keys — agent already tracks wikilinks, so we can use them for compaction cache lookups.

The LLM self-scoring adds intelligence: high-scoring turns are "hot cache", low-scoring are "cold cache". This enables retrieval by relevance, not just recency.

Potential optimization: Pre-compute embeddings for compacted summaries to enable semantic retrieval (not just link-based).
