# sRNA Embeddings Validation

This document describes how sRNA embeddings are validated.

## Method

We use PCA analysis to reduce dimensionality of embeddings before clustering.

## Clustering

Clustering is performed using k-means with k=10 clusters.

## Related Pages

For detailed variance analysis, see [[wiki/bioinformatics/inter-rep-variant-analysis.md]].

For clustering metrics, see [[wiki/machine-learning/clustering-metrics.md]].

## Summary

sRNA embeddings are validated via PCA projection and k-means clustering (k=10).
