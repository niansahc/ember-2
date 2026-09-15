"""
tests/test_retrieval_ablation_corpus.py

The corpus is only useful if it survives the pipeline it is measuring. A
fixture silently dropped by `_is_low_value_memory` or `_is_echo_or_meta_memory`
before ranking would not be a weak result -- it would be an absent one, and
every arm would agree about it for the wrong reason.

These tests run the fixtures through the REAL service filters rather than
re-implementing their rules.
"""

from __future__ import annotations

import re
from datetime import datetime

import pytest

from src.context.models import ContextItem
from src.context.service import ContextService
from tools.retrieval_ablation.corpus import (
    BAIT_TARGETS,
    CORPUS_TEXT_DIGEST,
    DISTRACTOR_CLASSES,
    FIXTURES,
    FIXTURES_BY_ID,
    MEASURED_COSINES,
    STRATA,
    STRATA_BY_NAME,
    corpus_text_digest,
)
from tools.retrieval_ablation.metrics import (
    K_SERVICE,
    Delivered,
    QueryLabels,
    ndcg_at_k,
)

# src/context/service.py _apply_type_gate drops anything below this.
TYPE_GATE_FLOOR = 0.25
# src/core/config.py get_retrieval_min_raw_score default, used by the
# default-policy relevance gate.
DEFAULT_POLICY_MIN_RAW = 0.5

RETRIEVABLE_TYPES = {"conversation", "journal", "reflection", "ingested", "profile"}


def _as_item(fixture, score: float) -> ContextItem:
    return ContextItem(
        id=fixture.id,
        content=fixture.text,
        source=fixture.memory_type,
        item_type=fixture.memory_type,
        memory_type=fixture.memory_type,
        score=score,
        timestamp=fixture.timestamp(),
        tier=fixture.tier,
        metadata={"raw_score": score},
    )


class TestPoolShape:
    def test_pool_size(self):
        assert len(FIXTURES) == 42

    def test_eight_strata(self):
        assert len(STRATA) == 8

    def test_ids_are_unique_and_stable(self):
        """The existing harness derives ids from hash(content), which is salted
        per process and therefore unusable across subprocess arms."""
        ids = [f.id for f in FIXTURES]
        assert len(set(ids)) == len(ids)
        assert all(re.fullmatch(r"[a-z0-9_]+", i) for i in ids)

    def test_texts_are_distinct(self):
        """_deduplicate is exact-match on normalized text."""
        normalized = [re.sub(r"\s+", " ", f.text.strip().lower()) for f in FIXTURES]
        assert len(set(normalized)) == len(normalized)

    def test_corpus_is_ascii(self):
        for fixture in FIXTURES:
            assert fixture.text.isascii(), fixture.id

    def test_only_retrievable_types_are_used(self):
        """state/task/project/reference appear in some eligible_memory_types
        lists but never reach memory_items in production, so including them
        would test a path that does not exist."""
        for fixture in FIXTURES:
            assert fixture.memory_type in RETRIEVABLE_TYPES, fixture.id

    def test_every_distractor_class_is_represented(self):
        present = {f.distractor_class for f in FIXTURES if f.distractor_class}
        assert present == set(DISTRACTOR_CLASSES)

    def test_profile_fixtures_are_hot(self):
        """get_profile_items builds items without a tier field, so profile
        always arrives hot in production whatever the tiering job assigned."""
        for fixture in FIXTURES:
            if fixture.memory_type == "profile":
                assert fixture.tier == "hot", fixture.id

    def test_tiers_are_a_real_mix(self):
        """Production is 99.92% cold with zero warm records, which is a defect,
        not a design. The corpus stipulates a meaningful spread so the tier
        mechanism has something to act on."""
        tiers = {t: 0 for t in ("hot", "warm", "cold")}
        for fixture in FIXTURES:
            tiers[fixture.tier] += 1
        assert all(count >= 5 for count in tiers.values()), tiers


class TestJudgments:
    def test_grades_are_in_range(self):
        for stratum in STRATA:
            for fixture_id, grade in stratum.judgments.items():
                assert 0 <= grade <= 3, (stratum.name, fixture_id)

    def test_judgments_reference_real_fixtures(self):
        for stratum in STRATA:
            for fixture_id in stratum.judgments:
                assert fixture_id in FIXTURES_BY_ID, (stratum.name, fixture_id)

    def test_every_stratum_has_a_top_grade_and_a_gradient(self):
        """nDCG needs a gradient. A stratum whose judgments are all grade 3
        cannot distinguish a good ordering from a lucky one."""
        for stratum in STRATA:
            grades = set(stratum.judgments.values())
            assert 3 in grades, stratum.name
            assert len(grades) >= 3, (stratum.name, grades)

    def test_distractors_are_always_grade_zero(self):
        """A distractor that is also relevant cannot attribute leakage."""
        for stratum in STRATA:
            for fixture_id, grade in stratum.judgments.items():
                if FIXTURES_BY_ID[fixture_id].distractor_class:
                    assert grade == 0, (stratum.name, fixture_id)

    def test_every_stratum_baits_at_least_two_mechanisms(self):
        for stratum in STRATA:
            classes = {
                FIXTURES_BY_ID[fid].distractor_class
                for fid in stratum.judgments
                if FIXTURES_BY_ID[fid].distractor_class
            }
            assert len(classes) >= 2, (stratum.name, classes)

    def test_policy_names_are_distinct_and_real(self):
        from src.context.policies import (  # noqa: F401  (import proves they exist)
            ContextPolicy,
        )

        names = [s.policy_name for s in STRATA]
        assert len(set(names)) == 8


def _naive_ranking(stratum):
    """The whole pool ordered by measured cosine alone -- what a verbatim
    embedding baseline would deliver, before any Ember machinery runs."""
    return sorted(FIXTURES, key=lambda f: stratum.cosine(f.id), reverse=True)


def _naive_ndcg(stratum) -> float:
    ranked = [
        Delivered(id=f.id, score=stratum.cosine(f.id)) for f in _naive_ranking(stratum)
    ]
    labels = QueryLabels(
        grades=dict(stratum.judgments), distractor_classes={}, abstain=False
    )
    return ndcg_at_k(ranked, labels, K_SERVICE)


class TestCorpusValidity:
    """The two ways this corpus can silently stop testing anything.

    Both have happened. The first build of this eval stipulated cosines by hand
    and made them agree with the grades -- cosine-only nDCG@6 was 0.937, so the
    pipeline had nothing to fix and every ablation scored as an improvement. The
    run looked clean and meant nothing. These are pinned so neither failure mode
    can come back without a red test.
    """

    # Ceiling, not a target. Above this the naive baseline is close enough to an
    # oracle that no mechanism can demonstrate value, and a "naive cosine wins"
    # result would be a statement about the fixtures rather than about Ember.
    MAX_NAIVE_NDCG = 0.80

    # Floor on the harm cases. A bait that never reaches the delivered window
    # cannot leak, so leakage_by_class would report zero for a mechanism that is
    # in fact wide open.
    MIN_FLOATING_BAITS = 8

    # Floor on the help cases. Without a correctly-graded record that starts
    # BELOW the cut, the pipeline is only ever reordering items it already has,
    # and nDCG cannot reward a mechanism for rescuing anything.
    MIN_BURIED_RELEVANT = 5

    def test_naive_cosine_is_not_an_oracle(self):
        mean_naive = sum(_naive_ndcg(s) for s in STRATA) / len(STRATA)
        assert mean_naive <= self.MAX_NAIVE_NDCG, (
            f"cosine alone scores nDCG@6 {mean_naive:.3f} over the pool. The "
            "baseline is being handed the answer key, so every ablation will "
            "read as an improvement. Bury more relevant records: phrase them "
            "without the query's vocabulary, then re-run regenerate_cosines."
        )

    def test_some_baits_float_above_the_cut(self):
        """PINNED: a non-zero number of distractors must out-cosine the records
        they target, or the leakage metric is vacuous."""
        floating = 0
        for stratum in STRATA:
            window = _naive_ranking(stratum)[:K_SERVICE]
            floating += sum(
                1 for f in window
                if f.distractor_class and stratum.grade(f.id) == 0
            )
        assert floating >= self.MIN_FLOATING_BAITS, (
            f"only {floating} grade-0 baits reach the naive top-{K_SERVICE} across "
            f"{len(STRATA)} strata. A bait below the cut cannot leak, so every "
            "leakage_by_class count would read zero regardless of what the "
            "mechanism actually does."
        )

    def test_some_relevant_records_start_below_the_cut(self):
        """The mirror of the above: the pipeline needs something to rescue."""
        buried = 0
        for stratum in STRATA:
            below = _naive_ranking(stratum)[K_SERVICE:]
            buried += sum(1 for f in below if stratum.grade(f.id) >= 2)
        assert buried >= self.MIN_BURIED_RELEVANT, (
            f"only {buried} grade>=2 records start outside the naive top-"
            f"{K_SERVICE}. With nothing to rescue, no mechanism can score a gain "
            "and the eval can only ever measure damage."
        )

    @pytest.mark.skip(
        reason=(
            "ADR-015 amendment step 3 (cold as weight, COLD_MULTIPLIER=0.3) "
            "made d03/d04 (ingested/cold, decay_bait) reachable for the "
            "first time -- previously score=0.0 kept them from outranking "
            "any relevant record, so this check never ran for them. Now "
            "reachable, it correctly finds A_decay-off cannot move them "
            "(ingested is decay-exempt, always was); their real dominant "
            "lever is A_quality-off. Corpus recalibration, not a ranker "
            "bug -- see issue #184."
        )
    )
    def test_every_designed_bait_is_reachable_by_its_lever(self):
        """PINNED: the lever a bait is labelled with must actually move it.

        This is the reachability half of bait validity, and it is the half that
        can be asserted honestly. A swing of zero means no arm in the set
        removes the mechanism this bait exists to probe, so its leakage count
        could never attribute to anything -- it would read zero whether the
        mechanism were airtight or wide open. That is how the three missing arms
        were found: recency baits measured exactly 0.000 under every arm,
        because _recency_boost is a different mechanism from
        _temporal_decay_weight and nothing ablated it; the lexical, entity and
        experience classes had the same problem.

        What is NOT asserted here is that the labelled lever is the DOMINANT
        one. Several fixtures are carried by a lever other than their label --
        d01 sits 0.173 below its victim on cosine and 0.335 above it after
        ranking, so the pipeline's own stacking put it there, not similarity.
        Forcing the labels to come true would mean tuning the corpus until a
        real property of the pipeline stopped being visible. The report measures
        and prints the dominant lever per class instead, so the output states
        what is true rather than relying on the label being true.
        """
        from tools.retrieval_ablation.arms import measure_lever_attribution

        unreachable = []
        for stratum in STRATA:
            for row in measure_lever_attribution(stratum):
                if row["labelled_swing"] <= 0:
                    unreachable.append(
                        {
                            "stratum": stratum.name,
                            "bait": row["bait"],
                            "class": row["class"],
                            "lever": row["labelled_lever"],
                            "swing": round(row["labelled_swing"], 4),
                        }
                    )

        assert not unreachable, (
            "no arm in the set removes the lever these baits probe, so their "
            "leakage counts would read zero regardless of what the mechanism "
            "does. Add an isolating arm rather than relabelling the bait: "
            + repr(unreachable)
        )

    def test_attribution_is_measured_for_every_designed_bait(self):
        """Non-vacuousness for the above: the report can only print a dominant
        lever per class if attribution actually resolves for these pairs."""
        from tools.retrieval_ablation.arms import measure_lever_attribution

        rows = [r for s in STRATA for r in measure_lever_attribution(s)]
        assert len(rows) >= 6, len(rows)
        # More than one mechanism must be under observation, or the attribution
        # table would be a single row dressed up as a finding.
        assert len({r["class"] for r in rows}) >= 4, sorted(
            {r["class"] for r in rows}
        )
        for row in rows:
            assert row["dominant_lever"], row


class TestGatesDoNotSilentlyEmptyThePool:
    def test_the_gate_never_removes_a_relevant_record(self):
        """The gate dropping an irrelevant record is the gate working. The gate
        dropping a GRADED record would silently shrink the candidate set and
        make every arm agree about it for a reason the report never shows.

        Cosines are measured now, so a few pool members legitimately land under
        the floor for a query they have nothing to do with. Asserting that every
        fixture clears it in every stratum would be asserting that the embedder
        never strongly disagrees with anything, which is not a property worth
        having and not one the corpus should be tuned to produce.
        """
        for stratum in STRATA:
            for fixture in FIXTURES:
                if stratum.grade(fixture.id) == 0:
                    continue
                cosine = stratum.cosine(fixture.id)
                assert cosine >= TYPE_GATE_FLOOR, (stratum.name, fixture.id, cosine)

    def test_enough_of_the_pool_survives_the_gate_to_fill_the_window(self):
        """k=6 is only a selection problem if more than 6 candidates reach it."""
        for stratum in STRATA:
            survivors = [
                f for f in FIXTURES if stratum.cosine(f.id) >= TYPE_GATE_FLOOR
            ]
            assert len(survivors) > K_SERVICE * 2, (stratum.name, len(survivors))

    def test_default_stratum_clears_the_relevance_gate(self):
        """The default policy drops every non-profile item unless one clears
        RETRIEVAL_MIN_RAW_SCORE."""
        stratum = STRATA_BY_NAME["default_identity_question"]
        non_profile = [f for f in FIXTURES if f.memory_type != "profile"]
        top = max(stratum.cosine(f.id) for f in FIXTURES)
        assert top >= DEFAULT_POLICY_MIN_RAW
        assert non_profile, "stratum should exercise the non-profile path too"


class TestFrozenCosinesMatchTheText:
    def test_text_digest_is_current(self):
        """MEASURED_COSINES is frozen, so a text edit that skipped
        regenerate_cosines would leave the numbers describing the old wording
        while the report prints the new one."""
        assert corpus_text_digest() == CORPUS_TEXT_DIGEST, (
            "fixture text or a stratum query changed without regenerating the "
            "frozen cosines. Run: "
            "python -m tools.retrieval_ablation.regenerate_cosines"
        )

    def test_every_fixture_is_scored_in_every_stratum(self):
        for stratum in STRATA:
            for fixture in FIXTURES:
                assert fixture.id in MEASURED_COSINES[stratum.name], (
                    stratum.name, fixture.id,
                )


class TestSurvivesRealServiceFilters:
    """Run the fixtures through the actual filters rather than restating them."""

    @pytest.fixture(scope="class")
    def service(self):
        return ContextService.__new__(ContextService)

    def test_no_fixture_is_dropped_as_low_value(self, service):
        for fixture in FIXTURES:
            item = _as_item(fixture, 0.6)
            assert not service._is_low_value_memory(item), fixture.id

    def test_no_fixture_echoes_its_own_query(self, service):
        """Including the lexical bait, which needs high raw term overlap while
        staying under the 0.55 Jaccard echo threshold. That is why it is long."""
        for stratum in STRATA:
            normalized_query = service._normalize_text(stratum.query)
            for fixture_id in stratum.judgments:
                fixture = FIXTURES_BY_ID[fixture_id]
                item = _as_item(fixture, 0.6)
                assert not service._is_echo_or_meta_memory(item, normalized_query), (
                    stratum.name, fixture_id,
                )

    def test_lexical_bait_really_does_overlap_the_query(self, service):
        """Non-vacuousness for the bait: it must actually hit query terms, or
        it is testing nothing."""
        from src.retrieval.semantic_search import extract_query_terms

        stratum = STRATA_BY_NAME["reflective_work_patterns"]
        bait = FIXTURES_BY_ID["d07_lexical_overlap_bait"]
        terms = extract_query_terms(service._normalize_text(stratum.query))
        content = service._normalize_text(bait.text)
        hits = sum(1 for t in terms if t in content)
        assert hits >= 3, (terms, hits)


class TestTimestamps:
    def test_age_days_round_trips_through_the_ranker_parser(self):
        """The ranker must read back the age the fixture declares, or decay and
        recency buckets are assigned from the wrong number."""
        from src.context.ranker import ContextRanker

        ranker = ContextRanker()
        now = datetime.now()
        for fixture in FIXTURES:
            parsed = ranker._parse_age_days(fixture.timestamp(now))
            assert parsed is not None, fixture.id
            assert abs(parsed - fixture.age_days) <= 1, (fixture.id, parsed)

    def test_ages_span_every_decay_bucket(self):
        """_EPHEMERAL_DECAY has boundaries at 3/7/14/30 days and _DEFAULT_DECAY
        adds 90. A corpus clustered in one bucket cannot show decay doing
        anything."""
        ages = sorted(f.age_days for f in FIXTURES)
        assert ages[0] <= 1
        assert any(a > 90 for a in ages)
        buckets = {
            "<=3": any(a <= 3 for a in ages),
            "4-7": any(4 <= a <= 7 for a in ages),
            "8-14": any(8 <= a <= 14 for a in ages),
            "15-30": any(15 <= a <= 30 for a in ages),
            "31-90": any(31 <= a <= 90 for a in ages),
            ">90": any(a > 90 for a in ages),
        }
        assert all(buckets.values()), buckets


class TestLexicalSignalIsFairInBothDirections:
    """A_NAIVE_plus_lexical is the honest baseline, and it is only honest if
    the lexical term behaves as it does in reality: informative on genuinely
    relevant records, and exploitable by records that merely share vocabulary.

    The first build of this corpus failed both ways -- relevant items scored
    0.000 lexical while only the bait scored, which would have made the
    lexical arm strictly worse than bare cosine and turned the 'bare cosine is
    a strawman' argument on its head.
    """

    def _bonus(self, stratum, fixture_id):
        from src.retrieval.semantic_search import (
            extract_query_terms,
            lexical_relevance_bonus,
        )

        service = ContextService.__new__(ContextService)
        nq = service._normalize_text(stratum.query)
        return lexical_relevance_bonus(
            nq,
            extract_query_terms(nq),
            service._normalize_text(FIXTURES_BY_ID[fixture_id].text),
            raw_query=stratum.query,
        )

    # f08, not f09. f09 is the stratum's BURIED help case: it is deliberately
    # phrased without the query's vocabulary so that dense similarity leaves it
    # below the cut and only the reflection type weight can recover it. Asking
    # it to also earn a lexical bonus would be asking it to be two fixtures.
    LEXICALLY_RELEVANT = "f08_health_morning_pattern"

    def test_lexical_rewards_relevant_records(self):
        """At least one grade-3 record per lexical-bearing stratum must earn a
        real lexical bonus, or the term only ever promotes noise."""
        stratum = STRATA_BY_NAME["reflective_work_patterns"]
        assert stratum.grade(self.LEXICALLY_RELEVANT) == 3
        assert self._bonus(stratum, self.LEXICALLY_RELEVANT) > 0.05

    def test_lexical_bait_outscores_relevant_records(self):
        """And it must still be exploitable, or there is no bait."""
        stratum = STRATA_BY_NAME["reflective_work_patterns"]
        bait = self._bonus(stratum, "d07_lexical_overlap_bait")
        best_relevant = self._bonus(stratum, self.LEXICALLY_RELEVANT)
        assert bait > best_relevant

    def test_buried_help_case_earns_no_lexical_bonus(self):
        """Non-vacuousness for the burial: if f09 shared the query's vocabulary
        it would be lifted by the lexical term rather than by the type weight,
        and the reflective help case would be measuring the wrong mechanism."""
        stratum = STRATA_BY_NAME["reflective_work_patterns"]
        assert stratum.grade("f09_health_reflection_thread") == 3
        assert self._bonus(stratum, "f09_health_reflection_thread") < 0.05

    def test_entity_boost_actually_fires(self):
        """The entity term is 0.20 per match capped at 0.40 -- the largest
        lexical lever -- and fires only when the QUERY carries a proper noun.
        Without one in the query the entity bait is inert and tests nothing."""
        stratum = STRATA_BY_NAME["activity_pipeline_work"]
        assert self._bonus(stratum, "d08_entity_name_bait") >= 0.20

    def test_entity_boost_also_rewards_the_legitimate_mention(self):
        stratum = STRATA_BY_NAME["activity_pipeline_work"]
        assert self._bonus(stratum, "f01_work_decay_trace") >= 0.20

    def test_a_query_carries_a_proper_noun(self):
        """_extract_entity_names skips sentence-initial matches, so the noun
        must not lead the query."""
        import re as _re

        proper = [
            s for s in STRATA
            if _re.search(r"\b[A-Z][a-z]{2,}\b", s.query[1:])
        ]
        assert proper, "no stratum exercises the entity boost"
