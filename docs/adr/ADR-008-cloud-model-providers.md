# ADR-008: Cloud Model Provider Support

**Status:** Proposed (not yet accepted)
**Date:** 2026-03-27

## Context

Local models (Qwen 2.5 14B, Llama 3.1 8B, Mistral 7B) produce responses
that lean heavily on trained assistant patterns — hedging, disclaiming,
over-explaining, redirecting preference questions. This undermines Ember's
character as defined in the system prompt and constitution. The
authentic_expression principle catches some of this in post-draft review,
but the root cause is the model's training.

Cloud models (Claude, GPT-4) have stronger capacity for nuanced,
character-consistent responses. Using them as the reasoning engine would
give Ember a more genuine voice.

Conversation quality evaluation (v0.10.1 baseline) scored qwen2.5:14b at
3.9/10 overall. Code-layer fixes improved self-attribution (6.3) and state
awareness (6.3). Remaining failures are model-ceiling problems: preference
expression (2.3), constitutional behavior (2.3), memory grounding (2.0),
tone (4.0). This eval provides quantitative justification for cloud model
support. See `docs/eval_history.md` for full results.

## The Tension

Ember's ethos says memory stays local. Cloud reasoning means the assembled
context packet — retrieved memories, reflections, state items, the user's
message, and the system prompt — leaves the machine during inference. The
context packet contains personal information.

This is not cloud storage (no data is persisted externally). It is cloud
exposure — the provider processes the context during the API call. The
distinction matters but does not eliminate the concern.

## Decision

**Under consideration.** Cloud providers will be:
- **Opt-in, never default.** Local model remains the default and always supported.
- **Explicit.** Users must acknowledge what is sent before enabling.
- **Visible.** The active provider is always displayed in the UI.

## Required Mitigations Before Acceptance

1. **Clear persistent UI indicator** when a cloud model is active — visible
   in the header or sidebar at all times, not just in settings.

2. **Installer warning** with explicit disclosure before API key entry:
   "When using a cloud model, your conversation context (including
   retrieved memories) is sent to [provider] for processing. Your vault
   stays on your machine."

3. **Plain-language terms of use** agreed to during install — not a wall
   of legalese, but a clear statement of what happens to the data.

4. **AGPL license acknowledgment** in installer — users should understand
   the open-source nature of the system.

5. **No conversation context cached or logged** beyond the provider's
   standard API terms. Ember does not add any additional external storage.

## Implementation (if accepted)

The clean seam is `LLMAdapter._chat()` in `src/llm/adapter.py`. Everything
above it (prompts, safety review, context assembly, state extraction) stays
the same regardless of provider.

Changes needed:
- Abstract `_chat()` to dispatch based on model provider (local vs cloud)
- Add provider-specific API key management (separate from Ember API key)
- Model switcher endpoints list both local and cloud models
- Cloud calls go through `httpx` or the provider's SDK
- Embedding model stays local regardless (sentence-transformers)

## Open Questions

- License terms language — how to phrase the data exposure disclosure
- Installer UX rework scope — how much changes for cloud onboarding
- Which providers to support at launch — Claude (Anthropic), GPT-4 (OpenAI), both?
- Per-conversation provider choice vs global setting
- Whether to allow mixing (local for casual, cloud for important conversations)

## Alternatives Considered

| Alternative | Why not yet |
|---|---|
| Larger local models (70B+) | Hardware barrier — most users don't have 48GB+ VRAM |
| Fine-tuned local models | Out of scope — requires training infrastructure and datasets |
| Hybrid routing (local for simple, cloud for complex) | Complexity not justified until cloud support is validated |
| No cloud support ever | Would limit Ember's voice quality to local model capabilities |
