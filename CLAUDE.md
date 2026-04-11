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

## Current State (v0.14.1)

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
- State layer (StateService, StateResolver, 9 categories including timer, context packet integration)
- Daily and weekly reflection generation (multi-source, junk-filtered, suppression tools)
- Constitutional review (9 principles, constitution v0.6, streaming-compatible — includes relational_honesty v0.5 and flourishing_over_preference v0.1)
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
- ember-2 backend: 779 pytest tests passing
- ember-2-ui: 64 Playwright e2e tests passing (includes BUG-001 regression test)
- ember-2-installer: 48 Playwright e2e tests passing (v0.5.9)

---

## Immediate Next Priorities

**v0.14.0 — Identity Foundation** ✓ (shipped 2026-04-06)
**v0.14.1 — Patch** ✓ (shipped 2026-04-09)

**v0.15.0 — Quality of Life Improvements:**
- Vault encryption at rest (five-layer envelope architecture — see TDD §38)
- Local model quality improvements — token reduction, latency optimization
- Constitutional review optimization — reduce false positive trigger rate, review prompt efficiency
- Web search interaction mode — ask-first (default: "I don't have enough on this — want me to search?") with opt-in autonomous toggle in Settings (autonomous behavior itself is v0.16.0; toggle and ask-first pattern are v0.15.0)
- Web search trigger broadening — current triggers require explicit "search"/"google"/"look up"; broaden to temporal currency markers and factual uncertainty
- API as a service — auto-start on boot for non-developer users (Windows startup task, Linux systemd unit, macOS launchd plist via installer)
- Hallucination reduction — surface uncertainty on vault-retrieved claims, not just web search; identify knowledge gaps and offer to look up rather than fabricate
- Source citation on vault-retrieved content — currently only web search responses show sources
- Quality of life testing — first non-developer user testing on local model
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
- Relational intensity amplification risk — relational_honesty, flourishing_over_preference, and the lodestone relational category can all activate in the same conversation with no retrieval policy gate preventing compounding. A gate suppressing lodestone relational records during relational_honesty or flourishing_over_preference triggers is the correct architectural fix. Not yet implemented. Documented in flourishing_over_preference behavior section in constitution.yaml.
- flourishing_over_preference cross-session pattern detection unenforceable — the principle's first rule scopes detection to within-session patterns only because cross-session detection requires the review service to have vault memory access (see context blindness above). The principle is filed but its strongest use case (noticing patterns across sessions) cannot be implemented until the review service gets vault context.
- Vault-retrieved content has no uncertainty signal — Ember presents vault-grounded claims with the same confidence as directly stated facts. When retrieval returns low-scoring or old records, the response should surface uncertainty ("based on what I have from a few weeks ago...") rather than presenting stale or weakly-matched content as certain. Currently only web search responses show source attribution.
- Knowledge gap fabrication — when Ember has no relevant vault content and no web search triggers, she sometimes fabricates plausible-sounding answers rather than saying she doesn't know and offering to look it up. The grounding verification layer (ADR-019) partially addresses this for identity queries but the gap is broader. v0.15.0 scope.
- Web search triggers too restrictive — queries about recent events, current facts, and time-sensitive topics only trigger web search if the user explicitly says "search", "google", or "look up". Natural questions like "what happened yesterday" or "who won the game" don't trigger. Broadening planned for v0.15.0.
- API requires manual start — non-developer users must run start_api.bat or launch_ember.sh manually. No auto-start mechanism (Windows startup task, Linux systemd unit, macOS launchd plist) exists. v0.15.0 scope via installer.

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
- [ ] Retrieval eval passing: python tools/eval_retrieval.py -- no regression
- [ ] Conversation eval run: python tools/eval_conversations.py -- document results
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
