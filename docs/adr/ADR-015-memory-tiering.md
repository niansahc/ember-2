# ADR-015: Memory Tiering

**Status:** Accepted
**Date:** 2026-04-02
**Version:** v0.13.0

## Context

Ember's vault grows indefinitely. Without tiering, old low-relevance records compete equally with recent ones during retrieval. Resolved state records (open_loop, next_action) remain in active retrieval after resolution. The nomic-embed-text embedding upgrade in v0.13.0 forces a full reindex — the right moment to introduce tiering, since tier assignment can be computed during the rebuild pass.

Research context: The Generative Agents paper (Park et al., UIST 2023) established the field default of exponential recency decay, but its decay parameters were arbitrary -- tuned to a simulated 16-hour game day with no empirical basis for real-world personal knowledge systems. Hou et al. (2024) demonstrated that pure recency makes a known error: it prefers frequently-seen shallow interactions over substantively important ones that appear less often. The ACT-R cognitive architecture provides the strongest theoretical grounding: base-level activation of a memory trace is a function of both recency and frequency of use -- recency-weighted frequency, not pure recency. MemoryBank implements this directly: each retrieval resets the recency clock and increments a frequency counter. MemoryOS (EMNLP 2025) implements a continuous heat score combining visit count and recency factor rather than calendar-threshold archival. The "Forgetful but Faithful" paper (Dec 2025) confirms that LRU (pure recency) is only optimal when time decay dominates -- for personal knowledge, records encoding durable identity and relationships have cross-situational value that does not decay on a calendar.

## Decision

Three-tier classification governing retrieval priority, not storage. Append-only contract unchanged. All canonical records remain in the vault.

### Tier Assignment: Composite Heat Score

Tier is determined by a composite heat score combining three signals. Calendar thresholds alone have no empirical basis for personal knowledge systems and make a known error: they discard important infrequently-accessed records in favor of recent mundane ones.

Heat score formula:
heat = (recency_score * 0.5) + (access_score * 0.3) + (importance_score * 0.2)

recency_score: exponential decay over days since last retrieval (or creation if never retrieved). Decay factor configurable via TIER_RECENCY_HALFLIFE_DAYS (default: 30). Score range 0.0-1.0.

access_score: normalized retrieval count. Computed as min(retrieval_count / TIER_ACCESS_CEILING, 1.0) where TIER_ACCESS_CEILING defaults to 10. A record retrieved 10+ times scores 1.0.

importance_score: heuristic signal for cross-situational value. For v0.13.0, derived from memory_type:
- Profile memory: 1.0 (always)
- Unresolved state records (open_loop, next_action, current_focus): 0.9
- Reflection records: 0.7
- Journal records: 0.6
- Conversation records: 0.4
- Ingested records: 0.3
Importance scoring will be refined with LLM-derived scores in a future version.

Tier thresholds (configurable via .env):
- heat >= TIER_HOT_THRESHOLD (default 0.5): Hot
- heat >= TIER_WARM_THRESHOLD (default 0.2): Warm
- heat < TIER_WARM_THRESHOLD: Cold

Hard overrides (bypass heat score):
- Profile memory: always Hot, no exceptions
- Unresolved state records: always Hot
- Resolved state records: heat score applies, but importance_score drops to 0.2 on resolution

Tier definitions:
Hot -- actively relevant, full retrieval weight. Heat score >= 0.5, or hard override applies.
Warm -- background context, reduced retrieval weight. Heat score >= 0.2 and < 0.5.
Cold -- low heat, excluded from default retrieval. Heat score < 0.2. Cold records remain in the SQLite index -- they are not removed. Cold exclusion means ContextRetriever applies a score of 0.0 to cold records during default search. They are retrievable via explicit user query or include_cold=True flag. False-cold archival recovery cost is one failed retrieval plus a fallback search -- at current scale, milliseconds. Records are never deleted.

### Tier Storage

Metadata fields on each SQLite record:
- `tier TEXT DEFAULT 'hot' CHECK(tier IN ('hot', 'warm', 'cold'))`
- `last_retrieved_at TEXT`
- `retrieval_count INTEGER DEFAULT 0`
- `importance_score REAL DEFAULT 0.5`
- `heat_score REAL DEFAULT 1.0`

importance_score is set at write time based on memory_type heuristics. heat_score is recomputed nightly by TieringService. Both are stored for auditability.

### Retrieval Integration

ContextRetriever applies tier as scoring modifier. Hot: no penalty. Warm: score × 0.7. Cold: excluded unless `include_cold=True`. Profile memory bypasses tier scoring entirely.

### TieringService

Runs nightly:
1. Read all records with their last_retrieved_at, created_at, retrieval_count, importance_score, and memory_type
2. Compute recency_score, access_score per record
3. Compute heat_score = (recency_score * 0.5) + (access_score * 0.3) + (importance_score * 0.2)
4. Apply hard overrides (profile memory, unresolved state records)
5. Assign tier based on heat thresholds
6. Write updated tier and heat_score fields only where values have changed (minimize writes)
7. Log transition counts and threshold summary to logs/tiering/YYYY-MM-DD.log

### ADR-014 Note

Resolved open_loop records move to warm immediately on resolution, cold after 30 days.

## Rationale

Tiering at retrieval (not storage) preserves append-only contract. Nightly batch is cheap and auditable. Score multiplier for warm avoids abrupt context loss. Cold exclusion reduces noise without destroying history. Profile exemption ensures identity context is never penalized. Aligning with the reindex means no extra full-scan pass.

## Consequences

**Positive:**
- Retrieval quality improves as corpus grows
- Resolved state records stop competing with active ones
- Cold archive enables time-travel queries
- Tier log is inspectable
- No data loss

**Negative:**
- Nightly tiering job adds a background process
- `last_retrieved_at` write on every retrieval adds minor overhead
- Thresholds should be configurable in `.env` or `config/`
- Initial reindex may cold-archive relevant but unqueried records — document this for users

## Resolves

Open decision from TDD §28: "Hot/warm/cold memory tiering policy design." Decision: composite heat score combining recency-weighted frequency (ACT-R model) and heuristic importance. Calendar thresholds are rejected -- they have no empirical basis for personal knowledge systems and make a known error discarding important infrequently-accessed records. Pure recency is also rejected for the same reason.

## Alternatives Considered

- **Deletion of old records** — rejected, violates append-only
- **Hard archive to separate table** — rejected, tiering-as-metadata is simpler
- **Calendar threshold archival (30/90 days)** — No empirical basis for personal knowledge systems; discards important infrequently-accessed records in favor of recent mundane ones (Hou et al. 2024)
- **Pure time-based decay** — rejected, ignores access patterns
- **Pure relevance-based decay** — rejected, too complex for v0.13.0
- **No tiering** — rejected, known TDD risk

## References

- TDD §35 -- Relevance Decay and Forgetting
- ADR-014 -- Commitment Detection (resolved open_loop records are tiering candidates)
- Hu et al. (2025), "Memory in the Age of AI Agents" (arxiv:2512.13564) -- taxonomy of memory dynamics; Ember's approach maps to token-level hierarchical memory with explicit retrieval dynamics
- Park et al. (2023), "Generative Agents" (UIST 2023) -- established recency decay as field default; decay parameters are arbitrary, not empirically derived for personal knowledge systems
- Hou et al. (2024) -- spaced recall intervals outperform pure recency; pure recency prefers shallow frequent interactions over substantively important infrequent ones
- ACT-R cognitive architecture -- base-level activation as function of recency and frequency; theoretical grounding for recency-weighted frequency over pure recency
- MemoryBank -- retrieval resets recency clock and increments frequency counter; clean implementation of recency-weighted frequency
- MemoryOS (EMNLP 2025) -- continuous heat score combining visit count and recency factor; inspiration for Ember's heat score approach
- "Forgetful but Faithful" (Dec 2025) -- LRU optimal only when time decay dominates; importance-based policies preserve cross-situational value

## Amendment (v0.19.0, issues #150 and #175)

### What the original design got wrong

The access term is effectively dead. `update_retrieval_stats` matches no rows
for any type except reflection, so `retrieval_count` is 0 and
`last_retrieved_at` is NULL across the corpus. That is a defect, and it is the
smaller half of the finding. The rest of this section is about properties of
the formula itself, which repairing the access term would not change.

**The importance term cannot preserve anything.** Its maximum contribution is
`0.2 x 1.0 = 0.20`, which is exactly `TIER_WARM_THRESHOLD`. Importance alone
can therefore never lift a record above cold; at best it ties the warm
boundary, and only at importance 1.0, which is profile, which is
hard-overridden hot anyway. "Resolves" above rejects calendar thresholds and
pure recency because they "discard important infrequently-accessed records in
favor of recent mundane ones." An infrequently-accessed record has
`access_score = 0` by definition, so importance was the only term that could
have protected that class -- and it arithmetically cannot. The counterweight
this ADR built to answer Hou et al. is inert against the case Hou et al.
describes. Repairing the access term does not fix it, because the protected
class is low-access by construction.

**Heat is one-way.** `_recency_score` reads `last_retrieved_at or created_at`.
With the former NULL and `retrieval_count` 0, heat is a monotonically
non-increasing function of wall-clock time against fixed thresholds. The access
term is not one of three signals; it is the only recovery mechanism in the
design, so its failure makes the system provably one-way. Zero `cold_to_hot`,
`cold_to_warm` and `warm_to_hot` transitions across 79 nightly logs is
arithmetic necessity, not a tuning observation.

**Two of the rejected alternatives are what shipped.** With access dead, heat
reduces to `0.5 * 2^(-d/30) + 0.2 * importance`. That is "Pure time-based
decay," rejected above. It is also observationally identical to "Calendar
threshold archival," also rejected above, on this per-type schedule at the
default halflife:

| type | hot until | cold after |
|---|---|---|
| reflection | 14.2 days | 91.8 days |
| journal | 11.9 days | 79.3 days |
| conversation | 7.6 days | 61.8 days |
| ingested | 5.5 days | 55.1 days |

**The stated recovery path does not exist in code.** "Tier definitions" above
promises that "False-cold archival recovery cost is one failed retrieval plus a
fallback search." There is no fallback search. No caller passes
`include_cold=True`, and `ContextRanker.apply_policy` sets `score = 0.0` for
cold with no recovery branch.

**Cold-as-zero destroys ordering, measured.** In the retrieval-architecture
ablation, the fraction of the ranked list sitting in unresolved tie bands is
0.178 with tiering on and 0.015 with it off, a roughly twelvefold increase.
Collapsing every cold record to a single score discards the relative relevance
of everything below the warm line. With most of the corpus cold, that means the
ordering of most of the corpus is decided by type and authorship constants with
no reference to the query.

One caveat on the same ablation, so it is not over-read: it cannot support the
conclusion that tiering is worthless. Its delivered window contained no warm or
cold records in any stratum, so the treatment had nothing to act on at that
layer, and with the access term dead the only live non-recency input to heat is
a type lookup, which makes the tiering and typing arms partly the same
intervention.

### Revised model: base activation, then context

Tier is assigned from a base activation score that combines recency with a
*decaying* frequency term, both on the same decay curve, so heat is one
temporal model rather than two signals that happen to be added together. This
is the ACT-R structure the original Context section cites, applied properly:
base-level activation is context-free, stored, and recomputed on the nightly
pass.

Activation is two-way. Retrieval raises a record's activation and can move it
back up a tier; time lowers it. The original design intended this and could not
deliver it, because the only term that could raise activation was the one that
never ran. A tier model with no upward path is a decay schedule, not an
activation model, whatever the formula looks like.

The frequency term must decay. The original `access_score`, a monotonic
`retrieval_count` normalised against a ceiling, installs a permanent floor: a
conversation record retrieved four times floors at heat 0.20, exactly the warm
line, and one retrieved ten or more times floors at 0.38. Either way it can
never go cold again, whatever happens afterwards. The set of permanently warm
records would then grow monotonically and never shrink, reintroducing the
unbounded-growth problem tiering exists to solve, on a longer timescale.
MemoryBank's increment was carried over; its forgetting was not.

Storage follows from this. `retrieval_count INTEGER` is insufficient, because a
decaying frequency term needs to know when the retrievals happened. A decayed
accumulator updated at retrieval time is O(1) in storage and replaces the
counter.

Age alone is no longer a tier signal.

### Context conditioning is ADR-007's boost, generalised

The second half of the ACT-R structure is that activation is conditioned on the
current context. This is applied at retrieval, not baked into the stored tier,
because it is by definition per-query while the tier is one nightly value per
record.

This amendment does not add a new mechanism for it. ADR-007's project boost
(`ContextRanker.apply_project_boost`, +0.15 for a matching `project_id`) is
hereby the first and currently the only instance of the context term. Project
scope and context conditioning are one design, not two.

Stating it this way is deliberate. `docs/what-replacement-means.md` leaves open
"how the boost interacts with the existing ranking levers, given that ranking
already carries type, tier, decay, authorship and lexical terms," and warns
that "a new multiplier added carelessly is how the reflection score hardcode
happened." Declaring the existing boost to be the context term answers that
question by reducing the number of mechanisms rather than adding one.

### Derived records are bounded by their sources

A derived record's tier cannot exceed the tier of the records it was built
from. The bound is the maximum over its sources: a reflection may be as warm as
its warmest source and no warmer.

Derived content has no independent standing. This follows directly from the
architectural rule that the LLM is not the system of record -- a reflection is
an artifact computed from canonical records, and it cannot outrank or outlive
the evidence it compressed.

This replaces the importance ladder as the mechanism for reflection longevity,
and it removes an inversion the ladder created. Under the original schedule a
reflection stays retrievable for 91.8 days while the conversations it was
derived from go cold at 61.8, so for thirty days the summary competes while its
sources score zero. That is the derived-record trap described in
`docs/research/memory-trust-gap-followup.md`, which the Recalling Too Well
Phase 1 work corrected at the ranker; the importance ladder reintroduced it at
the tier layer.

Three implementation constraints, stated here because they bound what the
follow-up can assume. `metadata.source_record_ids` is currently write-only:
nothing in production reads it and no resolver exists. Resolution must be
cross-type, because source ids mix journal, ingested, conversation and
reflection while every read helper is scoped to a single type directory. And
`flatten_metadata` truncates list metadata to the first twenty entries, so a
bound computed from provenance is computed from a subset once a derived record
has more than twenty sources.

### Legacy derived records

Every reflection record currently in the vault predates provenance and carries
no `source_record_ids`. More importantly, the two writers that produce the
overwhelming majority of them, `session_reflection.py` and
`session_summary.py`, still do not emit provenance today. Only
`generate_reflection.py` and `lodestone_synthesis.py` do. So this is not a tail
case that ages out; without a writer fix it is the permanent condition of most
derived records.

**Rule: a derived record with no resolvable sources is floored at cold, and the
writers that do not emit provenance are brought into compliance.** Both halves
are required. The floor without the writer fix is a silent permanent demotion
of most derived content, which is a different outcome from the one intended.

The floor is only a proportionate response because of the cold contract below.
Before cold became a weight, flooring a record meant `score = 0.0` and
effective deletion. It now means lowest weight with ordering preserved, still
reachable through context conditioning and direct addressing. That is precisely
"no independent standing," which is what a derived record whose sources cannot
be identified has.

Two alternatives were considered and not taken. Deriving the source window from
a reflection's cadence and timestamp resolves under a third of the existing
corpus, because most records carry neither a period cadence nor a session
identifier, and still needs a fallback for the rest. Regenerating legacy
reflections from their sources fits the rebuildable-derived-artifacts rule best
and is cheap at this corpus size; it remains available but is not required by
this amendment, with one warning: a regenerated record takes a new timestamp,
so unless the timestamp is pinned to the original period, regeneration
constructs the very trap it is meant to resolve.

### Cold is a weight, not exclusion

"Tier definitions" above specifies that cold records are "excluded from default
retrieval" via a score of 0.0. That is replaced. Cold is a reduced retrieval
weight, and ordering within cold is preserved.

The measured reason is the tie-band result above: zeroing collapses every cold
record to one indistinguishable value and discards the similarity ordering
among them. The structural reason is that the current implementation does not
do what this ADR says anyway -- the zero is applied in `apply_policy`, and the
later additive stages restore score afterwards, so cold has been neither an
exclusion nor a weight but an erasure of the similarity signal with the
metadata signal left intact.

An explicit archive flag remains available as a separate field if true
exclusion is ever wanted. It is not part of this amendment, and it would be a
different field from the tier, because a weight and a gate are different jobs.

**Correction (2026-09-19): the headroom this section promises does not exist.**

`COLD_MULTIPLIER = 0.3` was chosen with a stated rationale, recorded at
`src/context/ranker.py:22-40`: 0.3 is a clear third band below warm "while
still leaving enough headroom that a highly relevant cold record can outrank a
weakly relevant hot one when that is genuinely the better answer." That claim
is false as shipped, and the reason is the mechanism the correction above
names.

`_temporal_decay_weight` multiplies again, after tier. A conversation record
older than thirty days sits in `_EPHEMERAL_DECAY`'s floor of 0.10
(`ranker.py:405-412`), so an aged cold conversation record carries
`0.3 x 0.10 = 0.03` against `1.0` for a fresh hot one. Cosine is bounded by
1.0 and the additive pile independently favours fresh records
(`+0.18` recency inside seven days at `ranker.py:362`, against `-0.03` past a
year), so **no aged cold conversation record can outrank a fresh one at any
cosine value.** This is arithmetic, not a measurement, and it does not depend
on the ablation corpus, the relevance labels, or any probe.

The ordering defect this section already identifies is also unfixed. The
section correctly observes that the zero "is applied in `apply_policy`, and the
later additive stages restore score afterwards, so cold has been neither an
exclusion nor a weight but an erasure of the similarity signal with the
metadata signal left intact." Implementation step 3 replaced the zero with 0.3
and did not change the order. Tier still multiplies at `service.py:181`; the
ranker's additive pile still lands at `service.py:196`, at full magnitude, on
an already-discounted similarity signal. Cold discounts the query-dependent
half of the score and leaves the query-independent half alone, which is the
behaviour this section set out to end.

Neither correction reopens the cold-as-weight decision. Ordering within cold
is preserved and the tie-band result that motivated the change is real: the
re-run on 2026-09-19 measures `A0_FULL` `ranked_unresolved` at 0.0061 against
0.1780 before the change, a 29x collapse, which is this section working as
intended. What is wrong is the claim about cold-versus-hot headroom, and the
assumption that a single multiplier placed before the additive stages behaves
as a weight.

### The importance ladder is flattened

`IMPORTANCE_BY_TYPE` contributes nothing to heat. Every job it was doing is now
done elsewhere or was never achievable: it could not lift any record above the
warm line, reflection longevity is now bounded by sources rather than by a type
constant, and the reclassification below removes the low end of the ladder.

Type may still affect ranking through other mechanisms. It no longer affects
*tier* decay.

**Correction (2026-09-19).** As originally written this paragraph said type
"no longer affects decay", full stop. That is false of the shipped system and
was false when it was written. `ContextRanker._temporal_decay_weight`
(`src/context/ranker.py:396-452`) is a second, type-keyed age mechanism with
four separate curves -- `_NO_DECAY_TYPES` at a flat 1.0, `_REFLECTION_DECAY`,
`_EPHEMERAL_DECAY`, and `_DEFAULT_DECAY` -- and it is applied multiplicatively
at `ranker.py:262`, after tier. Flattening `IMPORTANCE_BY_TYPE` removed type
from the *tier* schedule only. It left type-keyed decay untouched, and in doing
so made `_temporal_decay_weight` the sole remaining place where memory type
affects how a record ages.

That mechanism has no ADR. It shipped in `1c64f4c` (2026-04-11), nine days
after this ADR was accepted, and this document references it exactly once --
at the start of "Timestamps and prior-substrate conversation" below, while
describing a contradiction it creates, never as something this ADR governs.
Two independent per-type age curves now exist and this ADR owns one of them.

### Timestamps and prior-substrate conversation

`created_at` means authorship time. One meaning, everywhere, for every type.
There is no dual reference in which display and decay read different fields.

**Prior-substrate conversation is conversation.** The imported ChatGPT-era
corpus runs from 2022-12 to 2026-03 and meets the native vault's start with no
gap. It is the record of one continuous relationship that changed substrate
partway through, not third-party material that arrived alongside it. It is
reclassified as `conversation` with a provenance marker, and it takes the
conversation decay contract. The general rule: any future export-and-ingest of
the same form inherits this classification. The `ingested` type remains, and
means genuine third-party material -- documents, articles, other people's
writing.

Both halves of the exchange reclassify, with role marking. User turns are
first-person. Assistant turns are marked assistant, per ADR-033. The exchange
stays whole: the assistant side is part of what constitutes Ember's continuity,
not third-party material that happened to arrive with the user's words. ADR-033
is satisfied by correct role marking; its concern is that assistant turns not
be laundered into first-person memory, not that they be discarded. Role
metadata is present on every record in the corpus, so the marking is
mechanically verifiable before the migration runs, and it should be verified
first.

**Correction (2026-09-26): this position governs, and the third_party class
is retired.**

The paragraph above and commit f9f5dda disagreed, and the disagreement was
live in the code. f9f5dda established `authorship='third_party'` with a
multiplier of 0.0 -- a hard exclusion on relational queries -- and the only
code that assigned it, `scripts/rebuild_authorship_index.py`, mapped
imported ChatGPT assistant turns into it. This ADR says those turns are part
of Ember's own continuity. Both could not hold.

**This ADR governs.** Issue #218 closes on that basis and the class is
retired from the ranker.

The evidence that it was already dead rather than merely contested:

- zero rows at `third_party` in the production index, against 9,034 at
  `mixed`, of which 9,023 are imported assistant turns -- the exact
  population the class was created for
- no live path could assign it. `classify_authorship`, which every write
  goes through, has no such branch and says so in its own docstring. The
  only assigner was a standalone backfill.
- guard counters over 36 turns: `ranker.authorship.branch` evaluated 55
  times, arms taken were `first_person` 40 and `unknown` 15. `mixed`,
  `third_party` and `unrecognised` were never taken.

A 0.0 multiplier with no population and no way to acquire one is worse than
no multiplier, because it reads as protection that is in force.

**What owns genuine third-party content.** This ADR already answered that,
two paragraphs up: "The `ingested` type remains, and means genuine
third-party material -- documents, articles, other people's writing." That
is a write-time provenance fact carried by memory type, and ADR-018 type
gating can suppress it per policy. It does not need a second expression as a
rank-time authorship multiplier. If document ingestion resumes, such records
arrive as `ingested` and are governed there; on the authorship column they
take `unknown` (0.5) rather than a hard exclusion, which is a deliberate
reduction in the ceiling of that protection and is recorded here as one.

**What this does not resolve.** UAT-005's relational contamination concern
is real and is now owned by the `mixed` multiplier (0.3) plus the cosine
gap, not by an exclusion. PR #217 measured relational contamination passing
in every arm including the fully reduced one, so the capability does not
depend on the retired class -- but the decoy in that suite was tagged
`third_party`, a value with no production rows, so the arm was being held
out by a mechanism that could not have fired on real data. The fixture is
retagged to `mixed` alongside this change and still passes, which is the
first time that result describes the deployed configuration.

This resolves two standing contradictions. The first is internal to the code:
`ContextRanker._NO_DECAY_TYPES` classes `ingested` as reference-grade and
exempts it from temporal decay entirely, alongside `profile` and `reference`,
while `IMPORTANCE_BY_TYPE` gives it the lowest importance in the system and
decays it fastest. One layer said never decay this; the other said decay this
first. Flattening the ladder above removes that disagreement at the source, and
reclassification removes the population that straddled both readings.

**Correction (2026-09-19): state the consequence of that reclassification.**

It resolves the contradiction by choosing "decay this first", and the
paragraph above does not say so or quantify it. Reclassifying the
prior-substrate corpus from `ingested` to `conversation` moves roughly 16,900
records **out of `_NO_DECAY_TYPES`** -- where `_temporal_decay_weight` returned
a flat 1.0 -- and **into `_EPHEMERAL_TYPES`**, whose floor is 0.10 beyond
thirty days (`src/context/ranker.py:396`, `:405-412`). The corpus spans
2022-12 to 2026-03, so essentially all of it is past that floor. The migration
(`63466b1`, 2026-09-16) therefore applied a new 10x multiplicative suppression
to about 99% of the retrievable corpus, as a side effect of a decision this
amendment justified on classification grounds alone.

Two qualifications, so this is not read as a regression it is not. Before
implementation step 3 those same records were cold at a multiplier of `0.0` --
excluded outright -- so the net movement across the amendment is from
effectively zero to roughly 0.03, which is an improvement. And the
reclassification itself is right: the imported corpus is conversation, ADR-033
agrees, and the alternative left it permanently mistyped. What is missing is
that the decay consequence was never stated, never measured, and belongs in
the same decision that caused it.

This is the clearest instance of the ownership problem the first correction
above describes. The amendment reasons carefully about the tier schedule it
owns, and moves a corpus across a boundary in a second age mechanism it does
not.

The second is between ADRs, and dates from v0.17.0: ADR-033 holds that ChatGPT
user turns are first-class memory eligible for all retrieval policies, while
this ADR's ladder made them the least important type in the vault. The two have
disagreed ever since. This amendment puts them back in agreement.

The reclassification is an index operation. It follows the precedent of
`scripts/rebuild_authorship_reflections.py`: index-only, dry-run by default,
canonical vault JSON untouched. That keeps the append-only contract intact and
treats the index as the rebuildable derived artifact it is. Role marking
belongs in the existing `authorship` column rather than a new field.

### Open: the assistant-authored penalties

Reclassifying the assistant side of the corpus into `conversation` brings it
under three mechanisms that penalise assistant authorship. These are flagged
here, not resolved, and they should be decided together rather than separately,
because they are three expressions of one judgement about whether Ember's own
prior words count as evidence.

| mechanism | assistant weight | measured |
|---|---|---|
| `source_quality_adjustment` | -0.20 | yes: removing it improved ranked nDCG by 0.028 |
| `_score_memory_item` role scoring | -0.25 | no |
| `_reflection_priority_score` | -0.4 | no |

The measurement covers only the first row. The ablation's source-quality arm
removed the retrieval-stage `source_quality_adjustment`; the ranker's larger
-0.25 was not removed by any arm and has no measurement, and the reflection
priority weight gates what enters a reflection rather than what is retrieved.
Treating the three as one measured result would overstate the evidence.

### Recovery

Recovery is via context conditioning and direct addressing. A cold record
surfaces because the active context raises it or because the user asks for it,
not because a search failed.

There is no automatic cold-fallback search. The promise in "Tier definitions"
above is retired rather than built. A fallback that fires because nothing good
was found is the retrieval form of fabricating an answer when there is nothing
to say: it converts an honest empty result into a confident poor one, and
nothing downstream can tell the difference afterwards.

### Out of scope

Deliberately not settled here, and not to be inferred from the above:

- Context-packet slot allocation and profile guaranteed slots (issue #172). The
  delivered window is currently too narrow for tier effects to be observable at
  all, so no delivered-set evidence about tiering is admissible until it is
  fixed.
- The explicit archive flag.
- The ingest writer and retrieval reader pointing at different stores (issue
  #174), which is a reachability defect independent of tiering.
- The missing embedding-dimension check (issue #176) and the record-count gap
  between disk and index (issue #177).
- The three assistant-authored penalties above.
- All implementation. This amendment states the contract; the follow-up work
  specifies and builds it.
