# Changelog

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
