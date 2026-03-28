# Ember-2

A local, private personal intelligence system for reasoning, memory, reflection, and long-term assistance.

[What Ember Believes](ETHOS.md) · [How Ember Works](docs/Ember2_TDD.md) · [Start Working With Ember](SETUP.md) · [Report a Bug](https://github.com/niansahc/ember-2/issues)

Download the Ember Setup installer for Windows, Mac, or Linux — https://github.com/niansahc/ember-2-installer/releases

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
- Ember UI (served from ui/ folder by FastAPI)
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

## Working (v0.10.0)
- memory storage (append-only JSON vault with typed enforcement via VALID_MEMORY_TYPES)
- ingestion pipeline (ChatGPT, PDF, DOCX, CSV, GDrive, POST /ingest/upload multipart)
- semantic retrieval
- context assembly
- reflection engine
- API + Ember UI (served from FastAPI)
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
- corpus quality suppression (3,574 of 16,728 ingested records suppressed; quality flag in SQLite)
- reflection scoring and filter improvements (length gate, diversity selection, skip filter tightened)
- prompt perspective fix (MEMORY CONTEXT split into user self-description and context sub-sections)
- profile retrieval score-gated at 0.3 (no more unconditional slot guarantee; irrelevant records excluded)
- runtime model switching (GET /model, POST /model; qwen2.5:14b and mistral:7b available)
- mid-conversation context compression (token-based, 70% threshold, LLM summarization, session summaries persisted to vault)
- journal ingestion (scripts/journal.py CLI + POST /journal endpoint; 20-char minimum; mood and tags metadata)
- multi-source reflection (daily + weekly reflection now blend journal and ingested content in a single pass)
- payload interference hardening (empty message guard; `### Task:` RAG injection guard; type-aware payload logging) — v0.7.10
- web search via local SearXNG (intent-gated; `web_items` in ContextPacket; results above memory context in prompt; apostrophe normalization) — v0.8.0/v0.8.1
- vision model integration (`EMBER_VISION_MODEL` in .env; `image_data` pipeline from openai_adapter → ContextPacket → Ollama `images=` kwarg; graceful text-only fallback) — v0.8.2
- security hardening: Tailscale-only API binding, API key auth middleware, Windows Credential Manager key storage (keyring), BitLocker encryption at rest, rate limiting (slowapi), path traversal protection on ingest endpoints, JSON audit logging to `logs/audit/`, Tailscale HTTPS via Serve, ACL restricted to `autogroup:member` — v0.8.3/v0.8.4
- auto state extraction from conversation turns (StateExtractor, background thread)
- conversation session system (session_id, project_id, rename, soft-delete)
- projects backend (CRUD, conversation assignment, project-scoped retrieval boost ADR-007)
- vector index in-memory caching (cache hit/miss logging, auto-invalidation on write)
- retrieval evaluation harness (15 benchmark cases, pass/warn/fail scoring)
- vault health audit (scripts/audit_memory.py — 7 checks, GREEN/YELLOW/RED)
- PWA manifest for Android/iOS home screen installation
- **streaming responses** — SSE from Ollama through FastAPI; first token in 1-2s
- **auto state extraction** — background detection of focus, blockers, goals from conversation
- **project-scoped retrieval** (ADR-007) — +0.15 boost for matching project_id
- **typed memory enforcement** — VALID_MEMORY_TYPES validates all writes
- **vault health audit** — scripts/audit_memory.py with 7 checks and health score
- **buffer compression backgrounded** — no longer blocks response
- self-echo prevention (role-labeled context, metadata-aware scoring)
- conversation quality eval with Claude as external evaluator (18 tests, 6 categories)
- local model comparison eval (automated, all installed models)
- reflection quality audit and suppression tools
- temporal grounding (date injection, timestamps on context items)
- profile retrieval tuning for identity queries
- authentic_expression constitutional principle
- Ember uses she/her pronouns
- 228 tests passing

> Note: Eval harness results reflect personal vault contents and are not generic benchmarks.

## Roadmap

**v0.10.2** — Model eval results, cloud integration (Claude Sonnet 4.6), model selection guide
**v0.11.0** — Cloud provider support (ADR-008), backup/export, recovery playbook, semantic safety triggers (ADR-010)
**v0.12.0** — Task layer, session reflection (ADR-009), Mac/Linux installer
**v0.13.0** — Memory tiering, embedding upgrade, vault encryption at rest
**v0.14.0** — Offline knowledge (Kiwix ZIM, Project Gutenberg)
**v0.15.0** — Agent orchestration, self-evaluation loops
**Post-v0.15.0** — Multi-user vault isolation, full platform parity

---

# Security

Ember-2 is hardened for single-user local deployment as of v0.8.3–v0.8.4.

## Controls in Place

| Control | Implementation |
|---|---|
| Vault encryption at rest | BitLocker (AES) on C: — covers `C:\EmberVault\` |
| Vault location | `C:\EmberVault\` — off OneDrive, not cloud-synced |
| API key storage | Windows Credential Manager via `keyring` — never in `.env` |
| API authentication | `Authorization: Bearer` or `X-API-Key` header; `secrets.compare_digest` |
| Network exposure | API bound to Tailscale IP only (`&lt;your-tailscale-ip&gt;`); LAN blocked |
| Transport encryption | HTTPS via Tailscale Serve (TLS cert from Tailscale CA) |
| Network access control | Tailscale ACL: `autogroup:member` only — no unauthenticated access |
| Rate limiting | 60/min global default; 30/min chat; 10/min reflect/ingest (slowapi) |
| Path traversal | Ingest endpoints validate `file_path` is inside `vault/imports/` |
| Audit logging | JSON lines to `logs/audit/YYYY-MM-DD.log` (ts, method, path, ip, status, ms) |
| SearXNG | Bound to `127.0.0.1:8888` — not reachable from network |

## Key Setup

```bash
# Store or rotate API key (run once after setup)
python scripts/set_api_key.py
```

The key is DPAPI-encrypted in Windows Credential Manager and tied to your Windows login. It is never written to `.env` or any plaintext file.

## Remaining Gaps (Single-User)

- No application-level file ACLs on vault (relies on OS + BitLocker)
- Audit log covers authentication layer only — not memory read/write events
- Rate limits are per-IP (Tailscale IP) — effective for single user, not a substitute for multi-user auth

## Multi-User

Not supported. Multi-user deployment requires per-user vault isolation, independent API keys, and a separate auth layer. See TDD §31 and §36.

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
src/
│   ├ api/           FastAPI app, OpenAI-compatible adapter, ingest routes
│   ├ context/       ContextService, ContextRetriever, ContextRanker, policies
│   ├ core/          Config (PRIVATE_VAULT_PATH, model settings)
│   ├ ingest/        Pipeline, chunker, filters, importers
│   ├ llm/           Ollama adapter, prompt builder, conversation buffer
│   ├ memory/        MemoryService, storage, read/write/search helpers
│   ├ retrieval/     VectorIndex, semantic search, embed helpers
│   ├ reflection/    Daily and weekly reflection generators
│   ├ safety/        ConstitutionLoader, SafetyPolicyService, ReviewService
│   ├ state/         StateService, StateResolver, state models
│   └ tools/         Internal tool helpers
│
├ config/
│   ├ constitution.yaml   Constitutional governance rules
│   └ searxng/            SearXNG configuration
│
├ docs/
│   ├ Ember2_TDD.md            Technical design document (canonical)
│   ├ Ember2_BRequirements.md  Business requirements
│   └ adr/                     Architecture Decision Records
│
├ scripts/
│   ├ seed_identity_template.py   Template for seeding your profile
│   ├ set_api_key.py              Store API key in Windows Credential Manager
│   ├ import_chatgpt.py           Ingest a ChatGPT export
│   ├ journal.py                  CLI journal entry writer
│   ├ audit_memory.py             Vault health check
│   └ repoint_vault_paths.py      One-time vault migration helper
│
├ tools/
│   ├ eval_retrieval.py           Retrieval evaluation harness
│   ├ inspect_indexes.py          Browse vector index contents
│   ├ view_safety_logs.py         View constitutional review logs
│   ├ audit_assistant_chunks.py   Audit assistant-generated chunks
│   └ suppress_assistant_noise.py Flag low-quality ingested records
│
├ tests/                  Pytest suite (123 tests)
├ prompts/                LLM prompt templates
├ logs/                   Audit logs, safety review logs (gitignored)
├ ui/                     Built Ember UI frontend (gitignored, built from ember-2-ui)
├ CLAUDE.md               AI coding instructions and architecture rules
├ ETHOS.md                Ember's founding principles
├ SETUP.md                First-time setup guide
├ .env.example            Environment variable template
├ docker-compose.yml      SearXNG container
├ start_api.bat           Windows API startup script
└ private_vault/          Excluded from git — all memory data lives here
```

---

## License

Ember's code is licensed under AGPL-3.0. Her visual identity, assets, and branding are licensed under CC BY-NC 4.0 — free to use personally, not for commercial products. Ember belongs to the community.
