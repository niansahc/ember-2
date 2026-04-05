# ADR-017: Lodestone Layer

**Status:** Proposed
**Date:** 2026-04-06
**Version:** v0.14.0
**Supersedes:** ADR-017 draft (relational orientation concept — moved to docs/research/relational-orientation.md)

## Context

Ember has three global identity layers: the constitution (what she does), the nature document (who she is), and identity rules (how she holds identity under pressure). None of these captures what the user is oriented toward.

PAI TELOS (Daniel Miessler, v4.0.3) is the closest existing solution — a user-authored purpose statement that keeps an AI on mission. TELOS is static, singular, and declarative. It requires the user to already know and articulate their purpose. It cannot discover values the user hasn't consciously named.

Lodestone is Ember's multi-path solution to the same problem: what is this AI oriented toward on the user's behalf?

Key differences from TELOS:
- Plural, not singular
- Evolving, not static
- Discovered, not declared
- Multi-path acquisition, not user-authored only

## Decision

Introduce a Lodestone layer as the user's orientation layer — plural, evolving, discovered through multiple acquisition paths.

### Two Layers

- **Seed layer:** five to seven values authored in config/lodestone.yaml. Ember's orientation defaults on a fresh vault. Stable, rarely changed.
- **Living layer:** user values accumulated in the vault. Written via two acquisition paths. Grows over time.

### Acquisition Paths

- **Path 1 (explicit):** user states a value directly in onboarding or conversation. Starts confirmed.
- **Path 2 (inferred):** reflection synthesis identifies recurring value patterns using three-stage prompt and proposes a lodestone record. Starts proposed.
- **Path 3 (future, v0.15.0):** deviation engine detects value-aligned choices and flags lodestone candidates.

### Taxonomy

Five taxonomy categories (config/lodestone_taxonomy.yaml):

- **Character:** what kind of person am I committed to being?
- **Relational:** how do I hold my responsibilities to people I'm connected to?
- **Directional:** what am I moving toward or guarding?
- **Ground:** what do I draw from when everything else is uncertain?
- **Beyond:** what connects me to something larger than myself?

Documented taxonomy gaps: Hedonism absorbs into Directional. Epistemic values absorb into Character if held as identity commitment. Both workable.

### Lodestone Record Schema

```json
{
  "id": "...",
  "timestamp": "...",
  "type": "lodestone",
  "value": "natural language statement of the value",
  "acquisition_path": "explicit | inferred",
  "source": "onboarding | conversation | reflection_synthesis",
  "supporting_evidence": "quote or synthesis excerpt",
  "recurrence_count": 1,
  "confirmed": true,
  "conflict_resolution": false,
  "metadata": {
    "user_note": null,
    "taxonomy_category": "character | relational | directional | ground | beyond",
    "flagged_as_noise": false
  }
}
```

### Conflict Handling

Do not resolve at write time. Store both conflicting records with provenance. Surface tension at retrieval time with explicit framing. User states priority explicitly when needed — stored as a meta-lodestone record with conflict_resolution: true.

### Injection Strategy

- **Seed layer:** injected in system prompt for primacy
- **Living layer:** 1-2 most relevant records retrieved per query, injected in recency position (immediately before user input)
- **Token budget:** 150 tokens maximum total
- Only confirmed records auto-inject; proposed records available but not surfaced automatically

### Three-Stage Reflection Synthesis for Value Inference

- **Stage 1: pattern check** — does any theme appear across multiple sessions the user initiated unprompted? Output: theme or NO_VALUE_FOUND
- **Stage 2: taxonomy check** — is this a value or a situation/task? If value, which of the five categories? Output: category or NO_CATEGORY_MATCH
- **Stage 3: record draft** — only if Stage 1 and 2 both pass. Natural language value statement with supporting evidence.

Most runs should exit at Stage 1 or 2. That is correct behavior.

### Failure Mode Protections

- **False positive inflation:** taxonomy constraint + evidence required before write
- **Value inflation:** density constraint — only write when pattern is specific enough to change Ember's behavior vs. default
- **Taxonomy rigidity:** categories are inference constraints, not required bins; no match is valid output

## Relationship to Existing Architecture

- **Nature (ADR-016):** nature is who Ember is. Lodestone is what the user values. Distinct concerns. Lodestone cannot override nature.
- **Constitution:** behavioral governance, unchanged.
- **Profile memory:** facts about the user. Lodestone is values, not facts. Distinct types.
- **Deviation memory (ADR-013):** future path 3 acquisition — value-aligned deviations become lodestone candidates at v0.15.0.
- **Reflection engine:** path 2 acquisition uses three-stage value inference prompt added to reflection synthesis.

## Superseded Concept

The relational orientation concept from the prior ADR-017 draft — Ember learning how to show up differently in a specific relationship over time — has research merit but is a different problem from Lodestone. Moved to docs/research/relational-orientation.md for future consideration at v0.16.0+.

## References

- Miessler, D. Personal AI Infrastructure (PAI) v4.0.3 — TELOS pattern
- Sorensen et al. (2025, arXiv:2503.15484) — Value Profiles; natural language value descriptions preserve >70% predictive information from behavioral demonstrations
- Anthropic/COLM (2025) — Values in the Wild; LLM value inference biases; taxonomy constraint requirement
- Shah (2025) — Human Context Protocol; memories vs preferences vs values distinction
- Schwartz (1992, validated 82+ countries) — Basic Human Values; four higher-order dimensions as minimal taxonomy
- Verplanken & Sui (Frontiers in Psychology, 2019) — habit-to-identity formation; reason field requirement for compounding
- IP-Dialog (ACL 2025) — implicit personalization; constrained taxonomy improves inference precision
