# Ember-2 Pre-Release History

Foundational work before v0.9.0. These releases built the core architecture piece by piece.

## v0.8.3 — 2026-03-23 — Security Complete
Security hardening for single-user deployment: API key auth, Tailscale binding, rate limiting, path traversal protection, audit logging, SearXNG localhost-only.

## v0.8.2 — 2026-03-23 — Vision
Vision model integration via `EMBER_VISION_MODEL`. Image analysis in chat with graceful text-only fallback.

## v0.8.1 / v0.8.0 — 2026-03-23 — Web Search
SearXNG integration for intent-gated web search. Results injected above memory context in prompt. Docker Compose for local SearXNG instance.

## v0.7.10 — 2026-03-23 — Interference Hardening
Empty message guard, `### Task:` RAG injection guard, type-aware payload logging. Fixed Open WebUI interference patterns.

## v0.7.9 — 2026-03-23 — Multi-Source Reflection
Daily and weekly reflections now blend journal and ingested content in a single pass.

## v0.7.8 — 2026-03-23 — Journal Ingestion
`scripts/journal.py` CLI and `POST /journal` endpoint. Mood and tags metadata. 20-character minimum for journal entries.

## v0.7.6 — 2026-03-22 — Context Compression
Mid-conversation buffer compression at 70% context window. LLM summarization of oldest turns. Session summaries persisted to vault.

## v0.7.5 — 2026-03-22 — Profile Quality
Profile retrieval score-gated at 0.3. Assistant noise suppression (3,574 total suppressed, 21.4% of corpus). Audit tools added.

## v0.7.4 — 2026-03-22 — Runtime Model Switching
`GET /model` and `POST /model` endpoints. Switch models without restart. Context window updates per model.

## v0.7.2 — 2026-03-22 — Corpus Quality
Quality column in SQLite. 3,327 records suppressed (short, noisy, or low-value). Non-destructive — suppressed rows remain in DB.

## v0.7.1 — 2026-03-22 — Reflection Filters
Tightened skip filters for reflection generation. 31 new tests. Jaccard-based diversity selection.

## v0.7.0 — 2026-03-22 — Conversation Memory Fixed
Conversation write path fixed — two records per turn (user + assistant). Memory type propagation end-to-end.

## v0.6.0 — 2026-03-16 — SQLite Retrieval
Ingested corpus migrated from 1.32 GB JSON to SQLite. SqliteVectorStore with struct-packed BLOBs. 16,728 records searchable.

## v0.5.x — 2026-03-21 — State Layer
StateService, StateResolver, StateRecord, StateItem. 8 state categories. Context packet integration. Prompt rendering. API endpoints.

## v0.4.0 — 2026-03-20 — Constitutional Review
Constitutional review flow: trigger → review → allow/revise/refuse. `config/constitution.yaml` with 7 principles. Safety review logging.

## v0.3.0 — 2026-03-16 — RAG Pipeline
Retrieval-augmented generation pipeline. Semantic search, context assembly, policy-weighted ranking.

## v0.2.0 — 2026-03-15 — Core Architecture
Memory storage, retrieval, reflection engine. The bones.

## v0.1 / v0.0.x — 2026-03-15 — Genesis
Storage layer, retrieval prototype, first reflection generation. Ember-2 begins.
