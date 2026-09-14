"""
tests/test_retrieval_ablation_arms.py

The seam's job is to make exactly one mechanism stop working per arm, and to
leave every other mechanism untouched. A patch that silently no-ops would
produce a clean-looking ablation measuring nothing, so the central test here is
that every arm actually moves the ranking.

Runs against the session test vault via conftest's isolate_to_test_vault.
"""

from __future__ import annotations

import pytest

from src.context.models import ContextItem
from src.context.ranker import ContextRanker
from tools.retrieval_ablation.arms import (
    ARMS,
    ARMS_BY_NAME,
    NEUTRAL_ITEM_TYPE,
    PristineScoreError,
    _candidate_fixtures,
    _patched_type_gate,
    _policy_for,
    assert_pristine,
    base_policy,
    build_candidates,
    retrieval_score,
    run_cell,
)
from tools.retrieval_ablation.corpus import FIXTURES_BY_ID, STRATA, STRATA_BY_NAME

A0 = ARMS_BY_NAME["A0_FULL"]
TREATMENTS = [a for a in ARMS if a.name != "A0_FULL"]


class TestEveryArmActuallyAblates:
    """A patch that no-ops would give a tidy result measuring nothing."""

    @pytest.mark.parametrize("arm", TREATMENTS, ids=lambda a: a.name)
    def test_arm_changes_the_ranking_somewhere(self, arm):
        moved = 0
        for stratum in STRATA:
            reference = run_cell(stratum, A0)
            treated = run_cell(stratum, arm)
            if [i.id for i in treated.ranked] != [i.id for i in reference.ranked]:
                moved += 1
        assert moved > 0, f"{arm.name} changed no ranking on any stratum"

    def test_tier_ablation_reaches_the_items(self):
        """H-off changes no DELIVERED set on this corpus, so the ablation has
        to be shown to fire at all -- otherwise that result is indistinguishable
        from a broken patch."""
        stratum = STRATA_BY_NAME["factual_recall_retrieval_theory"]
        full = {i.id: i.tier for i in build_candidates(stratum, A0)}
        off = {i.id: i.tier for i in build_candidates(stratum, ARMS_BY_NAME["A_H-off"])}

        flipped = [k for k in full if full[k] != off[k]]
        assert len(flipped) > 10
        assert all(off[k] == "hot" for k in flipped)

    def test_tiering_is_masked_not_inert(self):
        """The distinction the whole ranked-window capture exists for: tiering
        reorders the ranking while leaving the delivered set alone, because
        profile takes guaranteed slots ahead of the limit."""
        arm = ARMS_BY_NAME["A_H-off"]
        ranked_moved = delivered_moved = 0
        for stratum in STRATA:
            reference = run_cell(stratum, A0)
            treated = run_cell(stratum, arm)
            if [i.id for i in treated.ranked] != [i.id for i in reference.ranked]:
                ranked_moved += 1
            if [i.id for i in treated.delivered] != [i.id for i in reference.delivered]:
                delivered_moved += 1
        assert ranked_moved > 0, "tier ablation did not move any ranking"
        assert ranked_moved > delivered_moved


class TestConstantFreeAblations:
    """Each ablation removes a mechanism's INPUT rather than restating its
    constants, so none can drift when a weight is retuned."""

    def test_neutral_item_type_scores_zero_type_boost(self):
        """The sentinel must fall through _score_memory_item's type ladder.
        Checked against the real function, not against a copied table."""
        ranker = ContextRanker()

        def probe(item_type):
            item = ContextItem(
                id="p", content="a sufficiently long probe body for the scorer to accept",
                source=item_type, item_type=item_type, memory_type=item_type,
                score=0.0, timestamp=None, metadata={},
            )
            return ranker._score_memory_item(item).score

        assert probe(NEUTRAL_ITEM_TYPE) == pytest.approx(probe("ingested"))
        assert probe("conversation") > probe(NEUTRAL_ITEM_TYPE)

    def test_decay_off_is_uniform_one_not_the_default_curve(self):
        """Collapsing to _DEFAULT_DECAY would strip the no-decay exemption that
        ingested and profile rely on and can flip delta_T's sign."""
        ranker = ContextRanker()
        stratum = STRATA_BY_NAME["factual_recall_retrieval_theory"]
        arm = ARMS_BY_NAME["A_decay-off"]

        real_weights = set()
        for item in build_candidates(stratum, arm):
            real_weights.add(ranker._temporal_decay_weight(item))
        assert len(real_weights) > 1, "corpus must span several decay buckets"

        from unittest.mock import patch

        with patch.object(ContextRanker, "_temporal_decay_weight", lambda self, i: 1.0):
            off = {
                ContextRanker()._temporal_decay_weight(i)
                for i in build_candidates(stratum, arm)
            }
        assert off == {1.0}

    def test_profile_relabel_preserves_decay(self):
        """T-off_policy strips profile privilege by relabelling to "reference".
        Both are in _NO_DECAY_TYPES, so decay is unchanged -- relabelling to a
        decaying type would have applied a 10x side effect and contaminated
        the arm."""
        ranker = ContextRanker()
        stratum = STRATA_BY_NAME["default_identity_question"]

        full = {
            i.id: ranker._temporal_decay_weight(i)
            for i in build_candidates(stratum, A0)
        }
        stripped = {
            i.id: ranker._temporal_decay_weight(i)
            for i in build_candidates(stratum, ARMS_BY_NAME["A_T-off_policy"])
        }
        profile_ids = [
            f.id for f in _candidate_fixtures(stratum)
            if f.memory_type == "profile"
        ]
        assert profile_ids
        for pid in profile_ids:
            assert full[pid] == stripped[pid] == 1.0

    def test_profile_relabel_removes_the_privilege(self):
        stratum = STRATA_BY_NAME["default_identity_question"]
        stripped = build_candidates(stratum, ARMS_BY_NAME["A_T-off_policy"])
        assert all(i.memory_type != "profile" for i in stripped)
        assert any(i.memory_type == "reference" for i in stripped)


class TestTypeGateReadsRawScore:
    """The gate applies min_score to item.score, which every arm shifts. Left
    alone it would be a treatment-detector: a lower-scoring arm would lose
    candidates before ranking even started."""

    def test_item_below_floor_on_adjusted_score_survives_on_raw(self):
        stratum = STRATA_BY_NAME["activity_pipeline_work"]
        policy = _policy_for(stratum, A0)
        item = ContextItem(
            id="probe", content="a long enough probe body to pass the content filters",
            source="conversation", item_type="conversation", memory_type="conversation",
            score=0.01,                      # far below policy.min_score
            metadata={"raw_score": 0.90},    # but strongly similar
        )
        kept = _patched_type_gate(A0)(None, [item], policy)
        assert [i.id for i in kept] == ["probe"]

    def test_item_below_floor_on_raw_is_dropped(self):
        stratum = STRATA_BY_NAME["activity_pipeline_work"]
        policy = _policy_for(stratum, A0)
        item = ContextItem(
            id="weak", content="a long enough probe body to pass the content filters",
            source="conversation", item_type="conversation", memory_type="conversation",
            score=0.99, metadata={"raw_score": 0.01},
        )
        assert _patched_type_gate(A0)(None, [item], policy) == []

    def test_type_policy_off_skips_eligible_filtering(self):
        """factual_recall excludes journal and reflection. Under T-off_policy
        that exclusion must not apply."""
        stratum = STRATA_BY_NAME["factual_recall_retrieval_theory"]
        journal = ContextItem(
            id="j", content="a long enough probe body to pass the content filters",
            source="journal", item_type="journal", memory_type="journal",
            score=0.5, metadata={"raw_score": 0.5},
        )
        gated_full = _patched_type_gate(A0)(None, [journal], _policy_for(stratum, A0))
        arm = ARMS_BY_NAME["A_T-off_policy"]
        gated_off = _patched_type_gate(arm)(None, [journal], _policy_for(stratum, arm))

        assert gated_full == []
        assert [i.id for i in gated_off] == ["j"]


class TestPristineScores:
    def test_passes_on_a_fresh_candidate_set(self):
        stratum = STRATA[0]
        assert_pristine(build_candidates(stratum, A0), stratum, A0)

    def test_fires_on_a_mutated_score(self):
        """Non-vacuousness: every ranking stage mutates ContextItem.score in
        place, so this guard has to actually catch a carried-over score."""
        stratum = STRATA[0]
        items = build_candidates(stratum, A0)
        items[3].score *= 0.7
        with pytest.raises(PristineScoreError):
            assert_pristine(items, stratum, A0)

    def test_fires_on_a_mutated_raw_score(self):
        stratum = STRATA[0]
        items = build_candidates(stratum, A0)
        items[2].metadata["raw_score"] = 0.999
        with pytest.raises(PristineScoreError):
            assert_pristine(items, stratum, A0)

    def test_arms_do_not_compound(self):
        """Running an arm after another must give the same answer as running it
        alone, or multipliers are leaking between arms."""
        stratum = STRATA_BY_NAME["activity_pipeline_work"]
        alone = run_cell(stratum, ARMS_BY_NAME["A_decay-off"])
        run_cell(stratum, A0)
        run_cell(stratum, ARMS_BY_NAME["A_T-off_policy"])
        after = run_cell(stratum, ARMS_BY_NAME["A_decay-off"])
        assert [i.id for i in after.delivered] == [i.id for i in alone.delivered]
        assert [round(i.score, 9) for i in after.delivered] == [
            round(i.score, 9) for i in alone.delivered
        ]


class TestDeterminismAndParity:
    def test_same_arm_twice_is_identical(self):
        stratum = STRATA_BY_NAME["reflective_work_patterns"]
        first = run_cell(stratum, A0)
        second = run_cell(stratum, A0)
        assert [i.id for i in first.delivered] == [i.id for i in second.delivered]
        assert [i.score for i in first.delivered] == [i.score for i in second.delivered]

    def test_candidate_pool_is_identical_across_arms(self):
        """Pool size must not decide the comparison. Every arm sees the same
        candidates -- stricter than equalising per-store limits, since there
        are no stores under injection."""
        stratum = STRATA_BY_NAME["activity_pipeline_work"]
        pools = {
            arm.name: {i.id for i in build_candidates(stratum, arm)} for arm in ARMS
        }
        assert len(set(map(frozenset, pools.values()))) == 1

    def test_naive_arms_deliver_the_same_budget(self):
        """The naive baseline is cut at the limit its pinned policy would have
        used, so the comparison is not decided by how many items an arm
        returns."""
        for stratum in STRATA:
            reference = run_cell(stratum, A0)
            naive = run_cell(stratum, ARMS_BY_NAME["A_NAIVE_cosine"])
            assert len(naive.delivered) >= len(reference.delivered)

    def test_profile_candidates_respect_the_retriever_limit(self):
        """get_profile_items returns at most 3 on a normal query, so injecting
        more profile records than production can produce would crowd real
        memory out of every arm."""
        for stratum in STRATA:
            profile = [
                f for f in _candidate_fixtures(stratum) if f.memory_type == "profile"
            ]
            cap = 8 if stratum.name == "default_identity_question" else 3
            assert len(profile) <= cap, stratum.name


class TestPolicyPinning:
    @pytest.mark.parametrize("stratum", STRATA, ids=lambda s: s.name)
    def test_every_stratum_resolves_its_policy(self, stratum):
        assert base_policy(stratum.policy_name).name == stratum.policy_name

    def test_diversity_is_off_in_every_arm(self):
        """Controlled variable: the quota forces a 2/2/2 type split at limit 6
        regardless of score and would clamp composition identically everywhere."""
        for stratum in STRATA:
            for arm in ARMS:
                assert _policy_for(stratum, arm).diversity is False

    def test_type_scoring_off_equalises_the_weight_split(self):
        stratum = STRATA_BY_NAME["reflective_work_patterns"]
        full = _policy_for(stratum, A0)
        off = _policy_for(stratum, ARMS_BY_NAME["A_T-off_scoring"])
        assert full.reflection_weight != full.memory_weight
        assert off.reflection_weight == off.memory_weight


class TestRetrievalScoreSwitches:
    def test_naive_cosine_is_the_bare_signal(self):
        stratum = STRATA_BY_NAME["activity_pipeline_work"]
        arm = ARMS_BY_NAME["A_NAIVE_cosine"]
        for fixture_id in stratum.judgments:
            fixture = FIXTURES_BY_ID[fixture_id]
            assert retrieval_score(fixture, stratum, arm) == pytest.approx(
                stratum.cosine(fixture_id)
            )

    def test_lexical_arm_adds_only_lexical(self):
        stratum = STRATA_BY_NAME["activity_pipeline_work"]
        bare = ARMS_BY_NAME["A_NAIVE_cosine"]
        lex = ARMS_BY_NAME["A_NAIVE_plus_lexical"]
        fixture = FIXTURES_BY_ID["f01_work_decay_trace"]
        assert retrieval_score(fixture, stratum, lex) > retrieval_score(
            fixture, stratum, bare
        )

    def test_type_terms_are_removed_without_touching_the_prefix_term(self):
        """query_intent_adjustment mixes a type term with a content-prefix
        term. Only the type half may go."""
        stratum = STRATA_BY_NAME["reflective_work_patterns"]
        full = ARMS_BY_NAME["A0_FULL"]
        toff = ARMS_BY_NAME["A_T-off_scoring"]
        conversation = FIXTURES_BY_ID["f10_health_routine_intent"]

        delta = retrieval_score(conversation, stratum, full) - retrieval_score(
            conversation, stratum, toff
        )
        assert delta > 0, "conversation should lose a positive type term"
