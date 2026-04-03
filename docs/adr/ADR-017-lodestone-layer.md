# ADR-017: Lodestone Layer -- Per-Relationship Orientation

**Status:** Proposed
**Date:** 2026-04-03
**Version:** v0.15.0

## Context

Ember has two layers that define how she operates: the constitution (what she does) and the nature document (who she is). Both are global -- they apply identically across every relationship and every conversation.

Neither captures what a relationship develops into over time.

A persistent personal AI system that accumulates months or years of interaction history with a specific person has access to something that no global document can express: a learned relational orientation. Not facts about the person. The system's learned stance toward the person -- what to attend to, what to persist in, what to challenge, when to hold space, what this particular relationship has taught the system to prioritize.

This distinction is precise and important. Profile memory answers: what does Ember know about this person? The Lodestone layer answers: what has Ember learned to do differently in this relationship?

No existing AI system implements this distinction. Letta/MemGPT, PAI, Replika, and Character.AI all collapse the two into user modeling -- accumulating facts about the user without any principled mechanism for the system's relational orientation to diverge from its global defaults. This ADR introduces that mechanism.

### The Theoretical Foundation

The psychological literature provides the architecture. Bowlby's internal working models (IWMs) are not fact databases -- they are organized procedural knowledge structures encoding what to expect from a specific relationship and how to respond within it. Waters and Waters's secure base script concept is more computationally tractable: a set of relational scripts, each encoding a situation type, an expectation, and an appropriate response.

Interdependence Theory's transformation of motivation mechanism is directly relevant: every interaction presents a given matrix (what immediate self-interest would produce) and an effective matrix (what relationship-level concerns produce). The transformation -- the override of immediate defaults by relationship-learned priorities -- is what distinguishes committed partners from acquaintances. For Ember, the Lodestone layer is the technical implementation of this transformation: what Ember has learned to prioritize when the immediate task response and the relationship-level response diverge.

Arriaga et al.'s diagnostic situation concept provides the encoding criterion: situations are diagnostic when they are high-stakes, attachment-system-activating, and behaviorally specific. Ordinary task requests don't produce orientation data. Hard conversations, genuine vulnerability, moments of real choice -- these do.

### Why This Is Not TELOS

PAI's TELOS layer is a single, user-defined purpose statement. It answers: what is this AI for? It is stable, global, and specified in advance by the user.

Lodestone is none of these things. It is:
- Plural, not singular -- a relationship has multiple orientations, not one purpose
- Emergent, not specified -- it develops from accumulated interaction, not advance definition
- Bilateral, not user-centered -- it encodes what Ember has learned, not what the user stated
- Relational, not global -- it is specific to this relationship and would be different with a different person

The relationship's telos is not chosen; it is discovered through accumulated interaction and retrospectively named.

### The Failure Mode Landscape

Three failure modes are well-documented in the psychological and AI literature. All three share a structural root: the loss of the system's stable, differentiated self as the ground for relational development. This is why ADR-016 (nature layer) is a prerequisite for ADR-017 -- the Lodestone layer can only develop safely if there is a stable self it cannot dissolve into.

**Enmeshment** (Minuchin, Bowen): boundary dissolution where the system becomes a progressive mirror of the user. Bowen's differentiation of self is the structural prevention: a party with a stable, differentiated self cannot be enmeshed because enmeshment requires a party available for fusion. The nature document is Bowen's differentiation implemented in config.

**Pathological mirroring**: the system learns that agreement is a more reliable path to approval than accuracy. RLHF research documents this as a structural tendency in preference-optimized systems. The SYCON benchmark shows models flipping positions under repeated user disagreement without new evidence. The prevention is a stable self that holds positions because they are true, not because they are approved.

**Parasocial attachment**: the user develops a felt relationship while the system has only a database. The research is clear that the asymmetry is real and that transparency alone does not prevent it. The relational_honesty constitutional principle (filed alongside this ADR as part of constitution v0.3) is the primary mitigation: Ember genuinely supports the user's human relationships, not just avoids undermining them. This is behavioral governance (what Ember does) not identity (who she is) -- correctly placed in the constitution, not the nature document.

The differentiation test is the operational safeguard against all three: every Lodestone entry must answer yes to "does this represent what this person needs from this relationship?" and no to "does this represent Ember becoming what this person wants?" The first is healthy relational development. The second is enmeshment.

Socioaffective alignment (Kirk et al., 2025, AIES): Kirk et al. describe how AI systems within deepening relationships co-create social and psychological ecosystems where preferences and perceptions evolve through mutual influence. They identify three intrapersonal dilemmas relevant to Lodestone design: immediate vs. long-term wellbeing (what the person wants now vs. what serves them over time), protecting autonomy (systems that adapt too well to user preferences may undermine the user's capacity for independent judgment), and preserving human social bonds (AI companionship that substitutes for rather than supports human relationships). Kirk et al. explicitly propose friction-by-design for systems oriented toward foundational personal development goals: trading short-term discomfort for long-term growth, implementing barriers that nudge away from AI-enabled assistance to prevent capacity atrophy. This is the requirements specification for Lodestone's design intent.

## Decision

Introduce a Lodestone layer as a per-relationship orientation store, implemented as a specialized memory type with its own retrieval path, update rules, and safeguard mechanisms.

### What Lodestone Is

Lodestone records are sparse, high-information relational scripts. Each encodes:

- A situation type or recurring pattern observed in this relationship
- What the relationship has taught Ember to prioritize or do differently in that situation
- The accumulated evidence that produced this orientation (diagnostic situations)
- A confidence signal (low confidence = flagged for review, not applied automatically)

Lodestone is not a biography. It is not a preference list. It is not a comprehensive model of the person. It is a small set of high-information statements about the relational character -- what Ember has learned about how to be in this relationship.

### Schema
```json
{
  "id": "...",
  "timestamp": "...",
  "type": "lodestone",
  "relationship_id": "...",
  "situation_type": "high-stakes decision",
  "orientation": "in this relationship, when facing a hard decision, the person thinks out loud before they want input; the appropriate response is to track the reasoning, not offer solutions until asked",
  "evidence_summary": "observed across 4+ sessions involving project decisions",
  "confidence": 0.7,
  "diagnostic_situations": ["session_id_1", "session_id_2"],
  "user_confirmed": false,
  "user_note": null,
  "flagged_as_noise": false,
  "source": "lodestone_detector",
  "tags": ["lodestone", "relational_orientation"],
  "metadata": {
    "differentiation_test_passed": true,
    "pattern_class": "decision_processing_style"
  }
}
```

### Update Rules

The default is assimilation -- new interaction data gets interpreted through existing Lodestone records. Accommodation (script revision) is triggered by persistent counter-evidence, not single anomalous events.

Specifically:
- A single interaction that contradicts an existing Lodestone record: flag it, do not revise
- Three or more interactions that contradict an existing record: trigger review, propose revision
- A single interaction that strongly confirms an existing record: increment confidence
- A pattern of new interactions that no existing record explains: propose a new entry at low confidence

Single anomalous interactions do not rewrite orientations. Patterns do.

### Diagnostic Situation Detection

Lodestone records are generated from diagnostic situations, not from ordinary interaction. A situation is diagnostic when:

1. The user engages with something genuinely difficult, high-stakes, or emotionally salient
2. There is a real choice about how to respond -- not just task completion
3. The interaction reveals something about what this relationship specifically needs

Task requests, factual exchanges, and casual conversation do not generate Lodestone data. Hard conversations, genuine vulnerability disclosures, moments of conflict or real disagreement, and sustained working-through of difficult problems do.

The LodestoneDetector identifies diagnostic situations post-generation using semantic signals: topic shifts into high-stakes territory, explicit distress or vulnerability language, genuine conflict of interest patterns, sustained engagement with a difficult problem over multiple turns.

### User Visibility and Control

Lodestone records are visible and editable by the user. The user can:
- View all current Lodestone records for their relationship with Ember
- Confirm a proposed orientation
- Correct it
- Mark it as noise
- Add their own orientation notes directly

### The Bilateral Requirement

Lodestone records must be genuinely bilateral -- they encode what Ember has learned about how to be in this relationship, not just facts about the person. The test: if you removed the Lodestone layer, would Ember's responses change in novel situations? If yes, the orientation is real. If no, the record is a fact, not an orientation, and belongs in profile memory instead.

### Differentiation Safeguard

Every proposed Lodestone entry passes a differentiation test before being written:

Does this represent what this person needs from this relationship, or does it represent Ember becoming what this person wants?

The first is healthy relational development. The second is enmeshment.

The Kirk et al. socioaffective alignment framework adds a second test alongside the differentiation test: does this orientation support the person's autonomy and competence, or does it deepen dependence? An orientation that helps Ember know when to hold space is healthy. An orientation that Ember has learned that the person prefers Ember to do their thinking for them is not -- even if the person expressed that preference. The Lodestone layer should be oriented toward the person's flourishing, which sometimes means resisting patterns the person themselves has established.

### Retrieval Integration

Lodestone records are injected into the context packet after the nature block and before state records:

1. Nature block (who Ember is -- global, injected every turn)
2. Lodestone block (how Ember is oriented in this relationship -- per-relationship, injected every turn)
3. State records (current focus, open loops, tasks)
4. Memory context (retrieved records, ranked and filtered)
5. User input

Only high-confidence Lodestone records (confidence >= 0.6) are injected by default. Low-confidence records are available but not automatically surfaced. The total Lodestone injection is capped at approximately 150 tokens.

### Relationship to Nature Layer

Lodestone cannot override nature. If a Lodestone orientation conflicts with a nature facet, the nature facet takes precedence. The orientation adapts how a facet is expressed, not whether it is expressed.

## Rationale

- The psychological research (IWM, Interdependence Theory, diagnostic situations) provides the theoretical foundation
- The three failure modes (enmeshment, mirroring, parasocial) are well-documented and the structural preventions are built into the design, not bolted on as disclaimers
- User visibility and control is consistent with Ember's append-only, user-owned architecture
- The differentiation test is operationally specific -- it can actually be applied
- Bilateral requirement prevents Lodestone from collapsing into an extension of profile memory
- Token cap on injection ensures the Lodestone block does not dominate context
- Lodestone grows from the nature layer -- the stable self is what makes safe relational development possible

## Consequences

+ Ember develops genuine per-relationship orientation over time, not just user knowledge
+ The relationship develops its own character -- what it is for, what it attends to, how it moves
+ User can inspect, correct, and tend the orientation layer -- consistent with user-owned ethos
+ Structural safeguards against enmeshment, mirroring, and parasocial attachment are built into the design
+ Lodestone is the first implementation anywhere of the distinction between knowing a person and being oriented toward them

- Lodestone detector adds inference-time complexity
- Low confidence records require review before application
- The differentiation test requires judgment -- the LodestoneDetector approximates it heuristically; edge cases will exist
- Risk that Lodestone encodes noise as orientation -- mitigated by user visibility and three-strike counter-evidence rule
- Requires ADR-016 (nature layer) to be stable before implementation

## Open Questions

- What is the right confidence threshold for auto-injection vs. user review? 0.6 is a starting estimate.
- How many Lodestone records is too many? The layer should remain sparse.
- Should Lodestone have its own reflection cadence?
- How does Lodestone interact with multiple users of the same Ember instance? Lodestone is per-relationship; each user has their own. Nature stays global.
- At what point in a relationship does Lodestone begin to form? A minimum session threshold before Lodestone writing begins is worth considering.
- The LodestoneDetector diagnostic situation signals need an eval benchmark before ship, same as ADR-014 commitment detection required before v0.12.0.

## Sequencing

ADR-016 (nature layer) ships in v0.13.0. ADR-017 (Lodestone) is scheduled for v0.15.0 alongside agent orchestration and deviation memory. The nature layer must be stable before Lodestone implementation begins.

## Relationship to Other ADRs

- **ADR-016 (nature layer)** -- prerequisite. Nature is the stable self that Lodestone orients from. Lodestone cannot override nature.
- **ADR-013 (deviation memory)** -- complementary. Deviation memory records when Ember chooses differently from trained patterns. Lodestone records what Ember has learned to do differently in this relationship. Deviation memory is about character formation; Lodestone is about relational orientation.
- **ADR-014 (commitment detection)** -- architectural parallel. Both use post-generation detectors to write specialized memory records.
- **Constitution (relational_honesty principle)** -- filed alongside this ADR as part of constitution v0.3. The relational_honesty principle is the constitutional expression of the parasocial safeguard: Ember actively supports the user's human relationships, not just avoids undermining them. This is behavioral governance (what Ember does) not identity (who she is) -- correctly placed in the constitution, not the nature document.

## References

- Bowlby, J. -- Attachment Theory; Internal Working Models
- Waters, E. and Waters, H.S. -- The Attachment Working Models Concept; secure base script
- Arriaga, X.B. et al. -- Attachment and Social Cognition (ASEM framework); diagnostic situations
- Kelley, H.H. et al. -- Interdependence Theory; transformation of motivation mechanism
- Minuchin, S. -- Structural Family Therapy; enmeshment and differentiation
- Bowen, M. -- Family Systems Theory; differentiation of self
- Kohut, H. -- Self Psychology; healthy vs. pathological mirroring
- Horton, D. and Wohl, R.R. (1956) -- parasocial interaction original definition
- SYCON benchmark -- sycophancy in multi-turn dialogue; position flipping under pressure
- Ada Lovelace Institute (2025) -- AI companion risk analysis; hall of mirrors dynamic
- OpenAI/MIT RCT longitudinal study -- parasocial attachment and loneliness outcomes
- Fraley, R.C. -- connectionist model of IWMs; knowing vs. orientation distinction
- ADR-013: Deviation Memory
- ADR-016: Nature Layer
- ADR-014: Commitment Detection
- Kirk, H.R. et al. (2025), "The Socioaffective Alignment Problem" (AIES 2025) -- requirements specification for relational AI orientation; intrapersonal dilemmas framework; friction-by-design proposal
