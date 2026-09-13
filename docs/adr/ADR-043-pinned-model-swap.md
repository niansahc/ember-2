# ADR-043: Pinned Model Swap for Evaluation Sweeps

**Status:** Accepted
**Date:** 2026-09-12
**Target:** v0.19.0
**Related:** ADR-039 (safe JSON I/O), ADR-008 (cloud model providers), ADR-027 (Ollama model serving), ADR-029 (response quality eval framework), issue #155 (dead context-window call)

## Context

Ember runs one model in every role by design. `POST /model` writes the selected
model to `model_override.json`, and `get_ember_model()` reads that file on every
call, so a single switch moves the generator and every auxiliary LLM call
together. For a personal install that is correct and is what should ship.

It breaks model evaluation. When a sweep switches to a candidate, that candidate
becomes the Stage-3 intent classifier (`intent_classifier.py`), the coaching
filter's rewriter and yes/no detector (`coaching_filter.py`), the deviation
detector, and the reflection and onboarding paths -- as well as the generator
under test. The intent class then decides which streaming branch runs and which
filters fire, so a candidate that classifies differently is scored on a
different pipeline, by a filter that is itself the candidate. A ranking produced
that way cannot separate "better model" from "routes itself into a gentler
path".

There is a second, smaller cost already recorded. The eval tools switch by
POSTing and restore by POSTing back, so each run clobbers the user's persisted
model mid-flight, and a crash between the two leaves the eval's model persisted.
CHANGELOG.md records the symptom: the default model reset to qwen3:8b after
model_override.json was set by prior testing.

## Decision

### Pin by omission, not by substitution

A request may set `persist: false` on `POST /model`. The model is applied to the
adapter singleton and the persisted override is not written. Because every
auxiliary role resolves `get_ember_model()` at call time, and that function
reads a file the candidate never touched, those roles keep using the reference
model with no further work.

No routing shim, no second resolver, no per-role model parameter threaded
through ten call sites, and no change to `get_ember_model()` itself. The pin is
the absence of a write.

`persist` defaults to `true`, so every existing caller -- the UI model switcher
and the three eval tools -- behaves exactly as before. A personal install never
sends the field, and its behaviour is byte-identical to the previous version.
That is what keeps this inside the deployment boundary: nothing about a
PC-only install changes.

### get_ember_model() and llm_adapter.model are allowed to diverge

Under a pin these two deliberately disagree: `llm_adapter.model` is the
candidate, `get_ember_model()` is the reference. This is the entire mechanism,
not an oversight. In production, with `persist` defaulting true, they are
identical on every path.

The divergence is reported rather than left to be inferred. `GET /model` gains
two additive keys beside the existing `model`, `available` and `cloud`:

- `reference_model` -- what every call-time role resolves
- `pinned` -- whether the two disagree

An eval harness needs this because the pin is in-memory only. A `--reload`
uvicorn re-runs the adapter constructor on any touched file and silently reverts
the pin, which would relabel reference results as candidate results with no
error anywhere. The harness must re-read `GET /model` and re-assert the pin
before each candidate.

Roles that follow the candidate under a pin: Ollama and cloud generation, both
streaming and non-streaming. Roles that keep the reference: intent classifier,
coaching filter, deviation detector, reflection and lodestone synthesis,
onboarding formatting, and state extraction. The grounding verifier is pinned to
a hardcoded literal independently of this decision and is untouched.

### The context-window write is removed, not gated

`POST /model` also called `conversation_buffer.set_context_window(model)`. That
assignment is read nowhere in `src/` -- the live context budget is computed
independently in `LLMAdapter._get_num_ctx` -- and it silently no-ops on a model
absent from `MODEL_CONTEXT_WINDOWS`, so during a sweep it retained the previous
candidate's value. It is deleted rather than moved inside the `persist` branch
(issue #155).

## Consequences

- An eval sweep can test a candidate as the generator while holding the rest of
  the pipeline constant, which is a precondition for a defensible ranking. The
  pinned configuration is not what ships, so a pinned result does not by itself
  predict shipped behaviour; that remains a separate question about the whole
  pipeline.
- `POST /model` performs no validation of the model name. Under `persist: false`
  a typo now leaves no trace on disk at all, so the harness must validate the
  candidate against the installed model list before posting.
- The persisted override lives in the active vault, and the vault can be swapped
  at runtime. `get_ember_model()` can therefore change under a live pin while
  `llm_adapter.model` stays put. A sweep should not swap vaults mid-run.
- `get_private_vault_path()` raises when `PRIVATE_VAULT_PATH` is unset, so
  `POST /model` currently fails in that configuration. Under `persist: false`
  the write is skipped, so the request now succeeds where it previously did not.
- `src/api/chat.py` constructs a second `LLMAdapter` that `POST /model` has
  never updated, so that path already ignores runtime model switches. This is a
  pre-existing divergence, unchanged here and recorded so it is not mistaken for
  a consequence of the pin.
- Old clients sending only `model` remain valid. A client sending `persist` to a
  server predating this change is silently accepted rather than rejected, since
  unknown fields are ignored, so a harness cannot detect an old server by the
  request alone -- it should check for the `pinned` key on `GET /model`.
