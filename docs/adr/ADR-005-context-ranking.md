# ADR-005: Multi-Stage Context Retrieval and Ranking Strategy

Status: Accepted
Date: 2026-03-19

## Context

Ember-2 must retrieve relevant memory efficiently while avoiding:
- noise from irrelevant memories
- duplication
- over-reliance on a single signal type

The system currently retrieves:
- memory items
- reflection items

## Decision

Implement a multi-stage retrieval pipeline:

1. Retrieval
   - vector similarity search (semantic relevance)

2. Ranking
   - apply scoring based on:
     - similarity score
     - recency (timestamp weighting)
     - metadata signals (tags, type)

3. Deduplication
   - normalize text and remove near-duplicates

4. Formatting
   - assemble top-N results into structured context packet

## Rationale

- vector search alone is insufficient for quality results
- recency helps maintain conversational relevance
- metadata enables domain-specific prioritization
- deduplication prevents wasted context space

## Consequences

+ higher quality context selection
+ more stable responses
+ extensible ranking logic

- increased complexity in ranking logic
- requires tuning weights over time
- potential performance cost for large datasets

## Alternatives Considered

### Pure vector similarity only
Rejected:
- ignores recency and metadata
- leads to noisy or stale context

### Manual rule-based retrieval only
Rejected:
- lacks semantic understanding
- brittle across domains
  