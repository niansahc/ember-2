# Changelog

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
