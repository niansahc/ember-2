# Ember-2: NIST AI Risk Management Framework Mapping

**Framework version:** NIST AI RMF 1.0 (January 2023) + Generative AI Profile (NIST AI 600-1, July 2024)
**Date:** April 2026
**Status:** Living document — updated at each major release

This document maps Ember-2's current architecture and practices against the four functions of the NIST AI Risk Management Framework: Govern, Map, Measure, and Manage. It is honest about gaps. It is not aspirational.

---

## GOVERN

The Govern function establishes policies, accountability, and transparency across the AI lifecycle.

### What Ember Has

**Explicit governance artifacts:**
- `config/constitution.yaml` — external, inspectable, user-modifiable behavioral governance. Not buried in prompts or model weights.
- `docs/adr/` — 14 Architecture Decision Records documenting every significant design decision, rationale, and alternatives considered.
- `ETHOS.md` — founding principles covering data ownership, privacy, user control, and what Ember refuses to do.
- AGPL-3.0 license — ensures the system remains open and community-owned.
- CC BY-NC 4.0 for assets — prevents commercial exploitation of Ember's identity.

**Accountability:**
- All responses subject to post-generation constitutional review. Outcomes logged to `logs/safety_reviews/`.
- Review decisions are inspectable — trigger signals, principles applied, and outcomes recorded.
- Append-only vault — nothing is silently changed or deleted. Every change is traceable.

**Transparency:**
- Users can see which model is active at all times.
- Web search usage is indicated on messages that used it.
- Vault path is accessible (masked by default, user can reveal).
- Cloud model disclosure — users explicitly acknowledge that context is sent to the provider when using cloud models.

**Privacy:**
- Local-first architecture enforces privacy structurally, not through policy promises.
- No telemetry, no analytics, no external data transmission without user opt-in.
- User data never leaves the vault unless the user explicitly enables a cloud model or web search.

### Gaps

- No formal organizational governance structure (single-person project).
- No documented AI incident response process beyond the recovery playbook.
- No formal third-party governance audit.

---

## MAP

The Map function establishes context, intended use, and risk framing for the AI system.

### What Ember Has

**Intended use documented:**
- `docs/Ember2_BRequirements.md` — business requirements covering vision, goals, user experience principles, constraints, and success criteria.
- `docs/Ember2_TDD.md` — full technical design including scope, out-of-scope items, and system context.
- `BUILDING_EMBER.md` — plain-language description of the project, intended audience, and how it is built.

**Risk identification:**
- TDD section 24 — risks and mitigations table covering contaminated corpus, index corruption, memory class mixing, assistant self-echo, prompt folklore, over-triggering, and state layer absence.
- Known Issues section in CLAUDE.md — actively maintained list of open issues.
- Known Gaps section — explicit acknowledgment of what is not yet built.

**User population:**
- Designed explicitly for neurodivergent users (ADHD, autism) — cognitive load reduction is a stated design principle.
- Non-technical users supported via installer and guided onboarding tour.
- Technical users supported via API-first architecture and documented setup.

**Data sensitivity acknowledged:**
- Health data, financial data, personal communications, and image data (vision pipeline) flagged as sensitive.
- Explicit policy required in constitution.yaml before sensitive integrations are enabled.
- Image data processed locally via vision model — descriptions are ephemeral by default (not persisted to vault). See ADR-032.

**User-initiated governance reduction:**
- Bare mode (ADR-028) — user can disable personality layers while preserving safety guarantees. Constitutional review reduced to position_collapse, sycophancy, and non_embellishment only. This is a deliberate user-initiated governance reduction, not a bypass. The user must enable it in app settings first (two-layer gate).
- Stateless mode (ADR-031) — user can disable vault reads/writes per conversation. Constitutional review still fires and outcomes ARE persisted to logs/safety_reviews/ — governance logging is mode-invariant because safety logs are repo-local, independent of vault state.

### Gaps

- No formal stakeholder analysis document.
- No formal impact assessment.
- No documented process for evaluating new capabilities against risk criteria before building.

---

## MEASURE

The Measure function evaluates AI risk and performance through quantitative and qualitative methods.

### What Ember Has

**Testing:**
- 1559 pytest tests covering backend services, retrieval, state, safety, and API endpoints.
- 39 Playwright e2e tests covering UI flows.
- 73 Playwright e2e tests covering installer flows.

**Retrieval evaluation:**
- `tools/eval_retrieval.py` — 14-query benchmark harness measuring retrieval quality across intent classes. Results logged to `logs/`.
- `docs/eval_history.md` — historical eval results across model versions with root cause analysis.

**Commitment detection evaluation:**
- `tools/eval_commitment_detector.py` — 25-case benchmark. Precision 1.00, recall 0.93 at v0.12.0.

**Constitutional review:**
- All review decisions logged with trigger signals, principles applied, and outcomes.
- Review logs inspectable via `tools/view_safety_logs.py`.

**Model evaluation:**
- Cloud models (Haiku, Sonnet) and local models (qwen3:8b) evaluated on the same 6-category quality rubric.
- Known model limitations documented in eval_history.md.

### Gaps

- No formal bias or fairness evaluation.
- No evaluation against external benchmarks (e.g. CIMemories contextual integrity benchmark — planned when system matures).
- No third-party or independent audit.
- Eval harness results reflect the developer's personal vault — not generalizable benchmarks.
- Post-generation coaching filter (ADR-030) introduces false positive risk — legitimate emotional responses may be incorrectly flagged and rewritten by Stage 1 pattern matching. No systematic false positive rate measurement exists yet.

---

## MANAGE

The Manage function addresses risk response, monitoring, and improvement over time.

### What Ember Has

**Operational continuity:**
- Append-only vault — canonical records are never overwritten, always recoverable.
- Rebuild capability — indexes and derived artifacts can be deleted and rebuilt from canonical storage.
- `scripts/audit_memory.py` — vault health check with 7 checks and health score.

**Incident response:**
- `docs/RECOVERY_PLAYBOOK.md` — documented recovery procedures for common failure modes.
- `docs/BACKUP_AND_EXPORT.md` — vault backup and export procedures.
- Known Issues maintained in CLAUDE.md with active tracking.

**Monitoring:**
- Audit logs at `logs/audit/` — all API requests logged as JSON.
- Safety review logs at `logs/safety_reviews/` — constitutional review outcomes. Mode-invariant: logs persist in all conversation modes including stateless (vault off). Safety logs are repo-local, not vault-dependent.
- Retrieval eval run before each release.

**Improvement:**
- Research monitoring practice documented in TDD — sources reviewed at each major release boundary.
- Watch Items section in TDD tracks relevant research with attribution.
- ADR practice ensures architectural improvements are documented and traceable.

**Supply chain:**
- No axios dependency — native fetch used throughout.
- Confirmed unaffected by March 2026 axios supply chain attack.
- Dependency security policy documented in CLAUDE.md.

### Gaps

- No automated post-deployment monitoring beyond audit logs.
- No formal process for users to report issues beyond GitHub Issues.
- No SLA or uptime commitment (single-user local deployment — not applicable in current form).
- Clean install testing on fresh hardware is a known gap due to developer hardware constraints.

---

## Summary

| Function | Coverage | Key Gaps |
|---|---|---|
| Govern | Strong | No formal org governance, no third-party audit |
| Map | Moderate | No formal stakeholder analysis or impact assessment |
| Measure | Moderate | No bias evaluation, no external benchmarks |
| Manage | Moderate | No automated monitoring, no formal incident process |

Ember's strongest alignment is with the Govern function — constitutional review, ADR practice, explicit privacy architecture, and user data ownership are genuine governance artifacts, not checkboxes.

The primary gaps are in formal evaluation (Measure) and systematic monitoring (Manage), both of which are appropriate for a single-user local deployment at this stage and will be addressed as the system matures and the user base grows.

---

*This document is updated at each major release. Last updated: v0.17.1, April 2026.*
