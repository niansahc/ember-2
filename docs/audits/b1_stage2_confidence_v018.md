# B1 Stage 2 Confidence Measurement (v0.18.0)

## Purpose

Per the v0.18.0 plan pre-condition gate: confirm that B1 queries reach Stage 3 of the intent classifier (where the prompt enrichment fix would apply) rather than being confidently misrouted at Stage 2 (where the fix is dead code).

## Procedure

Ten synthetic queries (no vault content, ASCII only) run through `_stage1_classify`, then `_stage2_classify`, then `_stage3_classify_with_timeout` directly. Captured the final stage, label, Stage 2 confidence, and elapsed milliseconds per query.

Environment: `qwen3:8b` Ollama local, `INTENT_CLASSIFIER_TIMEOUT_MS=800` (default), measurement taken 2026-05-12 against the `fix/debug-print-privacy-gate` branch.

## Results

### B1 fixture (should route vault_answerable)

| Query | Final stage | Label | Stage 2 conf | ms |
|---|---|---|---:|---:|
| current job vs old job | timeout | vault_answerable | 0.518 | 3329 |
| comparing my current job and old job | timeout | vault_answerable | 0.555 | 833 |
| differences between my current role and previous role | timeout | vault_answerable | 0.539 | 843 |
| current career vs previous career path | timeout | vault_answerable | 0.532 | 843 |
| thoughts on my current job versus the old one | timeout | vault_answerable | 0.580 | 857 |

### Adjacent web fixture (should route needs_internet)

| Query | Final stage | Label | Stage 2 conf | ms |
|---|---|---|---:|---:|
| current job market in tech | timeout | vault_answerable | 0.602 | 842 |
| best career advice for engineers | timeout | vault_answerable | 0.454 | 829 |
| average salary for software engineers | timeout | vault_answerable | 0.415 | 843 |
| current state of the labor market | stage2 | needs_internet | 0.724 | 32 |
| history of remote work culture | timeout | vault_answerable | 0.471 | 845 |

## Gate evaluation

Plan condition: "If at least 3 of 5 B1 queries reach Stage 3, the B1 prompt fix is on the correct layer."

- B1 queries reaching Stage 3 (including timeout path): **5 / 5**

Gate technically passes. But the result is more nuanced than the plan envisioned.

## Critical finding: Stage 3 is timing out, not deciding

Every B1 query that "reached Stage 3" timed out at ~830-857ms. The 800ms `INTENT_CLASSIFIER_TIMEOUT_MS` cap is too aggressive for `qwen3:8b` warm latency on this hardware. Stage 3 is not running to completion; the safe default (vault_answerable) is firing in its place.

The B1 prompt enrichment under these conditions is dead code: Stage 3 never returns a real decision for the prompt to influence.

By coincidence, the safe-default behavior is correct for B1 (timeout maps to vault_answerable, which B1 queries should get). But it is wrong for the adjacent web fixture: 4 of 5 legitimate-web queries also time out and incorrectly route to vault_answerable. This is consistent with the broader user-facing experience of "needs_internet detection feels broken on novel queries."

The single exception in the adjacent set ("current state of the labor market") hits an existing Stage 2 exemplar ("what is the current state of the housing market") at 0.724 cosine similarity, escaping the timeout path.

## Implications for v0.18.0 B1 fix

The plan's B1 fix (Stage 3 prompt enrichment, single sentence) cannot achieve its stated goal under the current `INTENT_CLASSIFIER_TIMEOUT_MS=800` setting. Three viable paths:

1. **Raise `INTENT_CLASSIFIER_TIMEOUT_MS`** before applying B1 prompt fix. A 1500ms cap would let Stage 3 actually complete on this hardware. The prompt fix then has real effect. Side effect: per-classify p99 latency rises by ~700ms on the residual ambiguous query path.
2. **Defer B1 entirely.** The timeout-as-safe-default already routes B1 queries correctly. The needs_internet detection problem is real but is a separate fix (timeout cap, not prompt content).
3. **Ship B1 prompt fix as future-proofing.** Land the prompt change with no behavior change today. When the timeout cap is later raised, the prompt fix is already in place.

Path 1 closes both the B1 bug and the adjacent-web bug. Path 2 leaves the adjacent-web bug open. Path 3 leaves both open until a follow-up.

## Recommendation

Raise the timeout (Path 1) is the only path that meaningfully changes user-visible behavior in v0.18.0. Recommend a separate measurement of Stage 3 actual completion latency over 50+ queries to set the new timeout cap, then apply the B1 prompt fix on top of the raised cap.

If the team prefers to ship B1 prompt fix in v0.18.0 without raising the timeout, document the dead-code status and surface the adjacent-web miss class in KNOWN_ISSUES.

## Raw data

Saved to `.audit_b1_stage2.json` (ignored by git; transient).

## Update 2026-05-13: Step 1 landed (timeout raise)

Path 1 from the recommendation has been taken. `INTENT_CLASSIFIER_TIMEOUT_MS` default raised from 800ms to 1500ms in `src/core/config.py`. The prior 5/5 timeout rate measured against the 800ms cap should no longer fire on warm `qwen3:8b` at p95 latencies of 830-857ms.

Step 1 ships independently of step 2 (prompt sentence). Step 2 is conditional on a follow-up re-measurement of the 5 B1 fixtures against the raised cap:

- If 3+ complete AND at least one returns `needs_internet` (wrong answer): land the Stage 3 prompt reframe in a follow-up commit.
- If 3+ complete AND all return `vault_answerable` (correct): step 2 defers; current prompt + safe default sufficient.
- If fewer than 3 complete: step 2 defers; timeout-bound.

Re-measurement is pending. It runs against live `qwen3:8b` with the same 5 B1 fixtures using the existing `_stage3_classify_with_timeout` path. Result will be appended to this doc when measurement completes.

### Re-measurement result (2026-05-13, live `qwen3:8b`, raised cap)

Environment: `qwen3:8b` Ollama local, `INTENT_CLASSIFIER_TIMEOUT_MS=1500` (new default), explicit warm-up call before the run.

| Query | Label | Timed out | ms |
|---|---|---|---:|
| current job vs old job | vault_answerable | True | 1508 |
| comparing my current job and old job | vault_answerable | True | 1506 |
| differences between my current role and previous role | vault_answerable | True | 1512 |
| current career vs previous career path | vault_answerable | True | 1506 |
| thoughts on my current job versus the old one | vault_answerable | True | 1515 |

- Completions: **0 / 5** (below the 3-of-5 threshold)
- `needs_internet` returns: 0 / 5

**Gate decision: B1 step 2 (prompt sentence) DEFERS.** Per the locked decision tree, fewer than 3 of 5 complete means the prompt sentence is still dead code on this hardware. The 1500ms cap is helpful for queries that complete in 830-1499ms (the original audit's range), but warm `qwen3:8b` latency on these particular B1 queries today lands at ~1500-1600ms, exceeding even the raised cap.

The 800-to-1500ms raise still ships (step 1) on its own merit: it gives any future Stage 3 invocation that completes in the 800-1499ms window a chance to actually decide, and it matches the audit's original Path 1 recommendation. The prompt sentence does not ship in this PR.

Follow-up question (out of scope for PR #1): is the latency floor on these queries hardware-bound, model-config-bound (e.g. context length, options), or driven by Ollama warm/hot state? A separate measurement over 50+ queries with controlled warm-up would inform whether a higher cap (e.g. 2000ms) is worth the latency cost, or whether B1 step 2 is permanently unreachable on this hardware.

### Re-measurement 2026-10-23 (after the top-level think=False fix, PR #306)

The follow-up question above has an answer: model config, not hardware. Stage 3 sent `think` inside `options`, where Ollama ignores it, so qwen3:8b thought before answering. PR #306 sends `think=False` as a top-level chat argument.

Environment: live `qwen3:8b`, `INTENT_CLASSIFIER_TIMEOUT_MS=1500`, warm, two runs through `_stage3_classify_with_timeout`. Both runs returned the same labels.

| Query | Outcome | Label | ms (run 1 / run 2) |
|---|---|---|---:|
| current job vs old job | stage3 | vault_answerable | 265 / 268 |
| comparing my current job and old job | stage3 | vault_answerable | 275 / 291 |
| differences between my current role and previous role | stage3 | vault_answerable | 280 / 285 |
| current career vs previous career path | stage3 | needs_internet | 290 / 274 |
| thoughts on my current job versus the old one | stage3 | vault_answerable | 296 / 266 |
| current job market in tech | stage3 | needs_internet | 287 / 295 |
| best career advice for engineers | stage3 | vault_answerable | 257 / 285 |
| average salary for software engineers | stage3 | needs_internet | 275 / 306 |
| current state of the labor market | stage3 | needs_internet | 286 / 287 |
| history of remote work culture | stage3 | needs_internet | 288 / 289 |

- Completions: B1 5/5, adjacent web 5/5. B1 `needs_internet` returns: 1/5.
- Gate: 3+ complete and at least one returns `needs_internet`, so per the decision tree above step 2 (the Stage 3 prompt reframe) lands in a follow-up commit. It is not part of PR #306.
- A cold model load (the first call after Ollama unloads qwen3:8b) still overran the cap.

## Step 2 landed (2026-10-24)

`_STAGE3_SYSTEM_PROMPT` in `src/llm/intent_classifier.py` was reframed. needs_internet is limited to current, time-sensitive or external information the model cannot know (news, prices, schedules, recent releases, live status). A fact that stays the same from year to year is not needs_internet. Everything else is vault_answerable, including the user's own life and a comparison of two periods of it, with or without "my" and whatever words like current, old or previous it uses. The Variant C sentence the earlier entries refer to was never recorded, so this wording was measured from scratch. One candidate without the stable-fact sentence was screened first and rejected: it sent "what is the capital of Australia" to needs_internet in 1 of 2 runs.

Environment: live `qwen3:8b`, `INTENT_CLASSIFIER_TIMEOUT_MS=1500`, warm. Same session for before and after. Before: the PR #306 head, 2 measurements. After: the new prompt, 3 measurements. Cells count labels over all runs (VA vault_answerable, NI needs_internet). Every Stage 3 call answered (0 timeouts, 0 failures); median 182ms, max 296ms over 420 calls.

### B1 audit fixtures (Stage 3 direct; rows 1-5 want VA, 6-10 want NI)

| Query | Stage | Before | After |
|---|---|---|---|
| current job vs old job | stage3 | VA 4 | VA 6 |
| comparing my current job and old job | stage3 | VA 4 | VA 6 |
| differences between my current role and previous role | stage3 | VA 4 | VA 6 |
| current career vs previous career path | stage3 | NI 4 | VA 6 **changed** |
| thoughts on my current job versus the old one | stage3 | VA 4 | VA 6 |
| current job market in tech | stage3 | NI 4 | NI 6 |
| best career advice for engineers | stage3 | VA 4 | VA 6 |
| average salary for software engineers | stage3 | NI 4 | NI 6 |
| current state of the labor market | stage3 | NI 4 | NI 6 |
| history of remote work culture | stage3 | NI 4 | NI 6 |

Runs: before 4, after 6.

### 20-query code-path set (5 queries x 4 runs per measurement; Stage 3 direct)

| Query | Stage | Before | After |
|---|---|---|---|
| what changed in the latest release of the framework | stage3 | NI 8 | NI 12 |
| remind me what we decided about the garden plan | stage3 | VA 8 | VA 12 |
| is the conference still happening next month | stage3 | NI 8 | NI 12 |
| how did the last sprint go for the backend work | stage3 | VA 8 | VA 12 |
| what is the best laptop for programming right now | stage3 | NI 8 | NI 12 |

Runs: before 8, after 12.

Full-cascade sets, same runs (2 before, 3 after):

- `tools/eval_web_search.py`, 30 questions: 30/30 needs_internet before and after, in every run (12 decided at Stage 3).
- 10 synthetic general-knowledge and how-to questions: 10/10 vault_answerable before and after, in every run (all decided at Stage 3).

Gates: the adversarial Category 2 case `current_career_vs_previous_career` passed 5/5 warm runs. The full adversarial file plus calibration passed under `-m eval` twice (30 passed each). No needs_internet label changed in any set.
