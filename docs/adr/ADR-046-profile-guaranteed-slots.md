# ADR-046: Profile Guaranteed Slots

**Status:** Accepted
**Date:** 2026-09-26
**Target:** v0.19.0
**Amends:** ADR-018 (intent-aware type gating) -- see "Relationship to ADR-018"
**Related:** ADR-005 (context ranking), ADR-015 (memory tiering, profile tier exemption), ADR-044 (retrieval score composition contract), ADR-045 (supersession), PR #221 (profile relevance measurement), PR #226 (profile stops charging the retrieval window), issues #224, #225, #204

## Context

Profile records are prepended to every context packet, unconditionally, ahead of the ranked set and outside the limit cutoff. The mechanism has been in production since before the ADR series reached the retrieval layer and no ADR states it.

Issue #224 found that: forty ADRs, none naming the guarantee, its count, or its rationale. Issue #225 found the other half -- `_apply_type_gate` exempts profile from every clause of the gate, including the `min_score` floor, and ADR-018's own pseudocode contains no such exemption. Filed as a defect because the code and the document disagree.

They do disagree. This ADR decides which one is wrong, and it is the document.

## Decision

**Profile records are guaranteed a place in every context packet. The guarantee is intentional. It is capped at three on an ordinary query and eight on an identity query.**

Three parts, argued separately below: that identity must be present, that relevance cannot decide it, and that the count is a budget choice rather than a measurement.

### 1. Profile is the sole carrier of stated fact about the user

There is no second owner. This was checked against every layer that could plausibly be one:

| layer | what it carries | why it is not a second owner |
|---|---|---|
| system prompt | instructions and role | carries no user facts, deliberately, and says so in its own text |
| nature | dispositions and register | how Ember behaves, not what she knows about anyone |
| state | ten operational categories | current focus, blockers, open loops -- none of them identity |
| conversation | what was said | facts appear only incidentally, and only if retrieved |
| reflection | derived synthesis | about patterns over records, not a record of fact |

If profile does not surface, no other layer supplies what it would have said. That is the structural reason the guarantee exists, and it is the reason a relevance gate on profile is not a smaller version of the same behaviour but a different one.

### 2. Relevance is the wrong instrument for identity

PR #221 measured profile cosine against two query classes on the production embedder:

| query class | profile cosine |
|---|---|
| plausibly related to the user | 0.325 - 0.570 |
| unrelated to the user | 0.287 - 0.503 |

The distributions overlap across almost their whole range. There is no threshold on this embedder that separates "this query is about the user" from "this query is not". A gate placed anywhere in that band admits and excludes on differences the embedder is not resolving.

So a relevance gate on profile would not be a relevance decision. It would be arbitrary exclusion wearing a score, and the score would make it look principled. That is worse than no gate, because it is harder to argue with.

This is a claim about identity records specifically, not a general argument against thresholds. For the memory channel the same embedder resolves a usable ordering; ADR-044 records the production top-8 spread at 0.0815 around a mean rank-1 of 0.6375. The profile case is different because the question is not "which record is most similar" but "does this person's own identity belong in this turn", and similarity does not answer that question at all.

### 3. Ember is a continuity system

The product claim is a durable personal intelligence layer that improves with use. A retrieval score deciding whether Ember knows who she is talking to fails at the thing she exists to do. A turn where identity is absent because the cosine came in at 0.31 rather than 0.34 is not a tuning miss; it is the system not being the system.

## Relationship to ADR-018

ADR-018 specifies the gate as three unconditional clauses and a floor:

```python
def _apply_type_gate(self, items, policy):
    if policy.suppress_memory_types:
        items = [i for i in items if i.memory_type not in policy.suppress_memory_types]
    if policy.eligible_memory_types is not None:
        items = [i for i in items if i.memory_type in policy.eligible_memory_types]
    items = [i for i in items if i.score >= policy.min_score]
```

It contains no profile exemption, and its "Min Score Floor" section says the floor applies "across all retrieval paths".

The implementation exempts profile from all three clauses. **This ADR is the amendment. The exemption is correct and the pseudocode is incomplete.** Issue #225 is closed as intent, not defect, and the exemption stays.

Two consequences worth stating rather than leaving implied:

- Profile bypasses `eligible_memory_types` as well as the floor. `status_state` and `factual_recall` both list profile among their eligible types, so the bypass is not currently observable on those policies -- but it would hold even on a policy that excluded profile, and that is deliberate under this ADR.
- The exemption lives in `ContextService._apply_type_gate`, at the branch counted as `type_gate.profile_bypass`. Line numbers for it have moved three times in the last week (#233, #238, #245), so it is named here by function and counter rather than by line.

## The cap

**Three is a budget choice. It is not a relevance outcome and should not be described as one.** No measurement says three is the right number; measurement cannot say so, for the reason in decision 2. Three is a judgement about how much of a finite prompt to spend on always-on identity.

Where it is enforced: `ContextRetriever.get_profile_items` requests `limit = 8 if is_identity else 3`. The service layer applies no cap of its own -- it partitions whatever the retriever returned and prepends all of it. So the count is a retrieval parameter, and the eight-slot identity case is part of the same decision: when the query is explicitly about the user, the budget triples.

### What the cap costs, now that #226 has landed

Before PR #226 the slots were charged against the memory retrieval window, so three profile records meant three fewer source memories. That was a real starvation cost and on the reflective policy, whose limit is four, it left one non-profile slot and disabled diversity selection outright.

Since #226 the slots no longer charge that window. The remaining cost is narrower and worth naming exactly:

- **prompt tokens**, three records on every turn
- **the `prompt_builder` non-profile cap of `[:4]`**, which profile does not compete with directly but does share a prompt with

Counters from the personal-vault window, 36 turns:

| site | reading |
|---|---|
| `reserved_slots.profile_present` | 36 of 36 -- fires on every turn |
| `reserved_slots.non_profile_truncated` | 27 of 36 |
| `reserved_slots.profile_over_limit` | 2 of 36 |
| `type_gate.profile_bypass` | 118 of 482 evaluations |
| `profile.identity_query` | 2 of 36 |

`profile_present` firing on every evaluation means it does not discriminate -- it is a constant, which is what a guarantee looks like in a counter.

### What would change the number

Three is revisitable on evidence, and these are the things that would constitute it:

1. **Growth in the profile record count.** Sixteen records today. At a substantially larger count, three stops being "most of what is known" and starts being a sample, at which point which three becomes the dominant question rather than how many.
2. **A change to the prompt slice.** The non-profile `[:4]` cap and the three profile slots are one budget seen from two ends. Moving either without the other changes the ratio silently.
3. **Evidence that three displaces more than it carries.** This is measurable and has not been measured: a comparison of delivered-answer quality with three, one, and zero profile slots on queries with no plausible relation to the user. Nothing in this ADR rests on the outcome, and the outcome could move the number.

## Open, and not settled here

### Ordering: which three of sixteen

Unspecified, and this is the real remaining defect in the mechanism.

Selection is currently a composite rank over the distribution in decision 2 -- the one with almost complete overlap between related and unrelated queries. At any count, the winners are therefore effectively arbitrary and vary query to query. The guarantee holds; which facts it delivers does not.

`metadata.category` exists on profile records and nothing in the retrieval path reads it. One correction to how this has been described: it is populated on **3 of 16 records**, with three distinct values, not across the full taxonomy. So it is not usable as an ordering key today without a backfill, and any decision that leans on it inherits that work.

This needs its own decision. It is not folded into this ADR because it is a different question -- this one is whether identity is guaranteed, that one is which identity.

### Relevance-ordering within the guarantee

Whether profile should be ordered by relevance even though it is not gated by it. The two are separable: an unordered guarantee and a relevance-ordered guarantee both deliver three records on every turn. Given decision 2, relevance ordering would be ordering on a signal that does not discriminate, which argues against it -- but the alternatives (recency, category priority, round-robin over categories) each imply a different answer to the ordering question above, so this is left open with it.

## The trade-off, stated plainly

**Three always-on records on every query, including queries with no plausible relation to the user, is a real cost.** A question about the weather carries three facts about the person asking. That is prompt budget spent on context the turn does not need, on most turns.

It is paid deliberately, for a guarantee that identity is never absent. The alternative is not "the same behaviour, cheaper" -- it is a system that sometimes does not know who it is talking to, with the decision made by a score that cannot tell the difference. This ADR takes the cost.

## Consequences

**Positive**

- The guarantee has an owner, a count, and a rationale that can be argued with.
- #225 stops being an open defect against correct code.
- The cap is stated as a budget choice, so the next person to change it knows what kind of argument to bring.
- The ordering question is separated out and named as the live one.

**Negative**

- Prompt budget is spent on every turn regardless of relevance, and this ADR declines to reduce it.
- The exemption remains a divergence between ADR-018's pseudocode and the implementation, resolved by amendment rather than by making them literally match.
- Deciding the guarantee without deciding the ordering means the mechanism is documented and still arbitrary in what it delivers. That is honest but unfinished.

**Neutral**

- No code changes. This is a decision record; the implementation already behaves as decided.
