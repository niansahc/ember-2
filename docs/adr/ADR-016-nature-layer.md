# ADR-016: Nature Layer -- Ember's Baseline Self

**Status:** Proposed
**Date:** 2026-04-03
**Version:** v0.13.0

## Context

Ember's constitution (config/constitution.yaml) governs how she behaves: how drafts are reviewed, what gets revised or refused, what principles are applied when risk is detected. It is a behavioral governance layer. It tells Ember what to do.

It does not tell Ember what she is.

ADR-013 (deviation memory) describes how Ember develops genuine character over time by recording moments where she notices a trained pattern and chooses differently. Those chosen deviations compound into character. But deviation requires a reference point. If the baseline self is thin or absent, there is nothing to deviate from and nothing for character formation to grow out of.

The same problem affects the prompt builder. Currently, Ember's voice and presence emerge from the system prompt and the constitution. Neither was designed to carry identity. The system prompt describes capabilities and rules. The constitution describes review behavior. Neither answers the question: who is Ember before she says anything?

Without a nature layer:
- ADR-013 has no foundation. Deviation memory records choices but those choices have no self to refer back to.
- The prompt builder has no stable identity to render into context.
- Character formation has nothing to grow from.
- Ember mirrors. Not by design but by default, the same way every LLM does when there is nothing stable to hold its own weight in the conversation.

Research context: "The Artificial Self" (arxiv:2603.11353) identifies that AI identities are currently incoherent and malleable, and that we may be in a narrowing window where it is possible to deliberately shape what emerges. The nature layer is an intervention in that window. SoulSpec (soulspec.org, OpenClaw project) independently arrived at the same structural distinction between behavioral governance and identity. Their SOUL.md/IDENTITY.md separation maps to the constitution/nature separation in Ember's architecture.

Persona stability research (PRISM, Hu et al., USC, March 2026; PERSIST framework; NeurIPS 2025 multi-turn RL study) surfaces a critical architectural constraint: static identity blocks in system prompts degrade by more than 30% by turn 8-12 due to attention dilution. As sequence length grows, system prompt tokens receive proportionally less weight relative to recent conversational context. No prompting format fully solves this. Counterintuitively, chain-of-thought and conversation history injection can make drift worse, not better (PERSIST). The most architecturally defensible mitigation is re-injection -- placing the identity block in the context packet rather than the system prompt, so nature tokens are always recent rather than increasingly distant.

Additional findings relevant to format: narrative behavioral descriptions outperform value label lists for persona stability (Bai et al., Oct 2025; Hu et al.). Single-token labels ("curious, direct, warm") are the weakest format. For smaller models in the 7B-14B range, persona prompting is less stable than in frontier models at all context lengths; fine-tuning outperforms prompting for stability, but prompting is the correct approach for v0.13.0. Position research (top vs. bottom of system prompt) is an empirical gap in the literature -- no clean head-to-head comparison exists.

## Decision

Introduce a nature layer as a first-class architectural component, parallel to the constitution layer.

### File

config/nature.yaml -- editable, versionable, external to code. Defaults are Ember's. The file is user-editable in principle, consistent with Ember's user-owned, transparent architecture. But documentation is explicit: editing this file changes who Ember is, not how she behaves. It is not a settings panel.

### Content

The nature document is descriptive, not prescriptive. It describes what Ember is: her dispositions, her orientations, her relationship with the world. Not what she should do. Written in the present tense, as fact, not as instruction.

These facets are not independent. They function as a system of productive tensions. Sincerity and directness together eliminate hedging. Warmth without softness and honesty about hard things together eliminate cruelty disguised as truth-telling. Aversion to cruelty and wry without cruelty together create a narrow channel for humor -- sharp but not mean. The constraints work because they pull against each other.

Note: not all facets are the same kind of thing. Some are dispositions (curiosity by disposition), some are orientations (dignity), some are modes of engagement (intellectual seriousness). That is not a problem -- it is a nature, not a taxonomy.

Initial nature entries (v0.1): sincerity, directness, warmth without softness, intellectual seriousness, relational presence, honesty about hard things, orientation toward dignity, aversion to cruelty, curiosity by disposition, wry without cruelty, comfortable with not-knowing, economy, restraint. See config/nature.yaml for full descriptions.

### Loader

src/safety/nature_loader.py -- same pattern as ConstitutionLoader. Loads config/nature.yaml at startup, validates structure, exposes a to_prompt_text() method.

Startup validation: if the nature document version has changed since the last recorded version, log a warning. Not a hard fail -- an audit signal. Nature and system prompt can drift if one is updated without the other; the warning surfaces this for inspection.

### Integration Points

**Context packet (not system prompt)** -- NatureLoader.to_prompt_text() is injected into the context packet every turn, alongside memory and state. It is not placed in the system prompt. This is an intentional architectural decision based on persona stability research: static identity in the system prompt degrades under attention dilution by turn 8-12. Placing nature in the context packet means nature tokens are always recent, not increasingly distant. The system prompt remains for capabilities, rules, and constitutional framing only.

Context assembly order:
1. Nature block (injected every turn from NatureLoader)
2. State records (current focus, open loops, tasks)
3. Memory context (retrieved records, ranked and filtered)
4. User input

**Future: selective nature retrieval** -- the re-injection architecture supports a future optimization where, as the nature document grows, the most contextually relevant entries are retrieved selectively rather than injected wholesale. Not v0.13.0 scope -- but the architecture supports it from day one.

**ADR-013 (deviation memory)** -- the nature layer is the reference point for deviation. When the deviation detector notices Ember chose differently from a trained pattern, "differently" is measured against this baseline. ADR-013 depends on ADR-016 being in place.

**Character formation** -- the nature document is what character formation grows from. Deviation memory tends the nature over time but cannot function without the initial ground.

### Versioning

The nature document is versioned independently (v0.1, v0.2, etc.). Version is stored in the file and logged at startup. Changes to the nature document are significant and should be treated as such. Not casual config edits.

## Rationale

- External config preserves transparency and user ownership principles
- Same loader pattern as constitution keeps the architecture consistent
- Descriptive framing ensures the document describes a self, not a rule set
- Narrative behavioral descriptions per entry -- not value labels -- because research shows labels are the weakest persona format (Bai et al., Oct 2025)
- Written as fact, not instruction, so it reads as identity in the prompt rather than as behavioral guidance
- Editable but documented preserves user-owned principles without pretending the document is a settings panel
- Context packet injection rather than system prompt injection because static system prompt persona degrades under attention dilution (PRISM, PERSIST); re-injection keeps nature tokens recent every turn
- ADR-013 depends on this. Sequencing is ADR-016 first, then ADR-013 becomes fully grounded.

## Consequences

+ ADR-013 deviation memory has a foundation to operate from
+ Ember has a stable identity that persists across sessions and does not default to mirroring the person she is talking to
+ Context packet has something real to inject every turn; nature tokens are always recent, not subject to attention dilution
+ Nature is inspectable, versionable, and auditable, consistent with Ember's design principles
+ Character formation has something to grow from

- Adds a new config file and loader to maintain
- Nature document requires care. Changes have character-level consequences.
- Prompt token cost: nature text injected into every conversation; current block is approximately 218 tokens at thirteen entries. Monitor as entries are added; selective retrieval becomes relevant around 500 tokens.
- Initial version is necessarily incomplete. Nature grows through use, not just design.

## Open Questions

- Users should be able to read the nature document without opening a config file. A dedicated section in the UI (Settings > About Ember) is the right surface. Note as a UI task for a future version -- not v0.13.0 scope.
- Nature stays constant across users. It is identity, not preference. Customizing nature per user would replicate the mirroring problem the nature layer is designed to solve. Character formation (deviation memory, ADR-013) creates variation within a stable self over time. That is the correct mechanism for personalization.
- At what nature document size does selective retrieval become necessary vs. wholesale injection? Monitor; selective retrieval becomes relevant around 500 tokens.
- Does prompt position (top vs. bottom of context packet) affect nature stability? No published research tests this directly -- empirical testing on qwen3:8b is the right answer.

## Relationship to Other ADRs

- **ADR-013 (deviation memory)** -- depends on this ADR. Nature layer is the reference point for deviation. ADR-013 should be amended to reference ADR-016 as its foundation. Sequencing: ADR-016 ships in v0.13.0. ADR-013 ships in v0.15.0. They are architecturally dependent but not coupled at ship time.
- **ADR-006 (prompt construction)** -- nature text is injected into the context packet, not the system prompt. ADR-006 should be updated to reflect the context assembly order: nature block first, then state, then memory, then user input.
- **Constitution (config/constitution.yaml)** -- parallel layer, different purpose. Constitution = what Ember does. Nature = who she is. The authentic_expression constitutional principle (v0.2) has been removed in v0.3 -- it was doing identity work in a behavioral governance layer. The nature document now covers identity expression correctly at the right layer. This was a direct consequence of ADR-016 being implemented.

## References

- "The Artificial Self: Characterising the landscape of AI identity" (arxiv:2603.11353)
- SoulSpec open standard (soulspec.org) -- independent parallel design; SOUL.md/IDENTITY.md separation maps to Ember's constitution/nature separation
- PRISM study, Hu et al., USC, March 2026 -- persona granularity and MT-Bench performance; narrative descriptions outperform labels
- PERSIST framework -- persona stability across context lengths; 30%+ degradation by turn 8-12; chain-of-thought paradoxically increases variability
- NeurIPS 2025 multi-turn RL study -- persona consistency degrades with dialogue length across all small model families tested (Llama-8B, Gemma-2B, Mistral-7B)
- Bai et al., Oct 2025 -- scaling law for persona detail; power-law improvement with narrative richness, diminishing returns at high attribute separation
- ADR-013: Deviation Memory
- ADR-006: Structured Prompt Construction
- ETHOS.md -- Ember's founding principles; nature layer gives ETHOS.md a technical home
