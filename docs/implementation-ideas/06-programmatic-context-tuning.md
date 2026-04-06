# Programmatic Context Tuning

**Status:** Draft Design Idea

**Context:** Managing working memory size across multi-turn traversal

---

## Problem

As traversal progresses, working memory grows. Eventually:
- Context window is hit (can't fit more)
- Latency increases (more tokens = slower LLM calls)
- Noise increases (early turns may be less relevant)

We need a way to **tune context** programmatically — drop old turns while preserving useful information.

---

## Solution: Profiles + Hard Budgets

**Two mechanisms:**
1. **User profiles** — different behavior for different use cases
2. **Hard token/turn budgets** — predictable limits, no LLM overhead

---

## User Profiles

Different users / use cases have different needs:

| Profile | Use case | Max turns | Max tokens | Description |
|----------|------------|------------|--------------|-------------|
| **fast** | Quick reference, simple queries | 3 | 4k | Aggressive pruning, prioritize speed |
| **standard** | Typical usage, balanced | 10 | 16k | Default, good trade-off |
| **deep** | Research, complex queries | None | 32k | Minimal pruning, prioritize completeness |
| **batch** | Overnight jobs, analytics | 5 | 8k | Efficient, cost-conscious |

### Profile Configuration

```yaml
# ~/.hermes/llm-wiki/config.yaml
traversal:
  profiles:
    fast:
      max_turns: 3
      max_tokens: 4000
      compaction_aggressive: true

    standard:
      max_turns: 10
      max_tokens: 16000
      compaction_aggressive: false

    deep:
      max_turns: null  # No turn limit
      max_tokens: 32000
      compaction_aggressive: false

    batch:
      max_turns: 5
      max_tokens: 8000
      compaction_aggressive: true
```

### Profile Selection

**CLI:**
```bash
llm-wiki traverse "How do we validate sRNA embeddings?" --profile fast
```

**API:**
```python
traversal = WikiTraversal(
    wiki_root="~/repos/llm-wiki",
    profile="standard"
)
```

**Default:** If no profile specified, use "standard".

---

## Hard Token/Turn Budgets

When limits are hit, **drop oldest turns** (no LLM overhead).

### Budget Checking

```python
@dataclass
class WorkingMemory:
    turns: List[dict]  # Each turn has page, summary, etc.

class ContextTuner:
    def __init__(self, profile: str = "standard"):
        self.profile = self._load_profile(profile)

    def _load_profile(self, profile: str) -> dict:
        profiles = {
            "fast": {"max_turns": 3, "max_tokens": 4000},
            "standard": {"max_turns": 10, "max_tokens": 16000},
            "deep": {"max_turns": None, "max_tokens": 32000},
        }
        return profiles[profile]

    def check_and_trim(self, memory: WorkingMemory) -> WorkingMemory:
        """Trim memory if over budget."""
        new_turns = memory.turns

        # Check turn budget
        if self.profile["max_turns"] and len(new_turns) > self.profile["max_turns"]:
            new_turns = new_turns[-self.profile["max_turns"]:]
            print(f"[ContextTuner] Trimmed to {len(new_turns)} turns (max: {self.profile['max_turns']})")

        # Check token budget
        if self.profile["max_tokens"]:
            total_tokens = sum(self._estimate_tokens(t) for t in new_turns)
            if total_tokens > self.profile["max_tokens"]:
                # Drop oldest until under budget
                total = 0
                result = []
                for turn in reversed(new_turns):
                    turn_tokens = self._estimate_tokens(turn)
                    if total + turn_tokens > self.profile["max_tokens"]:
                        break
                    total += turn_tokens
                    result.insert(0, turn)
                new_turns = result
                print(f"[ContextTuner] Trimmed to {total} tokens (max: {self.profile['max_tokens']})")

        return WorkingMemory(turns=new_turns)

    def _estimate_tokens(self, turn: dict) -> int:
        """Fast token estimate."""
        # Word count × 1.3 is decent approximation
        return int(len(turn["summary"].split()) * 1.3)
```

### Compaction Trigger

**Instead of aggressive trimming, trigger compaction:**

```python
def check_and_compact(self, memory: WorkingMemory) -> WorkingMemory:
    """Check budget and compact if needed."""
    new_turns = memory.turns

    # Check turn budget
    if self.profile["max_turns"] and len(new_turns) > self.profile["max_turns"]:
        # Compact oldest turns
        to_compact = new_turns[:-3]  # Keep last 3
        compacted = self.compactor.compact(to_compact)
        self.compactor.add_to_cache(compacted)
        new_turns = new_turns[-3:]

    # Check token budget
    if self.profile["max_tokens"]:
        total_tokens = sum(self._estimate_tokens(t) for t in new_turns)
        if total_tokens > self.profile["max_tokens"]:
            # Compact until under budget
            to_compact = []
            to_keep = []
            total = 0
            for turn in reversed(new_turns):
                turn_tokens = self._estimate_tokens(turn)
                if total + turn_tokens > self.profile["max_tokens"]:
                    to_compact.insert(0, turn)
                else:
                    total += turn_tokens
                    to_keep.insert(0, turn)
            compacted = self.compactor.compact(to_compact)
            self.compactor.add_to_cache(compacted)
            new_turns = to_keep

    return WorkingMemory(turns=new_turns)
```

This uses [[Compaction with /tmp Index]] for intelligent trimming, not just dropping.

---

## Adaptive Profiles

**Question:** Can profiles be adaptive based on query complexity?

### Query Complexity Indicators

| Indicator | How to measure | High complexity if... |
|-----------|-----------------|------------------------|
| **Candidate count** | Links per page | > 10 links |
| **Query length** | Word count | > 20 words |
| **Subquestion count** | LLM decomposition | > 2 subquestions |
| **Ambiguity** | Multiple high-relevance candidates | Top 3 candidates within 0.1 relevance |

### Adaptive Selection

```python
class AdaptiveProfileSelector:
    def __init__(self):
        self.complexity_thresholds = {
            "fast": 5,
            "standard": 10,
            "deep": 20,
        }

    def select_profile(self, query: str, first_page: dict) -> str:
        """Select profile based on query complexity."""
        complexity = self._calculate_complexity(query, first_page)

        if complexity < self.complexity_thresholds["fast"]:
            return "fast"
        elif complexity < self.complexity_thresholds["standard"]:
            return "standard"
        else:
            return "deep"

    def _calculate_complexity(self, query: str, first_page: dict) -> float:
        """Calculate complexity score."""
        score = 0

        # Query length
        score += len(query.split()) * 0.5

        # Candidate count
        score += len(first_page["links"]) * 1.0

        # Ambiguity (if top candidates have similar relevance)
        if first_page.get("candidate_relevances"):
            top_3 = first_page["candidate_relevances"][:3]
            if max(top_3) - min(top_3) < 0.1:
                score += 5.0  # High ambiguity

        return score
```

---

## Implementation Sketch

```python
@dataclass
class TraversalConfig:
    profile: str = "standard"
    max_turns: Optional[int] = None
    max_tokens: Optional[int] = None

class WikiTraversal:
    def __init__(self, wiki_root: str, config: TraversalConfig):
        self.wiki_root = Path(wiki_root)
        self.config = self._resolve_config(config)
        self.context_tuner = ContextTuner(self.config["profile"])
        self.compactor = WorkingMemoryCompactor(tmp_dir="/tmp/wiki-compaction")

    def _resolve_config(self, config: TraversalConfig) -> dict:
        """Merge user config with profile defaults."""
        profiles = self._load_profiles()
        profile_config = profiles[config.profile]

        # Override with user settings
        if config.max_turns is not None:
            profile_config["max_turns"] = config.max_turns
        if config.max_tokens is not None:
            profile_config["max_tokens"] = config.max_tokens

        return profile_config

    def traverse(self, query: str) -> dict:
        """Full traversal loop with context tuning."""
        working_memory = WorkingMemory(turns=[])

        # Turn 1: Read first page
        first_page = self._read_first_page(query)
        working_memory.add(first_page)

        # Traversal loop
        while True:
            # Check and tune context
            working_memory = self.context_tuner.check_and_compact(working_memory)

            # Check stop condition
            if working_memory.should_stop():
                break

            # Read next page
            next_page = self._decide_next_page(working_memory)
            working_memory.add(next_page)

        # Synthesize answer
        return working_memory.synthesize_answer()
```

---

## Open Questions

1. **What's the right default profile?** "standard" with 10 turns / 16k tokens?
2. **Should users set profiles globally or per-query?** CLI flag vs. config file vs. adaptive?
3. **Can profiles override each other?** `--profile deep --max-turns 5` — which wins?
4. **How to expose profiles to users?** Simple names (fast/standard/deep) or granular (max_turns, max_tokens)?
5. **Should compaction be profile-dependent?** Aggressive compaction for "fast", gentle for "deep"?

---

## Related Ideas

- [[Compaction with /tmp Index]] — Context tuning triggers compaction
- [[Turn 0 Optimization]] — Fast profile might favor Turn 0
- [[Parallel Agents]] — Parallel strategies might need different profiles

---

## Notes

Programmatic context tuning is about **predictability**. Users should know:
- How many turns before context is trimmed?
- How many tokens before compaction?
- What happens when limits are hit?

Profiles are a clean way to expose these as simple choices, but we should also allow manual override for power users.

The hard budget approach (no LLM overhead) is key. Compaction is the intelligent fallback — when you hit the budget, don't just drop; compact first.
