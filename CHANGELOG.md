# Changelog

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
