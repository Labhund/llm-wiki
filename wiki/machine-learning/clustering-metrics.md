# Clustering Metrics

Evaluation metrics for clustering algorithms.

## Silhouette Score

Silhouette score ranges from -1 to 1:
- **> 0.5**: Well-separated clusters
- **0.2 - 0.5**: Moderate structure
- **< 0.2**: Poor or no structure

Formula measures:
- Cohesion: How close points are within same cluster
- Separation: How far clusters are from each other

## Related Pages

For application to sRNA embeddings, see [[wiki/bioinformatics/srna-embeddings.md]].
For variant analysis, see [[wiki/bioinformatics/inter-rep-variant-analysis.md]].

## Summary

Silhouette scores are the primary metric for evaluating clustering quality in sRNA embedding validation.
