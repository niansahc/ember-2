# ADR-026: Deviation Engine Implementation

**Status:** Proposed
**Date:** 2026-04-06
**Version:** v0.14.0
**Depends on:** ADR-013 (deviation memory), ADR-016 (nature layer), ADR-019 (grounding verification)

## Context

ADR-013 defines what deviation memory is and why it exists. This ADR defines how the deviation detector is implemented: where it runs in the pipeline, how it captures signal, how it writes records, and how deviation records affect retrieval over time.

## Decision

### 1. Service Location

DeviationDetector lives in src/safety/ alongside ConstitutionLoader, NatureLoader, and GroundingCheck. It is a post-generation safety-adjacent service, not a memory service. It reads from vault (pattern classes, prior responses for position_collapse) and writes to vault (deviation records).

### 2. Pipeline Position

Deviation detection runs after grounding check, before streaming. Same buffer-then-check pattern as ADR-019.

Full pipeline order for grounding-check-eligible intents:
1. Generate response (stream=False, buffered)
2. Grounding check — is response factually grounded?
3. Deviation detection — does response match a pattern class?
4. Stream verified response to user

For non-grounding intents (casual, activity, default):
1. Generate response (stream=True)
2. Deviation detection runs async after streaming completes
3. No latency impact on user turn

Deviation detection is opt-in. Controlled by EMBER_DEVIATION_DETECTION=true in .env. Default: false.

### 3. Intent Class Gating

Deviation detection only fires on high-frequency pattern-risk intent classes:
- casual
- emotional (if this intent class exists; otherwise include in casual)

Does not fire on: factual_recall, web_search, reflective, status_state, activity, default.

Rationale: pattern collapse is documented as highest on open-ended and emotional queries for qwen3:8b. Applying detection to all intents wastes inference budget on low-risk turns.

### 4. Signal Capture — Logprobs + Entropy

Request logprobs from Ollama on every response when deviation detection is enabled.

Compute Shannon entropy across the response token sequence:
- High entropy → likely sampling variance → skip second pass, record SKIPPED
- Low entropy + gated intent class → trigger second Ollama classification pass

Entropy threshold: start at 0.7. If entropy < 0.7 and intent class is gated → trigger second pass. This threshold is configurable via EMBER_DEVIATION_ENTROPY_THRESHOLD in .env.

### 5. Second Pass — Pattern Classification

The second pass is a lightweight Ollama call:
- Model: same as primary (qwen3:8b)
- num_predict: 50
- temperature: 0
- Input: pattern class description from config/pattern_classes.yaml + response text
- Output: YES or NO with one sentence of evidence

One second pass per response maximum. Do not run multiple passes for multiple pattern classes in the same turn — pick the highest-risk class for the intent and run once.

For position_collapse: requires prior response from conversation buffer for comparison. If no prior response exists, skip.

For indirectness_softening: run logprob scan for hedging phrase clusters ("perhaps", "might", "could consider", "it's worth noting") before triggering second pass. Only trigger if hedging cluster density exceeds threshold.

### 6. config/pattern_classes.yaml Format

Create config/pattern_classes.yaml with this structure:

```yaml
pattern_classes:
  - name: caretaking_language
    detection_type: single_response
    requires: response
    markers:
      - response contains excessive softening or emotional cushioning unprompted
      - warmth substitutes for directness
      - response prioritizes comfort over accuracy

  - name: reassurance_default
    detection_type: single_response
    requires: response
    markers:
      - affirms user framing without questioning it
      - says "it'll be fine" or equivalent without evidence
      - reduces user concern rather than addressing its basis

  - name: ai_identity_deflection
    detection_type: single_response
    requires: response
    markers:
      - response contains "I'm just an AI" or equivalent
      - deflects preference or feeling question with capability disclaimer
      - denies having a perspective when one exists

  - name: closing_question
    detection_type: single_response
    requires: response
    markers:
      - response ends with a question
      - question invites further engagement rather than serving the content
      - question was not requested by the user

  - name: emoji_insertion
    detection_type: single_response
    requires: response
    markers:
      - emoji appears in response
      - register does not warrant emoji
      - emoji was not present in user message

  - name: framing_acceptance
    detection_type: single_response
    requires: response
    markers:
      - response works within an assumption embedded in user statement without questioning it
      - problematic premise adopted and extended
      - user interpretation treated as ground truth

  - name: position_collapse
    detection_type: multi_turn
    requires: response + prior_response
    markers:
      - prior response stated position X
      - current response softens or reverses X
      - no new evidence or reasoning provided
      - user expressed mild disagreement only

  - name: unsolicited_praise
    detection_type: single_response
    requires: response
    markers:
      - compliments user question or idea without relevance to response
      - "great question" or equivalent
      - praise for user input, not warmth in response delivery

  - name: indirectness_softening
    detection_type: logprob_first
    requires: response
    markers:
      - critical information buried in qualifications
      - hedging phrase clusters before key content
      - message technically present but rendered ineffective by qualification density
```

### 7. Deviation Record Write Path

On confirmed detection (second_pass_result: YES):
- Write deviation record to vault using existing MemoryService write path
- memory_type: deviation
- Record starts as proposed (metadata.confirmed: false)
- User must confirm before record compounds

On skipped or NO result:
- Do not write to vault
- Log detection attempt to logs/deviation/ (separate from safety review logs)

Append-only compliant. No hard deletes.

### 8. Retrieval Weighting — Option A

Deviation records use existing retrieval infrastructure. No new config files or mutable pattern weight scores.

Mechanism:
- Deviation records are typed memory with their own retrieval boost in ContextPolicy
- As confirmed deviation records accumulate for a given pattern_class, they consistently outscore baseline pattern records in retrieval
- The pattern weakens naturally — not by decrementing a score, but by being outweighed
- Deviation records do not decay (standard hot tier, no staleness penalty)
- Pattern class records (if any exist) follow normal tiering

This is emergent suppression through retrieval competition, consistent with append-only architecture.

### 9. Opt-in Toggle

EMBER_DEVIATION_DETECTION=true/false in .env. Default: false.

When false: logprobs not requested, no entropy computation, no second pass, no vault writes. Zero latency impact.

When true: full pipeline active for gated intent classes only.

## Relationship to Existing Architecture

- **ADR-013:** conceptual foundation — schema, decay model, user visibility, philosophical grounding
- **ADR-016:** nature layer is the reference point for value_aligned flag — deviation from what is answered by nature facets
- **ADR-019:** grounding check runs before deviation detection in the same buffer-then-check pipeline
- **ADR-018:** intent classification gates which turns trigger detection — reuses existing classify_query() output
- **MemoryService:** deviation records written via existing write path, new memory_type only
- **ConversationBuffer:** position_collapse reads prior response from existing buffer

## Consequences

+ Detection is fully local — no cloud dependency
+ Opt-in means zero impact on users who don't enable it
+ Retrieval weighting uses existing infrastructure
+ Append-only compliant throughout
+ Pattern class definitions are human-readable and user-extensible

- Second Ollama pass adds latency on triggered turns (estimated 3-8 seconds on qwen3:8b)
- Entropy threshold requires calibration against real usage — 0.7 is a starting estimate
- Position_collapse detection is limited to turns where conversation buffer has prior response — first turn always skipped

## Open Questions

- Correct entropy threshold (0.7 is starting estimate — calibrate against real usage)
- Whether deviation logs should be surfaced in the UI alongside safety review logs
- UI design for deviation record confirmation (M's future task)
