# ADR-002: Append-Only Structured Memory with Vector Index

Status: Accepted
Date: 2026-03-19

## Context

Ember-2 is designed as a persistent cognitive system where memory
is the source of truth, not the LLM.

The system must support:
- long-term accumulation
- traceability
- retrieval relevance
- rebuildability

## Decision

Memory will be:

- append-only (no mutation or deletion)
- stored as structured JSON documents
- organized by memory type (journal, notes, etc.)
- indexed via vector embeddings for retrieval

## Rationale

- append-only ensures auditability and history preservation
- structured format enables filtering and metadata usage
- vector index supports semantic retrieval
- decouples memory storage from LLM behavior

## Consequences

+ full traceability of knowledge evolution
+ consistent ingestion pipeline
+ flexible retrieval strategies

- storage growth over time
- requires periodic reindexing
- deduplication handled at retrieval layer

## Alternatives Considered

### Mutable memory store
Rejected:
- loss of historical state
- harder to debug and audit

### Raw text storage only
Rejected:
- limits metadata filtering
- reduces retrieval precision