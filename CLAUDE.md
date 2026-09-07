# CLAUDE.md — Ember-2

Canonical design: `docs/Ember2_TDD.md`
Business requirements: `docs/Ember2_BRequirements.md`
ADRs: `docs/adr/`

---

## Response brevity

Reports and status updates: facts, numbers, hashes. No narrative.

No preamble. No "I'll now..." or "Let me..."
No postamble. No "Let me know if..." or "Want me to..."
No apology unless a real error occurred.
No restatement of the request before answering it.

Grill answers: number only unless clarification needed.

Report format: what changed, the identifier (commit/PR/branch), verification result. One line per fact when possible. Prose only when explanation genuinely aids understanding.

Do not narrate what you are about to do. Do it and report the result.

Exceptions: real diagnoses, real trade-off explanations, and grill findings where the model of the world matters. Verbosity there is signal.

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

## Deployment Boundary

Ember-2 ships for a single personal computer. Reference deployment: one user, local model via Ollama, default model per version.json era.

Multi-user, voice, smart home, and server-class deployments are downstream consumers built outside this repo (e.g. ember-voice-gateway). The only in-repo support for such deployments is generic API capability: `caller_identity`, `access_tier`, and `modality` fields, ignorable by default.

**Gate for any change: if a PC-only user's install changes at all, it does not ship in ember-2.**

Nothing hardware-specific or deployment-specific enters defaults, docs, or the installer. Models are swappable components selected per release by eval, never an architectural dependency.

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

## Current State

See [docs/current_state.md](docs/current_state.md).

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

**v0.16.0 — Stability & UAT Cycle** ✓ (shipped 2026-04-18)
- Autonomous web search default (`web_search_autonomous=True`); ask-first deferred to v0.17.0
- Vision pipeline fix — `image_data` wired through `LLMAdapter.chat` to model
- Vault badge fix — `state_items` included in `_build_vault_sources`
- Source badge suppress fix — `_suppress_source_badges` gated correctly on ask-first prompt turn only
- Constitutional review blank response fix — early-return paths return `StreamingResponse` when `stream=True`
- BUG-ASK-010, BUG-UAT-014 bug fixes
- UI: style pack system (OG/Hearth/Cool Hacker/Clean), self-hosted fonts, appearance tab, 180-variant greeting
- Autonomous search locked ON in UI; ask-first marked "coming in a future update"

**v0.17.0 — Smarter search routing, anti-sycophancy, ChatGPT import fixes** ✓ (shipped 2026-04-25)
- ~~UAT restructuring~~ ✓ — 25 behavioral acceptance tests; CI pytest workflow on PRs
- ~~Response quality for qwen3:8b ceilings~~ ✓ — anti-sycophancy rules in instruction section + nature layer; coaching_filter expansion (residual ceilings A-001/M-001 documented in KNOWN_ISSUES)
- ~~Ask-first interaction mode (LLM-based intent classification)~~ ✓ — three-stage pipeline (ADR-034)
- ~~ChatGPT import role separation~~ ✓ — ADR-033, StateExtractor gated to live conversation turns only
- ~~Shutdown endpoint~~ ✓ — `POST /v1/service/shutdown`
- BUG-STOP-001 — stop button latency: still open

**v0.17.1 — Retrieval quality, vision, and routing fixes** ✓ (shipped 2026-04-29)
- Constitutional review context signal (ADR-035) — `SafetyReviewContext` extension for vault-grounded and T2-pattern review prompts
- Cross-session pattern detection (ADR-021) — `PatternSignal`, `contains_named_third_party` flag
- Lodestone path 2 — three-stage reflection synthesis, inferred records, monthly cadence
- Vision pipeline configurability — `VisionService` reads `EMBER_VISION_MODEL`; image bytes cleared after VL preprocessing
- Fast-streaming review signal (ADR-036)
- Conversational acks short-circuit at intent classifier Stage 1
- Coaching filter span-based deletion fix
- Retrieval proper-noun boost
- ChatGPT import timestamp normalization (Unix epoch -> ISO 8601)
- Vision pipeline structured logging

**v0.18.0 - UAT Response Cycle (Workstream B)** ✓ (shipped 2026-08-01)
- Classifier, coaching filter, deviation detector, and self-narrative check fixes addressing 2026-05-11 UAT findings (B1-B7, B-CTX-001, B-NARR-001/002, B-DODGE-001, B-LOOP-001)
- Known issues catalogued in `docs/KNOWN_ISSUES.md`

**v0.19.0 — Research Graduation** (in progress)
- ADR-037 filed (Proposed) — intent classifier SetFit graduation, formalizing the ADR-034 upgrade path. Step A (labeled example buckets) exists but is stale/unmerged; Steps B-E not started or descoped. See ADR-037 for full status.
- Research review graduation items tracked in `docs/Ember2_TDD.md` §25.3: LightRAG architecture investigation, SWAY counterfactual CoT eval, MemMachine retrieval depth ablation, SetFit labeling session

**Deferred until actively using Ember:**
- Health ingestion (Fitbit/Apple/Garmin)
- Self-evaluation and decision-memory loops
- Controlled tool writes / agent orchestration
- Trace-driven learning
- Relational orientation layer
- Demo mode + feature presentation

Research tracking has moved to docs/Ember2_TDD.md. TDD is the source of truth for all watch items, research notes, and known gaps.

## Known Issues

See [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md).

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

2292 tests collected (70 deselected) covering: constitution loader, policy service (including relational_hedging and preference_compliance triggers), review service, vault read/write, state layer, state extractor, state staleness (and live-turn gating per ADR-033), timer service, project boost, index caching, memory type enforcement, health check, ingest upload, cloud provider dispatch, provider API key management, task layer, commitment detection, session reflection, PIN/passphrase service, soft-delete filtering, temporal awareness, nature loader, identity rules loader, type gating, memory tiering, SQLite migration, grounding check, JSON import, SSE events, model filter, monthly reflection, index.html cache, manual eval (multi-annotation), lodestone loader, lodestone service, lodestone resolver, lodestone API, lodestone synthesis (path 2 inferred records), deviation detector, deviation API, web search pipeline, vision pipeline, vision service logging, badge signal integrity, ChatGPT role separation and normalization, intent classifier (ADR-034), pattern detector (ADR-021), third-party flag, CSP headers, vault storage, vault swap, vault toggle, PreGeneration terminal router and enrichment-dependent interceptors (ADR-041), GenerationContext two-phase enrichment pipeline (ADR-042), SSE wire contract v2 (ADR-040), response-quality eval framework (drift/grounding/register aggregation, judge retry and transient-failure tolerance, baseline provenance).
Tests do not mock the filesystem vault (real path via `PRIVATE_VAULT_PATH`). Integration tests hit real storage.

When adding features: unit test normalizers, filters, ranking functions, and state resolution. Integration test full pipeline paths.

Eval regressions require running the eval twice before concluding a 3+ point drop is real. A single run is not sufficient to call a regression.

---

## Working With This Codebase

- Code is written by AI, reviewed and approved by the human
- Always replace full files — never partial find-and-replace edits
- Work on one file at a time
- Small, frequent commits with clear descriptive messages
- Never add Co-authored-by: Claude or any Claude attribution to commit messages or PR bodies. Attribution is acknowledged in docs/BUILDING_EMBER.md.
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

Pre-release checklist: run `/pre-release`

Release-please PRs (title format: "chore(main): release X.Y.Z") must NEVER have auto-merge enabled. These PRs require explicit human approval and manual merge only. The human decides when a release is cut. All other PR types (feat, fix, docs, test, chore non-release) may use auto-merge as normal.

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

## Bug Fix & Implementation Standards

1. Any early-return path in the streaming endpoint must return a `StreamingResponse` when `stream=True`. Never return a JSON response object when the client expects SSE — this produces a blank response in the UI with no error surfaced.

2. `refuse_redirect` responses bypass `coaching_filter` — never filter or rewrite refusal text after the constitutional review layer has already decided to refuse.

3. Clear all source attribution (vault + web) when ask-first substitutes the user query. Attribution fires only after search results confirm a web retrieval occurred.

4. Suppress `knowledge_gap_line` when `has_web_items` is `True` in `_render_authority_rules()`. The knowledge gap phrase must not appear on responses that include live web results.

5. A bug is not verified fixed until tested live with the actual trigger. Code inspection alone is not verification. "I fixed it" means nothing until the trigger produces the correct behavior at runtime.

6. Bug fixes require: (1) a test that would have caught the regression, and (2) logging at the fix point confirming the correct path executes at runtime. A fix with no test and no runtime confirmation is unshipped.

7. Use ASCII only in diagnostic print statements. Non-ASCII characters (e.g. arrows, em dashes, emoji) crash the request handler on Windows cp1252 and produce silent failures that look like routing bugs.

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

## Manager Chat Protocol

The manager chat (this chat) handles architecture, planning, prompts, and decisions. Claude Code handles implementation.

**Prompt format:**
- All prompts for CC terminals are delivered in a fenced code block with a copy button
- No rationale in prompts — spec the output, include commit message, done
- Combined prompts unless there is a dependency reason to separate

**Working style:**
- One question at a time
- Bullets and multipart responses are fine, no walls of paragraphs
- Manager makes recommendations, does not ask permission
- "We came up with a plan" means already in progress, no prompt needed
- "Approved" means done, no prompt needed
- Architectural and prompt decisions are research-backed — use the Deep research channel before making significant decisions, do not guess

**CC efficiency rules:**
- Always include parallel subagents instruction when task touches 3+ independent files
- Release gate is a hard stop — no tag until all three repos green and human approves
- Vault privacy rule: no real vault content ever in prompts, commits, or logs
- All evals and tests must run against the test vault only — never the real vault
- When tests fail, find the root cause and implement a scalable solution — do not write code to pass the test

**Session handoff:**
- Outgoing manager writes full handoff at session end
- Incoming manager reads CLAUDE.md before anything else
- After every release, remind the human to sync project knowledge files to the Claude project

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

## Git Hooks (business hours push protection)

Blocks pushes during US Eastern business hours (9am-5pm Mon-Fri). Two layers:

1. **Local pre-push hook** — `hooks/pre-push`. Git hooks are not committed, so install manually after cloning:
   ```bash
   cp hooks/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push
   ```
   On Windows without bash, copy the file and ensure Python is on PATH.

2. **GitHub Actions check** — `.github/workflows/business-hours-check.yml`. Runs on push to main, fails if the push arrived during business hours. Catches anything that bypasses the local hook.

Hook handles EST/EDT automatically via Python's `zoneinfo` and `America/New_York`.

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

Full release process, gates, and sequence: run `/pre-release`
