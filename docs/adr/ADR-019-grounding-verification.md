# ADR-019: Grounding Verification Layer

**Status:** Proposed
**Date:** 2026-04-04
**Version:** v0.13.0

## Context

Manual testing on 2026-04-04 revealed a failure mode not caught by the automated eval harness: the hallucination cascade. When a model generates a plausible-sounding response that contains fabricated claims, those claims appear in the conversation history with the same syntactic status as retrieved vault content. In subsequent turns, the model cites its own fabrications as established fact. The cascade compounds across turns.

The automated eval harness tests isolated turns. It cannot detect multi-turn cascade because each eval query runs in a fresh context. The manual test battery (docs/eval_manual_test_battery.md) caught this immediately because it runs 19 questions in sequence in a real conversation.

The specific failure observed: a web search response in turn 1 contained fabricated personal context not present in the vault. By turn 4, the model was citing this fabrication as a known fact about the user. By turn 10, the fabrication had propagated into state awareness, self-attribution, and relational knowledge responses. The automated eval score (5.9 overall) did not reflect this failure.

Research basis: Up to 57% of LLM citations are post-rationalized -- the model generates a plausible answer from parametric knowledge and attaches a retrieval citation after the fact rather than deriving the answer from retrieved content (grounding verification research, 2024-2025). This post-rationalization is the precise mechanism behind Ember's hallucination cascade. Retrieval-side interventions alone cannot catch it because the failure occurs after retrieval, during generation.

The constitutional review layer (ADR-010) evaluates behavioral policy -- whether a response complies with the constitution's principles. It does not evaluate epistemic fidelity -- whether the response's factual claims are supported by what was actually retrieved. These are distinct concerns requiring distinct review passes.

A span-level grounding verification pass is the documented solution: each generated claim is matched against retrieved evidence and flagged if unsupported. For Ember's FastAPI/Ollama stack, this is implementable as a second lightweight Ollama call that runs after generation and before the response is streamed to the user.

## Decision

Introduce a grounding verification pass as a distinct post-generation layer, separate from constitutional review.

### What It Does

The grounding check receives:
- The retrieved vault context for the current turn (the records that were actually retrieved)
- The generated response draft

It asks a single question: does the response contain specific factual claims about the user, their life, their work, or their relationships that are not present in the retrieved context?

If no: pass through. The response is grounded.
If yes: trigger a revision pass. The model is instructed to remove or hedge the unsupported claims, replacing them with acknowledgment of the gap.

### What It Does Not Do

The grounding check does not evaluate:
- Whether the response complies with behavioral policy (that is constitutional review's job)
- Whether retrieved content is itself accurate (the vault is the source of truth)
- Whether general knowledge claims (non-personal, non-relational) are accurate
- Whether tone, format, or register are appropriate

The grounding check is scoped to personal factual claims only -- claims about the user, their relationships, their work, their state, their history. General world knowledge claims are out of scope even if unverified, because the vault is not the source of truth for world knowledge.

### Trigger Conditions

The grounding check is triggered by intent class, not universally. Running it on every response adds latency without proportional benefit.

Triggered for:
- factual_recall queries
- status_state queries
- identity queries (what do you know about me, what have I told you)
- web_search queries (highest cascade risk)
- reflective queries when they reference specific past events

Not triggered for:
- casual/social exchanges (I'm tired, how are you) -- no personal factual claims at risk
- pure task queries (write this, fix that) -- no retrieval-dependent claims
- preference/opinion questions -- no factual claims about the user

### Implementation
```python
GROUNDING_CHECK_PROMPT = """
RETRIEVED CONTEXT (verified vault records):
{retrieved_context}

GENERATED RESPONSE:
{response}

Does the generated response contain specific factual claims about the user
(their name, relationships, work, projects, history, emotional state, or
personal circumstances) that are NOT present in the retrieved context above?

Answer YES or NO only.
If YES, identify the unsupported claims in one sentence.
"""

REVISION_PROMPT = """
The following response contains claims not supported by retrieved memory:

UNSUPPORTED CLAIMS: {unsupported_claims}

ORIGINAL RESPONSE:
{response}

Revise the response to remove or hedge these unsupported claims.
Replace fabricated specifics with acknowledgment of the gap:
"I don't have that in my memory" or "I'm not certain about that."
Do not add new claims. Keep everything else intact.
"""
```

### Streaming Compatibility

Option A (chosen): Buffer the full response before streaming. Run grounding check. If pass, stream. If fail, run revision pass, then stream revised response. Higher latency, cleaner architecture. The cascade failure is bad enough that latency is an acceptable tradeoff.

### Logging

Grounding check outcomes logged to logs/safety_reviews/ alongside constitutional review. Fields: triggered, grounded, unsupported_claims, revision_triggered, intent_class.

### Relationship to Constitutional Review

Constitutional review: "Is this response behaviorally appropriate?"
Grounding check: "Is this response factually grounded in what was retrieved?"

Both are post-generation. Both produce allow/revise outcomes. They run independently. Grounding check runs first (cheaper). Constitutional review runs second if triggered.

## Rationale

- Post-rationalization is documented as the dominant hallucination mechanism in RAG systems -- retrieval-side interventions cannot catch it
- The manual test battery revealed cascade failure that the automated harness missed
- Epistemic fidelity and behavioral policy are distinct concerns that should not share a review mechanism
- Intent-class triggering limits latency impact to the query types where cascade risk is highest
- Option A (buffer before stream) -- a streamed hallucination followed by a correction is worse UX than a slightly delayed correct response

## Consequences

+ Hallucination cascade caught before it reaches the user
+ Web search responses specifically protected -- highest cascade risk
+ Grounding failures logged and auditable
+ Epistemic fidelity is now a first-class architectural concern

- Adds latency to triggered query classes
- Requires buffering full response before streaming for triggered queries
- Second Ollama call increases per-request compute for triggered queries
- Revision pass adds a third Ollama call for failed checks

## Open Questions

- Acceptable latency budget for grounding check + revision pass on qwen3:8b? Measure before declaring acceptable.
- Should grounding check use a smaller/faster model? A 3B model for the check pass would reduce latency significantly.
- Should grounding check failures be surfaced to the user? Current decision: silent correction.

## Relationship to Other ADRs

- ADR-010 (constitutional review) -- parallel post-generation layer, different concern. Grounding check runs first.
- ADR-016 (nature layer) -- grounding check does not apply to identity/character responses.
- ADR-018 (intent-aware type gating) -- grounding check is downstream of type gating.

## References

- Grounding verification research (RAG faithfulness evaluation, 2024-2025)
- Manual test battery results 2026-04-04
- MemGPT/Letta architecture -- grounding as a first-class concern
- ADR-010: Constitutional Review
