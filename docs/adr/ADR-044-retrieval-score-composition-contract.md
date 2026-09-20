# ADR-044: Retrieval Score Composition Contract

**Status:** Proposed
**Date:** 2026-09-19
**Target:** v0.19.0
**Related:** ADR-005 (context ranking), ADR-015 (memory tiering, and its 2026-09-19 corrections), ADR-007 (project-scoped retrieval), ADR-018 (intent-aware type gating), issues #204, #205, #206, #211

## Context

Issue #204 proposed that Ember's retrieval scoring pipeline be substantially
reduced or removed on the grounds that bare cosine outperforms it. That
proposition was stress-tested against the code, the ADRs, the git history and
the committed ablation. It does not survive in the form it was filed -- but
neither does the status quo, and the reason both fail is the same.

The evidence for reduction is weaker than it appeared. Its headline
query-independence figure came from a harness variant not in this repository
and has been withdrawn. Its quality metrics are measured on a 42-fixture corpus
whose type, tier, role and age distributions are close to the inverse of the
deployed vault, and in which profile guaranteed slots consume 50-75% of the
delivered window. Its production probe did not reproduce the observation that
started the investigation, and contaminated the tiering data it reported (#206,
now fixed).

The evidence against the status quo is stronger, and does not depend on any of
that. Two findings are arithmetic:

**The composition order is wrong.** Scores pass through three stages that
compose with no normalization and no commensurability rule:

```
semantic_search   raw cosine + four additive terms   (query-independent swing 0.98)
apply_policy      x memory_weight, + recency/prefer terms, x TIER
rank              + _score_memory_item pile (+0.58/-0.71), x _temporal_decay_weight
```

The tier multiply lands at `service.py:181`; the ranker's additive pile lands
at `service.py:196`. So a cold record's cosine-derived signal is discounted by
0.3 while the query-blind constants are added afterward **at full magnitude**.
Tier does not attenuate the constants it was meant to attenuate. ADR-015's
amendment named this exactly -- "neither an exclusion nor a weight but an
erasure of the similarity signal with the metadata signal left intact" -- and
its implementation step 3 replaced the zero with 0.3 without changing the
order.

**The result is unreachable records.** An aged cold conversation record carries
`0.3 x 0.10 = 0.03` against `1.0` for a fresh one, because `_temporal_decay_weight`
multiplies again after the pile. Cosine is bounded by 1.0, so no aged cold
conversation record can outrank a fresh one at any cosine value. That falsifies
`COLD_MULTIPLIER`'s own stated rationale.

Two further facts bear on the remedy. Nothing in any design document calls for
scoring type and role twice, or recency three times; the two scoring layers
landed on 2026-03-17 in separate single-file commits, and ADR-005, which
specifies exactly one ranking stage, is dated 2026-03-19. And the constants'
stated provenance does not exist: `ranker.py` attributes them to an eval with
"15 benchmark cases" which has 5 and no graded relevance.

## Decision

Four decisions. The fourth is a deferral, recorded because deferring it was a
choice.

### 1. The defect is composition, not term count

Neither framing offered in #204 is adopted. "Remove the stack" is not what the
evidence measures: the naive arm bypasses the entire service layer -- type
gating, echo filters, dedup, the profile partition -- and ranks a candidate
pool up to 2.2x larger, so it cannot answer what removing the *constants*
would do. "The layers double-count" is real but insufficient: the arm that
removes the doubled type term at both stages moves delivered nDCG by 0.0000.

The defect is that three stages compose scores with no normalization, on a
scale nobody owns, in an order that inverts the intended effect of a
multiplier. Both the double-count and the 0.03 floor follow from that. Fixing
the composition addresses them together; removing terms addresses neither, and
would reverse an accepted ADR to do it.

### 2. Contract form: `score = cosine x prior x tier`, prior in cosine units

The `prior` is assembled **once**, from the consolidated type, role,
`content_kind` and length terms currently spread across `semantic_search` and
`_score_memory_item`. It is a bounded multiplier, not an additive pile. The
composed score stays in cosine units.

Staying in cosine units is a requirement, not an aesthetic preference. Three
consumers already threshold the composed score as though it were a similarity:

| consumer | threshold | position |
|---|---|---|
| `_apply_type_gate` (`service.py:345`) | `>= 0.25` | pre-multiplier |
| `_build_retrieval_confidence` (`prompt_builder.py:1121-1123`) | `>= 0.6` / `>= 0.4` | post-everything |
| vault badge gate (`openai_adapter.py:1837`) | `< 0.6` clears sources | post-everything |

The last two are user-visible and both are currently stuck. Aged records
finalize at 0.03-0.15x raw cosine, so the confidence block reads "low -- records
are old or weakly matched" on essentially every vault-grounded turn, and vault
citation badges are suppressed whenever `state_items` is empty. Neither is
reporting match quality; both are reporting the decay multipliers. A contract
that keeps the composed score in cosine units restores their meaning without
retuning them. Any contract that leaves those units -- an additive pile with a
budget cap, or rank fusion -- must recalibrate all three in the same change,
with tests, and rank fusion would additionally require replacing them with
something rank-based.

### 3. One age model, owned by ADR-015

`_temporal_decay_weight` is **absorbed into `TieringService`**, which gains a
per-type halflife, not kept alongside it. The ranker applies exactly one age
multiplier: tier.

Today there are two, and they compound. Tier decays on an exponential curve off
`last_retrieved_at or created_at`; `_temporal_decay_weight` decays on stepwise
per-type buckets off `created_at` only. ADR-015 governs the first and is silent
on the second, which has no ADR at all and is the larger multiplier in
production. ADR-015's amendment states that type "no longer affects decay",
which is false precisely because the second mechanism exists -- corrected in
that document on the same date as this one.

Absorbing rather than deleting preserves what the per-type curve is for: a
reflection and a conversation turn of the same age should not decay
identically. Deleting it would remove the last type-keyed age signal in the
system, which ADR-015 removed from tier deliberately and did not intend to
remove altogether. Absorbing follows the amendment's own stated principle --
answer the question "by reducing the number of mechanisms rather than adding
one."

This also makes ADR-015 true again as written, and gives the activation model
a single place where age is expressed.

### 4. Role scoring is deferred, not decided

Role survives the second-owner audit as the one capability with no alternative
owner. On a corpus that is 16,993 of 17,008 conversation records with role
metadata on every row and roughly half of them assistant turns, the combined
-0.45 role penalty is the only mechanism that demotes a plain assistant turn.
The exclusion filters at `semantic_search.py:464-494` match literal meta-markers
only; a role-tagged assistant turn passes every one of them. Assistant
self-echo is a named Key Design Risk with a documented production incident.

It is also the term with the worst evidence. The only measurement anyone has
says removing its retrieval-stage half *improved* ranked nDCG by 0.028, and
ADR-015's amendment already lists the three assistant penalties as one open
judgement to be made together.

So it is not decided here. The **incident-reproduction suite** decides it:
synthetic reproductions of the three incidents with real production provenance
(self-echo, third-party relational contamination, named-entity confusion),
against four arms -- shipped, prior-only, prior plus a role predicate, and
predicate-only. Correctness there is a construction fact rather than a graded
judgement, so it is immune to every labelling objection raised against the
fixture corpus. It has no blockers and commits as a permanent regression suite.

Until it reports, role stays in the prior at its current effective weight. The
open question is not whether the capability is real but whether it belongs in
score space at all, or as a predicate or per-authorship quota using the
existing `authorship` column.

## The bound, and what it is asserted against

The prior is bounded, and **the bound is asserted against the measured cosine
spread, not chosen by feel.** This is the part of the contract most likely to
decay into folklore, because every constant in the current stack got there that
way.

The reference quantity is the realized top-k spread of the embedder over the
corpus -- 0.1504 mean top-8 on the fixture corpus, measured, with the
production figure still unmeasured and needed. A prior permitted to move a
record further than the entire observable similarity range is not a tiebreaker;
it is the ranking signal, with cosine as a tiebreaker. The current stack is
already there: a query-independent additive swing of 0.98 at the retrieval
stage alone, against a spread of 0.15, is roughly 6.5:1.

Two conditions on the bound:

**It covers the composed multiplier, not the prior term.** Tier sits outside
the prior, and under decision 3 the absorbed age curve sits inside tier. A
prior bounded to `[0.5, 1.5]` composed with a tier floor of 0.3 yields an
effective floor of 0.15, and the current tier-times-decay product reaches 0.03.
Bounding the prior alone would leave the reachability defect exactly where it
is. The assertion is on `prior x tier`, and it is what decides whether a
highly relevant cold record can outrank a weakly relevant hot one -- the
property `COLD_MULTIPLIER`'s rationale claims and does not deliver.

**It is a test, not a comment.** One place computes the bound, one test asserts
it, and the ratio to the measured spread is stated in the code rather than
implied. The failure this prevents is the one already in the record: the entity
boost was sized against an *assumed* cosine variance of 0.3-0.5 and shipped
with a cap of 0.40, which is 2.7x the spread it was meant to nudge.

## Why this is not the alternative ADR-005 rejects

ADR-005 rejects "Pure vector similarity only" by name, on the grounds that it
ignores recency and metadata and yields noisy or stale context. That rejection
stands and this ADR does not disturb it.

This contract keeps every signal ADR-005 asked for. Type, role and
`content_kind` remain, in the prior. Recency remains, in tier. Metadata-driven
gating remains -- ADR-018 type gating, the authorship multiplier, ADR-007's
project boost, which ADR-015's amendment declares the context-conditioning half
of the activation model. Nothing is deleted.

What changes is how those signals combine with similarity: once instead of
twice or three times, multiplicatively instead of additively, bounded against a
measured quantity instead of unbounded, and after the similarity signal rather
than before the constants that swamp it. ADR-005 specifies exactly one ranking
stage; the second stage was never documented anywhere. **Consolidating to one
bounded stage restores ADR-005's design rather than reversing it.** The
pipeline drifted from that ADR two days after it was written.

The honest cost is that this is still a re-ranking stage, and a re-ranking
stage can still be wrong. A bounded multiplicative prior can suppress a
relevant record, just less far and more legibly than an unbounded additive one.
The trade-off being accepted is that a metadata prior is worth having if and
only if its authority over similarity is explicit and capped. The current stack
fails that test; bare cosine passes it by having no prior at all, at the cost of
every capability ADR-005 named. This contract is the third option, and it is
not free: it adds a bound that must be maintained, re-measured per embedder,
and defended against the same drift that produced the present state.

## What this ADR does not settle

Recorded explicitly so the gaps are not mistaken for oversights.

**Prior magnitudes.** Which multiplier each type, role and `content_kind` takes.
Pending the **read-only inversion and score-composition census** (experiment 1)
against the real vault: how often the pipeline overturns its own cosine
ordering, which terms are responsible, and what share of final-score variance
is query-independent. That measurement needs no relevance labels, which is why
it is the one that decides magnitudes. It was blocked on #206 and is now
unblocked, but should run after #211's index rebuild -- a census over an index
missing 1,787 records, including every profile record, would describe a
transitional state.

**Per-type halflife values.** Which halflife each memory type takes under
decision 3, and whether the absorbed curve keeps `_temporal_decay_weight`'s
stepwise form or adopts tier's exponential one. Same dependency. The functional
form should be settled before the values: two curves of different functional
form cannot be compared by tuning.

**Role.** Decision 4, pending experiment 2.

**The measured production cosine spread.** The 0.1504 figure is from the
fixture corpus. The bound needs the production number and it has never been
taken.

**Whether query-independent convergence is a defect at all.** On the fixture
corpus an ideal retriever scores mean pairwise cross-query Jaccard of 0.0779
against 0.0771 for uniform random, so the metric has no discriminating
reference value at the low end. A continuity layer arguably *should* return
overlapping records across related questions about one life. This is a
judgement call, not an empirical question, and this ADR does not make it.

## Consequences

**Positive**

- One owner for the metadata prior, one for age, and a stated bound for each.
- The 0.03 reachability floor goes, by construction rather than by retuning.
- The confidence hedge and vault citation badge start reporting match quality
  again, with no threshold changes.
- The double-count is resolved as a side effect of consolidation rather than as
  a separate cleanup.
- ADR-015 becomes true as written.
- The scoring budget becomes a number in one place that a test asserts, which
  is the condition under which "nobody owns the budget" stops being true.

**Negative**

- Every scoring constant in the repo is re-expressed. Roughly 142 tests across
  nine files assert current behaviour and will need review; those that pin a
  magnitude rather than an ordering are the ones to re-derive rather than
  re-fit.
- The bound is a new maintenance obligation. It is embedder-dependent and must
  be re-measured when the embedding model changes.
- Absorbing `_temporal_decay_weight` moves a per-request computation into a
  nightly one, so a record's age multiplier becomes up to a day stale. Already
  true of tier; now true of all age handling.
- Multiplicative composition cannot express a term that should apply regardless
  of similarity. Nothing in the current stack needs that, but it is a real
  expressiveness loss and a future term might.
- This does not address corpus health, and CLAUDE.md core rule 6 puts source
  quality before retrieval sophistication. #211 is the prerequisite, not this.

## Alternatives Considered

- **Reduce to bare cosine (#204 as filed)** -- rejected. Reverses ADR-005 on
  evidence whose headline figure has been withdrawn, whose corpus inverts the
  deployment on every dimension the stack keys on, and which never isolated the
  constants: the naive arm bypasses the whole service layer.
- **De-duplicate only** -- rejected as insufficient. Removing the doubled type
  term at both stages moves delivered nDCG by 0.0000. It is a real conformance
  defect against ADR-005 and is fixed here as part of consolidation, but it is
  not the remedy on its own.
- **Additive pile with a measured budget cap** -- rejected. Smallest diff, but
  the composed score leaves cosine units, so all three absolute thresholds need
  recalibrating in the same change, and an additive prior can still outrank
  similarity outright.
- **Rank fusion (RRF)** -- rejected for now. Structurally immune to the
  commensurability problem, but it destroys score as a quantity and would
  require replacing the confidence hedge, the badge gate and the type gate with
  rank-based equivalents. Largest blast radius. Worth revisiting if the bound
  proves unmaintainable.
- **Move the prior out of score space entirely** (predicates and per-class
  quotas, `score = cosine x tier`) -- not rejected, deferred. It is the
  strongest option for role specifically and experiment 2 tests exactly that.
  Rejected as a blanket approach because quota sizes become the new unowned
  parameter and an unfillable-quota rule is needed.
- **Change nothing, fix corpus health first** -- rejected as an either/or. #211
  is sequenced first and this ADR says so, but the composition defects are
  arithmetic and do not become false once the index is repaired.

## References

- ADR-005 -- Context Ranking. Specifies one ranking stage; rejects pure vector
  similarity.
- ADR-015 -- Memory Tiering, with the 2026-09-19 corrections on type-keyed
  decay and cold headroom.
- ADR-007 -- Project-Scoped Retrieval. The +0.15 boost, declared by ADR-015's
  amendment to be the activation model's context-conditioning term.
- ADR-018 -- Intent-Aware Type Gating.
- Issue #204 -- retrieval scoring budget, with its corrections comment.
- Issue #211 -- indexing gap; prerequisite for experiment 1.
- Issue #206 -- `/debug-context` mutating retrieval state; was the blocker for
  experiment 1, fixed.
- `logs/retrieval_eval/published/` -- the canonical ablation run and its
  registry.
