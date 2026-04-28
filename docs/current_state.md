# Current State — Ember-2 v0.17.1

All items from the original TDD §25 build order are complete through step 7. The system is feature-complete for single-user local deployment on Windows, Mac, and Linux. Cloud reasoning is available as an opt-in alternative via Anthropic Claude or OpenAI providers.

v0.13.x shipped embedding upgrade, memory tiering, nature layer, grounding verification, and XML context restructuring. v0.14.0 adds Lodestone layer, deviation engine, and context packet reorder. v0.15.x shipped web search broadening, temporal decay, constitutional review overhaul, knowledge gap suppression, vault citation signals, and multiple bug fixes. v0.16.0 ships autonomous web search as default, vision pipeline fix, vault citation signal hardening, attribution badge fixes, and UAT-cycle stability work. v0.17.0 ships an ask-first intent classifier (three-stage: structural, embedding, LLM fallback), ChatGPT import role separation for state extraction and embedding, and anti-sycophancy / coaching-register rule expansion; the UAT suite was rewritten as 25 behavioral acceptance tests and a CI pytest workflow was added.

## Core Systems

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
- Autonomous web search default (web_search_autonomous=True); ask-first intent classifier landed in backend (ADR-034), UI re-enable pending
- Vault citation signal — state_items now included in vault source builder (UAT-004 fix)
- Vision pipeline — image_data forwarded through LLMAdapter to model (v0.16.0 fix)

## Evaluation & Tooling

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

## v0.14.2 Additions

- Constitutional review optimization — MVR (Minimum Viable Review) prompt with three fixed criteria, trigger-signal-to-principle append for non-MVR principles
- Constitution v0.7 — flourishing_over_preference rewritten with four-condition fire gate
- Think block orphaned-tag stripping (pass 2: orphaned close, pass 3: orphaned open)
- Knowledge gap suppression across all three injection paths with curly apostrophe normalization
- BUG-008/009 fixes
- PIN change endpoint, disk encryption status endpoint
- Service health/restart/developer status endpoints
- Runtime vault swap (dev-mode gated)
- Claude Code hooks (.claude/hooks/)
- DEVEmberVault structure

## v0.15.x Additions

- Web search trigger broadening — temporal currency markers, factual uncertainty markers, entity-type triggers (Layer 1)
- Multiplicative temporal decay weighting in ContextRanker
- Vault citation signal — X-Ember-Vault-Used response header and vault_sources SSE event
- Retrieval confidence metadata injection
- Knowledge gap suppression strengthened
- BUG-010 fix: ThinkBlockFilter dual-buffer architecture
- Think block stripping — full pipeline
- Contrastive few-shot examples for preference expression
- Relational intensity amplification gate
- Embedding batching — 3 Ollama embedding calls reduced to 1 per query
- Cross-platform watchdog for API restart and stop
- Streaming SSE regression test added to release gate (Tier 3)
- Launch-installer endpoint, version/release triggers in prompt

## v0.16.0 Additions

- Autonomous web search default (web_search_autonomous=True); ask-first deferred to v0.17.0 for LLM-based intent classification
- Explicit/implicit web marker split in src/context/policies.py — prevents false-positive ask-first bypass
- Vision pipeline fix — image_data forwarded through LLMAdapter.chat to model (closes known bypass)
- Vault badge fix — state_items included in _build_vault_sources (UAT-004 root cause)
- Source badge suppress fix — _suppress_source_badges gated on _ask_first_active (ASK-005/011)
- Constitutional review blank response fix — override detection path now returns StreamingResponse (UAT-015)
- BUG-ASK-010 — orphaned "I don't have that in my memory" phrase suppressed in web search responses
- BUG-UAT-014 — retrieval leakage: ingested content no longer surfaces on status_state queries
- Post-gen pipeline ask_first_active threaded directly from adapter (removes double-computation divergence)
- Confirmation flow query handling — original query restored to context on Yes confirmation

## v0.17.0 Additions

- Ask-first intent classifier — three-stage pipeline (ADR-034): stage 1 structural rules, stage 2 embedding similarity, stage 3 LLM fallback with timeout
- Intent classifier integrated into classify_query in context pipeline
- StateExtractor gated to live conversation turns only (ADR-033) — prevents historical ChatGPT import content from writing state records
- ChatGPT import: assistant-role chunks no longer embedded (ingest-side filter complements ADR-033)
- Instruction section: explicit anti-sycophancy and register rules added
- Nature entries extended with anti-sycophancy and anti-softening language
- coaching_filter extended with additional sycophancy and therapeutic register patterns
- UAT suite replaced with 25 behavioral acceptance tests
- CI pytest workflow on pull requests (.github/workflows/tests.yml)
- open_pr.sh developer helper script

### Open across v0.17.x

- BUG-STOP-001 — stop button latency under load (still open)

## v0.17.1 Additions

- Constitutional review context signal (ADR-035) — `SafetyReviewContext` gains `is_vault_grounded` bool and `t2_pattern_category` label; two-step review prompt for T2-triggered cases (Item 7)
- Cross-session pattern detection (ADR-021) — `PatternSignal`, `detect_t2_pattern()`, `contains_named_third_party` flag at write time, `<cross_session_pattern>` prompt injection (Item 8)
- Lodestone path 2 — three-stage reflection synthesis produces inferred vault records (`acquisition_path: "inferred"`, `confirmed: false`); monthly cadence; confirmed-only injection gate unchanged (Item 9)
- Vision pipeline fix — `VisionService` now reads `EMBER_VISION_MODEL` env var (was hardcoded to `qwen3-vl:8b`); `image_data` cleared after successful VL preprocessing to prevent raw image bytes reaching the text model
- Ask-first toggle re-enabled in UI Settings (ADR-034 backend live)

## Security

- API key auth via OS credential store (Windows Credential Manager, macOS Keychain, Linux SecretService)
- Auth only on API routes — UI static files are public
- Rate limiting, path traversal protection, JSON audit logging
- SearXNG bound to localhost only
- Tailscale serve uses localhost binding
- Vault path masked in UI with timed reveal (ADR-012 Phase 1)
- Cloud API keys stored in OS credential store, never displayed in UI
- PIN/passphrase lock (ADR-012 Phase 2) — bcrypt, keyring, rate limiting, idle timeout, recovery
- Dependency security policy — native fetch, no axios; documented after March 2026 supply chain attack

## UI (ember-2-ui, served from ui/)

- Streaming chat with markdown, copy, edit/resend, regenerate, scroll-to-bottom, export
- Collapsible sidebar with icon row and localStorage persistence
- Model indicator in top bar; local/Cloud model selector tabs in Settings
- Secure API key entry in Cloud tab
- Vault path masked by default
- Vision toggle defaults to on when vision model configured
- Version reads from /api/health not hardcoded
- Sidebar with projects, conversations, search, right-click context menu
- Settings: 5 themes, web search toggle, conversation memory toggle
- About panel with Ember's story, beliefs, ethos
- PWA manifest for Android/iOS home screen installation
- .txt file upload, multi-image upload
- Web search transparency indicator — magnifying glass icon
- Conversational style selector — Casual/Balanced/Thoughtful
- Task sidebar tray — bottom-anchored, checkbox to complete, 30s polling
- Guided first-run tour (Shepherd.js, 6 steps)
- PIN/passphrase lock screen with idle timeout
- Restore active conversation on page refresh
- Style pack system (OG / Hearth / Cool Hacker / Clean) — v0.16.0
- Self-hosted fonts (Fraunces, JetBrains Mono, Inter) — v0.16.0
- Appearance tab in Settings — v0.16.0
- Personalized time-of-day greeting (180 variants, Ember's voice) — v0.16.0
- Autonomous search locked ON; ask-first marked "coming in a future update" — v0.16.0

## Installer (ember-2-installer)

- Auto-install prerequisites via winget on Windows
- Mac/Linux support
- Curated model cards with eval-based descriptions
- Default model: qwen3:8b
- Model selection guide linked from model selection screen
- Venv/API lock detection, auto-start API with health check polling
- Retry button on Done screen
- Tailscale walkthrough, progress bar + fun facts
- Clones ember-2 repo, builds UI from ember-2-ui
- Electron 33

## Tests

- ember-2 backend: 1733 pytest tests collected (20 deselected)
- ember-2-ui: 163 Playwright e2e tests passing (2 conditional skips)
- ember-2-installer: 73 Playwright e2e tests passing
