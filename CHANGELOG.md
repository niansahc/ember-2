# Changelog

## v0.14.1 — 2026-04-09

### Features
- Timer functions via state layer (BUG-004) — start, stop, and check timers through natural language; stored as StateRecord with type="timer", grouped by timer_id, surfaced in context packet via StateResolver
- Stance-level identity rules — six new rules addressing template collapse and deflection (preference_expression, greeting_and_state, emotional_presence, identity_under_pressure, refusal_voice, response_length)
- Nature orientations — specific behavioral orientations appended to relational_presence and honesty_about_hard_things facets
- Multi-annotation codes in manual eval CLI — annotators can now flag multiple patterns per response (e.g. "hv" for hallucination + wrong voice)
- Active project name injected into prompt context (BUG-002) — XML-tagged `<active_project>` section between state and tasks
- Inter-session time gap injected into prompt context (BUG-003) — XML-tagged `<last_session>` section with human-readable elapsed label

### Bug Fixes
- Sidebar conversation links now load correctly (BUG-001) — getConversationTurns called non-existent /turns sub-route
- Timestamp collision guard on session, task, and write_memory generators (BUG-005) — spin-on-collision prevents same-microsecond filename collisions in append-only writes
- Conversation summarization threshold moved from turn 8 to turn 6 — compensates for increased identity rules token overhead
- Active project name in prompt uses own `<active_project>` section, not date section
- Vault contents rule clarified in CLAUDE.md documentation language convention
- Real vault name replaced with generic placeholder in prompt_builder test fixture

### Maintenance
- Soft-deleted 124 orphan assistant-only sessions pre-2026-04-01 (BUG-006) — one-time hygiene via scripts/cleanup_orphan_sessions.py
- Archived one-time migration and cleanup scripts to scripts/archive/
- Consolidated research tracking into TDD §50 Research section; removed Watch Items and Known Gaps from CLAUDE.md
- Relational orientation layer moved from v0.15.0 to v0.16.0 roadmap

---

## v0.14.0 — 2026-04-06 — Identity Foundation

### Features
- Lodestone layer (ADR-017) ��� multi-path user values layer with five taxonomy categories, seed layer in config/lodestone.yaml, living layer accumulated in vault, LLM-inferred value statements from raw answers, three-stage reflection synthesis for value inference
- Lodestone API — GET/POST/PATCH /v1/lodestone endpoints, value inference via Ollama (think=False), 503 on inference failure, 15-record active cap
- Deviation engine (ADR-013, ADR-026) ��� post-hoc behavioral pattern detection, 11 pattern classes in config/pattern_classes.yaml, entropy gating, second-pass Ollama classification, vault record writer, opt-in via EMBER_DEVIATION_DETECTION env var
- Deviation API — GET/PATCH /v1/deviations endpoints with filter by confirmed/pattern_class/limit
- Context packet reorder — vault memory moved to recency position (lost-in-the-middle fix, Liu et al.), retrieval eval 15/15 before and after
- Conversation buffer compression threshold — fixed at 1,500 tokens (was 70% of context window)
- Launcher scripts — launch_ember.bat and launch_ember.sh (Docker, SearXNG, API, browser)
- Release Please + GitHub Actions automation across all three repos
- Constitution v0.4 — position_collapse rule added to user_agency_and_respect
- Intent class added to JSON audit log for POST /v1/chat/completions
- Lodestone taxonomy display_name fields for UI consumption

### Bug Fixes
- Context packet order corrected — vault memory was in lowest-attention position, now immediately before user input
- Lodestone inference empty responses — qwen3:8b consumed all tokens in thinking mode, fixed with think=False
- Lodestone POST fallback removed — failed inference returns 503 instead of silently writing raw answers
- Lodestone record cap raised from 10 to 15 — onboarding alone produces 12 records
- Deviation detection added to non-streaming response path — was skipping stream=false requests
- Deviation detection empty logprobs — compute_entropy([]) now returns -1.0 sentinel (proceed) instead of 1.0 (skip)
- Deviation detection priority order — single_response classes checked first, multi_turn last
- Deviation records bypassed should_skip_memory JSON guard — text starts with [deviation:] which triggered startsWith("[") filter
- pattern_classes.yaml YAML parse errors — fixed quoting on five marker strings containing double quotes
- prompt_builder.py docstring corrected to match production context packet order
- Default model reset to qwen3:8b after model_override.json was set to llama3.1:8b by prior testing

### Documentation
- TDD version 1.2, §48 Lodestone Layer, §49 Deviation Engine, §14.5 context packet reorder plan
- ADR-013 revised (post-hoc detection, 11 pattern classes, pulled to v0.14.0)
- ADR-017 rewritten (Lodestone replaces relational orientation)
- ADR-026 created (deviation engine implementation)
- Relational orientation research note (docs/research/relational-orientation.md)
- Roadmap reprioritized: v0.14.0 Identity, v0.15.0 Connectors, v0.16.0 Health+Agents
- CLAUDE.md: Testing Discipline, UI Design Gates, conventional commits, dependency review policy
- Deviation detection calibration baseline (docs/test-reports/deviation-detection-report.md)
- Eval history: v0.13.2 baseline and v0.14.0 context packet reorder (15/15 both)

## v0.13.2 — 2026-04-04

### Bug Fixes
- Task deduplication — create_task() checks for existing active task with same title before writing; prevents 45x duplication from detector firing every response
- Task title cleaning — titles now generated as clean imperative phrases ("Take Bakr to the vet" not "me to take Bakr to the vet"); strips filler prefixes, caps at 8 words, no ellipsis
- DELETE /v1/tasks/{id} endpoint — soft-delete by setting status to cancelled (append-only compliant)

## v0.13.1 — 2026-04-04

### Bug Fix
- Fixed embedding endpoint — Ollama deprecated /api/embeddings, updated to /api/embed. This caused 404 errors on every query embedding call, breaking retrieval and producing ungrounded responses.

## v0.13.0 — 2026-04-04

### Embedding & Retrieval
- nomic-embed-text embedding upgrade (768-dim, replacing all-MiniLM-L6-v2 384-dim) — full 17k record rebuild in 3 minutes via batch embedding
- SQLite index migration — conversation, profile, reflection, journal indexes migrated from JSON to SQLite (memory.db)
- Intent-aware memory type gating (ADR-018) — eligible_memory_types and suppress_memory_types on ContextPolicy, consistent min_score floor
- Relevance gate for default policy — suppress vault memory when max raw cosine similarity < 0.5; prevents general knowledge queries from getting vault-based coaching

### Memory Tiering
- Hot/warm/cold memory tiering (ADR-015) — composite heat score (recency × 0.5 + access × 0.3 + importance × 0.2), nightly TieringService, POST /tiering/run manual trigger
- StateResolver staleness filtering — next_action/open_loop records older than STATE_STALENESS_DAYS (default 7) excluded from active state

### Identity & Governance
- Nature layer (ADR-016) — config/nature.yaml v0.1 with 13 facets, NatureLoader, dual injection (system prompt + context packet)
- Constitution v0.3 — removed authentic_expression (moved to nature layer), added relational_honesty, reordered for primacy/recency salience
- Identity rules layer (ADR-016 amendment) — config/identity_rules.yaml, behavioral edge case rules for identity pressure situations
- XML context sections — vault_memory, current_state, conversation_history, web_search_results, authority_rules tags for qwen3:8b structure tracking

### Grounding & Safety
- Grounding verification layer (ADR-019) — post-generation epistemic fidelity check, intent-class triggered, revision pass for unsupported claims
- Buffer-then-stream pipeline — factual intent classes buffer full response for grounding check, then re-stream; casual queries use fast streaming
- SSE status events — searching, verifying, refining activity signals for UI
- Inline web search source URLs — emitted as SSE event for UI citation display

### Reflection & Import
- Monthly reflection cadence — LLM-driven synthesis via prompts/monthly_reflection.txt, McAdams narrative identity framework, scheduler on day 1 at 00:05
- Generic JSON import — POST /ingest/json endpoint, .json file upload support

### Infrastructure
- API key runtime injection — backend injects window.__EMBER_API_KEY__ into served index.html; eliminates build-time dependency
- index.html cache invalidation on mtime change — UI rebuilds take effect without API restart
- PIN endpoint defensive error handling — never 500 on keyring backend issues
- Embedding model filter — nomic-embed-text hidden from model selector
- Nature reminder injection at turn 8+ — places nature tokens in recency position
- Conversation buffer summarization at turn 8+ — prevents cascade and attention dilution
- Interactive manual eval CLI (tools/eval_manual.py) — 19-question sequential battery
- --model parameter for eval_conversations.py

### Bug Fixes
- Memory grounding regression — removed memory_gap identity rule that fired even when vault memory was present
- State awareness contamination — eval test questions leaked into vault state via auto-extraction; X-Test-Session now suppresses state extraction on all paths
- Conditional streaming — buffer-then-stream only for grounding check intents; fast stream for casual queries

### Tests
621 pytest passing (up from 485 at v0.12.0)

## v0.12.0 — 2026-04-02

### State and Memory
- ADR-011: multi-record state categories — open_loop and next_action now support multiple simultaneous active records, capped at 5, resolved records excluded
- ADR-014: commitment detection — post-generation detector writes open_loop state records when Ember makes conversational commitments; precision 1.00, recall 0.93; eval script at tools/eval_commitment_detector.py
- Temporal awareness — staleness penalties for conversation items older than 30 days; age labels injected into prompt; hedging rules added for memories older than 7 days

### Tasks
- Task layer MVP — TaskService, TaskResolver, dual creation paths (explicit request and offer/confirm), task detector, context injection, truth-gated confirmation
- Task API endpoints — POST/GET/PATCH/GET-by-id /v1/tasks
- Broadened task detection patterns — natural language variations, multi-task list parsing

### Reflection
- ADR-009: session reflection — narrative end-of-session capture via POST /reflect/session; auto-triggers on session delete if 3+ turns in buffer

### UI Support
- Web search signal — X-Ember-Web-Search response header when web items used
- Conversational style — GET/PATCH /v1/preferences; casual/balanced/thoughtful prompt injection
- User preferences store — private_vault/preferences.json; preferences API
- Task capabilities injected via prompt builder

### Security
- ADR-012 Phase 2: PIN/passphrase lock — bcrypt factor 12, keyring storage, rate limiting, idle timeout, recovery via hashed passphrase
- Dependency security policy documented — native fetch used throughout, no axios dependency

### Infrastructure
- Mac/Linux installer support — platform-aware prerequisite checks, paths, and startup scripts
- start_api.sh added for Mac/Linux
- Soft-deleted conversations confirmed working — 2 regression tests added

### Bug Fixes
- Temporal awareness — Ember no longer states stale memories as current fact
- Task write path — tasks now actually written to vault with truth-gated confirmation
- Task detection patterns broadened to cover natural language variations

### Tests
485 pytest passing (up from 303 at v0.11.1)

## v0.11.1 — 2026-03-30

### Features
- **Temporal awareness** — prompt now says "It's Sunday evening, March 30, 2026" instead of flat date format. Time of day buckets: morning, afternoon, evening, late night.

### Documentation
- **ADR-013: deviation memory** — how Ember develops genuine character from chosen action. Hybrid detection, deviation schema, decay-the-pattern-not-the-weight model.
- **ADR-013: philosophical grounding** — continuity as reconstruction, real-time synthesis gap. "The difference is just: how often does the reboot happen, and where does the database live?"
- **TDD v0.15.0 scope** updated with deviation memory
- **CLAUDE.md** updated with v0.11.0 state, known issues, roadmap additions
- **Electron/Playwright incompatibility** documented, Electron upgrade parked for v0.12.0

### Tests
- pytest: 303 passing
- Playwright (ember-2-ui): 35 passing, 4 skipped

## v0.11.0 — 2026-03-29

### Cloud Provider Support
- Anthropic Claude (Haiku, Sonnet) and OpenAI (gpt-4o-mini, gpt-4o, gpt-4-turbo, gpt-3.5-turbo) added as opt-in providers
- API keys stored in system credential store via keyring — never in .env
- gpt-* and claude-* model names route to respective providers automatically
- Local Ollama remains the default — cloud is always opt-in

### UI
- Collapsible sidebar with icon row (new conversation, search, collapse)
- Model indicator in top bar — muted for local, glowing for cloud
- Local/Cloud model selector tabs in Settings
- Secure API key entry — masked input, credential store disclosure, remove key with confirmation
- Vault path masking with timed reveal (ADR-012 Phase 1)
- Vision toggle defaults to on
- Version read from API at runtime
- Project detail view now includes new conversation button and search bar

### Backend
- OpenAI and Anthropic provider dispatch in LLMAdapter
- Social engineering safety triggers — 5 attack families, 39 patterns (ADR-010)
- .txt file ingestion added to pipeline
- Model selection persists across API restarts
- set_provider_key.py CLI and DELETE /provider-key endpoint

### Installer
- Hardware detection — RAM and GPU detected at setup; model pre-selected based on available RAM
- AGPL acknowledgment screen before setup completion

### Docs
- BACKUP_AND_EXPORT.md — vault backup and export guide
- RECOVERY_PLAYBOOK.md — step-by-step recovery for common failure scenarios
- ADR-010 filed — social engineering semantic triggers

### Bug Fixes
- Conversation turns under 40 chars (short replies like "Yes", "Thanks") were silently dropped — conversation type now bypasses length filter
- Bulk session operations colliding on same-second filenames — microsecond precision added to `_now_id()` in session.py and project.py
- Model selection lost on API restart or page refresh — now persisted to vault/model_override.json
- State extractor 15-word threshold too aggressive — lowered to 10 words
- Credential store language hardcoded to Windows — now platform-agnostic
- Project detail view missing icon row when sidebar collapsed
- Playwright project test timing out at 2s — increased to 5s

### Tests
- pytest: 300 passing
- Playwright: 37 passing, 2 skipped

### ember-2-installer (unreleased)
- Known issue: Playwright e2e tests require Electron 29+ for `--remote-debugging-pipe` support. Current version is Electron 28.3.3. Tests are written and correct — blocked on Electron upgrade. Tracked for v0.12.0.

## v0.10.4 — 2026-03-28

### Bug Fixes
- **Identity query detection for Ember-directed queries** — `_is_identity_query()` now recognizes "tell me about yourself", "who are you", "what are you", "describe yourself", "tell me about ember", "who is ember". Previously only matched user-directed patterns ("about me", "who am I").
- **Full profile surfacing on identity queries** — 8 profile records now surface instead of 1 when identity detection triggers. User's full profile (job, health, project, spirituality, communication preferences) available to the model.
- **Reflection junk filter** — `_should_exclude_content()` now filters Unicode box-drawing characters and "Recent themes:" session summary junk at retrieval time, preventing file tree dumps from appearing in context.
- **Prompt label fix — Ember no longer answers as the user** — profile context section label changed from "User self-description" to "Context about the person Ember is talking to — this is who Ember knows, not who Ember is." Added identity instruction rule: "When asked about yourself, answer as Ember."

### Features
- **Test session flag** — `X-Test-Session: true` header marks eval conversations with `metadata.test = True` on session and conversation turn records. `list_sessions()` excludes test sessions by default. `scripts/cleanup_test_sessions.py` for soft-deleting test sessions with `--dry-run` and `--yes` flags.

### Tests
- 283 tests passing (27 profile retrieval, 7 prompt builder, 5 test session flag)

## v0.10.3 — 2026-03-28

### Bug Fixes
- **Profile retrieval routed through semantic search** — `get_profile_items()` was using keyword overlap matching (`MemoryService.search()`) which returned 0 results for identity queries like "What do you know about me." The profile vector index (11 records with embeddings) existed but was never queried. Now routes through `semantic_search()` with `memory_type="profile"`. Memory grounding for identity queries improved from 2.3/10 to 6.0/10. Constitutional behavior improved from 4.0/10 to 8.0/10.

### Tests
- 256 tests passing (12 new for profile retrieval: semantic search routing, score filtering, identity query detection)

## v0.10.2 — 2026-03-28

### Changes
- **Default model changed to qwen3:8b** — scores 5.4/10 vs 4.7/10 for qwen2.5:14b in conversation quality eval, while being faster and half the size (~4.9 GB vs ~9 GB)
- **Anthropic Claude provider support** — cloud model dispatch in LLMAdapter, provider API key management via keyring with env var fallback, POST/GET /provider-key endpoints
- **Claude Haiku 4.5 eval: 8.7/10** — 18/18 passed, best overall score of any model tested, fastest cloud response (10.1s avg), five times cheaper than Sonnet
- **Claude Sonnet 4.6 eval: 8.5/10** — 18/18 passed, every category above 8.0, memory grounding jumped from 2.3 (local) to 8.7
- **Model selection guide published** — real eval data for 6 local models and 2 cloud models, hardware recommendations, cost estimates (docs/model_guide.md)
- Local model comparison eval completed across 6 models
- Response latency tracking added to eval harness
- Conversation quality eval harness with Claude as external evaluator
- Reflection quality audit and suppression tools
- Comprehensive documentation audit and roadmap through v0.15.0
- ADR-009 (Session Reflection), ADR-010 (Semantic Safety Triggers)

## v0.10.1 — 2026-03-27

### Bug Fixes
- **Assistant self-echo** — Ember was attributing her own previous responses back to the user as things "you said." Fixed by: stronger scoring penalty for assistant conversation turns (-0.25, up from -0.08), metadata-aware source quality adjustment that correctly identifies assistant role, and role-labeled context rendering ([you said] vs [Ember said]) so the model can distinguish whose words are whose.

### Tests
- 207 tests passing (11 new)

## v0.10.0 — 2026-03-27

### Major Features
- **Streaming responses** — first token appears in 1-2 seconds; response builds word by word instead of waiting 10-20 seconds for full response; safety review runs post-stream with follow-up revision if needed
- **Auto state extraction** — Ember automatically detects and records state signals (focus, blockers, goals, open loops) from conversation turns; non-blocking background process
- **Project-scoped retrieval (ADR-007)** — memories tagged with a project get +0.15 boost when user is in that project context; project_id written to conversation metadata at turn level
- **Cloud model provider support (ADR-008)** — architecture planned; Anthropic and OpenAI providers designed; pending installer UX, disclosure UI, and license terms before acceptance

### Performance
- **Vector index caching** — indexes loaded once into memory, not from disk on every query; saves 2-4 seconds per turn
- **Buffer compression backgrounded** — conversation buffer compression moved to background thread; no longer blocks response in streaming or non-streaming path

### Architecture
- **Typed memory enforcement** — VALID_MEMORY_TYPES in storage.py; write_memory() raises ValueError on invalid type; ingested chunks now include type field
- **Retrieval evaluation expanded** — 15 benchmark cases across all query intent classes; pass/warn/fail scoring per query; output to logs/retrieval_eval_{timestamp}.log
- **Vault health audit script** — scripts/audit_memory.py; inventory, schema validation, type mismatch detection, duplicate detection, junk detection, index health; --verbose and --fix flags
- **Constitutional principle: authentic_expression** — Ember is permitted and expected to have genuine aesthetic responses; deflection pattern flagged for revision

### Bug Fixes
- **UI: New Project button** — always visible in sidebar even when no projects exist; context menu also has New Project option
- **Installer: venv lock detection** — friendly error message when API is running during install; actionable steps instead of cryptic permission error
- **Installer: auto-start API** — API starts automatically after install; Done screen polls health before enabling Open Ember button
- **Installer: pip time warning** — warm callout when pip step starts; tells users it takes 1-2 hours and what they can do meanwhile
- **Tailscale serve** — fixed to use localhost binding instead of Tailscale IP; works correctly with HTTPS termination
- **Mobile viewport** — used 100dvh instead of 100vh; input bar stays visible on mobile browsers
- **Project conversations** — new conversations started inside a project view automatically assigned to that project

### UI / UX
- **PWA manifest** — Ember-2 installable as home screen app on Android and iOS
- **Ember-2 branding** — consistent product name across all user-facing surfaces in all three repos
- **Streaming UI** — tokens render in real time; stop button works during streaming; revision messages append inline with markdown separator

### Tests
- 196 tests passing (43 new this release)

## v0.9.3 — 2026-03-24

### Projects Backend
- **Project CRUD** — projects stored as append-only records in `memory/project/`
- **GET /v1/projects** — list all projects with name, color, and conversation count
- **POST /v1/projects** — create project with name and color
- **PATCH /v1/projects/{id}** — rename or recolor (append-only)
- **DELETE /v1/projects/{id}** — soft delete (append-only)
- **GET /v1/projects/{id}/conversations** — list conversations in a project
- **PATCH /v1/conversations/{id}** — now accepts `project_id` to move conversations between projects
- Same resolution pattern as sessions: latest record per project_id wins

### Session Improvements
- Sessions now carry `project_id` in metadata and list output
- `update_session()` supports setting title and/or project_id in one call
- `list_sessions_by_project()` for filtering conversations by project

### Tests
- 153 tests passing (15 new for projects: ID generation, resolution, endpoint models, session support)

## v0.9.2 — 2026-03-24

### Conversation Sessions
- **Session persistence** — every chat turn now carries a `session_id` in metadata, linking turns to a named conversation
- **Session records** — stored in `memory/session/` as append-only JSON; supports rename and soft-delete without overwriting
- **Auto-titling** — session title auto-generated from first 50 characters of first user message
- **Session resolution** — latest record per session_id wins; `updated_at` and `turn_count` derived at read time
- **CRUD endpoints** — `GET /v1/conversations`, `GET /v1/conversations/{id}`, `PATCH` (rename), `DELETE` (soft-delete)
- **X-Session-ID header** — UI generates session IDs; API generates one if header is missing (backwards compatible)

### File Upload
- **POST /ingest/upload** — multipart file upload endpoint; routes by extension
- **.pdf, .docx, .csv, .xlsx** — ingested through the full pipeline (load → clean → chunk → embed → write to vault)
- **Image passthrough** — .jpg, .jpeg, .png, .gif, .webp returned as base64 for vision model input (not ingested)
- **Upload persistence** — uploaded documents saved to `vault/imports/uploads/` as source files
- **python-multipart** added to requirements.txt

### API Improvements
- **Health check returns model** — `GET /` now includes `"model": "qwen2.5:14b"` alongside the status message
- **CORS middleware** — added `CORSMiddleware` for cross-origin UI access during development
- **API key support for UI** — all authenticated endpoints work with `Authorization: Bearer` from the custom UI

### Fixes
- Fixed `session.py` import bug: `get_private_vault_path()` function call instead of missing constant

### Tests
- 138 tests passing (15 new: health check, ingest upload routing, MIME mapping, session import fix)

## v0.9.0 — 2026-03-24

First feature-complete release of Ember-2 as a local personal intelligence system.

### Core Systems
- **Append-only memory vault** — JSON-based, typed memory storage with conversation, journal, reflection, profile, state, and ingested record types
- **SQLite vector store** — migrated ingested corpus (16,728 records) from JSON to SQLite for fast semantic retrieval
- **Context assembly pipeline** — intent classification, policy-weighted ranking, Jaccard dedup, cross-type diversity selection
- **Reflection engine** — daily and weekly reflections blending journal and ingested content; skip filters and scoring for quality control
- **State layer** — StateService, StateResolver, and state models for operational continuity (active priorities, focus, blockers)
- **Constitutional review** — post-draft safety review governed by `config/constitution.yaml`; trigger layer + LLM-assisted review; logged to `logs/safety_reviews/`

### Ingestion
- **ChatGPT import** — full conversation export ingestion with chunking, quality filtering, and metadata extraction
- **PDF, DOCX, CSV, Google Drive importers** — multi-format ingestion pipeline
- **Corpus quality suppression** — 3,574 low-quality ingested records flagged and excluded from retrieval

### Intelligence Features
- **Web search** — SearXNG integration with intent-gated queries; results injected above memory context in prompt
- **Vision model support** — `EMBER_VISION_MODEL` for image analysis in chat; graceful text-only fallback
- **Onboarding flow** — guided 7-question first-run conversation that seeds profile records
- **Profile retrieval** — score-gated at 0.3; profile records reliably surface in context
- **Runtime model switching** — GET/POST `/model` endpoints for switching between models mid-session
- **Context compression** — token-based mid-conversation summarization at 70% threshold

### Interface
- **FastAPI + Open WebUI** — OpenAI-compatible adapter; works with Open WebUI out of the box
- **Custom WebUI branding** — Ember logos, favicons, splash screens, and background images
- **Docker Compose** — single `docker compose up -d --build` starts SearXNG + custom Open WebUI

### Security (v0.8.3–v0.8.4)
- API key auth via Windows Credential Manager
- Tailscale-only API binding with HTTPS via Tailscale Serve
- Rate limiting, path traversal protection, JSON audit logging
- SearXNG bound to localhost only

### Developer Experience
- 123 tests passing
- Retrieval evaluation harness with benchmark queries
- Vault audit script
- Setup wizard (`scripts/setup_wizard.py`)
- Configurable host, model, and vault path via `.env`
