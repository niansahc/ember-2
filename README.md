> Canonical source: docs/Ember2_TDD.md
> This file is a high-level overview.
> 
# Ember-2

A local, private personal intelligence system for reasoning, memory, reflection, and long-term assistance.

Ember-2 is designed to evolve beyond a chatbot into a structured cognitive system that can support life, work, and decision-making over time.

---

# Core Principles

- **Local-first architecture**
- **LLM is not the system of record**
- **Append-only memory**
- **Structured retrieval over raw prompting**
- **Clean ingestion and rebuildability**
- **Separation of source vs derived knowledge**

---

# What Ember-2 Does

Ember-2 supports:

- contextual conversation grounded in memory
- long-term pattern recognition
- structured knowledge retrieval (RAG)
- reflective synthesis (daily/weekly insights)
- project and life context awareness (future state layer)

---

# System Overview

Ember-2 is built as a modular system, not a monolithic agent.

## Layers

### Interface Layer
- Open WebUI
- FastAPI API

Handles:
- user interaction
- request routing
- response delivery

---

### Reasoning Layer
- Local LLM (Ollama)

Handles:
- interpretation
- synthesis
- reflection generation

**Does not store memory**

---

### Cognitive Layer
- Context Builder
- Retrieval System
- Reflection Engine

Handles:
- selecting relevant memory
- assembling context
- prioritizing reflections and source material
- preparing structured input for the LLM

---

### State Layer (Planned)

Handles:
- active goals
- tasks and open loops
- project tracking
- current context for decision-making

---

### Memory Layer

Stores all persistent knowledge.

Includes:
- Source Memory (raw conversations, ingested data)
- Derived Memory (reflections, summaries)
- Reference Memory (documents, static knowledge)
- Vector Index (semantic retrieval)

Characteristics:
- append-only
- JSON-based storage
- rebuildable
- chronologically traceable

---

# Memory Model

Each memory record includes:

- timestamp
- type
- text
- source
- tags
- metadata

The system distinguishes between:

- **Source Memory** → original content
- **Derived Memory** → reflections/summaries
- **State Memory** → active context (future)
- **Reference Memory** → external documents

---

# Retrieval Strategy

Retrieval is based on:

- semantic similarity
- lexical relevance
- source quality
- memory type weighting
- query intent

The system prioritizes:

- user-authored content
- concrete experiences
- diverse evidence
- clean, non-meta content

---

# Ingestion Principles

- filter out JSON, tool traces, and prompt scaffolding
- remove trivial or low-value messages
- preserve meaningful user and assistant content
- attach structured metadata
- ensure full rebuild capability

---

# Reflection System

Reflection transforms memory into higher-level insight.

## Daily Reflection
- summarizes recent activity
- maintains short-term coherence

## Weekly Reflection
- identifies patterns
- synthesizes broader insights

Reflections are stored as first-class memory objects.

---

# Development Roadmap

## Current
- memory storage
- ingestion pipeline
- semantic retrieval
- reflection engine
- API + WebUI integration

## Next
- memory class enforcement
- retrieval evaluation benchmarks
- state/task layer implementation
- improved context assembly

## Future
- agent capabilities
- proactive suggestions
- scheduling and routines
- tool integrations
- index migration (SQLite/DuckDB)

---

# Design Philosophy

Ember-2 is not a chatbot.

It is a system that:
- remembers
- reflects
- understands context
- evolves over time

The goal is to build a durable, extensible personal intelligence system that improves with use.

---

# Repository Structure

ember-2/
│
├ api/
├ src/
│   ├ ingest/
│   ├ memory/
│   ├ retrieval/
│   ├ reflection/
│   ├ context/
│   └ core/
│
├ jobs/
├ prompts/
├ tools/
├ docs/
├ private_vault/  (excluded from git)
