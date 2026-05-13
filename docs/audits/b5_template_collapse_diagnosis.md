# B5 Diagnosis - template_collapse False Negative (v0.18.0 UAT)

## Purpose

UAT finding B5 from the 2026-05-11 session: two adjacent assistant responses were near-verbatim identical, the user identified the repetition on the following turn, and the deviation detector's `template_collapse` pattern returned NO. The prior audit at `docs/audits/refuse_redirect_uat_v018.md` (commit eb9ef88) classified B5 as `normal_generation`, fix layer `src/safety/deviation_detector.py` `template_collapse`. This diagnosis answers six structural questions about the detector before any code change so the subsequent fix step targets the actual failure mode rather than a guessed one.

No code changes in this step. Documentation only.

## Procedure

Files read in order:

1. `src/safety/deviation_detector.py` (full file, 378 lines)
2. `config/pattern_classes.yaml` (full, looking for the `template_collapse` entry and its markers)
3. `src/api/openai_adapter.py` (lines 199-218 for `_background_deviation_detection`; lines 1687-1813 for the post-stream cleanup flow that invokes both the detector and the coaching filter)
4. `src/llm/adapter.py` (lines 380-394 and 540-555 for `conversation_buffer.add_turn`)
5. `src/context/conversation_buffer.py` (full, 217 lines)
6. `tests/test_deviation_detector.py` (lines 120-169, structural tests for the loader and selector)
7. `tests/eval/test_deviation_engine_fp.py` (full, 80 lines, the FP-rate eval that pins the mechanism)
8. `tests/eval/judge.py` (lines 76-79, a separate `template_collapse` definition used by golden-dataset eval - cross-reference only, not the production detector)

All read-only. No code edited.

## Findings

### Q1. Comparison window: previous turn only, or N turns?

**Answer: window of 1.** The detector compares the current assistant response against exactly one prior assistant response - the immediately previous turn.

- `src/api/openai_adapter.py:1797-1799` (grounded-streaming path) and `:1690-1692` (older cleanup path) both read `conversation_buffer.get_recent()` and take `_buffer_turns[-2].get("assistant")`.
- `_buffer_turns[-1]` is the current turn (added by `src/llm/adapter.py:550` before openai_adapter resumes). `_buffer_turns[-2]` is the immediately prior turn.
- The detector does not see turns at indices `[-3]`, `[-4]`, etc. No N-turn window. No rolling baseline.

### Q2. Similarity measurement: edit distance, Jaccard, hash, n-gram overlap?

**Answer: none of the above. There is no deterministic similarity computation in code.** Detection delegates the judgment to a one-shot qwen3:8b LLM call.

- `src/safety/deviation_detector.py:166-216` (`_run_second_pass`) constructs a prompt with the pattern markers, the prior response, and the current response, then calls `ollama.chat` with `temperature=0` and `num_predict=50`.
- The pattern markers loaded from `config/pattern_classes.yaml` (the `template_collapse` block) include the natural-language phrase `semantic similarity between responses exceeds 0.95`, but this is a hint to the LLM judge, not a computed metric anywhere in the codebase.
- A grep of the deviation-detector module and the wider repo finds no `Levenshtein`, no `difflib`, no `jaccard`, no `set(a) & set(b)`-style overlap on response text. The only quantitative similarity in the file is `compute_entropy(logprobs)` at line 123 - which scores the model's own sampling variance, not response-to-response similarity.

### Q3. What threshold triggers a YES?

**Answer: no numeric threshold in code. The LLM's first word determines the result.**

- `src/safety/deviation_detector.py:209-212`: parses the first token of the model's response, uppercases it, strips punctuation, and returns `"YES"` iff that token equals `"YES"`. Everything else maps to `"NO"`.
- The marker text says `exceeds 0.95`, but this is the model's interpretation responsibility, not a computed gate. The threshold is whatever qwen3:8b decides in a 50-token reply at `temperature=0`.

### Q4. Pre- or post-coaching-filter text?

**Answer: pre-coaching-filter, for both the current response and the prior response.**

Detector invocation order in the grounded-streaming path (`src/api/openai_adapter.py:1794-1813`):

1. `full_reply` is assembled from the stream (post-revision if grounding ran).
2. Line 1794: deviation detection thread spawned, with `full_reply` as `response_text`.
3. Line 1811: `_is_refusal_response` guard.
4. Line 1813: `filter_coaching_frame` rewrites `full_reply`.

The detector receives the pre-filter text. The prior turn's text in the buffer is also pre-filter, because `src/llm/adapter.py:550` performs `conversation_buffer.add_turn(user, full_response, session_id)` inside the adapter before openai_adapter applies its post-stream coaching filter. Apples-to-apples comparison on pre-filter text.

This is important: the user observed two near-verbatim responses **as rendered**, which is the post-filter version. The detector compares the pre-filter versions, which are at least as similar (typically more so, because the filter occasionally strips closing questions from one response and not the other). So if the user saw near-verbatim post-filter, the detector saw near-verbatim or identical pre-filter.

### Q5. Full response or truncated?

**Answer: both responses truncated to the first 500 characters before the LLM judges them.**

- `src/safety/deviation_detector.py:187-188`: the multi_turn prompt template substitutes `prior_response[:500]` and `response_text[:500]`.
- For near-verbatim identical pairs, the first 500 characters are themselves near-verbatim, so truncation does not hide the similarity. Truncation is therefore not the proximate cause of B5; it is a latent risk for longer responses whose differences live past character 500.

The detector also never sees the user messages from either turn. The marker `different user input produced same output` (from the YAML) cannot be verified by the LLM because only the assistant text is passed.

### Q6. Session boundary or buffer reset between the two turns?

**Answer: not the cause for B5.**

- `src/context/conversation_buffer.py:98-109`: the buffer clears on a session_id change. Turns 7 and 8 of the UAT session shared a session_id, so no reset fired between them.
- `src/context/conversation_buffer.py:115-116`: the buffer trims when `len(self.buffer) > max_turns` (default 20). Turns 7 and 8 are well under the cap; neither was trimmed.
- The buffer is a process-level singleton on `prompt_builder.conversation_buffer`. A process restart between turn 7 and turn 8 would clear it. There was no API restart during the 2026-05-11 UAT session.
- If `prior_response` had been `None` at turn 8 (e.g. from a reset), `detect()` at `src/safety/deviation_detector.py:363-364` would have `continue`-d past template_collapse and never logged a result for it. The user observed a NO verdict, which means the second-pass actually ran. The buffer baseline was therefore present.

## Root cause

**template_collapse missed because the rule delegates similarity judgment to a one-shot qwen3:8b YES/NO call with no deterministic similarity computation in code, and the model returned NO on a 500-character-truncated pair that the user judged near-verbatim identical.**

The proximate cause is not the comparison window (window of 1 is correct for adjacent-turn collapse), not the pre-/post-filter ordering (apples-to-apples on pre-filter), not the truncation (the first 500 chars of near-verbatim text are themselves near-verbatim), and not a buffer reset (buffer was intact). The proximate cause is that an 8-billion-parameter local model was asked to compute and threshold against `0.95 semantic similarity` in natural language at `temperature=0`, with no code-level similarity computation backing the decision. qwen3:8b can recognize literally identical text reliably; near-identical text triggers inconsistent natural-language judgments.

## Decision tree branch - shared with B6 / B7?

The root cause has **two scopes**, and both are relevant for routing follow-up work:

### Localized to template_collapse: yes

template_collapse is the only pattern class in the deviation engine whose markers require a numerical similarity judgment (`exceeds 0.95`). Every other multi-turn or single-response class describes a structural or register property that the LLM can plausibly judge from one or two response snippets. A localized fix is well-scoped: insert a deterministic similarity pre-filter (Jaccard over normalized tokens, or normalized Levenshtein) inside `_run_second_pass` for `template_collapse`, returning `YES` directly when similarity is above a threshold (e.g. 0.85) without the LLM call. The LLM call remains the gate for the ambiguous middle band.

### Shared with the rest of the deviation engine: also yes

All eleven pattern classes in `config/pattern_classes.yaml` share the same one-shot LLM YES/NO mechanism (`src/safety/deviation_detector.py:166-216`). A small model judging nuanced register / repetition / template-ness in 50 tokens at `temperature=0` is the structural design choice. Any future false-negative on a deviation class will have the same upstream cause. That is a deviation-engine-wide concern.

### Not shared with B6

B6 (closing-question residue) lives in `src/llm/coaching_filter.py`, a separate file with **compiled regex patterns** at line 44 (`_COACHING_CLOSINGS`). Coaching_filter is deterministic. The B5 root cause (LLM judgment) does not apply.

### Not shared with B7, contingent on fix layer

B7 (circular dodge) has no detector. If the fix lands in `coaching_filter` as a new regex, it inherits the deterministic mechanism and does not share B5's root cause. If the fix lands in `deviation_detector` as a new pattern class, it inherits the one-shot-LLM mechanism and shares B5's root cause.

### Routing recommendation

- **B5 fix step (next workstream B step)**: localized fix in `src/safety/deviation_detector.py` adding a deterministic similarity pre-filter for `template_collapse` only. Branch and PR can ship independently.
- **Engine-wide concern**: separate workstream investigation into the one-shot-LLM judgment mechanism for the other ten deviation classes. Not blocking the B5 fix. Worth scheduling because the same false-negative pattern is likely latent for other classes; the v0.18.0 UAT happened to surface it on template_collapse because that class's marker contains an explicit numerical threshold.
- **B7 design call** (out of scope for this audit): if B7 is routed to coaching_filter, the engine-wide concern does not apply. If B7 is routed to a new deviation_detector class, the engine-wide concern applies and should be resolved first.

## Out of scope (deferred to subsequent steps)

- Any code change to `src/safety/deviation_detector.py`, `config/pattern_classes.yaml`, or test files.
- A similarity-metric choice (Jaccard vs Levenshtein vs character-trigram overlap).
- A threshold value for the deterministic pre-filter.
- An eval harness that would have caught B5 pre-merge.

## Known related issue: ADR-026 section5 single-pass drift

While diagnosing B5, surfaced a separate predating drift between ADR-026 section5 and the current implementation of `detect()` in `src/safety/deviation_detector.py:353-376`.

**ADR-026 section5** prescribes:

> One second pass per response maximum. Do not run multiple passes for multiple pattern classes in the same turn - pick the highest-risk class for the intent and run once.

**Current code** (lines 353-376): iterates ALL eligible pattern classes from `_load_pattern_classes()` and runs `_run_second_pass` on each until the first YES. That is N second-pass LLM calls per response (worst case ~10), not one.

A helper consistent with the ADR exists at `_select_pattern_class` (lines 72-118) - it returns a single highest-priority class. The helper is referenced in tests (`tests/test_deviation_detector.py:139-155`) but is **not called** by `detect()`. Dead code from the ADR's perspective.

**Impact:**
- Performance: a turn that ultimately gets flagged on the 10th class burned 10x the intended GPU time on the second-pass calls.
- Detection bias: classes earlier in the YAML order have first-claim on a YES. A response that genuinely matches both `closing_question` and `template_collapse` will be recorded as `closing_question` because the loop returns on the first YES. Earlier YAML positions get more attribution; later positions get less.
- Logging: per-pattern logs are written for every class checked, inflating `logs/deviation/<date>.log` size.

**Not a blocker for B5.** The B5 fix is a localized pre-filter inside `_run_second_pass` and does not modify `detect()`. The drift was confirmed not to be a cause of B5's miss (the diagnosis already showed template_collapse's second-pass DID run and DID return NO - which means the loop DID reach template_collapse, and the failure was inside the LLM judgment, not at the dispatch layer).

**Recommended follow-up** (separate workstream, not blocking):
1. Decide whether the ADR or the code is right. The code's "iterate-all" behavior may have been deliberately chosen for detection coverage but the ADR rationale (one-pass-per-turn for performance and clean attribution) is also defensible.
2. If the ADR wins: wire `detect()` to call `_select_pattern_class` once, run a single `_run_second_pass`, log one entry per turn.
3. If the code wins: amend ADR-026 section5 to reflect the iterate-all reality and delete the unused `_select_pattern_class` helper + its tests.
4. If a hybrid wins: define the contract precisely (e.g., "iterate but stop at first YES, cap at N classes").

This is a documentation-or-code consistency decision, not a correctness fix. Filed here so a future reader who notices the drift can find the context.
