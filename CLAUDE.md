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
State Layer          — StateService, StateResolver, StateExtractor, auto-extraction
                       from conversation turns, manual write via POST /write-state (src/state/)
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
Planned (v0.15.0): `deviation` -- chosen behavioral deviations from trained patterns (ADR-013)

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

## Current State (v0.12.0)

All items from the original TDD §25 build order are complete through step 6. The system is feature-complete for single-user local deployment on Windows, Mac, and Linux. Cloud reasoning is available via Anthropic Claude with full UI support. Task layer, session reflection, commitment detection, and PIN lock shipped in v0.12.0.

**Core Systems:**
- Append-only JSON vault with typed memory enforcement (`VALID_MEMORY_TYPES`, 17 types)
- Ingestion pipeline (ChatGPT, PDF, DOCX, CSV, TXT, GDrive, POST /ingest/upload multipart)
- Semantic retrieval via vector indexes (cached in memory, no disk load per query)
- Context assembly with policy-weighted ranking, diversity selection, project-scoped boost (ADR-007)
- SSE streaming responses from Ollama or Anthropic through FastAPI
- Cloud model provider support — Anthropic Claude (Haiku 4.5, Sonnet 4.6) and OpenAI (gpt-4o-mini, gpt-4o, gpt-4-turbo, gpt-3.5-turbo) via LLMAdapter
- Provider API key storage via keyring with env var fallback (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`)
- Provider dispatch by model name prefix (`claude-` → Anthropic, `gpt-` → OpenAI, else → Ollama)
- Social engineering safety triggers — 5 attack families, 39 patterns (ADR-010)
- DELETE /provider-key/{provider} endpoint for key removal
- Model selection persists via vault/model_override.json (survives API restarts)
- Auto state extraction from conversation turns (StateExtractor, background thread, threshold: 10 words)
- State layer (StateService, StateResolver, 8 categories, context packet integration)
- Daily and weekly reflection generation (multi-source, junk-filtered, suppression tools)
- Constitutional review (8 principles including authentic_expression, streaming-compatible)
- Conversation sessions (session_id, project_id, rename, soft-delete, auto-title)
- Projects backend (CRUD, conversation assignment, project-scoped retrieval)
- Self-echo prevention (role-labeled context, metadata-aware scoring, -0.25 assistant penalty)
- Temporal grounding (current date injected, timestamps on context items)
- Profile retrieval via semantic search (identity queries for both user and Ember-directed)
- Prompt label: "person Ember is talking to" (prevents identity confusion)
- Buffer compression backgrounded (no longer blocks response)
- Conversation turns never filtered by length (short messages like "Yes" are saved)
- Test session flag (X-Test-Session header, filtered from listings, cleanup script)
- Default model: qwen3:8b (4.9-6.7/10 eval range, best local model tested)
- Ember uses she/her pronouns (system prompt)
- Version field in /api/health endpoint (reads from version.json)
- Multi-record state categories for open_loop and next_action (ADR-011, capped at 5)
- Commitment detection (ADR-014) — post-generation, writes open_loop state records
- Session reflection (ADR-009) — narrative end-of-session capture, auto-triggers on delete
- Task layer MVP — TaskService, TaskResolver, task detector, dual creation paths, context injection
- Task API endpoints (POST/GET/PATCH/GET-by-id /v1/tasks)
- Temporal awareness — staleness penalties, age labels, hedging rules for old memories
- User preferences store (private_vault/preferences.json, GET/PATCH /v1/preferences)
- Conversational style (casual/balanced/thoughtful) via preferences API
- Web search signal (X-Ember-Web-Search response header)

**Evaluation & Tooling:**
- Retrieval evaluation (15 benchmark cases, pass/warn/fail scoring, latency tracking)
- Conversation quality eval with Claude as external evaluator (18 test cases, 6 categories)
- Local model comparison eval (automated, all installed models, comparison table) — `tools/eval_local_models.py`
- Cloud model eval: Claude Haiku 4.5 scored 8.7/10, Claude Sonnet 4.6 scored 8.5/10
- Eval history: `docs/eval_history.md` (all models, full category breakdowns, variance documentation)
- Model selection guide: `docs/model_guide.md` (linked from installer model selection screen)
- Vault health audit (7 checks, GREEN/YELLOW/RED health score, --fix flag)
- Reflection quality audit and suppression tools
- `scripts/set_provider_key.py` CLI (--provider, --check, --remove)
- `scripts/cleanup_test_sessions.py` for soft-deleting eval conversations

**Security:**
- API key auth via OS credential store (Windows Credential Manager, macOS Keychain, Linux SecretService)
- Auth only on API routes — UI static files are public
- Rate limiting, path traversal protection, JSON audit logging
- SearXNG bound to localhost only
- Tailscale serve uses localhost binding
- Vault path masked in UI with timed reveal (ADR-012 Phase 1)
- Cloud API keys stored in OS credential store, never displayed in UI
- PIN/passphrase lock (ADR-012 Phase 2) — bcrypt, keyring, rate limiting, idle timeout, recovery
- Dependency security policy — native fetch, no axios; documented after March 2026 supply chain attack

**UI (ember-2-ui, served from ui/):**
- Streaming chat with markdown, copy, edit/resend, regenerate, scroll-to-bottom, export
- Collapsible sidebar with icon row (new conversation, search, collapse) and localStorage persistence
- Model indicator in top bar (muted dot local, pulsing accent dot cloud, click opens settings)
- Local/Cloud model selector tabs in Settings with underline-style active indicator
- Secure API key entry in Cloud tab (masked password input, credential store disclosure, never displayed)
- Remove key with inline confirmation dialog
- Vault path masked by default, eye icon reveals for 10 seconds, copy without displaying
- Vision toggle defaults to on when vision model configured, persisted in localStorage
- Version reads from /api/health not hardcoded
- Sidebar with projects, conversations, search, right-click context menu, New Project button
- Settings: 5 themes, web search toggle, conversation memory toggle
- About panel with Ember's story, beliefs, ethos
- Bug reports to GitHub Issues, update checker
- PWA manifest for Android/iOS home screen installation
- Consistent Ember-2 branding, WCAG 2.1 AA accessible
- .txt file upload support with document context injection into chat message
- Multi-image upload — select and send multiple images in a single message
- Web search transparency indicator — magnifying glass icon on messages that used web search
- Conversational style selector — Casual/Balanced/Thoughtful card selector in settings
- Task sidebar tray — bottom-anchored, checkbox to complete, internal scroll, 30s polling
- Guided first-run tour (Shepherd.js, 6 steps, triggers once via preferences API)
- PIN/passphrase lock screen with idle timeout and recovery
- Restore active conversation on page refresh via localStorage
- Timestamp parsing fix — hyphenated vault timestamps no longer show Invalid Date
- Regenerate button on assistant messages

**Installer (ember-2-installer):**
- Auto-install prerequisites via winget on Windows (Git, Python, Node, Ollama, Docker)
- Mac/Linux support — platform-aware prereqs, paths, Homebrew soft check, Gatekeeper note
- Curated model cards with eval-based descriptions, disk sizes, RAM requirements
- Default model: qwen3:8b (was qwen2.5:14b)
- Model selection guide linked from model selection screen and Done screen
- Venv/API lock detection, auto-start API with health check polling
- Retry button on Done screen with warm troubleshooting hints
- Tailscale walkthrough, progress bar + fun facts, pip time warning
- Clones ember-2 repo, builds UI from ember-2-ui
- git pull uses origin main explicitly (no tracking info errors)
- Electron 33 (upgraded from 28.3.3, unblocks Playwright e2e tests)

**Tests:**
- ember-2 backend: 485 pytest tests passing
- ember-2-ui: 40 Playwright e2e tests passing, 3 skipped
- ember-2-installer: 12 Playwright e2e tests passing

---

## Immediate Next Priorities

**v0.13.0 — Memory Tiering + Embedding Upgrade:**
- nomic-embed-text embedding upgrade via Ollama — ship first; run retrieval eval before and after
- Hot/warm/cold memory tiering by recency and relevance (ADR-015) — tier assigned during reindex; nightly TieringService; cold excluded by default; append-only intact
- Index migration: remaining JSON indexes to SQLite — conversation, profile, reflection, journal; align with reindex pass
- Monthly/thematic reflection
- Generic CSV/JSON import — JSON importer is the delta (CSV exists)
- Custom theme with color picker
- Vault encryption at rest — DEFERRED to v0.14.0

**v0.14.0 — Offline Knowledge + Integrations + Encryption:**
- Vault encryption at rest (deferred from v0.13.0)
- Email read-only ingestion IMAP (deferred from v0.13.0)
- GitHub read-only ingestion (deferred from v0.13.0)
- Fitbit / Apple Health / Garmin export ingestion (deferred from v0.13.0)
- Kiwix ZIM ingestion adapter
- Project Gutenberg adapter
- NOMAD-compatible path

**v0.15.0 — Agent Orchestration:**
- Self-evaluation and decision-memory loops
- OpenJarvis Learning primitive as reference implementation
- Controlled tool writes with stricter policy gates
- Trace-driven learning — local interaction traces inform retrieval routing (informed by OpenJarvis, ADR-013)

**Post-v0.15.0:**
- Multi-user vault isolation
- Windows/Mac/Linux full parity across all features

## Watch Items
- OpenJarvis Learning primitive — reference for self-evaluation loops (active at v0.15.0)
- OpenClaw (github.com/openclaw/openclaw) — reference for SKILL.md integration format and proactive heartbeat pattern (Peter Steinberger, 2026)
- PAI TELOS pattern — evaluate against constitution + profile memory during onboarding work
- Multi-user vault isolation — post-v0.15.0 milestone
- Eval harness uses user's own vault — results are personal, not generic benchmarks
- "Memory in the Age of AI Agents" (arxiv.org/abs/2512.13564, Zhang et al., December 2025) — survey proposing factual/experiential/working memory taxonomy; relevant to Ember's memory class design and v0.13.0 tiering work

## Known Gaps (tracked)
- Vault encryption at rest — v0.13.0
- ~~Mac/Linux installer~~ — complete (v0.12.0, platform-aware prereqs, paths, and startup)

## Known Issues
- Installer Node.js prerequisite check exists but a user bypassed it somehow — needs investigation (Node IS in the prereqs screen, Next is disabled when missing)
- qwen3:8b hallucination pattern: generates news-sounding content without web search when context is poor. Model limitation, not a code bug. classify_query() web search triggers investigated and confirmed clean. Cloud models do not exhibit this. Documented in eval_history.md.
- The API must be restarted after any backend code changes for them to take effect. Changes to task detection, prompt building, or any src/ file do not hot-reload in production mode. Run `./start_api.bat` or kill and restart uvicorn after deploying changes.

---

## Dependency Security

Ember-2 uses native fetch for all HTTP requests in the frontend and installer — no axios dependency. This was a protective decision confirmed during the March 31, 2026 axios npm supply chain attack (compromised versions 1.14.1 and 0.30.4 contained a RAT; Ember was not affected).

When adding new npm dependencies:
- Prefer native browser/Node APIs over third-party packages where feasible
- Pin exact versions in package.json rather than using ^ or ~ ranges for production dependencies
- Check new packages against known vulnerability databases before adding
- Run `grep -r "plain-crypto-js" package-lock.json` after any npm install as a sanity check during active supply chain attack windows

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

485 tests covering: constitution loader, policy service, review service, vault read/write, state layer, state extractor, project boost, index caching, memory type enforcement, health check, ingest upload, cloud provider dispatch, provider API key management, task layer, commitment detection, session reflection, PIN/passphrase service, soft-delete filtering, temporal awareness.
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
- After making backend changes that affect the running API, restart it automatically — kill existing uvicorn process(es) and start a new one. Do not ask. The human should not have to manage API restarts during development.
- After making UI changes, rebuild (npm run build in ember-2-ui) and copy dist/ to ember-2/ui/ automatically. Do not ask.

## Documentation Language Convention

Public-facing documentation, ADRs, code comments, and test fixtures use generic, non-personal language. Reference "the user," "the developer," or "a user" rather than specific individuals. Personal details belong in the vault, not in the codebase. This applies to all three repos.

Documentation can have personality and even humor -- it just should not contain personal identifying information about specific people. Exception: About.jsx creator attribution is intentional and at the author's discretion.

## Release Checklist

Run this before every release. No exceptions. Each repository versions independently — check only the relevant section(s) for the release being cut.

---

### ember-2 (backend)
- [ ] All pytest tests passing (`pytest tests/`)
- [ ] Run retrieval eval (`python tools/eval_retrieval.py`) — no regression
- [ ] No uncommitted changes
- [ ] CHANGELOG.md updated
- [ ] version.json bumped
- [ ] TDD updated to reflect what shipped
- [ ] README updated if user-facing changes
- [ ] CLAUDE.md updated — priorities, working state, and any new conventions
- [ ] ADRs filed for any new architectural decisions
- [ ] ADRs included in this release updated from Proposed → Accepted
- [ ] Git tag created

### ember-2-ui (frontend)
- [ ] All Playwright tests passing (`npm run test:e2e`)
- [ ] Mobile tested
- [ ] No uncommitted changes
- [ ] CHANGELOG.md updated
- [ ] version bumped in package.json
- [ ] Git tag created

### ember-2-installer (installer)
- [ ] Installer builds cleanly
- [ ] Install flow tested end-to-end on Windows
- [ ] No uncommitted changes
- [ ] Git tag created

---

### Before opening next version (all repos)
- [ ] Handoff notes written
- [ ] Next version scope documented in TDD roadmap
- [ ] CLAUDE.md priorities updated to reflect next version
- [ ] Research review completed — scan Watch Items in TDD, check arxiv/GitHub/HuggingFace for new relevant work, update Watch Items with anything new before opening next version

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
