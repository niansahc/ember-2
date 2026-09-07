# ADR-032: Vision Pipeline Architecture

**Status:** Accepted (final)
**Date:** 2026-04-13

## Context

Ember-2 supports vision model input (image uploads) but the current implementation bypasses the full prompt construction and constitutional review pipeline (documented in CLAUDE.md Known Issues: "Vision model pipeline bypass"). Images are sent directly to Ollama with only the base system prompt. This means vision responses skip context assembly, identity rules, nature injection, and constitutional review.

A redesigned vision pipeline must integrate image understanding into the existing response pipeline without requiring both models loaded simultaneously — VRAM constraints on a single GPU (RTX 5080, 16 GB) prohibit concurrent model loading at the sizes Ember uses.

## Decision

Implement a two-model architecture with single-model-swap for vision.

### Models

- **Vision preprocessor:** qwen3-vl:8b (vision-language model)
- **Text primary:** qwen3:8b (unchanged)

These models are NOT loaded simultaneously. The vision model is loaded on demand, runs the vision pass, then the text model is reloaded for generation.

### Pipeline

When an image is attached to a message:

1. **Auto-trigger:** Vision runs pre-generation when an image is attached. No user action required — the presence of an image is the trigger.
2. **Model swap:** Ollama loads the vision model (qwen3-vl:8b).
3. **Vision pass:** Image + task-specific prompt → vision description (300 token cap).
4. **Model swap back:** Ollama loads the text model (qwen3:8b).
5. **Context injection:** Vision description injected as `<vision_context>` section in the context packet, positioned after `<web_search_results>` and before the user message.
6. **Normal generation:** Text model generates response with full pipeline (context assembly, identity rules, nature, constitutional review).

### Vision Prompt Strategy

The vision prompt is task-specific, not generic:

- **Text, code, errors:** Verbatim OCR transcription. Prompt instructs the model to reproduce text content exactly as shown, preserving formatting and structure.
- **Photos, screenshots, diagrams:** Conversational description. Prompt instructs the model to describe what is shown in natural language, focusing on content relevant to the user's message.

Task detection is based on the user's message content and image characteristics.

### Output Format

Vision descriptions are capped at 300 tokens. The output is injected into the context packet as:

```xml
<vision_context>
[Vision model description of attached image]
</vision_context>
```

### Source Attribution

Responses that use vision context include source attribution indicating the information came from image analysis, not vault memory.

### Vault Saving

Saving vision descriptions to the vault is deferred as an opt-in enhancement. Vision descriptions are ephemeral by default — they exist only in the context packet for the current turn.

## Rationale

- Two-model architecture avoids the pipeline bypass problem — the text model generates the final response through the full pipeline, using the vision description as additional context.
- Single model swap avoids VRAM contention on a single GPU. Ollama handles model loading/unloading transparently.
- Auto-trigger removes friction — users don't need to know about the vision pipeline.
- Task-specific prompts produce better output than generic "describe this image" prompts.
- 300 token cap prevents vision descriptions from dominating the context window.
- Deferred vault saving keeps the initial implementation simple and avoids writing potentially low-quality descriptions to permanent memory.

## Consequences

+ Vision responses go through the full pipeline (context, identity, constitution).
+ No VRAM contention — single model loaded at a time.
+ Source attribution prevents confusion between vault-grounded and vision-grounded claims.
- Model swap adds latency (Ollama model load time, typically 2-5 seconds).
- Two Ollama calls per vision message (vision pass + text generation).
- Vision descriptions are ephemeral — no persistence across turns unless vault saving is enabled later.

## Alternatives Considered

### Single multimodal model for everything
Rejected: Current multimodal models (llama3.2-vision, qwen3-vl) produce lower quality text responses than dedicated text models at the same parameter count. Using a single multimodal model would regress text quality for all conversations.

### Concurrent model loading
Rejected: 16 GB VRAM is insufficient for two 8B models loaded simultaneously at reasonable quantization. Sequential swap is the only viable approach on current hardware.

### Manual vision trigger (button/toggle)
Rejected: Adds friction for no benefit. If an image is attached, the user wants it analyzed. Auto-trigger is the correct default.

## Amendment (v0.19.0, issue #138)

`vision_enabled` (`src/core/preferences.py`, default `True`) is a global opt-out preference honored at the image gate in `src/api/openai_adapter.py`. This does not reverse the "Manual vision trigger" rejection above: that alternative was a *per-message* activation step (the user takes an action on every image before analysis runs), which is still rejected on the same friction grounds. `vision_enabled` is a *global, defaulted-on* preference — auto-trigger remains the behavior for every user who never touches the setting. It exists to let the minority of users who want vision categorically off (privacy/cost control, per issue #131) do so once, not to reintroduce per-image friction.

When `vision_enabled=False`, the preprocessor is skipped and the turn proceeds to normal text generation without a `<vision_context>` section — this is a deliberate skip, not a failure, and must not trigger the `VISION_UNAVAILABLE_RESPONSE` short-circuit reserved for genuine preprocessor failures. Raw image bytes are still stripped from the context packet in both cases, per the existing rationale above (RLHF refusal trigger, issue #130).

`vision_model` preference resolution (letting a user pick which vision model runs, mirroring the existing chat-model override precedence) is explicitly out of scope for this amendment — deferred to issue #131.