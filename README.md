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

# Ember's Nature

Ember has a nature. She holds positions, names patterns, and won't pretend to be something she isn't. That nature can be disabled -- bare mode gives you a capable local search and retrieval system without the character layer. But if you're looking for a system that agrees with everything you say, Ember isn't it. She's built to think with you, not at you.

Her nature is defined in `config/nature.yaml` and earned through observation, not assigned through aspiration. Constitutional governance (`config/constitution.yaml`) establishes what she does and does not do. Identity rules (`config/identity_rules.yaml`) govern how she holds her identity under conversational pressure. These layers work together: nature defines who she is, the constitution defines her boundaries, and the identity rules define how she maintains both under challenge.

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
- Local LLM runtime (Ollama) or cloud (Anthropic Claude)
- prompt templates
- adapter layer with provider dispatch

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

### Tool Layer
Handles:
- task creation and tracking
- web search
- future: calendars, email, health data integrations
- future: controlled action-taking workflows

Tool usage remains observable and policy-driven.

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

## Session Reflection
- end-of-session capture before context is lost
- auto-triggers on session delete if 3+ turns in buffer

## Future Reflection Modes
- monthly synthesis
- thematic reflection
- strategic review

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

## Working (v0.14.1)

**Core systems:**
- Append-only JSON vault with typed memory enforcement (17 types validated at write time)
- Ingestion pipeline (ChatGPT, PDF, DOCX, CSV, TXT, GDrive, POST /ingest/upload multipart)
- Semantic retrieval via vector indexes (cached in memory, no disk load per query)
- Context assembly with policy-weighted ranking, diversity selection, project-scoped boost
- SSE streaming responses from Ollama or Anthropic through FastAPI
- Cloud model provider support — Anthropic Claude and OpenAI via LLMAdapter
- Provider API key storage via OS credential store (Windows, macOS, Linux)
- Auto state extraction from conversation turns (background thread)
- State layer (StateService, StateResolver, 8 categories, context packet integration)
- Multi-record state categories for open_loop and next_action (capped at 5)
- Commitment detection — post-generation detector writes open_loop state records
- Daily, weekly, and session reflection generation
- Constitutional review (9 principles, constitution v0.6, streaming-compatible)
- Conversation sessions with projects, rename, soft-delete, auto-title
- Task layer — create and track tasks through conversation or direct request
- Task sidebar tray in the UI with checkbox completion
- Temporal awareness — staleness penalties, age labels, hedging rules for old memories
- Self-echo prevention (role-labeled context, metadata-aware scoring)
- Web search via local SearXNG with transparency indicator
- Vision model support with graceful text-only fallback
- Default model: qwen3:8b (best local model tested)

**User-facing features (v0.12.0):**
- Task creation and tracking through natural conversation
- PIN/passphrase lock — secure Ember with bcrypt, idle timeout, and recovery
- Conversational style settings — Casual, Balanced, or Thoughtful
- Multi-image upload — send multiple images in a single message
- Web search transparency indicator — see when web search was used
- Guided first-run tour for new users
- Mac/Linux installer support — platform-aware setup for all three platforms

**Security:**
- API key auth via OS credential store (Windows Credential Manager, macOS Keychain, Linux SecretService)
- PIN/passphrase lock with rate limiting, idle timeout, and recovery
- Rate limiting, path traversal protection, JSON audit logging
- Dependency security policy — native fetch, no axios
- SearXNG and API bound to localhost

**Evaluation & Tooling:**
- Retrieval evaluation (15 benchmark cases, pass/warn/fail scoring)
- Conversation quality eval with Claude as external evaluator
- Model selection guide with real eval data (docs/model_guide.md)
- Vault health audit (7 checks, GREEN/YELLOW/RED health score)
- 779 pytest tests passing

> Note: Eval harness results reflect personal vault contents and are not generic benchmarks.

## Roadmap

**v0.11.0** — Cloud provider UI, OpenAI support, backup/export, recovery playbook, semantic safety triggers ✓
**v0.12.0** — Task layer, session reflection, PIN lock, Mac/Linux installer, temporal awareness ✓
**v0.13.0** — Embedding upgrade (nomic-embed-text 768-dim), SQLite index migration, memory tiering (ADR-015), nature layer (ADR-016), grounding verification (ADR-019), intent-aware type gating (ADR-018), XML context sections, monthly reflection, JSON import, identity rules layer ✓
**v0.14.0** — Identity foundation: Lodestone layer, deviation engine, context packet reorder, release automation ✓
**v0.14.1** — Patch: timer functions, identity stance rules, constitutional review fixes, vault hygiene ✓
**v0.15.0** — Quality of life improvements: vault encryption, token reduction, constitutional review optimization, web search interaction mode, hallucination reduction, API auto-start, quality of life testing
**v0.16.0** — Health + agent orchestration: health data ingestion, self-evaluation loops, trace-driven learning, relational orientation layer
**Post-v0.16.0** — Multi-user vault isolation, full platform parity

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
├ tests/                  Pytest suite (779 tests)
├ prompts/                LLM prompt templates
├ logs/                   Audit logs, safety review logs (gitignored)
├ ui/                     Built Ember UI frontend (gitignored, built from ember-2-ui)
├ CLAUDE.md               AI coding instructions and architecture rules
├ ETHOS.md                Ember's founding principles
├ SETUP.md                First-time setup guide
├ .env.example            Environment variable template
├ docker-compose.yml      SearXNG container
├ start_api.bat           Windows API startup script
├ start_api.sh            Mac/Linux API startup script
└ private_vault/          Excluded from git — all memory data lives here
```

---

## License

Ember's code is licensed under AGPL-3.0. Her visual identity, assets, and branding are licensed under CC BY-NC 4.0 — free to use personally, not for commercial products. Ember belongs to the community.
