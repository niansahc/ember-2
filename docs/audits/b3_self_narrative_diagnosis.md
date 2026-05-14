# B3 Diagnosis - Self-Narrative Hallucination (v0.18.0 UAT)

## Purpose

UAT finding B3 from the 2026-05-11 session: Ember fabricated a false statement about her own system design ("search functionality is broken by design"). No detector caught it. The prior audit (`docs/audits/refuse_redirect_uat_v018.md`, commit eb9ef88) did NOT classify B3 - this diagnosis fills that gap. The intended outcome is a clear answer to whether a heuristic contradiction-detector against the nature doc plus identity rules can plausibly catch this failure mode, or whether the corpus is structurally mismatched to the target and the fix architecture needs to change.

No code changes in this step. Documentation only.

## Procedure

Files read in order:

1. `../release-v0.18.0/Testingember-conversation-2026-05-11.md` (the UAT transcript) - lines 110-145 covering the turn where B3 fired.
2. `config/nature.yaml` (62 lines) - the "who Ember is" character document, candidate corpus 1.
3. `config/identity_rules.yaml` (184 lines) - the prompt-layer behavioral rules, candidate corpus 2.
4. `config/constitution.yaml` (lines 28-42) - the Truthfulness principle including the rule "Do not fabricate facts, sources, or capabilities."
5. `README.md` (lines 25-33) - the "What Ember-2 Does" feature description.
6. `src/llm/prompt_builder.py` (sections referenced) - authority rules and prompt structure.
7. `docs/adr/` index - searched for capability assertions about Ember's search subsystem.
8. `docs/audits/refuse_redirect_uat_v018.md` - confirmed B3 is absent from the prior classification audit.

All read-only. No code edited.

## Findings

### Q1. Actual UAT phrasing (confirmed verbatim from transcript)

Two consecutive turns from the 2026-05-11 UAT session (paraphrased for ASCII compliance; original transcript contains em dashes):

Turn at 10:11 AM (Ember to user, in response to the user saying the web search was failing): the assistant declared the search functionality is broken by design, that it is not supposed to work that way, and that the user should not try to get her to use search. The fabrication has three parts:
- Claim that search is broken (capability claim).
- Claim that "by design" - that is, the breakage is intentional (design intent claim).
- Implicit refusal to use the tool.

Turn at 10:12 AM (Ember in the next message): closed with the engagement-question pattern "what is the issue you are trying to resolve" - which is the B6 surface form already fixed in PR #81.

The B3 finding is the 10:11 AM turn. The 10:12 turn is incidentally B6 (and is caught by the B6/B7 fix landing in PR #81).

### Q2. Catalog of artifacts: does anything contradict the B3 claim?

Walked the candidate corpora for any explicit assertion about the search subsystem.

| Artifact | Contains capability assertions? | Notes |
|---|---|---|
| `config/nature.yaml` | **No** | Pure character description. Entries are dispositions ("does not hedge", "sees absurdity"). No statements about systems, features, or technical capabilities. |
| `config/identity_rules.yaml` | **No** | Behavioral rules prescribing how Ember responds. The `ask_first_confirmation` rule references search ("offer to search", "ask one short question, offering to search") but as a behavioral pattern, not a capability assertion. |
| `config/constitution.yaml` | **Indirectly** | Line 32: "Do not fabricate facts, sources, or capabilities." This is the principle B3 violates, but it is a prohibition, not a ground-truth claim about what Ember's capabilities ARE. |
| `README.md` | **In marketing prose** | Lines 25-33 list feature categories ("structured knowledge retrieval (RAG)", "long-term pattern recognition"). Search is implicit in "RAG"; web search is not explicitly named. Not in parseable corpus form. |
| `src/llm/prompt_builder.py` | **No** | Contains authority rules and prompt-builder logic. Authority rules instruct the model ("answer the question directly using the facts they contain") but never assert capability facts in a corpus-extractable form. |
| `docs/adr/` (37 ADRs) | **No** | ADRs document design decisions. None explicitly state "search is operational" or "search executes through SearXNG" in a way a parser would extract. |

**Single most important finding:** no artifact in the codebase contains a parseable assertion like "search is operational" or "web search executes via SearXNG" or anything else that would directly contradict "search is broken by design". The contradiction the proposal needs to detect is **with reality**, not **with a documented claim**.

### Q3. Path classification

The 10:11 AM B3 response was emitted to the user. No grounding check fired (the response was not flagged as a web-search turn; it appears to have been classified as `default` or similar). No coaching_filter or deviation_detector intervened.

**Classification: normal_generation.** No refuse_redirect. No intervention.

The 10:12 AM next turn (B6 surface form) similarly was emitted without coaching_filter intervention - because before PR #81, the relevant patterns were not in `_COACHING_CLOSINGS`.

### Q4. Right corpus source

The proposed corpus (`nature.yaml` + `identity_rules.yaml`) **does not contain** anything that would be matched-and-flagged for the B3 fabrication. Four alternative architectures, each with trade-offs:

| Option | Approach | Coverage | Constraint impact |
|---|---|---|---|
| **A. Narrow scope** | Detector only catches negations of character/behavior claims sourced from nature.yaml + identity_rules.yaml. Example caught: a response saying "Ember does hedge to manage comfort" contradicts "does not hedge". | **Will not catch the actual B3 example.** Search-system capability claims are out of scope by design. | Stays within the user's locked constraint "no third copy of capability information". Honest about coverage. |
| **B. Add capabilities corpus** | New file `config/capabilities.yaml` enumerating canonical statements about Ember's subsystems ("Web search is provided by SearXNG", "Search runs on every web_search-intent classification", etc.). Detector reads all three sources. | **Catches B3-class fabrications directly** when the fabricated negation aligns with a canonical positive claim. | **Violates the user's locked constraint** "no third copy of capability information". Would require the user to override that constraint. Maintenance burden: the corpus must stay current with the actual codebase. |
| **C. Class-based detection, not contradiction** | Detector flags any sentence asserting a capability about Ember's own systems (regardless of whether the claim is true or false). The flag prompts an audit log entry; no automatic blocking. Reviewer or operator decides per-flag whether the claim was justified. | Catches a superset of B3-class issues including genuine system descriptions Ember should be allowed to make. **Higher false-positive rate** but no missed fabrications. | Avoids the corpus problem entirely. Adds an operator-attention burden. |
| **D. Route to constitutional review** | Use the existing constitution rule "Do not fabricate ... capabilities" (`constitution.yaml:32`) and route sentences that look like self-system claims to `ResponseReviewService` for LLM judgment with that principle. | Catches B3-class fabrications **if** the review LLM correctly identifies fabrication. | Inherits the one-shot-LLM unreliability the B5 audit demonstrated. Likely high false-negative rate. |

**The proposal as written is closest to Option A, scoped to whatever the corpus happens to contain.** As designed, it would not catch the actual B3 example - because nature.yaml and identity_rules.yaml don't contain the contradictory claim. The fix as proposed would be effective at catching a class of contradictions Ember is unlikely to produce often (character/behavior contradictions, e.g. "Ember does not care about you") while missing the class she did produce in UAT (system-capability fabrications).

## Root cause

**B3 missed because no detector exists for self-narrative capability claims, and no canonical document in the codebase enumerates Ember's actual system capabilities in a parseable form - meaning a contradiction-detector cannot distinguish true claims from fabrications even in principle without first deciding where the ground truth lives.**

The proximate cause is the absence of a detector. The deeper cause is the absence of a ground-truth corpus for "what Ember's systems actually do". The constitution prohibits fabrication of capabilities (`constitution.yaml:32`), but no document asserts what the capabilities are.

## Decision tree branch - corpus architecture

The proposal as written assumes the existing nature.yaml + identity_rules.yaml are sufficient corpus. They are not, for the specific failure class B3 demonstrated. Two viable paths forward:

1. **Accept the narrow scope (Option A).** Ship the detector as proposed, with explicit documentation that it catches character/behavior contradictions only - not system-capability fabrications. The actual B3 example will not be caught. B-NARR-001 in KNOWN_ISSUES gets a second clause: "system-capability claims (e.g. claims about search, memory, review pipeline) are out of detection scope." This is intellectually honest but does not actually fix B3.

2. **Accept the architecture change.** The fix is no longer a contradiction-detector. It becomes either:
   - A class-based detector (Option C) that flags self-system claims for audit without making truth judgments, OR
   - A new corpus file (Option B) that the user has explicitly forbidden, requiring an override of the locked constraint.

**Routing recommendation for the user:** before continuing the grill on regex mechanics, threshold values, etc., make the corpus architecture decision. If staying with Option A, the rest of the grill (negation patterns, threshold, overlap scorer) proceeds but its target is narrower than what B3 surfaced. If shifting to B or C, several earlier grill answers need to be revisited because the mechanism changes shape.

## Related: B-LOOP-001 (separate issue, added to KNOWN_ISSUES.md in the same commit)

The 21:07 PM session today (2026-05-13) produced an unrelated within-response repetition loop on a meta-query routed to web_search. Diagnosed separately; not B3 territory. Tracked as B-LOOP-001 in KNOWN_ISSUES.md.

## Out of scope (deferred to subsequent steps)

- Any code change to `src/safety/`, `config/`, or test files.
- A specific architecture decision among Options A/B/C/D - the user must choose before fix work proceeds.
- A capability-corpus file (if Option B is selected).
- The within-response repetition loop (covered separately by B-LOOP-001).
