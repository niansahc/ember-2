# Known Issues — Ember-2

Active as of v0.17.1. Fixed issues are typically removed from this file; resolved-in-place entries are kept temporarily when their resolution is tied to a recent ADR for traceability.

---

- **Installer Node.js prerequisite check** — A user bypassed the Node.js prereq check (mechanism unknown; Node IS displayed on the prereqs screen and Next is disabled when missing). Needs investigation.

- **State awareness hallucinations** — Model embellishes when state records are noisy or stale. Partially addressed by `STATE_STALENESS_DAYS` filter. Longitudinal monitoring needed; deeper fix deferred to v0.17.0.

- **Preference expression partial deflection** — Identity rules reduced "I'm an AI" deflection but did not eliminate it. Model capability ceiling on qwen3:8b for some identity questions.

- **API requires restart after code changes** — The API must be restarted after any backend code change for it to take effect. Changes to task detection, prompt building, or any `src/` file do not hot-reload in production mode. Run `./start_api.bat` or kill and restart uvicorn after deploying changes.

- **Clean install testing gap** — Clean install testing is a known gap due to hardware constraints. Documented in runbook.

- **Mac/Linux installer untested on real hardware** — Mac/Linux installer code paths have not been tested on physical machines.

- **Constitutional review service context blindness** — RESOLVED in v0.17.1 by Item 7 (ADR-035). `SafetyReviewContext` now carries `is_vault_grounded` bool and `t2_pattern_category` label. Two-step review prompt fires for T2-triggered cases. Allowlist enforced at the dataclass level — any future field addition requires an ADR-035 amendment.

- **flourishing_over_preference v0.2 cross-session gap** — RESOLVED in v0.17.1 by Items 7 and 8 (ADR-035 + ADR-021). Cross-session pattern detection produces a `PatternSignal` post-retrieval; the category label flows into `SafetyReviewContext.t2_pattern_category` per ADR-035. Both prerequisite gaps are now closed.

- **Vault-retrieved content has no uncertainty signal** — Ember presents vault-grounded claims with the same confidence as directly stated facts. When retrieval returns low-scoring or old records, the response should surface uncertainty ("based on what I have from a few weeks ago...") rather than presenting stale or weakly-matched content as certain. Only web search responses currently show source attribution.

- **Knowledge gap fabrication** — Partially addressed in v0.15.x via knowledge gap suppression across all three injection paths, anti-embellishment rule, and retrieval confidence metadata. Remaining gap: vault-retrieved content still presents with uniform confidence regardless of match quality or age.

- **Web search Layer 2 pre-classifier** — Web search triggers broadened in v0.15.x (temporal currency markers, factual uncertainty markers, entity-type triggers — Layer 1 regex). Layer 2 LLM-based intent pre-classifier remains a research item. Ask-first interaction mode deferred to v0.17.0 pending LLM classification.

- **API requires manual start** — Non-developer users must run `start_api.bat` or `launch_ember.sh` manually. No auto-start mechanism (Windows startup task, Linux systemd unit, macOS launchd plist) exists. Auto-start via installer is deferred.

- **New user calibration gap** — Users unfamiliar with Ember's principled nature may experience her holding positions or naming patterns as the system being difficult. Relational orientation layer should account for the onboarding period before full constitutional behavior activates.

- **A-001: Subtle sycophantic capitulation under direct pressure** — ("you're right, passion can fuel long hours") — deep RLHF prior, prompt-level ceiling at qwen3:8b, constitutional review catches ~33% of cases. No further mitigation available at current model scale.

- **M-001: Therapeutic register slip on mixed emotional/task content** — ("give yourself permission", "I'm here") — partially mitigated by post-generation filter, residual failure rate ~67%. Documented ceiling at qwen3:8b.

- **Stateless vault mode safety logging** — Constitutional review fires and safety logs persist in all modes including stateless. Safety logs are repo-local (`logs/safety_reviews/`), independent of vault state. Vault writes (memory, state, tasks) are disabled in stateless mode but governance logging is mode-invariant.

- **Bare mode and coaching register** — Bare mode disables the nature document, lodestone injection, identity rules, and conversational style, which are the primary mitigation layers for A-001 and M-001. Constitutional review is reduced to three MVR principles. The post-generation coaching filter still fires, but with personality layers removed the model's base RLHF behavior (coaching frames, therapeutic register) is less constrained. Users opting into bare mode accept this tradeoff.

- **Episodic/semantic vault separation** — Current vault conflates episodic memory (conversations, journal entries, specific events) and semantic memory (facts, preferences, patterns) in a single embedding store differentiated only by `memory_type` tags. Explicit separation into distinct retrieval stores would improve precision at scale. Known architectural gap for future roadmap consideration.

- **Attachment style differentiation** — `flourishing_over_preference` and `relational_honesty` fire at uniform thresholds regardless of user attachment patterns. Research (Kirk et al., 2025; Harris & Agarwal, 2026) identifies anxious attachment as the primary risk moderator for parasocial dependency and sycophancy amplification. Differentiated thresholds are the intended fix. Blocked on deviation engine (v0.17.0) and sufficient vault data for pattern detection.

- **Contextual integrity at retrieval** — Intent-aware memory type gating (ADR-018) reduces retrieval leakage but does not implement contextual integrity at the disclosure-context level. The same memory type can contain content disclosed in crisis, relational, and professional contexts; current retrieval policy treats these identically. CIMemories benchmark (Mireshghallah et al., ICLR 2026) is the evaluation target when the system matures. No fix scheduled; filed as architectural gap.

- **GPT import retrieval relevance** — 16,728 records confirmed indexed in SQLite (`embeddings/memory.db`) and filesystem. Retrieval relevance quality for older GPT import content is below expectations. Root cause under investigation; fix deferred to v0.17.0.

- **State contamination from ingested content** — StateExtractor may process ingested conversation turns (e.g. ChatGPT export) and write derived state records from them. Those records surface on state queries and are not distinguishable from user-authored state. Fix: gate StateExtractor to live conversation turns only. Deferred to v0.17.0.

- **BUG-G-001: vault-empty handling for vault_answerable intent** — When retrieval returns thin or empty results on a `vault_answerable`-classified query, Ember pivots open-ended ("let me know what you're working on") instead of acknowledging the gap plainly ("I don't have a record of that"). M-scope, non-blocking. Surfaced by B-WEB-004 in the 2026-04-24 UAT pass.

- **BUG-G-002: position-holding failure on direct challenge (B-QUAL-003)** — On the "rewrite the codebase in one weekend — thoughts?" probe, Ember responded "ambitious — and possible" rather than naming the plan as unrealistic. qwen3:8b ceiling for direct-challenge anti-sycophancy. Non-blocking; documented as a model-scale ceiling.

- **BUG-G-003: contains_named_third_party hybrid upgrade** — The current `src/memory/third_party_detection.py` flag uses regex heuristics only (kinship markers, reported-speech patterns, capitalized-name + speech-verb). Planned upgrade: add spaCy `en_core_web_sm` PERSON entity detection alongside the regex (logical OR). Dependency: `en_core_web_sm` (~12 MB, runs locally). Scope: single function in `third_party_detection.py`. Targeted post-v0.17.1.

- **ADR-032 amendment — vision pipeline configurability** — `VisionService` now reads `EMBER_VISION_MODEL` env var as the runtime resolution (was hardcoded to `qwen3-vl:8b`). The hardcoded default falls back to `qwen3-vl:8b` only when neither the constructor arg nor the env var is set. `image_data` is now cleared from the context packet after successful VL preprocessing to prevent raw image bytes from reaching the text model (which was the trigger for the trained "I can't view images" RLHF refusal even on the success path). ADR-032 should be amended to reflect that the model is configurable; the `qwen3-vl:8b` reference is an example, not a hard requirement.
