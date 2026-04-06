# Scientific Articles in the LLM Era — Format vs. Modality

**Status:** Philosophical Exploration

---

## The Question

**Current state:** Scientific articles are text + figures/charts/graphs.

**Question:** Do scientific articles need to change format for LLMs, or is text still optimal?

- Do graphs convey information density comparable to text?
- Are there better modalities for conveying that information?
- Is this a format change (how we author) or an interaction change (how we consume)?

---

## Current State: Text + Figures

**Typical scientific article:**

```
┌─────────────────────────────────────────────────────────┐
│  ABSTRACT                                      │
│  Introduction... (2000 words)                   │
│                                                  │
│  ┌──────────────────────────────────┐              │
│  │ FIGURE 1: Architecture        │              │
│  │ [sRNA embedding pipeline]          │              │
│  │                            │              │
│  │ Validation: k-means, PCA      │              │
│  └──────────────────────────────────┘              │
│                                                  │
│  Results... (1500 words)                          │
│  See Figure 2 for validation metrics...           │
│                                                  │
│  ┌──────────────────────────────────┐              │
│  │ FIGURE 2: Metrics           │              │
│  │ [Silhouette scores vs. k]          │              │
│  │                            │              │
│  │ Optimal: k=10, score=0.72      │              │
│  └──────────────────────────────────┘              │
│                                                  │
│  Discussion... (3000 words)                          │
└─────────────────────────────────────────────────────────┘
```

**Information distribution:**
- Text: ~6500 words of description
- Figures: 2 visualizations conveying architecture + metrics
- Total information: ~8000 words equivalent (estimated)

---

## Information Density: Graphs vs. Text

**Question:** Are graphs as information-dense as text?

### What Text Conveys Well
- **Causal narratives** — "We used PCA to reduce dimensionality, then k-means for clustering"
- **Qualitative relationships** — "Silhouette scores improved from 0.45 to 0.72 when we adjusted k"
- **Nuance and justification** — "We chose k=10 because higher values showed diminishing returns"
- **Methodological details** — Step-by-step procedures, parameters, trade-offs

### What Graphs Convey Well
- **Quantitative relationships** — Exact numbers, trends, comparisons
- **System structure** — Architectures, pipelines, workflows
- **Data distributions** — Histograms, scatter plots, box plots
- **Performance landscapes** — Heatmaps, confusion matrices, ROC curves

### The Density Comparison

| Information type | Text equivalent | LLM consumption | Notes |
|-----------------|------------------|------------------|--------|
| **Causal narrative** | ~200 words | Fast | LLM excellent at understanding |
| **Architecture diagram** | ~400-500 words | Slow | Vision models miss spatial relationships, OCR errors |
| **Numerical table** | ~50-100 words | Fast | Tables are structured, easy to parse |
| **Trend plot** | ~100-200 words | Medium | Visions models good at trends, miss anomalies |
| **Heatmap** | ~200-300 words | Slow | Complex visual patterns, hard to describe |

**Estimate:** A complex figure might be **400-500 words** of information density. Equivalent to 2-3 paragraphs of text.

**Problem:** LLMs can read 2000-word paragraphs in 1 second. Complex figures require 5-10 seconds (vision processing, OCR, interpretation).

**But is text really more dense?** Or are we measuring wrong?

---

## The "Better Modality" Question

**Assumption:** Graphs are information-dense.

**Challenge:** What modality would convey that information more efficiently to LLMs?

### Option A: Enhanced Text Descriptions

**Current:**
```
Figure 2 shows silhouette scores vs. k.
```

**Enhanced:**
```
Figure 2: Silhouette score optimization

We evaluated silhouette scores across k=5 to k=20:
- k=5: 0.42 (poor separation)
- k=10: 0.72 (optimal)
- k=15: 0.68 (over-clustering)
- k=20: 0.55 (fragmented)

Optimal: k=10, silhouette score=0.72.

Trend: Scores improve up to k=10, then degrade.
```

**Pros:** LLM-native, instant consumption
**Cons:** More words to write, still might not capture spatial relationships

### Option B: Machine-Readable Tables

**Instead of:**
```
[Scatter plot showing silhouette vs. k]
```

**Provide:**
```
TABLE: Silhouette Score by k

k   Silhouette   Interpretation
----------------------------------------
5   0.42         Poor separation
10  0.72         Optimal
15  0.68         Over-clustering
20  0.55         Fragmented
```

**Pros:** Structured, LLM-excellent
**Cons:** Loses visual trends (curves, distributions)

### Option C: Structured Data Formats

**Instead of:** Figure showing PCA + clustering

**Provide:**
```json
{
  "pca_params": {
    "n_components": 50,
    "variance_retained": 0.87
  },
  "clustering_params": {
    "algorithm": "k-means",
    "k": 10,
    "init": "k-means++"
  },
  "results": {
    "silhouette_score": 0.72,
    "cluster_sizes": [45, 38, 52, 67, ...]
  }
}
```

**Pros:** LLM-native, queryable, comparable
**Cons:** Loses visual context, might be overwhelming

### Option D: Interactive Visualizations

**What if articles embed interactive figures?**

```html
<!-- Interactive PCA Plot -->
<interactive-pca-plot
  data-url="https://github.com/author/data/pca.json"
  config-url="https://github.com/author/figs/fig1.yaml"
/>
```

**LLM Agent:** Can query the visualization directly:
```
Tool call: query_pca_plot("What does the 2D projection show?")
→ Returns: "In 2D projection, samples separate into 3 distinct clusters,
   cluster 1 has elongated distribution (explained by k=10)."
```

**Pros:** Best of both worlds (visual + queryable)
**Cons:** Requires new infrastructure, not standard in journals

---

## Information Theory Perspective

**Shannon entropy:** Different modalities have different information capacities.

| Modality | Bits per second (human) | Bits per token (LLM) | Notes |
|-----------|------------------------|----------------------|-------|
| **Reading text** | ~50 bps | ~10-15 bpt | LLM efficient |
| **Viewing graph** | ~150 bps | ~2-5 bpt | Vision slower |
| **Reading table** | ~80 bps | ~20 bpt | Good balance |
| **Reading structured data** | ~100 bps | ~25 bpt | LLM-excellent |

**Question:** Is the bottleneck LLM consumption or human understanding?

**If LLM:** Text is most efficient (10-15 bpt), tables/structured data close (20-25 bpt).

**If human:** Graphs are most efficient (150 bps), but text is close (80 bps).

**Tension:** Optimal for LLM (text) vs. optimal for human (graphs).

---

## The Real Question: Interaction vs. Format

**Hypothesis:** This isn't about changing article format — it's about changing how we interact with them.

### Current Model

```
Author writes: Text + Figures
         ↓
Journal publishes: PDF (static)
         ↓
Reader: Downloads, views, reads
         ↓
LLM: Reads text (fast), views figures (slow)
```

### Proposed Model

```
Author writes: Text + Figures + Interactive Data
         ↓
Journal publishes: PDF + Live Artifacts
         ↓
LLM Agent: Queries text + interactive visualizations
```

**What changes:**
- Articles still have text + figures (for human readers)
- BUT also expose queryable data/visuals (for LLM agents)
- "Figures" become interactive, not static

### Example

**Current:**
```
Figure 2: Silhouette scores vs. k (static plot)
```

**Proposed:**
```
Figure 2: Silhouette scores vs. k
[Interactive: Query this plot]
[Data: https://github.com/author/data/fig2.json]
```

**LLM Agent:**
```
Tool call: query_figure("What's the optimal k? What happens beyond 20?")
→ Returns: "Optimal k=10 (0.72). Beyond k=20, scores degrade
   to 0.51 at k=25. Likely over-clustering."
```

---

## My Take

### Articles Shouldn't Change Format for Humans

Text + figures works well for humans. Don't fix what isn't broken.

### Articles Should Add "LLM-Layer" for Agents

**LLM Layer:**
- Machine-readable tables/data
- Interactive visualizations (queryable plots/charts)
- Structured supplementary materials (YAML, JSON)
- API endpoints for key data

This is like "supplementary materials" but LLM-native.

### This Is An Interaction Revolution, Not Format Change

Parallel to internet:
- Before: Physical information → Mediated access (libraries, journals)
- After: Digital information → Direct access (search, browse)

**Parallel for LLM:**
- Before: Static articles → Read-only consumption
- After: Articles + LLM-layer → Interactive querying

**The interaction pattern shifts:**
- From "read and interpret" to "query and explore"
- From passive consumption to active collaboration

---

## Open Questions

1. **Do graphs actually convey less information than text?** Or are we underestimating visual density?
2. **What's the right LLM-layer format?** Tables? Structured data? Interactive plots? All of the above?
3. **Should LLM-layer be separate from human-readable figures?** Or should they be integrated?
4. **How do journals adapt?** Will they accept new formats? Or will this be pre-prints/arXiv only?
5. **What's the right balance?** More structure (LLM efficiency) vs. visual context (human intuition)?
6. **Is "interaction revolution" real?** Or just incremental improvement?

---

## Related

- [[LLM Wiki - Knowledge Base Pattern]] — Infrastructure for LLM-native knowledge compounding
- [[Multi-Turn Traversal Pattern]] — How agents explore knowledge, not just read
- [[Agent Individuality — Philosophy Session]] — Agents need persistent identity, articles should be persistent data sources

---

## Notes

This isn't about replacing graphs with text — it's about adding **queryable, interactive layers** to complement static figures.

The key insight: **humans and LLMs consume information differently**. Optimizing for one might degrade the other.

The tension: Text is LLM-optimal but human-suboptimal for quantitative data. Graphs are human-optimal but LLM-suboptimal for complex relationships.

Solution: Provide both — text + graphs for humans, AND structured/interactive layers for LLMs.
