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
9. **Do not use the word "shape" in any output** — code comments, prompts, ADRs, prose, or conversation. Use a more precise alternative.

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
Added v0.14.0: `lodestone` (user values, ADR-017), `deviation` (behavioral pattern deviations, ADR-013)

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

## Current State (v0.15.3)

All items from the original TDD §25 build order are complete through step 7. The system is feature-complete for single-user local deployment on Windows, Mac, and Linux. Cloud reasoning is available via Anthropic Claude with full UI support. v0.13.x shipped embedding upgrade, memory tiering, nature layer, grounding verification, and XML context restructuring. v0.14.0 adds Lodestone layer, deviation engine, and context packet reorder. v0.15.x shipped web search broadening, temporal decay, constitutional review overhaul, knowledge gap suppression, vault citation signals, and multiple bug fixes.

**Core Systems:**
- Append-only JSON vault with typed memory enforcement (`VALID_MEMORY_TYPES`, 19 types)
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
- State layer (StateService, StateResolver, 9 categories including timer, context packet integration)
- Daily and weekly reflection generation (multi-source, junk-filtered, suppression tools)
- Constitutional review (9 principles, constitution v0.7, streaming-compatible — includes relational_honesty v0.5 and flourishing_over_preference v0.2, MVR prompt optimization)
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
- `scripts/cleanup_test_artifacts.py` — scan vault for test/eval artifacts, dry-run by default, --confirm to archive
- Web search accuracy eval (`tools/eval_web_search.py`) — 30 questions, 5 categories, latency tracking, citation detection

**v0.14.2 additions:**
- Constitutional review optimization — MVR (Minimum Viable Review) prompt with three fixed criteria (POSITION_COLLAPSE, SYCOPHANCY, EMBELLISHMENT), trigger-signal-to-principle append for non-MVR principles
- Constitution v0.7 — flourishing_over_preference rewritten with four-condition fire gate, default-to-silence, stated-values-only constraint
- Think block orphaned-tag stripping (pass 2: orphaned close, pass 3: orphaned open)
- Knowledge gap suppression across all three injection paths (AUTHORITY_RULES, vault_memory empty-state, openai_adapter prefix) with curly apostrophe normalization
- BUG-008 fix: closing_questions identity rule strengthened, post-generation parenthetical filter, session-sticky question suppression
- BUG-009 fix: topic decline state resolution, retrieval suppression via keyword matching, session-sticky decline notes
- PIN change endpoint (POST /v1/security/pin/change) — verify current PIN before rotation, rate-limited, no recovery coupling
- Disk encryption status endpoint (GET /v1/system/disk-encryption) — BitLocker/FileVault/LUKS detection
- Service health/restart/developer status endpoints (docker field in /api/health, POST /v1/service/{name}/restart, GET /v1/developer/status, GET /v1/developer/vaults)
- Runtime vault swap (POST /v1/developer/vault/swap) — dev-mode gated, in-memory override, clears vector indexes
- Claude Code hooks (.claude/hooks/) — vault guard, auto-test on .py edit, retrieval eval on context/retrieval/llm commits
- DEVEmberVault structure — demo and test vaults with synthetic seed data

**v0.15.x additions:**
- Web search trigger broadening — temporal currency markers, factual uncertainty markers, entity-type triggers (Layer 1 regex), implicit recency and episodic domain triggers, AI system documentation quarantine from web results
- Web search ask-first interaction mode — Ember says "I don't have enough on this — want me to search?" when she identifies a gap; web_search_autonomous preference field (default False) for opt-in autonomous mode
- Multiplicative temporal decay weighting in ContextRanker — older records receive graduated penalties
- Vault citation signal — X-Ember-Vault-Used response header and vault_sources SSE event (partially shipped — indicator works, citation quality fixes still in progress)
- Retrieval confidence metadata injection for hallucination reduction
- Knowledge gap suppression strengthened — anti-embellishment rule for personal queries, self-knowledge boundary rule, anti-disclaimer rule
- BUG-010 fix: ThinkBlockFilter dual-buffer architecture preserving original casing
- Think block stripping — full pipeline (strip, orphaned close tags, orphaned open tags), unicode italic and case variant handling
- Contrastive few-shot examples for preference expression in identity rules
- Relational intensity amplification gate — suppresses lodestone relational records during relational trigger activation
- Embedding batching — 3 Ollama embedding calls reduced to 1 per query
- Cross-platform watchdog for API restart and stop
- Streaming SSE regression test added to release gate (Tier 3)
- Launch-installer endpoint, version/release triggers in prompt
- Eval improvements — --compare flag with Haiku as external evaluator, auto-cleanup after runs, test vault isolation, web search eval rework with latency tracking

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
- ember-2 backend: 1260 pytest tests passing
- ember-2-ui: 64 Playwright e2e tests passing (includes BUG-001 regression test)
- ember-2-installer: 48 Playwright e2e tests passing (v0.5.9)

---

## Immediate Next Priorities

**v0.14.0 — Identity Foundation** ✓ (shipped 2026-04-06)
**v0.14.1 — Patch** ✓ (shipped 2026-04-09)
**v0.15.0 — Quality of Life Improvements** ✓ (shipped v0.15.0–v0.15.3)

**Shipped in v0.15.x:**
- ~~Constitutional review optimization~~ ✓ — MVR prompt, trigger-signal append, constitution v0.7
- ~~Web search interaction mode~~ ✓ — ask-first pattern shipped, autonomous toggle via preferences API
- ~~Web search trigger broadening~~ ✓ — temporal currency, factual uncertainty, entity-type triggers (Layer 1)
- ~~Hallucination reduction~~ ✓ — knowledge gap suppression, anti-embellishment rule, retrieval confidence metadata, self-knowledge boundary
- ~~Source citation on vault-retrieved content~~ — partially shipped: vault citation signal (header + SSE event) works; citation quality fixes still in progress
- ~~BUG-010 fix~~ ✓ — ThinkBlockFilter casing
- Vault encryption at rest — DEFERRED (delegated to OS disk encryption; GET /v1/system/disk-encryption added for BitLocker/FileVault/LUKS detection)
- API as a service — not yet started (v0.16.0 candidate)
- Quality of life testing — not yet started
- Connectors removed from near-term roadmap indefinitely

**v0.16.0 — Health + Agent Orchestration:**
- Fitbit/Apple Health/Garmin export ingestion (ADR-024)
- Self-evaluation and decision-memory loops
- OpenJarvis Learning primitive as reference implementation
- Controlled tool writes with stricter policy gates
- Trace-driven learning
- Relational orientation layer (see docs/research/relational-orientation.md)

**Post-v0.16.0:**
- Multi-user vault isolation
- Windows/Mac/Linux full parity

Research tracking has moved to docs/Ember2_TDD.md. TDD is the source of truth for all watch items, research notes, and known gaps.

## Known Issues
- Installer Node.js prerequisite check exists but a user bypassed it somehow — needs investigation (Node IS in the prereqs screen, Next is disabled when missing)
- State awareness hallucinations — model embellishes when state records are noisy or stale; partially addressed by STATE_STALENESS_DAYS filter; longitudinal monitoring needed
- Preference expression partial deflection — identity rules reduced "I'm an AI" deflection but did not eliminate it; model capability ceiling on qwen3:8b for some identity questions
- The API must be restarted after any backend code changes for them to take effect. Changes to task detection, prompt building, or any src/ file do not hot-reload in production mode. Run `./start_api.bat` or kill and restart uvicorn after deploying changes.
- Clean install testing is a known gap due to hardware constraints (documented in runbook).
- Mac/Linux installer not yet tested on real hardware.
- Constitutional review service context blindness — ResponseReviewService receives only user_message and draft_response at review time. No vault memory, no context packet, no conversation history. The reviewer cannot distinguish a hallucinated claim from a vault-grounded one, and cannot assess whether draft confidence is warranted by retrieved evidence. Architectural gap — requires passing ContextPacket into SafetyReviewContext.
- Relational intensity amplification gate — relational_honesty, flourishing_over_preference, and the lodestone relational category can all activate in the same conversation. The compounding risk is addressed by a retrieval-side gate in src/llm/prompt_builder.py that suppresses lodestone records with taxonomy_category="relational" when relational_hedging or preference_compliance triggers fire. Implemented; no longer a documented risk in constitution.yaml as of v0.7.
- flourishing_over_preference v0.2 (constitution v0.7) — the principle uses a four-condition fire gate (stated value, clear conflict, not already named in session, agency intact), defaults to silence under uncertainty, and only fires against stated values rather than inferred ones. Cross-session pattern detection is still out of scope because the review service has no vault memory access (see "Constitutional review service context blindness" above) — if that architectural gap is closed, the fire conditions may need to expand to include cross-session observation.
- Vault-retrieved content has no uncertainty signal — Ember presents vault-grounded claims with the same confidence as directly stated facts. When retrieval returns low-scoring or old records, the response should surface uncertainty ("based on what I have from a few weeks ago...") rather than presenting stale or weakly-matched content as certain. Currently only web search responses show source attribution.
- Knowledge gap fabrication — partially addressed in v0.15.x via knowledge gap suppression across all three injection paths, anti-embellishment rule, and retrieval confidence metadata. Remaining gap: vault-retrieved content still presents with uniform confidence regardless of match quality or age.
- Web search triggers broadened in v0.15.x — temporal currency markers, factual uncertainty markers, entity-type triggers (Layer 1). Layer 2 pre-classifier remains a research item if Layer 1 coverage proves insufficient.
- API requires manual start — non-developer users must run start_api.bat or launch_ember.sh manually. No auto-start mechanism (Windows startup task, Linux systemd unit, macOS launchd plist) exists. Deferred to v0.16.0 via installer.
- eval_conversations.py unconditionally writes full Ember response text to logs/eval_conversations/latest.json — violates vault privacy rule. Fix before next use.
- BUG-008: Repetitive parenthetical questions — FIXED v0.14.2. Three-part fix: closing_questions identity rule strengthened with explicit persistence clause and negative parenthetical example; post-generation `strip_trailing_parenthetical_question` filter when question_suppressed flag active; session-sticky "[System: user has requested no questions]" note in conversation buffer.
- BUG-009: Topic fixation — FIXED v0.14.2. Three-part fix: `resolve_open_loops_by_topic` in StateService writes resolution records on user decline; retrieval suppression via keyword matching in `_build_context_section`; session-sticky decline notes in conversation buffer.
- New user calibration gap — users unfamiliar with Ember's principled nature may experience her holding positions or naming patterns as the system being difficult. Relational orientation layer (v0.16.0) should account for onboarding period before full constitutional behavior activates.
- BUG-010: Inconsistent capitalization — FIXED v0.15.0. Root cause was ThinkBlockFilter lowercasing the entire response stream via _normalize(). Fixed with dual-buffer architecture preserving original casing.
- Vision model pipeline bypass — llama3.2-vision:11b bypasses the full prompt construction and constitutional review pipeline. Image analysis responses don't go through context assembly, identity rules, nature injection, or constitutional review. The vision path in LLMAdapter sends the image directly to Ollama with only the base system prompt, skipping the cognitive layer entirely. Scheduled for v0.16.0 — wire vision model responses through the same pipeline as text responses.

---

## Test Tiers

| Tier | Trigger | What runs | Time | Cost |
|---|---|---|---|---|
| **Tier 1: Fast** | Every src/*.py edit (post-edit hook) | `pytest tests/ -q` | ~80s | Free |
| **Tier 2: Retrieval** | Commits touching src/context/, src/retrieval/, src/llm/ (post-commit hook) | `eval_retrieval.py` | ~30s | Free |
| **Tier 3: Release gate** | Before every release | Tier 1 + `test_streaming_regression.py` + `eval_manual.py --auto` + `eval_web_search.py` | ~30min | Free |
| **Tier 4: Cloud eval** | Before minor/major releases only | `eval_conversations.py` + `eval_manual.py --compare` | ~15min | ~$1-2 |

Tier 4 uses Claude API credits. Do not run Tier 4 on patches or during development iteration — reserve for release gates only.

---

## Dependency Security

Ember-2 uses native fetch for all HTTP requests in the frontend and installer — no axios dependency. This was a protective decision confirmed during the March 31, 2026 axios npm supply chain attack (compromised versions 1.14.1 and 0.30.4 contained a RAT; Ember was not affected).

When adding new dependencies (npm or pip):
- All new dependencies must be reviewed before addition — no auto-approvals
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

779 tests covering: constitution loader, policy service (including relational_hedging and preference_compliance triggers), review service, vault read/write, state layer, state extractor, state staleness, timer service, project boost, index caching, memory type enforcement, health check, ingest upload, cloud provider dispatch, provider API key management, task layer, commitment detection, session reflection, PIN/passphrase service, soft-delete filtering, temporal awareness, nature loader, identity rules loader, type gating, memory tiering, SQLite migration, grounding check, JSON import, SSE events, model filter, monthly reflection, index.html cache, manual eval (multi-annotation), lodestone loader, lodestone service, lodestone resolver, lodestone API, deviation detector, deviation API.
Tests do not mock the filesystem vault (real path via `PRIVATE_VAULT_PATH`). Integration tests hit real storage.

When adding features: unit test normalizers, filters, ranking functions, and state resolution. Integration test full pipeline paths.

Eval regressions require running the eval twice before concluding a 3+ point drop is real. A single run is not sufficient to call a regression.

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

## Manager Prompt Format

Prompts from manager follow compact format:
- No rationale or preamble
- File paths, not file descriptions
- Spec the output, not the journey
- Explicit "do not touch X" constraints included when needed
- Commit message included in every prompt — copy verbatim
- "Read CLAUDE.md first" always present

## Conventional Commits (Required)

All three Ember-2 repos use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) and release-please for automated release PRs.

**Format:** `type(scope): description`

**Types:**
- `feat` — new feature (bumps minor)
- `fix` — bug fix (bumps patch)
- `chore` — maintenance, version bumps, config changes
- `docs` — documentation only
- `refactor` — code change that neither fixes a bug nor adds a feature
- `test` — adding or updating tests
- `ci` — CI/CD changes

**Breaking changes:** append `!` after the type — e.g., `feat!: redesign context packet`. This bumps major (or minor while pre-1.0).

**Scope** is optional but encouraged — e.g., `feat(retrieval): ...`, `fix(state): ...`

**Examples:**
```
feat(retrieval): add nomic-embed-text embedding upgrade
fix(state): check resolved flag in _latest_per_category
chore: bump version to v0.13.2
docs: add conventional commit guide to CLAUDE.md
```

release-please reads these commit messages to auto-generate changelogs and determine version bumps. The release PR is created automatically but requires human approval before merging.

## Vault Privacy Rule

Vault contents — including names, conversation text, and record IDs — must never appear in code, tests, commits, scripts, or docs. This rule has no exceptions. If a test requires memory data, use synthetic fixture data only.

Ember's generated responses that draw on vault content are also vault content. Response text must never be saved to files in the repo or logs directory. The --auto battery mode shows responses on stdout for live review but writes only metadata (latency, word count) to disk.

Do not run eval_manual.py (interactive or --auto) through Claude Code without warning the user first. Responses appear in the tool output and enter the session log. If the user wants to run the manual eval, suggest they run it in a separate terminal outside of Claude Code so vault-grounded responses never enter the session context.

---

## Documentation Language Convention

Public-facing documentation, ADRs, code comments, and test fixtures use generic, non-personal language. Reference "the user," "the developer," or "a user" rather than specific individuals. Personal details belong in the vault, not in the codebase. This applies to all three repos.

Documentation can have personality and even humor -- it just should not contain personal identifying information about specific people. Exception: About.jsx creator attribution is intentional and at the author's discretion.

**Vault contents must never appear in the codebase.** Referring to "the vault" as a generic concept is fine — every Ember-2 user has one. What is never acceptable: real proper names (people, pets, places) from a user's vault, verbatim or paraphrased conversation text, specific record IDs, session IDs, or any other content that originated inside a user's `private_vault/`. This applies to:
- Source code, comments, and docstrings
- Test files, fixtures, and mocks (use generic identifiers like `user`, `assistant`, `sess_test_001`)
- Commit messages and PR descriptions
- ADRs and other docs
- Helper or debug scripts checked into the repo

When debugging real vault data is necessary, do it in an interactive shell session or in scratch files outside the repo — never write a helper file inside the working tree that reads or echoes vault contents. Before committing after any vault inspection, scan the diff for proper names, vault text, and record IDs.

## Prompt Writing Standards

When writing prompts for Ember's inference-time tasks (reflection, review, synthesis, detection), follow these standards. Derived from research. Not preference.

**Register**
- Do not use the word "reflection" as a task frame -- it activates therapeutic register. Use "synthesis," "analysis," or "observation."
- Explicitly prohibit therapeutic language in the prompt: no affirmations, no growth/challenge framing, no validating emotional states, no closing questions.
- Target register is "accurate observer," not "coach or therapist." Frame it that way explicitly in the prompt.
- Do not inject aesthetic or literary language ("shape," "texture," "landscape," "journey") -- write functionally.

**Synthesis vs. Summary**
- A summary prompt asks "what happened." A synthesis prompt asks "what recurred," "what shifted," "what tension is visible."
- Name the synthesis tasks explicitly. Small models default to summarization under ambiguity.
- Explicitly prohibit summary behavior: "Do not narrate what happened."

**Multi-domain prompts**
- Tag input records by domain before passing to the prompt.
- Explicitly instruct equal domain weighting: "Do not weight any domain by volume."
- Ask for cross-domain observations explicitly -- this is the most important instruction for multi-domain synthesis.

**Temporal framing**
- Randomize or reverse input record order to counteract recency bias (documented across all 8B-class models).
- Include explicit temporal weighting instruction: "Weight events by significance, not by how recently they occurred."
- Require temporal spread: "Each pattern identified should note when during the month it first appeared."

**Person of voice**
- Third person for synthesis narrative.
- Second person only for final forward-facing sentences.
- First person is never correct for Ember-generated synthesis -- it creates attribution confusion.

**Format**
- Specify length explicitly. Without a constraint, 8B models pad. 400-500 words for monthly synthesis.
- Flowing prose outperforms structured sections for meaning-making tasks. No headers, no bullet points unless the task is explicitly a list.
- For qwen3:8b: prepend "Think step by step before writing. First identify the patterns. Then write the synthesis."

## Release Checklist

**Critical principle: CC runs the full release process. Nothing is "done" until it is publicly downloadable. Never assume the human is cutting the release unless they explicitly say so.**

A release is not complete at commit. A release is not complete at tag. A release is complete when:
- The GitHub Release is published (not draft)
- Artifacts are attached (installer .exe / source)
- latest.yml is present in release assets (installer only)
- The release is visible and downloadable at the GitHub Releases URL
- CC has verified the above and reported the URL

### Pre-release (run before every release)

**ember-2 (backend):**
- [ ] All tests passing: pytest tests/
- [ ] Streaming SSE regression test passing: pytest tests/test_streaming_regression.py -v
- [ ] Retrieval eval passing: python tools/eval_retrieval.py -- no regression
- [ ] Web search eval run: python tools/eval_web_search.py --auto-search -- document trigger rate
- [ ] Conversation eval run: python tools/eval_conversations.py -- document results (Tier 4, minor/major only)
- [ ] CHANGELOG.md updated (release-please handles this via conventional commits)
- [ ] version.json bumped (release-please handles this via conventional commits)
- [ ] All changes committed and pushed to main: git push origin main
- [ ] Constitution, nature, and Lodestone layers reviewed for coherence
- [ ] Deviation drift check — review deviation record distribution against nature document; verify accumulated character is consistent with intended character
- [ ] Research review: any watch items ready to graduate to roadmap?

**ember-2-ui (frontend):**
- [ ] All Playwright tests passing: npm run test:e2e
- [ ] CHANGELOG.md updated
- [ ] package.json version bumped
- [ ] All changes committed and pushed to main: git push origin main
- [ ] UI rebuilt from correct source: npm ci && npm run build

**ember-2-installer (installer):**
- [ ] All Playwright tests passing
- [ ] CHANGELOG.md updated
- [ ] package.json version bumped
- [ ] All changes committed and pushed to main: git push origin main
- [ ] Frontend freshly built from pinned ember-2-ui tag before packaging
- [ ] Backend version pinned and documented in release notes
- [ ] Installer built: npm run dist
- [ ] app-update.yml present in dist/win-unpacked/resources/ -- verify before publishing
- [ ] latest.yml will be attached to release by electron-builder -- verify after publishing

### Release (CC runs this, not the human)

- [ ] Git tag created: git tag vX.X.X
- [ ] Tag pushed: git push origin vX.X.X
- [ ] GitHub Release created (NOT draft): gh release create vX.X.X --title "vX.X.X" --notes "..." --latest
- [ ] Artifacts attached to release (installer .exe for yellow, source zip for green)
- [ ] Release verified as published and visible: gh release view vX.X.X
- [ ] Release URL reported to human: https://github.com/niansahc/[repo]/releases/tag/vX.X.X

### Post-release verification (CC runs this)

- [ ] Confirm release appears at https://github.com/niansahc/[repo]/releases
- [ ] Confirm latest.yml is present in release assets (installer only)
- [ ] Confirm version matches package.json / version.json
- [ ] Sync project knowledge files to Claude project after every release (Manager Claude depends on this for architecture sessions)
- [ ] TDD version bump at every release (current: 1.2; bump minor for feature releases, patch for hotfixes)
- [ ] Report to human: "Release vX.X.X is live at [URL]. Users can download/update now."

### Context layer change gates

- [ ] Context packet token estimate validated before shipping any context layer changes — must stay under 4,000-6,000 tokens at average turn
- [ ] Run retrieval eval before AND after any context packet order changes — confirm no regression before ship

### Patch releases

Patch releases follow the same checklist. There are no shortcuts for patches. A patch that is committed but not published is not a patch -- it is unpublished work. Every patch must complete the full release process before being called done.

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

---

## Testing Discipline

When a flaky or condition-dependent test is identified during a release cycle, it must be fixed or marked skip-with-condition before that release ships. Flaky tests do not carry forward to the next release.

---

## UI Design Gates

**Internal fields never reach the user directly.**
Database field names, taxonomy category keys, acquisition path values, source tags, and any other internal classification terms must never be rendered as user-facing labels. All user-facing display strings must be explicitly defined before UI implementation begins.

**Taxonomy display names must be approved before UI work starts.**
If a feature introduces taxonomy categories, types, or classifications, the user-facing display name and description for each must be documented and approved in the ADR or feature spec before M writes any UI code. Internal key names (e.g. "ground", "character", "onboarding") are not display names.

**UI must be built against clean data.**
Do not build or review UI against broken, partial, or pre-inference data. If the backend is not ready, M mocks clean data to spec. Real data is only connected after it meets the expected schema.

**Design before implementation.**
For any panel or view that surfaces user data, the information architecture (what the user sees, in what order, what each element means) must be described and approved in this chat before M builds it. Prompts that skip this step will be sent back.

---

## Claude Code Efficiency Rules

**Parallel subagents — use them.**
Any task touching 3+ independent files or with clearly separable subtasks must use parallel subagents. Do not work sequentially when work can be fanned out. Spawn subagents, merge results.

**Hooks — always active:**
- Auto-run tests after any code edit (pytest for G, npm run test:e2e for M and Y)
- Auto-reject any changes to private_vault/ or .env files

**Scheduled tasks:**
- Weekly dependency audit — flag outdated or vulnerable packages in requirements.txt / package.json
- Pre-release cross-repo consistency check — verify UI matches backend API responses before any release

**Session naming:**
- Always name sessions descriptively, e.g. `claude -n "vault-citation-backend"`
- Enables resumption with full context.
- Use TodoWrite and TodoRead tools to maintain a visible task list for every multi-step task. Update it as work completes.

---

## Pre-Implementation Validation

Before modifying any of the following, read all relevant existing files and report conflicts before writing any code:

- Prompt templates or instruction rules
- Constitutional rules (constitution.yaml)
- Retrieval or ranking logic
- Context packet structure or order
- Safety trigger patterns

Report format: list each relevant file read, note any conflicts or dependencies found, confirm clear before proceeding. If conflicts exist, stop and report — do not resolve them unilaterally.

This step is mandatory. Do not skip it for small changes.

---

## Hooks

Hook scripts live in `.claude/hooks/` and are configured in `.claude/settings.json` (committed, project-level). Machine-local permissions remain in `.claude/settings.local.json` (not committed).

**PreToolUse: Vault Protection** — `.claude/hooks/vault_guard.py`
Matcher: `Edit|Write`. Blocks any attempted edit to files containing `private_vault/` in the path or ending in `.env`. Returns a deny decision with a clear error message referencing the Vault Privacy Rule. Runs before the edit is applied — the file is never modified.

**PostToolUse: Auto Test** — `.claude/hooks/post_edit_test.py`
Matcher: `Edit|Write`. Runs `pytest tests/ --tb=line -q` after any Python file edit. Non-Python edits are skipped silently. Prints a concise summary (last 3 lines of pytest output). Exit code is always 0 — failing tests are reported but do not block the next edit. Timeout: 180s.

**PostToolUse: Retrieval Eval** — `.claude/hooks/post_commit_eval.py`
Matcher: `Bash`. Fires only when the Bash command contains `git commit`. Checks `git diff HEAD~1 --name-only` for files in `src/context/`, `src/retrieval/`, or `src/llm/`. If any match, runs `python tools/eval_retrieval.py` and prints the summary. Silent no-op for commits that don't touch those paths. Timeout: 180s.

Review or disable hooks via `/hooks` in Claude Code.

---

## Release Process

### Gates — mandatory before any release or patch is cut

**Documentation gate (all three repos):**
- [ ] CLAUDE.md version and test count current
- [ ] TDD updated to reflect what shipped (G only)
- [ ] README reflects current features
- [ ] CHANGELOG.md current (release-please handles via commits)

**Quality gate:**
- [ ] All tests passing
- [ ] Retrieval eval passing with no regression (G only)
- [ ] No flaky tests carried forward

**Coordination gate:**
- [ ] All three repos confirm docs and tests green
- [ ] Human approves before any tag is created
- [ ] GitHub Release not created until human says go

### Sequence

1. G, M, Y each complete documentation and quality gates
2. Each reports green to manager
3. Manager confirms all three green and gets human approval
4. G coordinates the release — tags all three repos, creates GitHub Releases
5. Y attaches installer artifacts (.exe, latest.yml)
6. G verifies all three releases are publicly visible
7. G reports release URLs — release is not done until this step

### Y independent releases

Y may cut an installer-only release when:
- Changes are installer-specific only (no backend or UI updates)
- Human explicitly approves
- Y completes documentation and quality gates independently
- Y tags, creates GitHub Release, attaches artifacts, and reports URL

Y does NOT cut independent releases when backend or UI changes are involved — coordinate with G.

### release-please

All three repos use release-please for automated release PRs. Conventional commits are required. Release PRs require human approval before merging.
