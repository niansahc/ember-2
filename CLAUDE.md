# CLAUDE.md — Ember-2

Canonical design: `docs/Ember2_TDD.md`
Business requirements: `docs/Ember2_BRequirements.md`
ADRs: `docs/adr/`

---

## What This Project Is

Ember-2 is a **local, private personal intelligence system** — not a chatbot. It supports reasoning grounded in real memory, pattern recognition over time, daily/weekly reflection, and long-term life and work continuity. The goal is a durable personal intelligence layer that improves with use.

The LLM is a reasoning engine. It is not the system of record. All canonical knowledge lives in the filesystem vault.

---

## Core Architectural Rules (Non-Negotiable)

These decisions are locked. Do not undermine them:

1. **Local-first.** No cloud storage of private data. External tools are opt-in.
2. **LLM is not the system of record.** The model generates, summarizes, and reflects. It does not store canonical truth.
3. **Append-only memory.** Records are never overwritten in place. Every change writes a new artifact.
4. **Rebuildable derived artifacts.** Indexes, reflections, and embeddings can all be deleted and rebuilt from canonical vault records. Never treat them as irreplaceable.
5. **Typed memory beats one big pile.** Source, derived, reference, state, archive, and operational artifacts must remain separated. Cross-contamination degrades retrieval.
6. **Source quality before retrieval cleverness.** Clean ingestion and typed metadata come before ranking sophistication.
7. **Constitutional review is orchestration, not training.** The review layer is triggered post-draft, lives in the Cognitive layer, and is governed by `config/constitution.yaml`. It must not contaminate retrieval logic.
8. **Explicit policy over prompt folklore.** Safety, refusal, and review behavior must be visible in code and config — not buried in prompt text or model behavior assumptions.

---

## System Layers

```
Interface Layer      — FastAPI (src/api/), Ember UI (ui/), CLI scripts
Reasoning Layer      — Ollama + local LLM, prompt templates (src/llm/)
Cognitive Layer      — ContextService, ContextRetriever, ContextRanker,
                       ReflectionEngine, SafetyPolicyService, ResponseReviewService
                       (src/context/, src/reflection/, src/safety/)
Memory Layer         — Append-only JSON vault, vector indexes (src/memory/, src/retrieval/)
State Layer          — PLANNED (src/state/ stub exists)
Tool Layer           — PLANNED (src/tasks/ stub exists)
```

The cognitive layer is the brain. It orchestrates retrieval, context assembly, LLM calls, and review routing. The LLM only sees what the cognitive layer gives it.

---

## Repository Structure

```
ember-2/
  src/
    api/            FastAPI app, OpenAI-compatible adapter, ingest route
    context/        ContextService, ContextRetriever, ContextRanker, policies
    core/           Config (PRIVATE_VAULT_PATH via .env)
    ingest/         Pipeline, chunker, filters, importers (ChatGPT, PDF, DOCX, CSV, GDrive)
    llm/            Ollama adapter, prompt builder, safety adapter
    memory/         MemoryService, storage, read/write/search helpers
    retrieval/      VectorIndex, semantic search, embed_memory
    reflection/     Daily + weekly reflection generators
    safety/         ConstitutionLoader, SafetyPolicyService, ResponseReviewService, logger
  config/
    constitution.yaml   External constitutional governance rules
  docs/
    Ember2_TDD.md       Full technical design (canonical)
    Ember2_BRequirements.md
    adr/                Architecture Decision Records
  scripts/
    import_chatgpt.py   Ingest ChatGPT exports
    test_context_retrieval.py
    test_search.py
  tools/
    eval_retrieval.py   Retrieval evaluation harness (5 benchmark queries)
    view_safety_logs.py
  tests/
    test_constitution_loader.py
    test_policy_service.py
    test_review_service.py
    test_vault.py
  ui/                 Built frontend served by FastAPI (gitignored, built from ember-2-ui)
  prompts/            LLM prompt templates
  logs/               Safety review logs, retrieval eval output
  private_vault/      EXCLUDED FROM GIT — all actual memory data lives here
```

---

## Private Vault Layout

```
private_vault/
  memory/
    conversation/   Raw conversation turns
    journal/        Journal entries
    reflection/     Derived daily/weekly reflections
    state/          Active state artifacts (current focus, open loops)
    ingested/       Chunked content from imports
    archive/        Lower-priority preserved material
  embeddings/
    conversation_index.json
    ingested_index.json
    reflection_index.json
    (etc.)
  imports/          Raw source files (ChatGPT exports, docs)
```

The vault path is set via `PRIVATE_VAULT_PATH` in `.env`. See [src/core/config.py](src/core/config.py).

---

## Memory Record Schema

Every canonical record is a JSON file with these required fields:

```json
{
  "id": "2026-03-17T20-15-00",
  "timestamp": "2026-03-17T20-15-00",
  "type": "reflection",
  "text": "...",
  "source": "reflection_engine",
  "tags": ["weekly", "reflection"],
  "metadata": {}
}
```

Valid types (taxonomy may evolve, separation principle must not):
`profile`, `journal`, `conversation`, `reflection`, `summary`, `state`, `task`, `project`, `reference`, `ingested`, `archive`, `system_event`, `decision`, `review_log`, `evaluation`, `session`

Canonical code reference: `VALID_MEMORY_TYPES` in `src/memory/storage.py`. All writes are validated against this set.

---

## Retrieval Architecture

Retrieval is **not** just cosine similarity. The pipeline is:

1. **Intent classification** — `src/context/policies.py` → classify_query() → policy object
2. **Candidate gathering** — semantic + (future: lexical + chronological)
3. **Policy weighting** — type weighting, source quality adjustments
4. **Dedup + diversity selection** — Jaccard-based, cross-type diversity
5. **Context packet** — `ContextPacket` with state_items, reflection_items, memory_items

Boost: user-authored content, concrete experiences, recent state, meaningful reflections
Penalize: assistant filler, tool traces, JSON payloads, short trivial content

Context packet order to model: system prompt → state → reflections → source memories → reference → user query

---

## Constitutional Review Flow

```
User query → Context build → LLM draft → Trigger check
  → not triggered: pass through
  → triggered: constitutional review (allow / revise / refuse+redirect) → log
```

The trigger layer (`SafetyPolicyService`) is fast and heuristic. Review (`ResponseReviewService`) is LLM-assisted. Both are governed by `config/constitution.yaml`. Review outcomes are logged to `logs/safety_reviews/`.

---

## Running the System

```bash
# Start API
./start_api.bat
# or: uvicorn src.api.main:app --reload

# Run retrieval evaluation
python tools/eval_retrieval.py

# Ingest ChatGPT export
python scripts/import_chatgpt.py

# Run daily reflection
python -m src.reflection.run_daily_reflection

# Run weekly reflection
python -m src.reflection.run_weekly_reflection
```

Docker Compose runs SearXNG only (private web search engine). The `ui/` folder contains the built Ember UI frontend, served by FastAPI at the same port.

Key API endpoints:
- `GET /` — serves the Ember UI when ui/ folder exists, otherwise health check JSON
- `POST /v1/chat/completions` — OpenAI-compatible chat
- `GET /debug-context?message=...` — inspect context packet for a query
- `GET /semantic-search?query=...` — direct vector search
- `POST /reflect` — trigger reflection
- `GET /search-memories` — keyword memory search

---

## Current Working State

These are confirmed working as of v0.9.4+:

**Core Systems:**
- Memory storage (append-only JSON vault)
- Typed memory enforcement (`VALID_MEMORY_TYPES` in `src/memory/storage.py` — all writes validated)
- Ingestion pipeline (ChatGPT, PDF, DOCX, CSV, GDrive importers, POST /ingest/upload)
- Semantic retrieval via vector indexes (cached in memory — no disk load per query)
- Context assembly with policy-weighted ranking and diversity selection
- Project-scoped retrieval boost (ADR-007 — +0.15 for matching project_id)
- Daily and weekly reflection generation
- State layer (StateService, StateResolver, StateExtractor for auto-extraction from conversations)
- FastAPI + Ember UI integration (UI served from ui/ folder, same port as API)
- Constitutional review flow end-to-end (8 principles including authentic_expression)
- Safety review logging
- Conversation session system (session_id, project_id, rename, soft-delete)
- Projects backend (CRUD endpoints, conversation assignment)

**Tooling:**
- Retrieval evaluation harness (`tools/eval_retrieval.py`, 15 benchmark cases, pass/warn/fail scoring)
- Vault health audit (`scripts/audit_memory.py` — 7 checks, GREEN/YELLOW/RED health score)
- Vector index in-memory caching (cache hit/miss logging, auto-invalidation on write)

**Security:**
- API key auth via Windows Credential Manager
- Auth only on API routes — UI static files are public
- Rate limiting, path traversal protection, JSON audit logging
- SearXNG bound to localhost only
- Tailscale serve uses localhost binding (API binds to 127.0.0.1, Tailscale handles routing)

**UI (ember-2-ui, served from ui/):**
- Chat with markdown rendering, copy, edit/resend, regenerate, scroll-to-bottom, export
- Sidebar with projects, conversations, search, right-click context menu, New Project button
- Settings: 5 themes, model selector, vision toggle, web search toggle
- About panel with Ember's story, beliefs, ethos
- Bug reports to GitHub Issues, update checker
- PWA manifest for Android/iOS home screen installation
- Consistent Ember-2 branding across all surfaces
- WCAG 2.1 AA accessible

**Installer (ember-2-installer):**
- Auto-install prerequisites via winget
- Curated model cards with disk sizes
- Ember detection scan, disk space summary
- Venv/API lock detection with friendly error messages
- Auto-start API after install with health check polling
- Tailscale walkthrough with correct serve binding
- Progress bar + fun facts during install
- Clones ember-2 repo, builds UI from ember-2-ui

---

## Immediate Next Priorities

1. **Cloud model provider support** — see ADR-008 (proposed). Abstract `LLMAdapter._chat()` to dispatch based on provider. Opt-in, never default.
2. **Context quality tuning** — run retrieval eval, review results, adjust policy weights and filters
3. **Installer UX polish** — post-install health check, animated transitions, better error messages
4. **Desktop/browser integrations** — research Claude-style integrations for Ember (system tray, clipboard, ambient presence)
5. **Conversation quality eval** — use Claude as evaluator to score Ember's response quality

Build order from TDD: ~~clean ingestion~~ → ~~typed memory~~ → retrieval policy → ~~state layer~~ → ~~evaluation suite~~ → review stabilization → task layer → ~~index migration~~ → tools → agent orchestration. ~~Streaming~~ complete.

---

## What Not to Touch

- `private_vault/` — never commit, never rewrite in place, never treat index files as source of truth
- The core architectural bets (local-first, append-only, LLM not system of record, triggered post-draft review) — these are the right bones
- `config/constitution.yaml` — external config by design; do not move governance into code or prompts
- The separation between retrieval logic and review logic — these must remain in separate services with no cross-dependency

---

## Testing

```bash
pytest tests/
```

196 tests covering: constitution loader, policy service, review service, vault read/write, state layer, state extractor, project boost, index caching, memory type enforcement, health check, ingest upload.
Tests do not mock the filesystem vault (real path via `PRIVATE_VAULT_PATH`). Integration tests hit real storage.

When adding features: unit test normalizers, filters, ranking functions, and state resolution. Integration test full pipeline paths.

---

## Working With This Codebase

- Code is written by AI, reviewed and approved by the human
- Always replace full files — never partial find-and-replace edits
- Work on one file at a time
- Small, frequent commits with clear descriptive messages
- Tag commits at major milestones
- After milestones: update TDD first, then README
- Code must be clean, well-commented, forward-thinking, and scalable
- If the human says **PAUSE** — stop and reorient
- If the human says **STOP** — drop the topic entirely
- The human has ADHD and Autism — minimize cognitive overhead, be explicit about what is changing and why before touching anything
- Before editing any file, state: what file, what change, and what the commit message will be

---

## Key Design Risks to Watch

| Risk | Watch for |
|---|---|
| Contaminated corpus | Low-quality ingestion slipping through filters |
| Index corruption | Oversized or malformed JSON index files |
| Mixed memory types | Ingested content written to wrong type folder |
| Assistant self-echo | Prior assistant responses polluting retrieved context |
| Prompt folklore creep | Policy decisions migrating into prompt text instead of code |
| Over-triggering review | Safety triggers firing on benign queries — check logs |
| State layer absent | System can remember but not manage current operational context |
