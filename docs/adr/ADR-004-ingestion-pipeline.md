# ADR-004: Scalable Ingestion Pipeline with Structured Chunking

Status: Accepted
Date: 2026-03-19

## Context

Ember-2 must ingest diverse data sources (chat logs, documents, notes)
into a unified memory system that supports retrieval and long-term use.

Ingestion must be:
- consistent across sources
- scalable to new formats
- structured for retrieval and filtering

## Decision

Implement a standardized ingestion pipeline with the following stages:

raw input
→ parsing (format-specific loaders)
→ chunking (size + semantic boundaries)
→ metadata tagging
→ embedding generation
→ storage (JSON memory files)
→ vector indexing

Chunking rules:
- target size: ~300–800 tokens
- preserve semantic boundaries where possible
- include source + position metadata

## Rationale

- consistent pipeline enables reuse across all data sources
- chunking improves retrieval precision
- metadata enables filtering and ranking
- decouples ingestion from retrieval logic

## Consequences

+ scalable to new data types (PDF, chat, markdown, etc.)
+ improved retrieval quality
+ consistent memory structure

- requires tuning chunk size and boundaries
- ingestion latency for large datasets
- need for reprocessing if chunking strategy changes

## Alternatives Considered

### Ad-hoc ingestion per source
Rejected:
- inconsistent structure
- difficult to maintain
- poor retrieval performance

### No chunking (store full documents)
Rejected:
- low retrieval precision
- inefficient embeddings