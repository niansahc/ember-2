> Canonical source: docs/Ember2_TDD.md
> This file is a high-level overview.

# Ember-2

A local, private personal intelligence system for reasoning, memory, reflection, and long-term assistance.

Ember-2 is designed to evolve beyond a chatbot into a structured cognitive system that supports life, work, decision-making, and future automation over time.

---

# Core Principles

- Local-first architecture
- LLM is not the system of record
- Append-only memory
- Structured retrieval over raw prompting
- Clean ingestion and rebuildability
- Separation of source vs derived knowledge
- Explicit policy over hidden prompt folklore

---

# What Ember-2 Does

Ember-2 supports:

- contextual conversation grounded in memory
- long-term pattern recognition
- structured knowledge retrieval (RAG)
- reflective synthesis (daily/weekly insights)
- project and life context awareness
- future state and task continuity
- explicit constitutional response governance

---

# System Overview

Ember-2 is built as a modular system, not a monolithic agent.

## Layers

### Interface Layer
- Open WebUI
- FastAPI API
- CLI scripts

Handles:
- user interaction
- request routing
- response delivery

---

### Reasoning Layer
- Local LLM runtime (Ollama)
- prompt templates
- adapter layer

Handles:
- interpretation
- synthesis
- reflection generation
- critique and revision prompts when orchestration requests them

Does not store memory or own canonical truth.

---

### Cognitive Layer
- ContextRetriever
- ContextRanker
- ContextService / ContextBuilder
- Reflection Engine
- Retrieval Policy
- SafetyPolicyService
- ResponseReviewService

Handles:
- retrieving relevant memory and reflections
- ranking and deduplicating evidence
- assembling structured context
- calling the reasoning layer
- deciding whether review is triggered
- applying constitutional review after draft generation

Constitutional review lives here as orchestration and policy logic, not as a separate top-level ethics layer.

---

### State Layer
Handles:
- active goals
- current priorities
- open loops
- project continuity
- near-term operational context

State is distinct from both raw memory and reflections.

---

### Memory Layer
Stores all persistent knowledge.

Includes:
- Source Memory
- Derived Memory
- State Memory
- Reference Memory
- Archive Memory
- Operational / Policy Artifacts
- Vector Index

Characteristics:
- append-only
- JSON-based storage today
- rebuildable
- chronologically traceable

---

### Tool Layer (Planned)
Handles:
- web lookup
- document search
- calendars, tasks, contacts
- local automations
- future controlled action-taking workflows

Tool usage should remain observable and policy-driven.

---

# Memory Model

Each canonical record includes:

- `id`
- `timestamp`
- `type`
- `text`
- `source`
- `tags`
- `metadata`

## Memory Classes

### Source Memory
Original first-order evidence:
- user statements
- conversation turns
- journal entries
- imported notes
- project logs
- imported documents

### Derived Memory
Synthesized artifacts:
- summaries
- reflections
- pattern analyses
- retrospectives

### State Memory
Operational continuity artifacts:
- active priorities
- blockers
- current focus
- routines
- next actions
- open loops

### Reference Memory
Imported background material:
- docs
- manuals
- architecture notes
- requirements
- chat exports

### Archive Memory
Older or lower-priority preserved material.

### Operational / Policy Artifacts
Inspectable governance artifacts:
- review logs
- evaluation results
- audit traces

---

# Retrieval Strategy

Retrieval is not just vector similarity.

It uses a hybrid policy built from:

- semantic similarity
- lexical relevance
- chronological recall
- memory type weighting
- source quality
- query intent

## Query Intent Classes
At minimum:

- reflective
- task/work
- timeline
- status/state
- research/reference
- operational/debugging

## Retrieval Priorities
The system generally boosts:

- user-authored content
- concrete experiences
- recent state
- meaningful reflections
- clearly scoped project records

It penalizes:

- assistant filler
- tool traces
- wrappers
- JSON payloads
- trivial or meta content

The top context packet should avoid duplication and thematic collapse.

---

# Ingestion Principles

Ingestion converts raw content into clean, typed, retrievable artifacts.

Principles:

- filter out JSON, tool traces, and prompt scaffolding
- remove trivial or low-value messages
- preserve meaningful user and assistant content
- attach structured metadata
- chunk according to meaning, not size alone
- write canonical records before derived index entries
- ensure full rebuild capability

Pipeline stages:

- import
- normalize
- chunk
- quality filter
- write canonical records
- generate embeddings
- update index

---

# Reflection System

Reflection transforms memory into higher-level insight.

## Daily Reflection
- summarizes recent activity
- maintains short-term coherence

## Weekly Reflection
- identifies patterns
- consolidates progress
- surfaces blockers and broader trends

## Future Reflection Modes
- monthly synthesis
- thematic reflection
- strategic review
- session reflection (end-of-session capture before context is lost)

Reflections are stored as first-class Derived Memory and remain traceable to source windows.

---

# Constitutional Response Governance

Ember-2 uses explicit constitutional response governance.

This is inference-time orchestration, not training.

## Core Design
- review is triggered, not universal
- review happens post-draft
- review outcomes are:
  - allow
  - revise
  - refuse + redirect
- constitution lives in external config
- review behavior is logged and inspectable

## Components
- `config/constitution.yaml`
- ConstitutionLoader
- SafetyPolicyService
- ResponseReviewService
- SafetyReviewLogger

## Review Flow
1. user query is processed
2. context is assembled
3. draft response is generated
4. trigger layer evaluates risk
5. if triggered, constitutional review critiques the draft
6. system allows, revises, or refuses + redirects
7. review path is logged

This governance layer must not contaminate retrieval logic.

---

# Observability and Debugging

The system is designed to be inspectable.

At minimum, debugging should expose:

- retrieved candidate chunks
- final selected context
- memory classes used
- dropped items and why
- reflection input windows
- current state resolution results
- whether review triggered
- which signals fired
- which constitutional rules were used
- whether the base model already refused before review
- whether review changed the draft or passed it through

Logs are intended to support debugging, tuning, and evaluation rather than act as canonical memory.

---

# Current State

## Working
- memory storage
- ingestion pipeline
- semantic retrieval
- context assembly
- reflection engine
- API + WebUI integration
- constitutional review flow
- safety review logging
- state layer (StateService, StateResolver, models, ContextPacket integration, prompt rendering)
- status_state query intent with state_boost routing
- ContextRanker state_boost wiring
- state API endpoints (GET /state, GET /state/{category}, POST /write-state)
- add_state.py CLI script
- audit_memory.py vault health check script
- SQLite vector store for ingested content (SqliteVectorStore, 16,728 records)
- model configurable via EMBER_MODEL in .env (default: llama3.1:8b)
- ingested corpus searchable via semantic retrieval (migrated from 1.32 GB JSON to SQLite)
- conversation memory write path (openai_adapter writes two records per turn; was silently broken)
- memory_type propagation end-to-end (ContextItem field + all retriever paths)
- profile memory retrieval (guaranteed context slots; profile records always reach the LLM)
- corpus quality suppression (3,327 of 16,728 ingested records suppressed; quality flag in SQLite)
- reflection scoring and filter improvements (length gate, diversity selection, skip filter tightened)
- prompt perspective fix (MEMORY CONTEXT split into user self-description and context sub-sections)

## Next
- formal typed memory class enforcement
- retrieval evaluation benchmarks
- audit scripts
- trigger coverage improvements without overfitting
- add ADR for state layer design decisions

## Future
- task layer
- tool integrations
- proactive assistance
- controlled agent workflows
- model selector (UI/CLI switching between models per use case)
- onboarding conversation flow (guided profile seeding for new users)
- session reflection mode (end-of-session capture distinct from daily/weekly)
- ~~index migration to SQLite / DuckDB~~ — complete for ingested corpus (v0.6.0)
- stronger evaluation and review analytics

---

# Design Philosophy

Ember-2 is not a chatbot.

It is a system that:

- remembers
- reflects
- retrieves with intent
- assembles context intelligently
- applies explicit policy
- evolves over time

The LLM is a reasoning engine, not storage.

The goal is to build a durable, extensible personal intelligence system that improves with use.

---

# Repository Structure

```text
ember-2/
│
├ api/
├ src/
│   ├ core/
│   ├ context/
│   ├ ingest/
│   ├ llm/
│   ├ memory/
│   ├ retrieval/
│   ├ reflection/
│   ├ safety/
│   ├ state/
│   └ tasks/        (planned)
│
├ config/
├ docs/
├ jobs/
├ logs/
├ prompts/
├ scripts/
├ tools/
├ private_vault/  (excluded from git)
