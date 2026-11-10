# ADR-049: User Thinking Setting

**Status:** Proposed
**Date:** 2026-11-09
**Target:** v0.19.0
**Related:** PR #312, PR #311, `docs/KNOWN_ISSUES.md` (B-GEN-001), ADR-043 (pinned model swap), ADR-042 (GenerationContext), `CONTEXT.md`

Terms follow `CONTEXT.md`: **Turn thinking** is whether the model reasons
before it writes an assistant **Turn**; an **Exchange** is one user **Turn**
and its reply.

## Context

- **Thinking was an env var only.** PR #312 added `EMBER_GENERATION_THINK`.
  Unset, the generation call passed no `think` argument, so a thinking model
  (qwen3 and later) reasoned by its own default. Set to false, every
  conversation turn sent `think=False`. Changing it meant editing `.env` and
  restarting the API. The user had no control in the UI.
- **A second env var ran a battery arm.** `EMBER_SEARCH_FAIL_THINK_OFF` sent
  `think=False` only on turns that carried a search note (search status
  unavailable or empty). It existed for manual battery Category 7. Those arms
  were never run.
- **Thinking can use the whole output budget.** B-GEN-001 (PR #311): on short
  emotional prompts, qwen3.6 reasoning reached `num_predict` before any reply
  text, and the turn ended in the empty-reply fallback. `src/llm/adapter.py`
  `_retry_without_thinking` repeats that one call with `think=False`.
- **Ollama checks the argument against the model.** Probe on Ollama 0.40.13:
  `think=True` on a model without the `thinking` capability returns HTTP 400
  "does not support thinking" before the model loads. `think=False` passes
  the capability check on the same model.

## Decision

### 1. A vault preference, off by default

`generation_think` is added to `PREFERENCE_DEFAULTS` (`src/core/preferences.py`)
with the default `False`. `generation_thinking_enabled()` is its only reader
and returns `True` only when the stored value is the JSON boolean `true`.
Missing, `null`, `"true"`, `1` and any other value read as off.

### 2. The env vars are retired

`EMBER_GENERATION_THINK` and `EMBER_SEARCH_FAIL_THINK_OFF` are removed from
`src/core/config.py` and `.env.example`. The preference is the only control.
Neither name is read anywhere in `src/`.

### 3. Off sends think=False; on sends nothing

- Off: the generation call sends `think=False`. Log line:
  `[GEN] turn thinking: off (preference)`.
- On: the generation call sends no `think` argument, so the model uses its own
  default. Log line: `[GEN] turn thinking: on (model default)`.
- `think=True` is never sent, so the HTTP 400 above cannot occur. Whether the
  model supports thinking drives the UI only (section 5), never the call.

### 4. The search-failure arm is retired

There is no code-decided force-off after a search failure. The user's choice
holds on every turn, search failure or not.

### 5. Dedicated endpoints

**`GET /v1/settings/thinking`**

- 200: `{"enabled": false, "model": "qwen3:8b", "supported": true, "cloud": false}`
  - `enabled` (bool): `true` only when the stored `generation_think` is the
    JSON boolean `true`. Missing, `null`, `"true"`, `1` read as `false`.
  - `model` (str): the active generation model (`llm_adapter.model`).
  - `cloud` (bool): `llm_adapter._is_cloud_model(model)`.
  - `supported` (bool or null):
    - `false` for a cloud model;
    - otherwise `"thinking" in show(model).capabilities` on the generation
      host, checked on every request (not cached);
    - `null` when `show()` errors or times out (5 s). Null means "could not
      check", not "unsupported".
- 401 without a valid key: `{"detail": "Invalid or missing API key"}`
  (existing middleware).

**`POST /v1/settings/thinking`**

- Body: `{"enabled": true}` or `{"enabled": false}`.
- 200: the same body as GET, read again after the write.
- 400 `{"detail": "enabled must be true or false"}`: the field is missing or is
  not a JSON boolean (`null`, `"true"`, `1`, ...). The stored value is
  unchanged.
- 422: the body is not a JSON object (FastAPI validation, as for bare mode).
- 500 `{"detail": "Could not save the thinking setting"}`: the preferences
  write raised `JsonIoError`.
- 401 without a valid key.

Other notes:

- `GET /v1/preferences` also returns `"generation_think"`. The UI uses the
  dedicated endpoint, not `PATCH /v1/preferences`.
- `GET /model` is unchanged.

### 6. Cloud models and non-thinking models

A cloud model is not affected; cloud providers never receive `think`. A
non-thinking Ollama model still gets `think=False` when the setting is off,
which passes Ollama's capability check, so no per-turn capability lookup is
needed.

### 7. Global, read once per request

The setting is global, not per conversation. It is resolved once at request
start in `src/api/openai_adapter.py` (`generation_thinking_enabled`, next to
the bare-mode read) and passed to the adapter as `thinking=...`. The adapter
never reads preferences. A change applies from the next **Exchange**; a reply
already in progress keeps its value. Ember's other model calls (review,
classification, extraction, reflection) are not affected.

## UI strings

Approved by the user 2026-11-09.

| State | String |
|---|---|
| Label | "Think before replying" |
| On | "Takes longer, works through the question first." |
| Off | "Replies faster." |
| `supported: false` or `cloud: true` | "This model doesn't support thinking." (toggle disabled) |
| `supported: null` | "Couldn't check whether this model supports thinking." (toggle enabled) |

## Reversal criterion

Recorded before any eval run. Off stays the default unless, in both of two
runs, on beats off by 3 or more points on Tier 4 quality
(`eval_conversations.py`), or on has fewer battery Category 7 column-(a)
failures than off. Latency (median, p90) and the empty-reply fallback count
are reported, but cannot reverse the default on their own.

## Consequences

- **Upgrade change.** After upgrade, qwen3 and other thinking models no longer
  reason before replying unless the user turns the setting on. The change
  ships with a `BREAKING CHANGE:` footer, so release-please proposes a minor
  bump.
- **The B-GEN-001 retry covers only the on path.** With the setting off the
  call already sends `think=False`, so `_retry_without_thinking` does not fire.
  Its logic is unchanged.
- **Retired env vars are ignored.** A `.env` that still sets
  `EMBER_GENERATION_THINK` or `EMBER_SEARCH_FAIL_THINK_OFF` has no effect.
- **Battery Category 7** compares the two values of the setting instead of the
  env arms (`docs/eval_manual_test_battery.md`).

## Alternatives considered

- **Keep the env var as a fallback under the preference** (preference over
  env var precedence). Rejected: two controls for one behaviour, and an
  install would act on a `.env` value the UI never shows.
- **Send `think=True` behind a capability gate.** Rejected: it needs a
  capability lookup on every turn, and the model default already reasons on a
  thinking model. Omitting the argument cannot produce the HTTP 400.
- **Keep a code-decided force-off on search-failure turns.** Rejected: the
  arm was never measured, and a hidden override would make the user's setting
  untrue on some turns.
- **Add capability to `GET /model`.** Rejected: `GET /model` has other
  consumers, and the capability check is a network call with a timeout that
  only the settings view needs.
- **Per-conversation override now.** Deferred: it can be added later in the
  same way as bare mode, with the global value as its default.

## References

- `src/core/preferences.py` -- `PREFERENCE_DEFAULTS`, `generation_thinking_enabled`.
- `src/llm/adapter.py` -- `turn_think`, `_retry_without_thinking`,
  `model_supports_thinking`.
- `src/api/openai_adapter.py` -- the per-request read.
- `src/api/main.py` -- `GET` and `POST /v1/settings/thinking`.
- `docs/KNOWN_ISSUES.md` -- B-GEN-001.
- `docs/eval_manual_test_battery.md` -- Category 7, thinking default
  confirmation.
- PR #311, PR #312.
