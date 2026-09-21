# ADR-045: Supersession via Write-Time Linking

**Status:** Proposed
**Date:** 2026-09-21
**Target:** v0.19.0
**Depends on:** ADR-044 (Proposed). Decision 5 is argued in terms of ADR-044's bounded budget; if ADR-044 is not accepted, decision 5 needs a different justification.
**Related:** ADR-038 (append-only pending resolution), ADR-011 (multi-record state categories), ADR-019 (grounding verification), ADR-014 (commitment detection), ADR-015 (memory tiering), ADR-033 (import role separation), issues #207, #204, #211

## Context

A query about a current biographical fact returned a record roughly 74 weeks old and the response asserted its content in the present tense. A later record in the same vault contradicts that fact. Issue #207 has the structural account; this ADR decides what to do about the half of it that is a memory-model problem.

One correction to how that incident is usually described, because the rest of this document depends on it. **Both records existed in the vault. Whether the later one was retrievable is not established.** #207 says the contradicting record "was not delivered" and then explicitly declines to say why: "Whether the contradicting record lost on score or never entered the candidate set is not established by this observation. Both are reachability questions and both are in scope for #204." This ADR does not resolve that and does not assume it away.

What it does establish is that three layers which might be expected to catch a contradiction structurally cannot.

**Retrieval cannot rank contradictions down.** Cosine separates contradictions from duplicates at AUROC 0.59, and contradictions sit *closer* in embedding space than paraphrases do, because a contradiction shares almost all of a statement's vocabulary and negates one element of it (MemStrata, arXiv:2606.26511). This forecloses a specific thing -- ranking on contradiction as an embedding-derived signal -- and it is worth stating the limit of the foreclosure: it says nothing about changing which candidates are retrieved, which is a live option and is considered in Alternatives.

**Generation-time instruction does not work.** A verbal instruction to prefer the newest authoritative source "has little effect and increases reliance in some settings" (Memory Trust Gap, arXiv:2609.01852, recorded in-repo at `docs/research/memory-trust-gap-followup.md:115`). Mem0's own extractor instruction dropped stale-fact performance to 0.398 (same paper; that figure is not in the in-repo summary). Telling the model to prefer recent records is not a weaker version of a fix.

**Grounding verification cannot see it.** ADR-019's oracle asks whether the response contains factual claims about the user not present in the retrieved context (`src/safety/grounding_check.py:34-43`). A superseded record *is* in the retrieved context, so the assertion passes. ADR-019 places "whether retrieved content is itself accurate" outside its scope (`ADR-019:40`), on the reasonable ground that the vault is the source of truth -- reasoning that holds and produces the wrong result here, because the vault contained both records and the oracle was shown one.

And timestamps were present. Per-item dates and deterministic age labels already render for retrieved memory items (`src/llm/prompt_builder.py:995-1008`), described elsewhere in that file as "the measured mitigation (metadata framing: timestamp + source per item)" (`:1266`). `docs/research/memory-trust-gap-followup.md:143-145` argues that raw append-only storage is not the exposure, precisely because a correction is honestly dated newer than what it corrects. That argument is right, and this failure happened anyway, because nothing connected the two records and neither was asked to answer for the other.

What is missing is a relation between two records. No amount of reading either record better supplies it.

## What this can buy, stated before the decision

The benchmark that motivates timestamp exposure also bounds what supersession can add, and the bound is not flattering. From the same table (`docs/research/memory-trust-gap-followup.md:111-112`), at 8B:

| intervention | 8B |
|---|---|
| metadata (timestamp + source per item) -- already shipped for memory items | +0.54 |
| oracle (stale item removed before the prompt) | +0.59 |

**Perfect supersession suppression is worth about +0.05 over timestamp exposure on that benchmark, and a fail-closed mechanism captures a fraction of that.** Anyone weighing this ADR should have that number before the argument rather than after it.

Three reasons the decision is still worth taking, offered as reasons rather than as proof:

1. That benchmark measures one thing -- whether a model follows a stale value when a correct one is also present. It does not measure the #207 configuration, where the correct record was not in the prompt at all and no amount of in-prompt marking could have helped.
2. The link is a fact about the vault, not a prompt intervention. It is available to the grounding check, to historical queries, and to anything later, none of which the +0.05 covers.
3. Timestamp exposure is already shipped for the classes it covers, so +0.54 is banked. The question is what to do about the residue, and the residue is where biographical facts live.

If the reader concludes the +0.05 does not justify a per-write model call and a new canonical record class, that is a reasonable reading of the same evidence and this ADR should be rejected on it.

## Decision

Seven sub-decisions.

### 1. Supersession is established at write time, by linking

On write, find prior records about the same subject by entity match, ask one bounded question about the best candidates, and on a positive answer append a link record.

The bounded question is the design constraint. Entity recognition at 8B runs 0.58-0.69 F1 and relation extraction 0.214 (Agents-K1, arXiv:2606.13669), so typed triple extraction is out of reach locally and anything depending on it fails quietly. What an 8B model can do is answer a closed question about two specific pieces of text. So the model is never asked what the subject is, what relation holds, or what the new value should be. It is asked, of one candidate pair: **does this record change the state of that subject, yes or no.** Entity match proposes; the bounded question disposes; nothing extracts.

Write-time LLM decisions are established here. `StateExtractor` makes a low-temperature call per live turn and returns state records that the caller writes (`src/state/state_extractor.py:82-100`; write at `src/api/openai_adapter.py:222-224`), and ADR-014's commitment detector drives an `open_loop` write on positive detection. CLAUDE.md rule 2 constrains where canonical truth lives -- the filesystem vault -- not what may propose a write.

**One precedent gap, named rather than glossed.** Both of those write *additive* records that are subject to the ordinary staleness and tiering machinery. A supersession link is the first model-authored canonical write that *suppresses* existing canonical content from the user's view, and it does not expire. That is a category difference, and it is the reason decisions 3 and 4 are stricter than either precedent required.

### 2. The link is a new record, and the pointer lives on the successor

Nothing is deleted or rewritten. This follows ADR-038, whose vocabulary this reuses rather than duplicating: ADR-038 appends a record carrying `metadata.original_id` pointing back at its predecessor and derives resolution at read time via `StateService.resolved_ids` (`src/state/state_service.py:351-374`). The predecessor is never touched.

That direction is load-bearing:

- **`supersedes`** is stored on the link record, pointing backward. Direct analogue of `original_id`.
- **`superseded_at`** is stored on the link record, carrying the timestamp of the *superseding* record -- which is not the link's own write time when a later backfill links records written before this mechanism existed.
- **`superseded_by` is derived, never stored.** Storing it would require rewriting an existing canonical file, violating core rule 3 and inverting the one precedent that got this right. A `superseded_ids(records)` function computes it at read time.

`superseded_ids` mirrors only the `original_id` branch of `resolved_ids`. `resolved_ids` has a second branch reading a record's own `metadata.resolved` flag, kept for back-compatibility with pre-ADR-038 in-place mutation (`state_service.py:367-372`); that branch is the direct analogue of a stored `superseded_by` and is exactly what decision 2 forbids. There is no flag branch and no back-compat path.

ADR-038 anticipated this generalisation in terms: "A future full event-sourced resolution (deriving resolution for all categories via `resolved_ids` in the resolver) remains available; this ADR is the first, contained step toward it" (`ADR-038:39`). This is that step, crossing the state/memory boundary `resolved_ids` has not -- it is a `StateService` staticmethod over `list[StateRecord]`, and nothing in `src/memory/`, `src/retrieval/` or `src/context/` reads `original_id`.

### 3. Fail closed

A low-confidence answer produces no link. ADR-014 supplies the method: threshold-gated to avoid false positives (`ADR-014:22`), with a labelled benchmark, a threshold sweep, and a stated precision floor chosen from the sweep -- "Minimum bar before v0.12.0 ships: precision > 0.85"; "Start conservative -- high threshold, low recall -- and loosen based on real vault data" (`ADR-014:30`). The number is deliberately not set here.

Fail-closed applies at the call site too. `StateExtractor` defaults `is_live_turn` to `False` so a caller that forgets the flag skips extraction (`state_extractor.py:99`, enforced at `:122-124`), and ADR-033 gated extraction away from import turns for a related reason. The linking hook inherits that posture.

### 4. Links are canonical, and a wrong link is correctable

CLAUDE.md rule 4 requires derived artifacts to be rebuildable from canonical records. A link produced by a non-deterministic 8B call is not reproducible -- rebuild twice, get two link sets. So: **supersession links are canonical vault records**, not a derived index.

Rule 4 therefore does not apply to them, and the obligation it would have imposed is replaced by a harder one. **A wrong link must be correctable, and the correction uses this same mechanism**: a link record can itself be superseded by a later link record. Under append-only that is the only available correction path, and an ADR creating permanent unfixable suppressions would be worse than the defect it addresses.

That has a consequence the "mirroring `resolved_ids`" analogy hides. `resolved_ids` is a single non-recursive pass. `superseded_ids` cannot be: it must resolve link-supersedes-link transitively before computing the suppression set, or a retracted link goes on suppressing its target. The correction path is a decision; its implementation is listed as unsettled.

### 5. Retrieval treats supersession as a predicate, never as a weight

On a present-tense query, a record whose id is in `superseded_ids` is **filtered from selection**. No supersession term enters `prior`, `tier`, or the composed score, and no supersession magnitude is introduced anywhere.

That is not stylistic. ADR-044 bounds the composed `prior x tier` against the measured cosine spread and requires one place to compute the bound and one test to assert it (`ADR-044:239-240`). Any supersession multiplier lands inside that budget and would have to be sized against the spread, reopening exactly what ADR-044 closed. Its 2026-09-21 amendment moved role out of score space on this reasoning; this is the second capability moved out on the same argument.

ADR-044 rejected "move the prior out of score space entirely" as a blanket approach because quota sizes become a new unowned parameter (`ADR-044:365-369`). That objection does not reach a per-record predicate over a derived set, which has no size to own.

Superseded records stay reachable. A superseded fact is past, not false: `valid_until` unknown, `superseded_at` set. Every bitemporal system in the literature preserves rather than deletes. **Reachability here means the predicate does not fire** -- it is conditioned on present-tense intent, and a query that is not present-tense does not filter. There is no fallback search, and none is proposed: ADR-015 retired that on the ground that a fallback firing because nothing good was found "is the retrieval form of fabricating an answer" (`ADR-015:476-486`).

**What this decision does not do, stated plainly: it suppresses the stale record; it does not deliver the current one.** On the #207 configuration -- superseded record retrieved, successor not -- filtering yields silence rather than a correct answer. Silence is the better failure, and it is not a fix. Delivering the successor is a reachability problem owned by #204, and ADR-044 documents why it is structurally likely to persist: the unfixed `tier x decay` floor of 0.03.

### 6. Timestamps: close the profile and state gaps, and resolve the rule that contradicts doing so

Per-item timestamps measure +0.54 at 8B and should ship regardless of supersession. For retrieved memory and reflection items they already have (`prompt_builder.py:995-1008`, `:1274-1275`).

The gap is at the classes that carry biographical facts. **Profile items render as bare bullets with no date and no age label** (`prompt_builder.py:983-987`). **State items render as `- [category] text` with no timestamp** (`prompt_builder.py:594-600`). Those are the two classes most likely to hold a fact that goes stale, and the two that reach the model undated.

Dating profile items collides with a shipped instruction. `_AUTHORITY_RULES_PROFILE_HEDGE_EXCLUSION` (`prompt_builder.py:211-217`) tells the model that profile records "do not decay", that "if a profile record is retrieved, it is current and certain", and to hedge only state, conversation and ingested records. Adding dates to profile items without touching that rule ships two opposing signals about the same records. **The rule is narrowed rather than kept or deleted: it applies to profile records that are not superseded.** A profile record with a live supersession link is exactly the case the rule's "current and certain" claim gets wrong, and #207 is that case.

### 7. ADR-019 gains the supersession signal, by amendment

The grounding oracle is given the supersession state of each retrieved record, and its prompt is extended to treat a superseded record as not supporting a present-tense claim.

This is recorded as a numbered decision rather than a consequence because it changes another Accepted ADR's stated scope. ADR-019 explicitly excludes judging whether retrieved content is accurate (`ADR-019:40`). Extending it is right -- a grounding check cannot become temporally aware by reading evidence better; it needs a signal from the store -- but it is an amendment to ADR-019, not an application of it, and it is owed as part of this work.

## The trade-off, stated plainly

Fail-closed misses implicit supersession. A record implying a change without stating it will not be linked, and every miss degrades to the behaviour that produced #207. STALE reports a best-model score of 55.2% on 400 scenarios -- cited here secondhand through the Memory Trust Gap related-work section, which notes "STALE would need a separate read" (`docs/research/memory-trust-gap-followup.md:80-83`), so it is offered as an indication that implicit cases are hard rather than as a measured ceiling for this mechanism.

The permissive alternative is worse in a way that is harder to see: complementary detail marked as replacement, and a valid fact hidden behind a link that should never have been written. A missed link leaves a known failure in place. A wrong link creates a new one, silently, in a system whose purpose is that the user can trust what it remembers -- and under append-only it is durable, correctable per decision 4 but only once someone notices. So: **precision over recall on the link.**

**The cost that argument hides, named here rather than omitted.** "Partial coverage is useful because a miss degrades to current behaviour" holds only while absence of a link carries no information. Decision 7 breaks that: once the grounding oracle reads supersession state, and once retrieval gates on it, *absence of a link becomes the operative signal that a fact is current*. Under fail-closed, absence is the overwhelmingly common case and means almost nothing. An unlinked stale fact would then be one a verification layer has implicitly cleared, which is worse than today, where nothing claims currency at all.

**So the ADR-019 amendment must state that absence of a supersession link is not evidence of currency, and the oracle must not treat it as such.** With that constraint, partial coverage is useful. Without it, this decision makes the system more confidently wrong, and the constraint is therefore part of decision 7 rather than a note on it.

## What this ADR does not settle

**Same-subject candidate scoping, and the bound on K.** How wide the entity match goes, and how many candidates the bounded question is asked about. Both are cost drivers -- see Consequences -- and K is currently unbounded in this document. `resolve_source_records()` (`src/memory/resolve_memory.py:47`) is the nearest cross-type id resolver and its `_CANDIDATE_TYPES` (`:44`) excludes state, task, project and reference, so a supersession resolver is not simply a reuse.

**The confidence threshold.** ADR-014's method applies; the number comes from the sweep.

**A live-path latency budget.** None is stated anywhere in this document, and decision 1 puts model calls on the write path of every turn. The mechanism has to fit inside a budget that does not yet exist.

**Whether present-tense query detection is reliable enough to gate on.** Decision 5 conditions the predicate on it. `classify_query` (`src/context/policies.py:218`) is keyword and marker based with no tense handling at all. If it is unreliable the gate either over-fires, hiding valid history from a historical question, or under-fires, which is today's behaviour. This is the most likely place for the mechanism to fail visibly.

**Whether the link record needs a new memory type.** `VALID_MEMORY_TYPES` is an 18-member frozenset with no link member and `MemoryStorage.get_memory_dir` raises `ValueError` outside it (`src/memory/storage.py:22-41`, `:58-62`). Either a new type is added, with the matching taxonomy update at `CLAUDE.md:161-165`, or the link rides the type of the record it links. The second option is ADR-038's choice but the analogy is inexact -- ADR-038's tombstones ride a *category* inside the single `state` type, whereas here a relational artifact would be written into `memory/conversation/`, `memory/journal/` and `memory/profile/`, which is core rule 5 and CLAUDE.md's named "Mixed memory types" risk.

**Whether an assistant-authored record may supersede a user-authored fact.** The adapter writes both a user and an assistant record per turn (`src/api/openai_adapter.py:618`, `:625`). Answering yes creates the assistant self-echo failure ADR-033 and CLAUDE.md's risk table exist to prevent.

**The transitive `superseded_ids` computation and its read cost.** Decision 4 requires recursion; nothing here specifies it. And `resolved_ids` is cheap because ADR-038 could charge it to an already-O(all-state-files) read (`ADR-038:36`). Memory retrieval has no equivalent whole-corpus read, so `superseded_ids` needs either a per-query scan over an accumulating link population or a derived index -- which reintroduces the rule 4 machinery decision 4 stepped around.

**Three mechanical constraints an implementation inherits.** `flatten_metadata` truncates list metadata to twenty entries (`src/memory/write_memory.py:140`) with `source_record_ids` exempted by name (`:123`), so a list-valued `supersedes` needs the same exemption. Every `write_memory` call embeds and indexes (`:209-250`), so a link record becomes independently retrievable unless excluded. And `write_memory` calls `should_skip_memory` and returns `None` on a skip (`:173`) -- a short structural link record is a plausible skip candidate and the skip is silent.

**The relationship to batch linting.** `docs/Ember2_TDD.md:2037` records "Vault knowledge linting" as a future periodic pass that scans for contradictions and superseded records. That is read-time and batch; this is write-time. They are not competitors -- write-time linking covers records written after it ships, and only a batch pass can reach the pre-existing corpus. This bounds that item to backfill without designing it.

**Corpus health is a prerequisite, not a detail.** Core rule 6 puts source quality before retrieval sophistication, and #211 records an index missing 1,787 canonical records including every profile record. Decision 1's entity matcher reads that index and decision 6 targets profile items specifically. Both are degraded until #211 lands.

## Consequences

**Positive**

- The missing relation exists, as a stored fact rather than an inference re-derived per turn.
- Append-only is preserved without exception, extending ADR-038's vocabulary rather than inventing a second.
- The signal lives in the store, so retrieval, the grounding check and anything later read the same thing.
- A wrong link is correctable through the same mechanism.
- The miss case is today's behaviour, provided decision 7's non-inference constraint holds.

**Negative**

- **Cost is 2 x K model calls per turn, not one.** The adapter writes two memory records per turn (`openai_adapter.py:618`, `:625`) and the bounded question is asked per candidate. `StateExtractor` already pays one call per turn; this is additive on top, on the live path, with K unbounded and no latency budget.
- Backfill over the pre-existing corpus is entity match plus K bounded questions per record, costed nowhere. It is a one-shot offline job, but it is not free.
- Link records accumulate and are never compacted. ADR-038 accepted roughly 2x growth for one state category (`ADR-038:36`); this applies across conversation, journal and profile.
- Links are non-deterministic, so two vaults built from the same records can disagree about what supersedes what, durably.
- ADR-019 must be amended, and the amendment carries a constraint (absence is not evidence of currency) without which this ADR makes things worse.
- **This does not close #207.** It addresses the suppression half of Failure 1. The delivery half is #204's. Failure 2 -- the confidence hedge as prompt folklore, core rule 8 -- is untouched and needs its own decision.
- The entity matcher inherits the same 0.58-0.69 F1 that rules out extraction. Candidate proposal need not be precise, but a subject never proposed is a link never considered.
- Measured headroom on the one benchmark available is about +0.05 over shipped timestamp exposure.

## Alternatives Considered

- **Rank contradictions down in retrieval** -- rejected on measurement. Cosine AUROC 0.59 separating contradictions from duplicates (arXiv:2606.26511). The signal is not in the embedding.
- **Entity-anchored co-retrieval: pull other records about the same subject into the same packet and let the model adjudicate** -- the strongest alternative, and deferred rather than dismissed. The same paper supports it directly: in the explicit-conflict condition, with both records present and honest timestamps, 4B and 8B models follow the stale value 0.01 and 0.00 of the time (`docs/research/memory-trust-gap-followup.md:76-77`). It reuses the entity matcher this ADR needs, costs no live-path model call, creates no non-deterministic canonical record, needs no new memory type and no ADR-019 amendment. Against it: it is a per-query cost paid forever rather than a one-time write cost; it consumes context budget against a four-item model-visible window (`prompt_builder.py:979`); it produces no durable fact, so the grounding check, reflection and any later consumer get nothing; and it cannot help when the successor is unreachable, which is the #207 configuration. **It is also complementary rather than exclusive**, and is the natural companion to decision 5's silence problem. It should be evaluated on its own terms rather than assumed subordinate to this ADR.
- **Generation-time instruction to prefer newer records** -- rejected on measurement (little effect, increases reliance in some settings) and on core rule 8: a policy living only in prompt text.
- **Full write-time extraction into typed triples with validity windows** -- rejected as out of reach locally at 0.214 relation-extraction F1. The right long-term representation; the TDD already notes MemPalace's entity-relation triples with validity windows as relevant (`Ember2_TDD.md:1999`), and MemPalace's own finding that raw verbatim beats AI-extracted summaries argues against forcing it now.
- **Mutate the superseded record in place** -- rejected on core rule 3, and it is the pattern ADR-038 removed. At least two in-place mutations remain live as counter-precedents not to imitate: the cascade delete (`src/api/main.py:396-403`) and `_update_deviation_record`, whose docstring is "Update a deviation record in-place." (`src/api/main.py:750-781`).
- **Delete or archive superseded records** -- rejected. A superseded fact remains the correct answer to a historical question.
- **Batch contradiction linting instead of write-time linking** -- not rejected, bounded to backfill. A poor primary mechanism because it re-scans a growing corpus to find relations that were cheap to establish once, at write, when the new record was in hand.

## References

- Agents-K1, arXiv:2606.13669 -- entity recognition 0.58-0.69 F1, relation extraction 0.214 at 8B.
- Memory Trust Gap, arXiv:2609.01852 -- metadata +0.54 and oracle +0.59 at 8B; verbal prefer-newest instruction has little effect and increases reliance in some settings; explicit-conflict stale-following 0.01 (4B) and 0.00 (8B); Mem0 extractor at 0.398. In-repo summary: `docs/research/memory-trust-gap-followup.md`. The Mem0 figure is not in that summary.
- MemStrata, arXiv:2606.26511 -- cosine AUROC 0.59 separating contradictions from duplicates.
- STALE, arXiv:2605.06527 -- 400 scenarios, best model 55.2%. Cited secondhand; not read.
- All-Mem -- the complementary-detail-marked-as-replacement harm case. Cited from the grill briefing without a locatable identifier; the claim should be given a full citation or replaced with reasoning that does not depend on it.
- ADR-038 -- the precedent this generalises, and its stated trajectory at `:39`.
- ADR-011, `src/state/state_resolver.py`, `src/state/state_service.py` -- supersession as it exists today, scoped to state.
- ADR-019 -- grounding verification. Amendment owed, per decision 7.
- ADR-014 -- the fail-closed threshold method.
- ADR-044 -- score composition contract and its 2026-09-21 amendment.
- Issues #207 (the incident), #204 (the delivery half), #211 (index prerequisite).
