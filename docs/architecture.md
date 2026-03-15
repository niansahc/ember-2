# Ember-2 Architecture

## Overview

Ember-2 is a local cognitive system designed to support reasoning, memory accumulation, and reflective synthesis.

The system is intentionally structured as modular services rather than a monolithic AI agent.

Core principles:

- Local-first architecture
- Append-only memory
- LLM used for synthesis, not storage
- Structured memory retrieval
- Scheduled reflection processes

The system evolves through the interaction of conversation, memory retrieval, and reflection.

---

# Core Architectural Rule

The LLM is **not the system of record**.

All persistent knowledge is stored in the memory vault.

The LLM is used only to:

- interpret
- summarize
- synthesize
- reflect

---

# System Layers

Ember-2 is organized into four conceptual layers.

## Interface Layer

Handles user interaction and system entry points.

Components:

- Open WebUI
- FastAPI API

Responsibilities:

- receive user messages
- expose memory endpoints
- trigger reflection jobs
- deliver responses to the user

---

## Reasoning Layer

Provides language reasoning and synthesis.

Components:

- Ollama model runtime
- Local LLM

Responsibilities:

- analyze prompts
- synthesize responses
- generate reflections
- interpret retrieved context

The LLM does **not** manage persistent state.

---

## Cognitive Layer

Coordinates reasoning with stored knowledge.

Components:

- Context Builder
- Reflection Engine

Responsibilities:

- assemble memory context
- prioritize relevant knowledge
- synthesize reflections
- convert raw memories into higher-level insights

---

## Memory Layer

Stores the canonical knowledge of the system.

Components:

- MemoryService
- JSON Memory Vault
- Vector Index
- Embedding Model

Characteristics:

- append-only storage
- JSON memory records
- semantic search capability
- reflection artifacts stored as memories

---

# Memory Architecture

Memory is stored in an append-only filesystem vault.

Characteristics:

- JSON records
- never overwritten
- chronological ordering
- schema-based structure

Memory entries are written through **MemoryService**, which enforces schema validation and append-only behavior.

This preserves a chronological reasoning history and enables:

- timeline reconstruction
- pattern detection
- long-term reflection
- decision tracing

---

# Memory Object Schema

Each memory record contains:

| Field | Description |
|------|-------------|
| timestamp | ISO timestamp used for chronological ordering |
| type | memory classification (journal, reflection, project, etc.) |
| text | primary memory content |
| source | component that created the memory |
| tags | optional classification labels |
| metadata | structured metadata used by system logic |

Example:

```json
{
  "timestamp": "...",
  "type": "reflection",
  "text": "...",
  "source": "reflection_engine",
  "tags": ["reflection"],
  "metadata": {
    "cadence": "weekly"
  }
}
# Retrieval System

The system supports three retrieval modes.

## Chronological Recall

Used for retrieving recent memories.

Supports:

- reflection generation
- recent context assembly

---

## Keyword Search

Basic text search across memory records.

Useful for:

- explicit recall
- debugging
- manual inspection

---

## Semantic Search

Retrieves memories by meaning using vector embeddings.

Embedding model:

sentence-transformers/all-MiniLM-L6-v2


Semantic search enables retrieval of conceptually related memories rather than exact matches.

---

# Conversation Reasoning Flow

```mermaid
flowchart TD

USER[User Message]

USER --> API[FastAPI Endpoint]

API --> RETRIEVE[Semantic Retrieval]

RETRIEVE --> VECTOR[Vector Index]
RETRIEVE --> MEMORY[Memory Vault]

VECTOR --> CONTEXT
MEMORY --> CONTEXT

CONTEXT[Context Builder]

CONTEXT --> PROMPT[Prompt Assembly]

PROMPT --> LLM[Local LLM via Ollama]

LLM --> RESPONSE[Generated Response]

RESPONSE --> API
API --> USER

# Reflection System

Reflection converts raw memories into higher-level understanding.

Reflection artifacts are first-class memory objects and are stored in the vault.

Reflection jobs run periodically.

## Daily Reflection

Processes a small window of recent memories.

Purpose:

summarize recent activity

maintain short-term coherence

## Weekly Reflection

Processes a larger memory window.

Purpose:

detect broader patterns

synthesize higher-level insights

Reflection results are written back into the memory vault and indexed for retrieval.

## Reflection Flow

flowchart TD

MEMORIES[Recent Memories]

MEMORIES --> REFLECT[Reflection Engine]

REFLECT --> SUMMARY[Reflection Summary]

SUMMARY --> STORE[MemoryService]

STORE --> VAULT[Memory Vault]

SUMMARY --> EMBED[Embedding Generation]

EMBED --> VECTOR[Vector Index]


## Combined Cognitive Loop
flowchart LR

USER --> CONVERSATION

CONVERSATION --> MEMORY_WRITE[Memory Write]

MEMORY_WRITE --> MEMORY_VAULT

MEMORY_VAULT --> RETRIEVAL

RETRIEVAL --> CONTEXT_BUILDER

CONTEXT_BUILDER --> LLM

LLM --> USER

MEMORY_VAULT --> REFLECTION_ENGINE

REFLECTION_ENGINE --> REFLECTION_MEMORY

REFLECTION_MEMORY --> MEMORY_VAULT

# Key System Loops

## Conversation Loop

User interaction retrieves relevant memory and feeds it into LLM reasoning.

## Reflection Loop

The system periodically analyzes stored memories and generates reflections.

## Memory Loop

New insights are written back to the memory vault, improving future reasoning.

---

# Context Builder Design

The context builder assembles memory context for LLM reasoning.

Recommended context structure:
1 system prompt
2 retrieved reflections
3 retrieved memories
4 user query
Reflections should be prioritized over raw memories when constructing context.

---

# Planned Future Layers

Several modules are planned but not yet implemented.

## Perception Layer

Connectors for external data sources.

Examples:

email
calendar
documents
files

# Planning Layer

Tracks structured decision-making.

Examples:

projects
tasks
goals
decision history

## Action Layer

Provides limited automation capabilities.

Examples:

email drafting
calendar suggestions
reminders
automation proposals

The LLM may suggest actions, but execution is handled by rule-based systems.

## Design Philosophy

The architecture should remain simple.

Core system components:

filesystem memory
vector embeddings
reflection jobs
context builder

Complexity should only be introduced when a clear capability requires it.

---

### Development Status

Current implemented modules:

memory storage
retrieval system
reflection engine
API layer

These components form the foundation of the Ember-2 cognitive system.

Future capabilities will build on top of these layers.

---

# Repository Structure

ember-2/
│
├ api/ # FastAPI endpoints
│
├ src/
│ ├ memory/ # MemoryService + MemoryStorage
│ ├ retrieval/ # Search systems (chronological, keyword, semantic)
│ ├ reflection/ # Reflection engine and reflection generation
│ └ core/ # Shared utilities and configuration
│
├ jobs/ # Scheduled background jobs (daily/weekly reflection)
│
├ docs/
│ ├ architecture.md
│ └ design-decisions.md
│
├ prompts/ # System prompts and prompt templates
│
├ tools/ # Indexing and ingestion utilities
│
└ private_vault/ # Append-only memory storage (excluded from git)

