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
Reasoning Layer      — Ollama + local LLM or Anthropic Claude, prompt templates (src/llm/)
Cognitive Layer      — ContextService, ContextRetriever, ContextRanker,
                       ReflectionEngine, SafetyPolicyService, ResponseReviewService
                       (src/context/, src/reflection/, src/safety/)
Memory Layer         — Append-only JSON vault, vector indexes (src/memory/, src/retrieval/)
State Layer          — StateService, StateResolver, auto-extraction (src/state/)
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

## Current State (v0.10.2)

All items from the original TDD §25 build order are complete through step 6. The system is feature-complete for single-user local deployment on Windows. Cloud reasoning is available via Anthropic Claude (backend only — UI in v0.11.0).

**Core Systems:**
- Append-only JSON vault with typed memory enforcement (`VALID_MEMORY_TYPES`, 17 types)
- Ingestion pipeline (ChatGPT, PDF, DOCX, CSV, GDrive, POST /ingest/upload multipart)
- Semantic retrieval via vector indexes (cached in memory, no disk load per query)
- Context assembly with policy-weighted ranking, diversity selection, project-scoped boost (ADR-007)
- SSE streaming responses from Ollama or Anthropic through FastAPI
- Cloud model provider support — Anthropic Claude (Haiku 4.5, Sonnet 4.6) via LLMAdapter
- Provider API key storage via keyring with env var fallback (`ANTHROPIC_API_KEY`)
- Provider dispatch by model name prefix (`claude-` → Anthropic, else → Ollama)
- Auto state extraction from conversation turns (StateExtractor, background thread)
- State layer (StateService, StateResolver, 8 categories, context packet integration)
- Daily and weekly reflection generation (multi-source, junk-filtered, suppression tools)
- Constitutional review (8 principles including authentic_expression, streaming-compatible)
- Conversation sessions (session_id, project_id, rename, soft-delete, auto-title)
- Projects backend (CRUD, conversation assignment, project-scoped retrieval)
- Self-echo prevention (role-labeled context, metadata-aware scoring, -0.25 assistant penalty)
- Temporal grounding (current date injected, timestamps on context items)
- Profile retrieval tuning (identity queries lower score gate, return more items)
- Buffer compression backgrounded (no longer blocks response)
- Default model: qwen3:8b (5.4/10 eval, best local model tested)
- Ember uses she/her pronouns (system prompt)

**Evaluation & Tooling:**
- Retrieval evaluation (15 benchmark cases, pass/warn/fail scoring, latency tracking)
- Conversation quality eval with Claude as external evaluator (18 test cases, 6 categories)
- Local model comparison eval (automated, all installed models, comparison table) — `tools/eval_local_models.py`
- Cloud model eval: Claude Haiku 4.5 scored 8.7/10, Claude Sonnet 4.6 scored 8.5/10
- Eval baseline documented: `docs/eval_baseline_v0.10.1.md` (all 8 models, full category breakdowns)
- Model selection guide: `docs/model_guide.md` (linked from installer Done screen)
- Vault health audit (7 checks, GREEN/YELLOW/RED health score, --fix flag)
- Reflection quality audit and suppression tools

**Security:**
- API key auth via Windows Credential Manager
- Auth only on API routes — UI static files are public
- Rate limiting, path traversal protection, JSON audit logging
- SearXNG bound to localhost only
- Tailscale serve uses localhost binding

**UI (ember-2-ui, served from ui/):**
- Streaming chat with markdown, copy, edit/resend, regenerate, scroll-to-bottom, export
- Sidebar with projects, conversations, search, right-click context menu, New Project button
- Settings: 5 themes, model selector, vision toggle, web search toggle
- About panel with Ember's story, beliefs, ethos
- Bug reports to GitHub Issues, update checker
- PWA manifest for Android/iOS home screen installation
- Consistent Ember-2 branding, WCAG 2.1 AA accessible

**Installer (ember-2-installer):**
- Auto-install prerequisites via winget (Git, Python, Node, Ollama, Docker)
- Curated model cards with disk sizes, Ember detection scan, disk space summary
- Venv/API lock detection, auto-start API with health check polling
- Tailscale walkthrough, progress bar + fun facts, pip time warning
- Clones ember-2 repo, builds UI from ember-2-ui
- Model selection guide linked from Done screen

**Tests:** 244 passing

---

## Immediate Next Priorities

**v0.11.0 — Cloud Provider UI + Recovery + Onboarding:**
- Cloud provider UI (persistent active indicator, settings panel, API key management)
- Hardware detection in installer, auto-recommends local model based on detected RAM
- AGPL acknowledgment screen in installer
- Backup and export story — vault backup guide, export to portable format
- Onboarding discoverability — verify first-launch triggers automatically
- Recovery playbook — "what to do if Ember breaks" user-facing doc linked from UI and installer
- Social engineering constitutional upgrade — semantic pattern matching in trigger layer (ADR-010)
- Tray icon / OS notifications research
- OpenAI provider support (GPT-4o, GPT-4o mini)

**v0.12.0 — Task Layer + Session Reflection + Mac/Linux:**
- Task objects with ISC verifiable completion criteria
- Task CRUD API and state lifecycle
- Task layer ADR
- Session reflection mode (end-of-session capture, ADR-009)
- Mac and Linux installer support
- Reflection corpus guidance in onboarding (set expectations on timeline)

**v0.13.0 — Memory Tiering + Embedding Upgrade + Encryption:**
- Hot/warm/cold memory tiering by recency and relevance
- nomic-embed-text embedding upgrade via Ollama
- Index migration for remaining JSON indexes to SQLite
- Monthly/thematic reflection
- Vault encryption at rest (Ember-managed, with key recovery story)

**v0.14.0 — Offline Knowledge:**
- Kiwix ZIM ingestion adapter (curated packs only)
- Project Gutenberg adapter (epub/txt/html as Reference Memory)
- Curated pack recommendations in docs
- NOMAD-compatible path supported

**v0.15.0 — Agent Orchestration:**
- Self-evaluation and decision-memory loops
- OpenJarvis Learning primitive as reference implementation
- Controlled tool writes with stricter policy gates

**Post-v0.15.0:**
- Multi-user vault isolation
- Windows/Mac/Linux full parity across all features

## Watch Items
- OpenJarvis Learning primitive — reference for self-evaluation loops (active at v0.15.0)
- PAI TELOS pattern — evaluate against constitution + profile memory during v0.11.0 onboarding work
- Multi-user vault isolation — post-v0.15.0 milestone
- Eval harness uses user's own vault — results are personal, not generic benchmarks

## Known Gaps (tracked)
- Memory grounding weakness — root cause analysis in progress (all local models score 2.0-4.0; Claude scores 8.7)
- Vault encryption at rest — v0.13.0
- Social engineering trigger upgrade — v0.11.0 (ADR-010)
- Mac/Linux installer — v0.12.0
- Backup/export story — v0.11.0
- Recovery playbook — v0.11.0

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

244 tests covering: constitution loader, policy service, review service, vault read/write, state layer, state extractor, project boost, index caching, memory type enforcement, health check, ingest upload, cloud provider dispatch, provider API key management.
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
