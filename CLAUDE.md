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

## Current State (v0.13.2 — v0.14.0 next)

All items from the original TDD §25 build order are complete through step 7. The system is feature-complete for single-user local deployment on Windows, Mac, and Linux. Cloud reasoning is available via Anthropic Claude with full UI support. v0.13.x shipped embedding upgrade, memory tiering, nature layer, grounding verification, and XML context restructuring. v0.14.0 adds Lodestone layer, deviation engine, and context packet reorder.

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
- ember-2 backend: 731 pytest tests passing
- ember-2-ui: 63 Playwright e2e tests passing, 4 skipped
- ember-2-installer: 48 Playwright e2e tests passing (v0.5.9)

---

## Immediate Next Priorities

**v0.14.0 — Identity Foundation:**
- Lodestone layer (ADR-017 revised — multi-path user values layer; replaces prior ADR-017 draft)
- Deviation engine (ADR-013 revised — pulled forward from v0.15.0; pattern detection design complete)
- Context packet reorder — retrieved memory to recency position (eval gate required before ship)
- Conversation history rolling summary compression at 1,500 token threshold
- Release Please + GitHub Actions automation (replaces manual release process across all three repos)
- Launcher script (launch_ember.bat / launch_ember.sh)

**v0.15.0 — Connectors + Vault Encryption:**
- Vault encryption at rest (five-layer envelope architecture — see TDD §38)
- ADR-020: Connector architecture (generic pattern before any connector built)
- Email IMAP ingestion connector (ADR-023)
- GitHub ingestion connector (ADR-022 — CLAUDE.md as first-class document, elevated retrieval priority)
- Calendar connector
- Notes ingestion (Obsidian/Notion export)
- Relational orientation layer (supersedes old ADR-017 concept — see docs/research/relational-orientation.md)

**v0.16.0 — Health + Agent Orchestration:**
- Fitbit/Apple Health/Garmin export ingestion (ADR-024)
- Self-evaluation and decision-memory loops
- OpenJarvis Learning primitive as reference implementation
- Controlled tool writes with stricter policy gates
- Trace-driven learning

**Post-v0.16.0:**
- Multi-user vault isolation
- Windows/Mac/Linux full parity

## Watch Items

**Active (research, not build):**
- OpenJarvis Learning primitive (github.com/open-jarvis/OpenJarvis) -- reference for self-evaluation loops; active at v0.15.0
- Supermemory dual-layer timestamping (2025) -- temporal reasoning SOTA; update/extends/derives tagging pattern; design implication for weekly reflection; post-v0.13.0
- Memory-T1 (arXiv December 2025) -- RL-based temporal retrieval; watch for future temporal reasoning work
- Kirk et al. socioaffective alignment (AIES 2025) -- requirements basis for ADR-017 Lodestone; active at v0.15.0
- nomic-embed-text-v2-moe -- next generation embedding model; evaluate when available on Ollama
- qwen3.5:9b with /no_think flag -- timed out at 120s in eval due to thinking mode overhead; worth retesting on faster hardware or with thinking disabled
- Letta/MemGPT core memory pattern -- informed ADR-016 amendment (nature block as pinned core memory, conversation compression); not yet implemented as full pattern; active at v0.15.0
- Grounding verification effectiveness -- ADR-019 shipped v0.13.0; longitudinal data needed to confirm real-world improvement; track via eval_history.md across versions
- Identity rules effectiveness -- partial improvement in manual battery post-architecture; preference expression still partially deflecting; watch across more conversations before concluding model capability ceiling
- State staleness threshold -- STATE_STALENESS_DAYS=7 implemented v0.13.0; Supermemory temporal tagging is the deeper architectural fix; monitor whether threshold is sufficient in practice
- llama3.1:8b -- tested 5.4/10 automated eval, below competitive threshold; revisit with faster hardware or after architecture changes mature further
- Sketch-of-Thought (Aytes et al., arXiv:2503.05179, EMNLP 2025) -- prompting framework, 70%+ token reduction on reasoning calls, no model changes required; Expert Lexicons paradigm candidate for constitutional review second-pass; prototype as prompt engineering experiment with eval gate; target v0.14.1; do not integrate as framework

**Graduated (researched and implemented):**
- ~~nomic-embed-text~~ -- shipped v0.13.0
- ~~CIMemories (ICLR 2026)~~ -- researched, implemented as ADR-018 intent-aware type gating
- ~~MemX low-confidence rejection~~ -- researched, implemented as min_score floor in ADR-018
- ~~Contextual integrity as retrieval policy~~ -- researched, implemented as ADR-018
- ~~PAI TELOS pattern~~ -- evaluated, diverged into Lodestone (ADR-017); different design
- ~~PRISM / PERSIST persona stability~~ -- researched, informed ADR-016 amendment (nature reminder injection, conversation summarization)

## Known Gaps (tracked)
- Vault encryption at rest — deferred to v0.14.0, architecture decided (five-layer envelope encryption, documented in GOVERNANCE.md)
- ~~Mac/Linux installer~~ — complete (v0.12.0)
- Tier 2 and Tier 3 evaluation — no standard methodology for periodic manual behavioral evaluation or longitudinal behavioral marker tracking in personal AI systems; open design problem; see TDD §44
- Conversation summarization Ollama call — adds latency at turn 8+; monitor whether noticeable in practice; may need optimization
- Template response collapse — qwen3:8b returns near-identical responses to semantically distinct emotional inputs; partial mitigation via identity rules and specificity forcing; model capability ceiling for some patterns; revisit with model swap
- State record expiry UX — STATE_STALENESS_DAYS=7 filters old records from retrieval but no UI for users to review and resolve stale state records; users cannot easily see what state Ember holds
- General knowledge routing — relevance gate (RETRIEVAL_MIN_RAW_SCORE=0.5) handles the case but a dedicated general_knowledge intent class would be more explicit; deferred
- Eval test leakage into state — StateExtractor extracted eval test questions as real state records; X-Test-Session suppression exists but contaminated records already in vault required manual cleanup; consider a vault cleanup tool
- Relational overlap across layers — constitution (relational_honesty), nature (relational presence), and lodestone (Relational category) all touch relational behavior from different angles; decision made 2026-04-05; not a release blocker; evaluate before any v0.15.0 relational work begins to determine whether consolidation, explicit scoping, or intentional overlap is the right design

## Known Issues
- Installer Node.js prerequisite check exists but a user bypassed it somehow — needs investigation (Node IS in the prereqs screen, Next is disabled when missing)
- StateResolver._latest_per_category() does not check resolved flag -- a resolved single-record category record (current_focus, active_project, priority) can still surface as the active value if it's the newest record. Fix: add resolved flag check to _latest_per_category(). Post-release, not blocking v0.13.0.
- State awareness hallucinations — model embellishes when state records are noisy or stale; partially addressed by STATE_STALENESS_DAYS filter; longitudinal monitoring needed
- Preference expression partial deflection — identity rules reduced "I'm an AI" deflection but did not eliminate it; model capability ceiling on qwen3:8b for some identity questions
- The API must be restarted after any backend code changes for them to take effect. Changes to task detection, prompt building, or any src/ file do not hot-reload in production mode. Run `./start_api.bat` or kill and restart uvicorn after deploying changes.
- Context packet order discrepancy: ADR-016 amendment and prompt_builder.py describe different orders. Verify which is authoritative and reconcile before v0.14.0 context packet reorder work begins.
- Clean install testing is a known gap due to hardware constraints (documented in runbook).
- Mac/Linux installer not yet tested on real hardware.

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

731 tests covering: constitution loader, policy service, review service, vault read/write, state layer, state extractor, project boost, index caching, memory type enforcement, health check, ingest upload, cloud provider dispatch, provider API key management, task layer, commitment detection, session reflection, PIN/passphrase service, soft-delete filtering, temporal awareness, nature loader, identity rules loader, type gating, memory tiering, SQLite migration, grounding check, JSON import, SSE events, model filter, monthly reflection, index.html cache, manual eval, lodestone loader, lodestone service, lodestone resolver, lodestone API, deviation detector, deviation API.
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

## Documentation Language Convention

Public-facing documentation, ADRs, code comments, and test fixtures use generic, non-personal language. Reference "the user," "the developer," or "a user" rather than specific individuals. Personal details belong in the vault, not in the codebase. This applies to all three repos.

Documentation can have personality and even humor -- it just should not contain personal identifying information about specific people. Exception: About.jsx creator attribution is intentional and at the author's discretion.

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
- [ ] Retrieval eval passing: python tools/eval_retrieval.py -- no regression
- [ ] Conversation eval run: python tools/eval_conversations.py -- document results
- [ ] CHANGELOG.md updated (release-please handles this via conventional commits)
- [ ] version.json bumped (release-please handles this via conventional commits)
- [ ] All changes committed and pushed to main: git push origin main
- [ ] Constitution, nature, and Lodestone layers reviewed for coherence
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
- [ ] TDD version bump at every release (current: 1.1; bump minor for feature releases, patch for hotfixes)
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
