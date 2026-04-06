# Intelligent Re-Reading

**Status:** Draft Design Idea

**Context:** Multi-turn wiki traversal

---

## Problem

Traditional cycle detection assumes re-reading is a mistake. But an LLM might genuinely want to revisit a prior page because it now has better context to appreciate some minutiae of content that it previously missed or didn't understand.

**Example:**

```
Turn 1: Read [[srna-embeddings.md]]
  → Mentions "silhouette scores used for validation"
  → Doesn't know what that means, continues

Turn 2: Read [[clustering-metrics.md]]
  → Learns: silhouette > 0.5 = good, < 0.2 = poor

Turn 3: Re-read [[srna-embeddings.md]] (deliberate)
  → Now understands: page mentions silhouette scores but NOT thresholds
  → Synthesizes: "Validation uses silhouette scores; thresholds from clustering-metrics.md"
```

---

## Solution: Soft Block with Intent Tracking

**Allow re-reading** when agent provides intent for why it needs to revisit.

### Data Structure

```python
@dataclass
class RevisitIntent:
    """Why agent wants to re-read a page."""
    page_path: str
    reason: str              # "Now understand thresholds from clustering-metrics.md"
    turns_since_last_read: int  # How many turns ago was this page read?
    confidence: float          # How confident is agent this re-read is useful?
```

### Policy

**Allow re-reading if:**
- Intent is explicit (not just "maybe useful")
- Confidence > threshold (e.g., 0.7)
- Not revisiting for the same reason twice in a row

**Disallow re-reading if:**
- No intent provided
- Low confidence (< 0.5)
- Same page, same reason, consecutive (looping behavior)

### Workflow

```
1. Agent decides: "I want to re-read [[srna-embeddings.md]]"
2. Traversal harness: "Why?"
3. Agent: "Now I understand silhouette score thresholds, need to check if page mentions them"
4. Traversal harness: Create RevisitIntent, check policy
5. If allowed: Read page, mark with [RE-READ for intent: ...]
6. If denied: "Why? Provide different reason or choose different page"
```

---

## Implementation Sketch

```python
class RevisitPolicy:
    def __init__(self, confidence_threshold: float = 0.7):
        self.confidence_threshold = confidence_threshold
        self.revisit_history = []  # Track recent re-reads

    def should_allow(self, intent: RevisitIntent) -> bool:
        # 1. Check confidence
        if intent.confidence < self.confidence_threshold:
            return False

        # 2. Check for same-page, same-reason loops
        for recent in self.revisit_history[-5:]:
            if (recent.page_path == intent.page_path and
                recent.reason == intent.reason):
                return False

        # 3. Check for too-frequent revisits (spam)
        same_page_count = sum(
            1 for r in self.revisit_history
            if r.page_path == intent.page_path
        )
        if same_page_count > 3:  # Max 3 revisits per page
            return False

        return True

    def record(self, intent: RevisitIntent):
        self.revisit_history.append(intent)
```

---

## Open Questions

1. **What's the right confidence threshold?** 0.5? 0.7? Adaptive based on query complexity?
2. **How many revisits per page is too many?** 3? 5? Should this be configurable?
3. **Should re-reads be logged separately?** Add special entry to wiki/log.md for audit?
4. **Can we learn from re-read patterns?** If agent always re-reads page A after page B, can we pre-fetch B when A is read?
5. **What about multi-source contradictions?** If agent re-reads page A because page B contradicts it, how do we flag this?

---

## Related Ideas

- [[Compaction with /tmp Index]] — Re-reading can pull from compaction cache
- [[Pre-fetching with Metadata]] — Pre-fetch might avoid need to re-read
- [[Working Memory Management]] — Re-reading adds to memory, may trigger compaction

---

## Notes

This turns "cycle detection" from a simple problem to a nuanced one. The LLM isn't stupid — it's doing context-driven re-reading. The question is how to distinguish between:
- Intelligent re-reading (new context → new understanding)
- Looping behavior (same reason repeatedly)
- Uncertain wandering (no clear intent)

The confidence score + intent capture is the key discriminator.
