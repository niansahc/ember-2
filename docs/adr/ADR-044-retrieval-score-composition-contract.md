# ADR-044: Retrieval Score Composition Contract

**Status:** Accepted
**Date:** 2026-09-19
**Amended:** 2026-09-21 (decision 4, role -- see 4a); 2026-09-25 (production cosine spread measured -- figures only, no decision changed); 2026-09-28 (implemented; bound derived, reachability measured, two implementation defects recorded, one Consequences claim falsified); 2026-09-30 (the bound was false as written -- five out-of-bound terms closed, magnitudes re-derived in log space from the first converged Sobol run, #250 resolved)
**Target:** v0.19.0
**Related:** ADR-005 (context ranking), ADR-015 (memory tiering, and its 2026-09-19 corrections), ADR-007 (project-scoped retrieval), ADR-018 (intent-aware type gating), issues #204, #205, #206, #211, #218, PR #217 (experiment 2), PR #236 (production cosine spread)

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

### 4a. Amendment (2026-09-21): role moves to a hard predicate

Experiment 2 has run (PR #217, `tests/test_incident_reproduction.py`). The
deferral is closed. **Role leaves the scoring budget and becomes a hard
predicate on the existing `authorship` column.** No schema change: the column
was added by f9f5dda and is already populated and indexed.

The measurement, at the model-visible window after the four-item slice:

| incident | endpoint | shipped | reduced | + role predicate | + entity boost |
|---|---|---|---|---|---|
| self-echo | presence | PASS | **FAIL** | **PASS** | FAIL |
| relational contamination | presence | PASS | PASS | PASS | PASS |
| entity confusion | order | PASS | **FAIL** | FAIL | **PASS** |

Three findings, and together they narrow role to a single job that a predicate
does completely.

**The pile is responsible for exactly one incident, and the predicate covers
it.** Self-echo recurs the moment the role pile is removed and is fully
suppressed by a predicate over the same records -- decoy gone, answer still
delivered. A predicate is not an approximation of the pile here; on this
incident it is a substitute for it.

**The pile was expensive for what it bought.** Its combined swing is -0.45
against a measured mean top-8 cosine spread of 0.0815 on the production
corpus: roughly five and a half times the entire observable similarity range,
spent on one term, to do a job a WHERE clause does at no cost to the budget at
all. Under the bound this ADR establishes, a term that large would have had to
justify itself against the spread, and it cannot. (This argument was first
written against the fixture-corpus figure of 0.1504, where the same swing is
three times the range. The production measurement makes it stronger, not
weaker -- see "The bound, and what it is asserted against".)

**Two capabilities that looked like role's were not.** Relational
contamination passes in *every* arm, including the fully reduced one, because
the authorship multiplier is a gate rather than a class constant and is
retained throughout -- that capability belongs to the authorship gate and was
never the role pile's. Entity confusion is resolved only by the entity boost, a
query-dependent term outside the role question entirely. Both were cited
during the grill as reasons the role pile might be load-bearing. Neither is.

What this does not say: assistant-turn demotion is unnecessary. It is
necessary, it is the one capability with no second owner, and the reduced arm
failing self-echo is the evidence. The change is only where it lives. Selection
is the better home because a predicate cannot be outvoted by an unrelated
constant, states its intent in the query rather than in a magnitude, and costs
nothing against a bounded budget.

Two things this amendment does not settle. Whether the predicate excludes
assistant-authored conversation outright or caps it by quota -- outright is the
simpler default and what experiment 2 measured, but a quota preserves access
for the case where an assistant turn is genuinely the best record, and nothing
here tests that case. And the interaction with the authorship population
problem: the predicate keys on the same column whose `third_party` value now
has zero rows behind it (issue #218), so whichever predicate ships must be
written against the values the column actually carries rather than the ones
f9f5dda assigned.

**Settled on implementation (2026-09-28).** Exclusion outright, unconditionally,
which is what experiment 2 measured. The untested quota case stays untested and
out of scope: a hedge shipped in advance of the measurement that would justify
it is how the constants this ADR is about got there. On the column values, the
predicate keys on metadata `role` rather than on an `authorship` value, because
`third_party` has no rows behind it.

A third thing, which this amendment did not anticipate because it did not occur
to anyone that the predicate might be scoped more narrowly than the measurement:
the first implementation gated exclusion to relational queries and therefore did
not fire on the self-echo incident at all. See "Defect found: 4a's predicate did
not cover 4a's incident" below.

## The bound, and what it is asserted against

The prior is bounded, and **the bound is asserted against the measured cosine
spread, not chosen by feel.** This is the part of the contract most likely to
decay into folklore, because every constant in the current stack got there that
way.

The reference quantity is the realized top-k spread of the embedder over the
corpus. **On the production corpus it is 0.0815** (median 0.0707, stdev
0.0553), measured on the corrected index in #236 by
`tools/cosine_spread.py`.

The definition, stated because two spreads measured differently are not
comparable and this document carries both:

> For each query, take the top 8 results by **raw cosine, before any
> additive or multiplicative adjustment**, and take `max - min` across those
> 8. The reported figure is the mean of that quantity over the query set.

Raw rather than composed is a requirement, not a detail. The composed score is
the quantity this bound governs; measuring the spread on it would compare the
prior against itself and always look reasonable.

| corpus | mean top-8 raw spread | status |
|---|---|---|
| production (#236, 36 queries) | **0.0815** | the figure the bound is asserted against |
| fixture (earlier, definition not reproducible in-repo) | 0.1504 | prior reference, retained for comparison only |

The fixture figure is kept visible because the arguments in this document were
originally written against it, and because its own definition cannot be
re-derived from this repository -- it is a reference point, not a second
measurement of the same thing.

**This tightens the ADR; it does not loosen it.** Production resolves 0.54x the
similarity range the fixture corpus does, so every bound in this document that
was asserted against 0.1504 was asserted against a spread roughly twice as wide
as the real one. Each ratio below therefore understated the problem by about a
factor of two. No decision changes as a result: they all move further in the
direction they already argued.

A prior permitted to move a record further than the entire observable
similarity range is not a tiebreaker; it is the ranking signal, with cosine as
a tiebreaker. The current stack is already there: a query-independent additive
swing of 0.98 at the retrieval stage alone, against a spread of 0.0815, is
roughly **12:1** (it was stated as 6.5:1 against the fixture figure).

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
with a cap of 0.40, which is 4.9x the measured production spread it was meant
to nudge (2.7x against the fixture figure). The assumption was wrong by a
factor of four to six, which is the case for measuring rather than assuming.

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

**Prior magnitudes.** ~~Which multiplier each type, role and `content_kind`
takes.~~ Closed 2026-09-28 -- derived from Sobol ST on the delivery endpoint;
see "Closed: prior magnitudes" in the amendment below. The original text
follows.

Pending the **read-only inversion and score-composition census** (experiment 1)
against the real vault: how often the pipeline overturns its own cosine
ordering, which terms are responsible, and what share of final-score variance
is query-independent. That measurement needs no relevance labels, which is why
it is the one that decides magnitudes. It was blocked on #206 and is now
unblocked, but should run after #211's index rebuild -- a census over an index
missing 1,787 records, including every profile record, would describe a
transitional state.

**Per-type halflife values.** ~~Which halflife each memory type takes under
decision 3, and whether the absorbed curve keeps `_temporal_decay_weight`'s
stepwise form or adopts tier's exponential one.~~ Closed 2026-09-28.
Exponential form, fitted at the knee of each replaced ladder: ephemeral 15
days, default 52, reflection 122. See "Defect found: three fitted halflives,
two implemented" below, which is also where the form question is answered. The
original text follows.

Same dependency. The functional form should be settled before the values: two
curves of different functional form cannot be compared by tuning.

**Role.** ~~Decision 4, pending experiment 2.~~ Settled by the 2026-09-21
amendment above: role leaves the budget for a predicate on the `authorship`
column. What remains open is narrower -- whether the predicate excludes
outright or caps by quota, and which column values it keys on given that
`third_party` currently has no rows behind it (issue #218).

**The measured production cosine spread.** ~~The 0.1504 figure is from the
fixture corpus. The bound needs the production number and it has never been
taken.~~ Closed 2026-09-25. Measured at **0.0815** mean top-8 raw spread on the
corrected index (#236), against 0.1504 on the fixture corpus. The bound and
every ratio derived from it are stated against the production figure above.

**Whether query-independent convergence is a defect at all.** On the fixture
corpus an ideal retriever scores mean pairwise cross-query Jaccard of 0.0779
against 0.0771 for uniform random, so the metric has no discriminating
reference value at the low end. A continuity layer arguably *should* return
overlapping records across related questions about one life. This is a
judgement call, not an empirical question, and this ADR does not make it.

## Amendment (2026-09-28): implementation, measurement, and four corrections

The contract shipped. This section records the numbers it was asserted against,
two defects found while implementing it, two findings about the test suite that
are worth more than the tests they came from, and one claim above that the
measurement falsifies.

### The tier bound, derived

`prior x tier` is bounded to `[0.8722, 1.1278]`, which is `1 +/- B` where

```
B = 0.0815 / 0.6375 = 0.12784
```

is the measured production top-8 raw cosine spread over the mean rank-1 cosine
(#236). The budget is split evenly **in the multiplicative sense**, `sqrt(0.87216)`
to each factor:

```
tier  in [0.9339, 1.0]
prior in [0.9339, 1.1278]
```

Even, because that is the only split under which the composed assertion and the
per-factor floors are the same statement: the worst case of both factors
together lands exactly on the bound. An arithmetic split would leave the product
below the floor, and the bound would then be two different claims depending on
which one you checked.

The split is **asymmetric at the top on purpose**. Tier's ceiling is 1.0 while
the prior reaches `COMPOSED_MAX`, so tier can only ever discount. A record
should be spared the cold discount, not promoted for being hot.

Tier weights become `0.9339 / 0.9664 / 1.0`, replacing `0.3 / 0.7 / 1.0`. Warm
is the geometric midpoint, not the arithmetic one, for the same reason the
contract is multiplicative: the three tiers are points on a ratio scale.

### Reachability: two corpora, two numbers, and why

The property `COLD_MULTIPLIER`'s rationale claimed -- that a highly relevant
cold record can outrank a weakly relevant hot one -- holds exactly when

```
(c1 - ck) / c1 > 1 - composed_floor
```

for a query's top-k raw cosines. Under the bound the threshold is `B` itself,
0.1278; a cold record needs a cosine advantage of `1/0.8722 - 1` = 14.7%.

|  | mean rank-1 cosine | mean top-8 spread | mean relative spread | reachable |
|---|---|---|---|---|
| production (#236, 36 queries) | 0.6375 | 0.0815 | 0.1278 | **16 / 36** |
| synthetic corpus (2026-09-28, 36 queries) | 0.5817 | 0.0540 | 0.0910 | **7 / 36** |

Both figures stand. They are not reconciled here and neither supersedes the
other, because they measure different corpora.

**Reachability yield is corpus-dependent, by construction.** `B` is derived from
a measured mean relative spread, so a corpus whose own mean relative spread sits
below `B` clears the threshold on fewer of its queries. Production's mean
relative spread is 0.1278, which is `B`, so roughly the above-mean half of its
queries clear it. The synthetic corpus resolves 0.0910, below `B`, so fewer do.
Neither number is a property of the contract on its own; each is a property of
the contract composed with an embedder and a corpus.

What is NOT corpus-dependent is the baseline. On the synthetic corpus the old
contracts reproduce exactly:

| contract | threshold | reachable |
|---|---|---|
| `0.3 x 0.10` as shipped | > 0.9700 | **0 / 36** |
| `0.3` tier alone, decay absorbed | > 0.7000 | **0 / 36** |
| `prior x tier` bounded | > 0.1278 | 7 / 36 |

The intermediate row is the one worth keeping. Absorbing the temporal decay
raises the composed floor from 0.03 to 0.30 and still delivers nothing, which
is why decision 3 on its own was never the remedy.

### The Morris inversion

Sensitivity screening over the migrated harness, on a synthetic trace, ranked by
mu* on the score endpoint:

```
ret.lexical.term_hit    0.1296   query-DEPENDENT
prior.kind.experience   0.0580   query-independent, bounded
prior.recency.d7        0.0541   query-independent, bounded
tier.hot                0.0219
tier.cold               0.0110
tier.profile_bypass     0.0030
```

Before this change `tier.cold` was the dominant parameter in the table, and the
screening harness's own test asserted it: `ranked.index("tier.cold") <
ranked.index(...)`. That assertion has been inverted rather than repaired.

The mechanism is the sweep interval. Under the old ranges `tier.cold` was
screened over `[0, 1]`, a width of 1.0. Under the bound it sweeps
`[0.9339, 1.0]`, a width of 0.066 -- among the narrowest in the vector.

This is ADR-044's central claim with a number attached for the first time. The
Context section above states it qualitatively: a query-independent additive
swing of 0.98 against a spread of 0.0815 is roughly 12:1, so "the metadata was
the ranking signal and cosine was the tiebreaker." The table above is the
reverse relation, measured: a query-dependent term at the top, tier near the
bottom. The test now asserts that ordering, so a future change that returns
authority to query-independent metadata fails rather than being discovered by
the next census.

The honest caveat: mu* is computed over the sweep intervals, and those
intervals changed in this PR. The comparison is therefore not
apples-to-apples with the pre-ADR-044 screening runs, and it is not meant to
be -- screening a bounded term outside its bound measures a system the contract
forbids. The break in comparability is deliberate and is recorded in
`tools/retrieval_trace/ranges.py`.

### Defect found: 4a's predicate did not cover 4a's incident

Amendment 4a decided that role leaves the scoring budget for a hard predicate,
on the measurement that a predicate suppresses the self-echo incident
completely. As first implemented, the predicate was gated to relational
queries -- narrower than 4a measured, on the reasoning that UAT-005 was a
relational incident.

**The self-echo incident's query is not relational.** `_matches_relational_query`
returns False for it. So the predicate did not fire on the one incident this
amendment cites as the single capability role had no second owner for, the role
pile was gone, and nothing replaced it. Assistant self-echo -- a named Key
Design Risk with a documented production incident -- was unprotected on every
non-relational query.

Two things made it invisible. The relational incident passes in every arm 4a
measured, including the fully reduced one, so the surviving coverage looked
like evidence the predicate worked. And `tests/test_incident_reproduction.py`
applied its own local filter rather than calling the shipped function, so the
permanent regression suite reported a PASS for code it never executed.

Corrected: exclusion is unconditional, and the suite calls
`role_predicate.apply`. A regression suite that reimplements the thing it
protects protects nothing.

### Defect found: three fitted halflives, two implemented

Decision 3 absorbs `_temporal_decay_weight` into `TieringService` as a per-type
halflife. Three were fitted, at the knee of each ladder they replace:

```
ephemeral   x0.25 at 30 days  ->  30 / log2(1/0.25)  =  15 days
default     x0.30 at 90 days  ->  90 / log2(1/0.30)  =  52 days
reflection  x0.60 at 90 days  ->  90 / log2(1/0.60)  = 122 days
```

Two were implemented. The default family fell through to the configured global
`TIER_RECENCY_HALFLIFE_DAYS`, which is 30 -- so the fitted 52 was stated in a
comment and never used, and the absorbed `_DEFAULT_DECAY` ladder was replaced by
a curve nobody derived. Fixed: the catch-all is 52.0.

`_DEFAULT_DECAY` was itself the catch-all, so every type falls in exactly one
of the three families and a configurable fallback could never be reached. The
parameter is therefore gone rather than left unreachable.
`TIER_RECENCY_HALFLIFE_DAYS` no longer affects tier decay. It keeps its other
consumer, so it is not dead config, but the nightly age curve is now fitted
rather than configured.

This also settles what "What this ADR does not settle" left open on functional
form: exponential, not stepwise. Tier's own recency was already exponential and
the point of decision 3 is to have one age model; keeping the stepwise form
would have been two forms inside one mechanism.

### What the removed terms were actually doing

The clearest demonstration in the record that the metadata was the ranking
signal, found by removing it.

A test fixture writes four records with a flat `[0.1]*768` vector and searches
with a query embedded for real. Raw cosine between them is **0.0052** -- every
record a total non-match. Before this change, two of the four cleared
`_apply_type_gate`'s `>= 0.25` **similarity** floor:

| record | raw cosine | score before | score after | gate before | gate after |
|---|---|---|---|---|---|
| conversation / user | 0.0052 | 0.5352 | 0.1352 | **PASS** | DROP |
| journal / user | 0.0052 | 0.3352 | 0.0352 | **PASS** | DROP |
| conversation / assistant | 0.0052 | -0.1748 | 0.0652 | DROP | DROP |
| profile | 0.0052 | 0.0752 | 0.0352 | DROP | DROP |

`memory_type_adjustment` and `source_quality_adjustment` contributed up to
**+0.40** of query-independent lift -- enough to carry a record with 0.0052
similarity across a similarity threshold. The Context section argues the
metadata outweighed cosine by roughly 12:1; here it was sufficient on its own,
with cosine contributing nothing.

This is also why the type gate's pass rate barely moves on a corpus with
realistic cosines (93.08% to 91.00%, -2.09pp): where raw cosines are genuinely
above the floor the pile was padding records that would have passed anyway. The
floor is unchanged and should stay unchanged.

### Test-suite finding: six tests green over an empty packet

Four `build_context` tests patched `src.retrieval.semantic_search.embed_text`
but not `src.retrieval.embed_memory.embed_text`, which is the binding
`ContextRetriever.retrieve` actually uses. The stored records were stubbed and
the query vector was not, so those tests searched their corpus at 0.0052
cosine. One failed outright once the additive lift was removed. **Six were
green, and none were testing what they claimed.**

The pattern is the finding, and it generalises past this defect:

> A test asserting an ABSENCE needs a positive precondition that the thing
> could have happened.

"No stats write", "records nothing", and `commit_delivery() == 0` are all
trivially true of zero delivered items. Each is now paired with an assertion
that the packet is non-empty. The non-vacuity guard is the durable fix; the
patch path was only the proximate cause, and the next way to empty a packet
will not be a patch path.

Worth recording separately: **the correct pattern already existed in the
repository.** `tests/test_debug_context_read_only.py` and
`tests/test_retrieval_stats_read_only.py` both patch both bindings and both
carry a comment describing this exact defect, including the observation that it
stays invisible until something downstream starts excluding on score. The two
broken files did not use it. A fix documented in one place does not propagate
to another by being correct.

### The clamp is enforcement, not a safety net

`clamp`'s docstring states the test for this: "If it fires often, the budget is
under-specified rather than merely tight, and that is a finding about the
derivation." It fires often, at shipped magnitudes.

Over the reachable branch space -- 5 content kinds x 3 length bands x 6 recency
buckets = 90 combinations -- **9 clamp low and 0 clamp high.** All nine are
short records, which is **30% of the short-record space**. Inside that region
the prior is a CONSTANT at `PRIOR_MIN`: `content_kind` and recency are erased,
because the product has already passed the floor before they are considered.

The cause is that the budget is allocated per term and spent multiplicatively.
`deviation_i = (1 - PRIOR_MIN) * ST_i / ST_max` gives each term independently
the right to consume the prior's entire half of the bound, and the largest-ST
term takes exactly that -- `LEN_UNDER_50 == PRIOR_MIN` by construction. Any
short record that also takes a downward kind or recency term is therefore
outside the bound before it is clamped back to it.

This is the same defect as the short-user-content finding below, one level up
and stated generally: the derivation allocates per term while the composition
multiplies terms. The remedy is to allocate in log space across the three
mutually-exclusive families, so the product is inside the bound by
construction and the clamp becomes unreachable:

```
log_dev_i = log(PRIOR_MIN) * ST_i / sum(ST over the worst one-per-family case)
```

Terms within `_KIND_FACTORS`, `_LENGTH_FACTORS` and `RECENCY` are mutually
exclusive, so the worst case sums three terms rather than six and the headroom
cost is small. The clamp would then remain as an assertion rather than as the
enforcement.

Not done here: it re-derives every prior magnitude, which is a change with its
own measurement, and this PR's remit was to implement the contract as derived
rather than to re-derive it. **Done in the 2026-09-30 amendment below**, where
the allocation moves to log space and the clamp becomes unreachable. Recorded
at the time with two tests --
`test_the_clamp_fires_on_short_records_at_shipped_magnitudes` pins the rate so
the fact is asserted rather than rediscovered, and `test_the_clamp_rate_is_observable`
pins that `prior.clamped_low` reaches the traffic window, because the counters
were added and then targeted by no query, so the mechanism built to surface
this would not have surfaced it.

### Short user-authored content is net-penalised

A consequence of deriving prior magnitudes from Sobol ST without checking how
the retained terms interact by sign.

```
LEN_UNDER_50       0.9339      (ST 0.1930, the largest retained term)
KIND_USER_CONTENT  1.0248      (ST 0.0724)
product            0.9570
```

A 46-character user turn entering at 0.5 finalizes at **0.4785**. The length
term is the larger of the two and points down, so user-authored content short
enough to trip it is demoted overall -- despite `content_kind` being the term
meant to favour it.

This matters more than the arithmetic suggests, because most user turns in a
conversational vault are short. The magnitudes are individually defensible and
their composition was not checked; ST measures each term's effect on delivery
independently, and nothing in the derivation asks whether two retained terms
routinely co-occur on the same records and cancel.

Not fixed here. The open question is whether the length term should apply to
user-authored content at all, which is a change to the prior's structure rather
than a retune of a constant, and it needs its own measurement. Tracked as
issue #250.

Not a regression, and worth saying why: the old additive ladder had the same
sign problem (`rank.len.lt50` -0.04 against `rank.kind.user_content` +0.05),
but `rank.role.user` at +0.12 dominated both and carried short user turns
anyway. Role has since left score space for a predicate, so the protection
that masked this interaction is gone. The interaction is newly VISIBLE rather
than newly introduced.

### Closed: prior magnitudes

"What this ADR does not settle" listed these as pending the read-only inversion
and score-composition census. They are now derived from Sobol total-order
indices on the DELIVERY endpoint (#232), which measures whether a term changes
what the model receives rather than whether it moves a number:

```
deviation_i = (1 - PRIOR_MIN) * (ST_i / ST_max)
```

`ST_max` is the largest ST among retained terms. So the term the measurement
says matters most may consume the prior's entire half of the bound on its own,
and everything else scales below it in proportion. Direction is carried over
from the previous constant's sign; only magnitude is re-derived.

Terms whose ST says they never reorder delivery are set to 1.0 and gone. The
type ladder is the whole of that group.

### The type ladder: a defect fix, not pruning on low sensitivity

Stated explicitly because the amendment above could be read as reversing
decision 1, and it does not.

Decision 1 rejects "the layers double-count" as a sufficient remedy and records
that removing the doubled type term at both stages moves delivered nDCG by
0.0000. That finding stands. The type ladder is removed **because it was
counted twice** -- once in `semantic_search`, once in `_score_memory_item` --
which is a conformance defect against ADR-005's single ranking stage. Its zero
sensitivity is why removing it is SAFE, not why it is right.

The distinction is load-bearing for anything that follows. "Low sensitivity,
therefore delete" would license deleting any term the current corpus does not
exercise, which is the reasoning `parameter_coverage` exists to prevent: a zero
index means "this corpus never exercised it" at least as often as it means
"this term does not matter."

## Amendment (2026-09-30): the bound was false as written; closing it

The 2026-09-28 amendment recorded the contract as implemented. It also left this
document claiming something untrue: that composition is bounded to
`[0.8722, 1.1278]`. Five terms in the same composition sat outside it.

### The full inventory, so a later reader can see whether any remain

| term | was | vs spread 0.0815 | now |
|---|---|---|---|
| `pol.prefer_active_work` | +0.22 additive | **2.7x** | x1.0179, inside |
| `pol.prefer_experience` | +0.20 additive | 2.5x | x1.0098, inside |
| `pol.exact.question` / `.other` | -0.05 / +0.03 | 0.6x / 0.4x | x0.9973 / x1.0018, inside |
| `proj.boost` | +0.15 additive, **after** the tier multiply | 1.8x | x1.0063, inside |
| authorship | x1.0 / x0.3 / x0.5 | up to **3.33x** | outside, own bound, stated below |
| weight split | `reflection_weight` 1.4 vs `memory_weight` 0.7 **within one list** | up to 2x | outside, root cause fixed |

Deliberately outside, and not a gap: the retrieval-stage lexical and intent
terms. They are query-DEPENDENT -- they measure query-record similarity -- and
the bound exists to stop query-INDEPENDENT metadata outweighing similarity. A
term that *is* a similarity estimate is not competing with similarity.

Not covered by the bound and not re-examined here: `memory_weight` itself as a
magnitude, and the three absolute thresholds this ADR declines to retune.

### The allocation was wrong under multiplication

The previous rule was

```
deviation_i = (1 - PRIOR_MIN) * ST_i / ST_max
```

It hands every term independently the right to consume the prior's entire
budget. The largest-ST term took exactly that, so `LEN_UNDER_50 == PRIOR_MIN`
by construction -- while `assemble` composes terms by MULTIPLYING them. Any
record taking two downward terms therefore left the bound and was clamped back
to it.

Measured over the reachable branch space at the old magnitudes: **9 of 90
combinations clamped**, all short records, 30% of the short-record space, and
inside that region the prior was a CONSTANT with kind and recency erased.

The fix is to allocate in the space the terms compose in:

```
log_dev_i = log(BOUND) * ST_i / S
S = sum of ST over the worst one-arm-per-family case, in that direction
```

The families are mutually exclusive, so a record takes at most one arm from
each and the worst case is a sum over FAMILIES rather than over terms. The
worst-case product then lands exactly on the bound -- verified to 1e-16 in both
directions -- and the clamp becomes **unreachable**: 0 of 3920 branch
combinations escape, against 9 of 90 before.

The clamp is kept, instrumented and tested for unreachability, demoted from
enforcement to assertion. A bound that holds by construction still needs
something to notice when a future term breaks the construction, and the test
that asserted the clamp fires has been inverted rather than deleted.

### Magnitudes: one converged run, and the first one

Every magnitude is re-derived, the six from the 2026-09-28 amendment included,
from a single Sobol run against the **production corpus**:

```
N=4096, k=37, 212,992 evaluations
scipy.qmc.Sobol(scrambled), seed 20260923
1000 bootstrap resamples, 95% intervals
st_ci_target 0.020: MET -- delivery 0.0141, score 0.0199
the log-log extrapolation independently demanded N=4099
```

This is the first converged run in the project's history. #232 stopped at its
ceiling with a delivery half-width of **0.0495** against the same 0.020 target,
and its values have been load-bearing ever since. Continuing to cite it was not
an option once the range convention changed: `tools/retrieval_trace/ranges.py`
states that mu*/ST from the pre-ADR-044 runs are not comparable with the
current intervals, and #232 was taken on the 45-parameter vector under the old
ones. Extending a disowned measurement to new terms would have compounded it.

The run was made possible by two harness fixes that are part of this change:
the Sobol stopping rule read the SCORE endpoint alone, so a run could report
convergence while delivery -- the endpoint these magnitudes come from -- was
still wide; and `proj.boost` was structurally unmeasurable, because the capture
CLI had no way to supply a project id, so `project_match` was always False and
the term was always reported unexercised.

Measured delivery ST, all resolved (widest half-width 0.0141):

| term | ST | +/- |
|---|---|---|
| `kind.experience` | 0.2481 | 0.0141 |
| `kind.question` | 0.1876 | 0.0115 |
| `kind.user_content` | 0.1149 | 0.0078 |
| `recency.older` | 0.1160 | 0.0077 |
| `recency.d365` | 0.0903 | 0.0064 |
| `len.lt50` | 0.0716 | 0.0043 |
| `pol.prefer_active_work` | 0.0669 | 0.0051 |
| `pol.prefer_experience` | 0.0370 | 0.0024 |
| `proj.boost` | 0.0237 | 0.0020 |
| `pol.exact.question` | 0.0156 | 0.0012 |
| `recency.d30` | 0.0111 | 0.0007 |
| `pol.exact.other` | 0.0069 | 0.0006 |
| `reflection` | 0.0030 | 0.0003 |
| `len.gt1200` | 0.0028 | 0.0002 |
| `kind.answer` | 0.0000 | 0.0000 |

`kind.answer` is in the run's `no_solo_delivery_effect` list and takes 1.0, by
the rule already applied to the type ladder. It previously mirrored
`kind.question`; the measurement separates them, so the mirror is gone.

### Three corpora disagree, and by how much

| term | #232 | synthetic | production | prod/#232 |
|---|---|---|---|---|
| `kind.experience` | 0.1696 | 0.0798 | **0.2481** | 1.46x |
| `kind.user_content` | 0.0724 | 0.0493 | **0.1149** | 1.59x |
| `recency.older` | 0.1559 | 0.1054 | **0.1160** | 0.74x |
| `recency.d365` | 0.1713 | 0.1571 | **0.0903** | 0.53x |
| `len.lt50` | 0.1930 | **0.0006** | **0.0716** | 0.37x |
| `len.gt1200` | 0.0451 | 0.0006 | 0.0028 | 0.06x |

A synthetic corpus was measured first and rejected on this evidence. It put
`len.lt50` at 0.0006, two orders of magnitude below both other runs, which
would have deleted the term. Activation was 3.18% of candidates on the
production corpus and about 3% on the synthetic one -- so the discrepancy is
not rarity. ST is a share of one corpus's variance, and a corpus that is
unrepresentative in what its other 97% look like produces an unrepresentative
share. **Magnitudes derived on a synthetic corpus are not production
magnitudes**, even when the activation rates match.

### The recency ladder is a MIXED derivation

Stated explicitly because it must not be read as uniformly measured.

```
MEASURED (contribute the family's magnitude):  older 0.1160   d365 0.0903   d30 0.0111
CARRIED  (ordering only, no measurement):         d7   d90
```

`d7` and `d90` are **unexercised** on the production corpus: nothing retrieved
falls within 7 days or in the 31-90 day band. That is a corpus fact and no
sample count fixes it -- see issue #252, where it is one symptom of a larger
problem.

The ladder's ORDERING is therefore carried from the additive ladder it replaced
(+0.18 / +0.12 / +0.06 / +0.02 / -0.03) and its MAGNITUDE comes from the
measured arms, scaled as one family.

The alternative was tried and rejected, for a reason worth recording: per-arm
ST allocation **inverts the ladder**. Measured `d365` (0.0903) is 8.2x measured
`d30` (0.0111), so allocating each arm by its own ST would give a year-old
record a larger boost than a month-old one. That is not a finding, it is a
broken prior -- recency arms are mutually exclusive, so they are compared
across records. ST is an activation-weighted variance share: it says how much a
term moves delivery on this corpus, not which arm of an ordinal ladder should
rank higher. Using it to ORDER an ordered ladder is a category error, and the
ladder is the one place in the prior where measurement cannot set the ordering.

### The two terms that stay outside, with derivations

**Authorship is a suppression gate, not a tiebreaker.** Its job is to stop
content of uncertain authorship answering as though it were the user (UAT-005),
regardless of how relevant that content is. The cosine-spread bound governs
terms that reorder records of comparable relevance; bounding a gate to a 6.6%
band converts it into a nudge and breaks the incident it exists for. Amendment
4a already established that gates live outside score space -- role was the
first. Authorship is the second.

Its stated bound is `[0, 1]`: a fraction, order-preserving toward zero. Its
governing test is `tests/test_incident_reproduction.py`, not the spread.

Recorded honestly: after #218 retired `third_party`, the surviving arms are
`mixed` 0.3 and `unknown` 0.5, which are graded class constants rather than a
gate's 0.0. The gate argument is weaker than it was. Converting authorship to a
predicate as role was, measured by the incident suite, is the consistent next
step and is not taken here.

**The weight split is fixed at the root rather than bounded.** It reordered
within one delivered list only because `reflection` is in
`SQLITE_MEMORY_TYPES`, so reflection records reach `memory_items` and took
`reflection_weight` (1.4 on the reflective policy) while their neighbours took
`memory_weight` (0.7) -- a 2x swing between two records in the same list, from
a term whose whole purpose is per-channel tuning.

`apply_policy` now takes the channel weight explicitly and `build_context`
passes it per channel, which it was already positioned to do. A uniform
positive scale on a list cannot reorder that list, so the term is legitimately
outside the bound -- where before, the same justification was false.

### Measurement

36-query traffic window, synthetic vault (500 records, real embeddings, 228
cold / 96 warm / 112 hot), before at `main` in a worktree against the same
corpus, both runs read-only under `retrieval_stats_disabled()`.

| | before | after |
|---|---|---|
| delivered | 203 | 203 |
| mean score | 0.4968 | 0.4854 |
| median | 0.4951 | 0.4941 |
| max | 1.4545 | **1.2088** |
| clamp firings | 0 / 305 | 0 / 305 |
| type gate | 92.63% | 92.63% |

Score ratio: p5 0.9428, p50 1.0000, p95 1.0031. 111 of 203 unchanged, 74 fell,
18 rose. The ceiling falling from 1.4545 is the additive terms no longer able
to push a composed score past what the bound permits.

**Delivered membership is identical. Jaccard 1.0000, all 36 queries, nothing
gained or lost.** That deserves stating rather than burying: on this corpus the
change is score-only. The terms brought inside the bound were not deciding
delivery before, which is consistent with their measured ST and is the outcome
the bound was supposed to produce -- but it also means the bound's effect on
delivery is not observable here, and this measurement cannot claim otherwise.

**The clamp fired zero times on this corpus before the fix as well.** The 9-of-90
figure is a property of the reachable branch space, not a corpus rate. The fix
is provable by construction and by exhaustive enumeration (0 of 3920 escapes);
it is not visible in corpus traffic, because the combinations that clamp are
ones this corpus does not produce. Both statements are true and neither
substitutes for the other.

The type gate is unchanged, as expected: it reads the retrieval-stage score,
which this change does not touch.

### #250 is resolved, by measurement

Short user-authored content was net-penalised because `LEN_UNDER_50` outweighed
`KIND_USER_CONTENT`: 0.9339 x 1.0248 = **0.9570**.

Under the log-space allocation on production ST it is 0.9876 x 1.0309 =
**1.0181**. Resolved, and not by the allocation alone -- on the production
corpus `kind.user_content` (0.1149) outranks `len.lt50` (0.0716), the opposite
of #232's ordering. Had the ST ordering held, the log-space fix would have
reduced the penalty to 0.9942 without removing it. The fix and the
re-measurement were both necessary; neither would have sufficed.

Closed.

### Two planning assumptions the measurement overrode

Recorded because the plan is part of the record.

`pol.exact.question` / `.other` were expected to sit at the noise floor and
take the identity, on the strength of two earlier runs where ST was ~0.0012
with ST < S1. On the production corpus they are 0.0156 and 0.0069, both
resolved and neither inconsistent, so they are derived like any other term.

`len.lt50`'s near-zero synthetic ST was expected to be an artefact of rare
activation. It was not: activation is 3.18% on production too. The synthetic
figure was wrong for a different reason -- see the three-corpus table.

## Consequences

**Positive**

- One owner for the metadata prior, one for age, and a stated bound for each.
- The 0.03 reachability floor goes, by construction rather than by retuning.
  Measured: the lowest delivered composed score rose from 0.0452 to 0.1810.
- ~~The confidence hedge and vault citation badge start reporting match quality
  again, with no threshold changes.~~ **Falsified 2026-09-28. This moved in the
  opposite direction and is now a negative, below.**
- The double-count is resolved as a side effect of consolidation rather than as
  a separate cleanup.
- ADR-015 becomes true as written.
- The scoring budget becomes a number in one place that a test asserts, which
  is the condition under which "nobody owns the budget" stops being true.

**Negative**

- **The vault citation badge is suppressed on MORE turns than before, not
  fewer.** Added 2026-09-28, replacing the positive claim struck out above.
  Both the confidence hedge (`prompt_builder.py:1131`) and the badge gate
  (`openai_adapter.py:1837`) threshold a per-query MEAN of the delivered
  scores, and that mean fell: 12 of 36 queries cleared 0.6 before, 8 of 36
  after. Removing the additive pile lowered the mean by more than removing the
  decay multiplier raised it.

  The reasoning behind the original claim was not wrong about the mechanism --
  aged records really were finalizing at 0.03-0.15x raw cosine, and that really
  was the badge reporting decay rather than match quality. What it missed is
  that the pile was also inflating the mean on everything else, so removing
  both moved the average down. The prediction was made without a measurement
  and a measurement contradicts it.

  Nothing is retuned here. The thresholds stay where they are, for the reason
  this ADR gives for not moving them in the first place: they are calibrated
  against a composed score in cosine units, and re-deriving them is its own
  change with its own evidence. Tracked as issue #249.
- Every scoring constant in the repo is re-expressed. Roughly 142 tests across
  nine files assert current behaviour and will need review; those that pin a
  magnitude rather than an ordering are the ones to re-derive rather than
  re-fit. (Measured on implementation: 117 failures across 11 files.)
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
