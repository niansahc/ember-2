# Known Issues — Ember-2

Active as of v0.16.0. Fixed issues are removed from this file; see git history for closed bugs.

---

- **Installer Node.js prerequisite check** — A user bypassed the Node.js prereq check (mechanism unknown; Node IS displayed on the prereqs screen and Next is disabled when missing). Needs investigation.

- **State awareness hallucinations** — Model embellishes when state records are noisy or stale. Partially addressed by `STATE_STALENESS_DAYS` filter. Longitudinal monitoring needed; deeper fix deferred to v0.17.0.

- **Preference expression partial deflection** — Identity rules reduced "I'm an AI" deflection but did not eliminate it. Model capability ceiling on qwen3:8b for some identity questions.

- **API requires restart after code changes** — The API must be restarted after any backend code change for it to take effect. Changes to task detection, prompt building, or any `src/` file do not hot-reload in production mode. Run `./start_api.bat` or kill and restart uvicorn after deploying changes.

- **Clean install testing gap** — Clean install testing is a known gap due to hardware constraints. Documented in runbook.

- **Mac/Linux installer untested on real hardware** — Mac/Linux installer code paths have not been tested on physical machines.

- **Constitutional review service context blindness** — `ResponseReviewService` receives only `user_message` and `draft_response` at review time. No vault memory, no context packet, no conversation history. The reviewer cannot distinguish a hallucinated claim from a vault-grounded one, and cannot assess whether draft confidence is warranted by retrieved evidence. Architectural gap — requires passing `ContextPacket` into `SafetyReviewContext`.

- **flourishing_over_preference v0.2 cross-session gap** — Constitution v0.7 uses a four-condition fire gate (stated value, clear conflict, not already named in session, agency intact), defaults to silence under uncertainty, and only fires against stated values rather than inferred ones. Cross-session pattern detection is out of scope because the review service has no vault memory access (see "Constitutional review service context blindness" above). If that architectural gap is closed, the fire conditions may need to expand to include cross-session observation.

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
