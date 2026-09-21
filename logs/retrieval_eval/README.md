# Retrieval-architecture ablation: run registry

`logs/` is gitignored. This file and `published/` are the only tracked
exceptions, because ablation results get cited in ADRs and issues and a
result that cannot be opened from a clean checkout cannot be checked.

Cite a run from `published/`. Anything else in this directory is local scratch
and may not correspond to any committed code.

## Published runs

| run | status | A0_FULL ranked nDCG@6 | notes |
|---|---|---|---|
| `ablation_2026-09-21T11-41-07` | **canonical** | 0.2515 | Current. Post the profile slot-budget change: profile is no longer charged against `memory_limit`, so delivered counts rise from 4-6 to 7-10 and the naive arm's budget tracks it. |
| `ablation_2026-09-19T08-15-13` | superseded | 0.2515 | Ran while profile was still billed to `memory_limit`. Its delivered counts are not comparable to the canonical run's; its ranked metrics are identical to it. |

## Invalidated runs — do not cite

**`ablation_2026-09-14T09-53-23`** — INVALIDATED. Not published, and its
numbers must not be quoted from any local copy.

This is the first build of the eval, before cosines were measured. It
hand-assigned the base similarity signal, and in doing so made it agree with
the grades (Kendall tau +0.64). A naive cosine baseline handed the answer key
wins by construction: every typed stage can only subtract, so every ablation
reads as an improvement and the result says nothing about Ember. Its
signature is `A_NAIVE_cosine` ranked nDCG@6 of **0.9367** — the number
`tools/retrieval_ablation/corpus.py` names in its module docstring as the
disqualifying failure. It also has 8 arms rather than 11 and ranked pools of
39-40 rather than 41-42.

Anyone reading that file instead of the canonical one gets a bare-cosine
advantage of roughly 3.5x instead of 2.3x. That is the entire reason this
registry exists.

**`ablation_2026-09-14T10-29-48`** — superseded, not invalid. The first run
with measured cosines, and the source of the figures quoted in issue #204. Its
nDCG and contamination numbers reproduce exactly in the canonical run. Prefer
the canonical run; this one is kept locally for provenance only and is not
published.

## What changed since 2026-09-14, and what did not

Every quality metric is identical to the `10-29-48` run, to full float
precision: `A0_FULL` ranked nDCG@6 0.2515, delivered nDCG@6 0.2260,
contamination@4 0.8333, and the same for all eleven arms. That is not the
harness failing to notice three merged PRs. It is the ADR-015 caveat showing
up as a number.

`ranked_unresolved` for `A0_FULL` moved **0.1780 -> 0.0061**, a 29x collapse
of the unresolved tie band. That is cold-as-weight (#185) landing: cold no
longer flattens every cold record to a single score of 0.0, so the ties it
used to manufacture are gone. The mechanism is live and the harness sees it.

The quality metrics did not move because every delivered record is `tier=hot`
in all eight strata, and the ranked window's top 6 is hot as well. Cold records
reorder among themselves below the measured cut. nDCG@6 cannot see that; the
tie-band metric can. So the two runs agree on the metrics that are structurally
blind to the change, and disagree on the one that is not.

Practical consequence: the staleness objection to citing the `10-29-48`
figures is retired for nDCG, contamination and Jaccard. It was never true that
those numbers described a different ranker's *output*; it was true that they
were produced by different ranker *code*, and the two turn out to coincide on
this corpus.

The `-0.18` low-value-prompt penalty removed in #208 had no effect here
either: it fired on three hardcoded literals and no fixture contains any of
them, which `tests/test_retrieval_ablation_corpus.py` independently asserts by
requiring that no fixture is dropped by `_is_low_value_memory`.

## Cross-query Jaccard, reported for the first time

`metrics.mean_pairwise_jaccard` is new in this run. It had been cited for
seven arms in issue #204 while existing in no tool, which is why five of those
rows were never reproducible -- the artifact carries `selection_jaccard`,
which compares an arm to the reference rather than a query to a query.

The two rows that were reproducible now reproduce from the metric itself:
`A0_FULL` 0.5962 and `A_NAIVE_cosine` 0.0883, a gap of -0.508. The five rows
in between remain unverifiable and should not be re-cited; they came from a
harness variant that is not in this repository.

Read the number against the other arms, not against zero. The strata were
authored with largely disjoint relevant sets, so a correct retriever scores
low *here* for reasons that do not transfer to a vault where a user's concerns
recur across questions.

## The 2026-09-21 change and what it moved

Profile records stopped being charged against `memory_limit`
(`service.py:244`), so the delivered window grew from 4-6 records to 7-10 and
the naive arm's cut was corrected to track the same budget -- `memory_limit`
non-profile plus every profile record -- because a hardcoded `memory_limit`
silently under-cut the naive arm once the service stopped subtracting, which
is a window-size confound rather than a ranking result.

What moved, all eleven arms:

- **ranked nDCG@6: identical, +0.000 everywhere.** The ranker's verdict did
  not change, which is the expected result for a selection change.
- delivered nDCG@6: +0.003 to +0.007.
- contamination@4: -0.021 on most arms, -0.031 on bare cosine.
- **cross-query Jaccard: up substantially**, A0_FULL 0.5962 -> 0.7317.

Read that last one carefully rather than as a regression. Jaccard over larger
delivered sets drawn from a fixed 42-fixture pool rises mechanically: at 9 or
10 delivered from 42, overlap between any two queries is partly forced. The
production corpus is 18,677 records, where a wider window does not compel the
same overlap. The number is real; its interpretation does not transfer.

## Two standing cautions about any run

**The two runs from 2026-09-14 are not replicates.** The harness is
deterministic — frozen cosines in `corpus.py`, `PYTHONHASHSEED=0`, per-arm
database snapshot and restore — so re-running reproduces a result bit for bit.
That is a property worth having, but it means CLAUDE.md's "run the eval twice
before calling a regression" rule cannot be discharged here by repetition.
Run-to-run variance is not measurable on this instrument; a second opinion has
to come from a second corpus.

**ADR-015's amendment rules this eval inadmissible on tiering.** Every
delivered record is `tier=hot` in all eight strata, so tier arms have nothing
to act on at the delivery layer. The amendment pre-registered that caveat
before the result existed. It still holds in the canonical run.
