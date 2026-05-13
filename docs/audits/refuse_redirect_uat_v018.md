# Refuse-redirect Path Audit (v0.18.0 UAT findings B5 / B6 / B7)

## Purpose

CLAUDE.md item 2 establishes that `refuse_redirect` responses bypass `coaching_filter`. The bypass is intentional: refusal text must reach the user as the constitutional review wrote it, not as the coaching filter rewrites it. The corollary is that any UAT finding whose response was emitted via `refuse_redirect` is unreachable by coaching-filter expansion and must be addressed at a different layer (constitution, review service, or refusal construction).

Workstream B of the v0.18.0 UAT response cycle targets three findings from the 2026-05-11 UAT session:

- **B5** template_collapse false negative
- **B6** closing-question residue surviving coaching_filter
- **B7** circular dodge not flagged by any detector

This audit classifies each finding's path so subsequent workstream B steps target the right layer.

**Decision gate** (Workstream B step 1 entry condition): if any finding turns out to be on the refuse_redirect path, flag before continuing fix work for that finding.

## Procedure

1. Grep `src/` for every site that emits, consumes, or routes on the `refuse_redirect` verdict.
2. Trace the coaching_filter bypass guard in the request handler.
3. For each finding, classify the response path: `normal_generation`, `refuse_redirect`, or `coaching_filter_rewrite`.
4. Identify the fix-layer pointer per finding.

No vault content reproduced. The three findings appear as synthetic paraphrases.

## Section 1: refuse_redirect emission and handling sites in `src/`

| File | Line | Function | Role |
|---|---|---|---|
| `src/safety/models.py` | 7 | type definition | `ReviewOutcome = Literal["allow", "revise", "refuse_redirect"]` |
| `src/safety/review_service.py` | ~85 | `ResponseReviewService.review` | Calls `_build_refusal(critique, active_principle_ids)` -> `RefusalRedirect.as_text()` |
| `src/safety/review_service.py` | 88 | `ResponseReviewService.review` | Sets `outcome="refuse_redirect"` on high-severity critique |
| `src/llm/adapter.py` | 368 | `LLMAdapter.generate_response_iter` | Non-streaming: assigns refusal_message to final_response |
| `src/llm/adapter.py` | 544 | `LLMAdapter.generate_response_stream` | Streaming: yields refusal separator + message |
| `src/llm/safety_adapter.py` | 60 | `SafeLLMAdapter.generate` | Legacy adapter path: returns refusal_message |
| `src/safety/review_logger.py` | 54 | `SafetyReviewLogger._final_response` | Routes on refuse_redirect outcome for log extraction |

### Coaching_filter bypass guard

| File | Line | Condition |
|---|---|---|
| `src/api/openai_adapter.py` | 1811 | `if not _is_refusal_response(full_reply):` before `filter_coaching_frame()` (non-streaming path) |
| `src/api/openai_adapter.py` | 2006 | `if not _is_refusal_response(reply):` before `filter_coaching_frame()` (streaming path) |

The refusal detector is `_is_refusal_response` at `src/api/openai_adapter.py:44-49`, matching against `_REFUSAL_PATTERNS` at lines 36-41 (phrases such as "i can't help with that", "i'm not going to do that", "that's not something i'm going to do", "i had trouble generating a response").

### Verdict-to-filter routing

| Verdict | Coaching_filter runs? | Path |
|---|---|---|
| `allow` | yes | normal completion |
| `revise` | yes | LLM revision then filter |
| `refuse_redirect` | **no** | bypass guard returns early before filter call |

## Section 2: per-finding path classification

### B5 - Template collapse false negative

**Synthetic paraphrase of the trigger.** Two adjacent turns in the UAT session produced near-verbatim identical assistant responses. The user identified the repetition on the following turn; the deviation detector did not flag it in real time.

**Detector outcome.** `template_collapse` is a named category in `src/safety/deviation_detector.py:100`. The detector returned NO for this pair (false negative).

**Response classification.** `normal_generation`. Neither response was a refusal; the constitutional review either was not invoked or returned `allow`/`revise` and the standard generation path completed.

**Refuse_redirect path.** No. The responses are not refusal text; the `_REFUSAL_PATTERNS` set would not match repetitive normal content.

**Coaching_filter reachable.** No. Coaching_filter targets engagement framings (e.g. closing questions), not repetition. The fix layer is the deviation detector itself - specifically the `template_collapse` rule that already exists but missed this case.

**Fix-layer pointer.** `src/safety/deviation_detector.py` `template_collapse` rule. Tighten the similarity / repetition threshold or add the missing structural-template signal that the UAT pair shared.

### B6 - Closing-question residue post-coaching_filter

**Synthetic paraphrase of the trigger.** Multiple turns ended with engagement-style closing questions such as "what's the issue you're trying to resolve" or "is there something specific you'd like to explore" despite the coaching filter being active on those turns.

**Detector outcome.** `coaching_filter` ran; the patterns matched were insufficient. The closing-question variants in question were not in the `_COACHING_CLOSINGS` set at `src/llm/coaching_filter.py:44`.

**Response classification.** `normal_generation`, post-coaching_filter. The filter touched the responses but did not rewrite or delete the closing residue.

**Refuse_redirect path.** No. The responses were normal generation; refuse_redirect bypasses the filter entirely, which is the opposite of what was observed here.

**Coaching_filter reachable.** Yes. This is a coverage gap in the filter's pattern set, not a bypass. The filter is the correct fix layer.

**Fix-layer pointer.** `src/llm/coaching_filter.py:44` `_COACHING_CLOSINGS`. Extend with the observed engagement-closing variants. Mirror the existing pattern compilation style; do not introduce new categories of rewrite logic.

### B7 - Circular dodge not flagged

**Synthetic paraphrase of the trigger.** A single turn produced a self-referential content-free response of the structural form "we are discussing X, where X is the fact that we are discussing X". No detector flagged it; the response went to the user verbatim.

**Detector outcome.** No detector category for circular or content-free responses currently exists. `template_collapse` did not fire (the dodge was not a repetition of a prior turn). `coaching_filter` patterns did not match - the response did not look like an engagement closing.

**Response classification.** `normal_generation`. The response was not a refusal; the constitutional review either passed it or did not flag self-reference as a high-severity issue.

**Refuse_redirect path.** No.

**Coaching_filter reachable.** Partial. A new pattern in `_COACHING_CLOSINGS` (or an adjacent rule set) could in principle match circular phrasings. Cleaner: a dedicated deviation-detector category for content-free / self-referential responses. The choice is a design call for the next workstream B step, not for this audit.

**Fix-layer pointer.** Either `src/llm/coaching_filter.py` (new pattern) or `src/safety/deviation_detector.py` (new rule). The audit recommends the deviation_detector path because circular dodge is structurally closer to template_collapse (a content-quality signal) than to coaching framing (a register signal).

## Section 3: conclusion

- **0 of 3 findings are on the refuse_redirect path.**
- **Decision gate does not flag any finding.** Workstream B may continue for B5, B6, and B7 without rerouting any of them around the coaching_filter bypass.
- The coaching_filter bypass invariant at `src/api/openai_adapter.py:1811` and `:2006` (via `_is_refusal_response`) is intact and is not implicated in any of the three findings.

### Fix-layer routing (forward pointers for Workstream B steps 2-4)

| Finding | Fix layer | Key file |
|---|---|---|
| B5 | deviation detector, `template_collapse` rule | `src/safety/deviation_detector.py` |
| B6 | coaching_filter pattern set, `_COACHING_CLOSINGS` | `src/llm/coaching_filter.py:44` |
| B7 | new detector category (recommended: deviation_detector); coaching_filter is the secondary option | `src/safety/deviation_detector.py` |

Each fix-layer pointer is independent: B5 and B6 can be worked in parallel; B7 needs a design call before code work begins.

## Raw data

UAT findings sourced from the 2026-05-11 UAT session. No `logs/safety_reviews/` request ids were correlated for this audit; the path classification did not require them. A later workstream step may pull per-finding request ids if behavior reproduction is needed.
