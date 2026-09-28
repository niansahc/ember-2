"""
tests/test_composition_bound.py

ADR-044's bound, as a test rather than a comment.

The ADR is explicit that this is the requirement: "It is a test, not a
comment. One place computes the bound, one test asserts it, and the ratio to
the measured spread is stated in the code rather than implied." The failure it
names is already in the record -- the named-entity boost was sized against an
ASSUMED cosine variance of 0.3-0.5 and shipped with a cap of 0.40, which is
4.9x the measured production spread it was meant to nudge. The assumption was
wrong by a factor of four to six, and nothing failed when it was.

Two properties, and they are not the same claim.

THE BOUND is that `prior x tier` cannot leave [0.8722, 1.1278] for any
candidate. That is an upper limit on how far query-independent metadata may
move a record, expressed in cosine units so it can be compared against what
the embedder actually resolves.

REACHABILITY is what the bound buys. ADR-015's COLD_MULTIPLIER claimed that a
highly relevant cold record could outrank a weakly relevant hot one. That was
false as shipped -- an aged cold conversation record carried 0.3 x 0.10 = 0.03
against 1.0, and cosine is bounded by 1.0, so it held in 0 of 36 queries at
any cosine value. Under the bound it holds in 16 of 36: the queries whose
internal top-8 spread exceeds what tier now costs.

A bound test alone would pass on a contract that permits nothing, which is why
the second property is here. Fixtures are synthetic (CLAUDE.md Vault Privacy
Rule).
"""

from __future__ import annotations

import contextlib
import itertools

import pytest

from src.context import prior
from src.context.models import ContextItem
from src.context.policies import ContextPolicy
from src.context.ranker import COLD_MULTIPLIER, WARM_MULTIPLIER, ContextRanker

# Every branch prior.assemble can take, enumerated rather than sampled. The
# space is small enough to cover exhaustively, and a sampled bound test can
# miss exactly the corner where the clamp is load-bearing.
CONTENT_KINDS = [None, "experience", "user_content", "question", "answer", "unrecognised"]
CONTENT_LENGTHS = [0, 1, 49, 50, 51, 600, 1200, 1201, 5000]
RECENCY_BUCKETS = ["d7", "d30", "d90", "d365", "older", "unparsed", "unrecognised"]
REFLECTION_FLAGS = [False, True]

# The tier multipliers as apply_policy applies them, including the identity
# arms. profile_bypass and hot are both 1.0 and are listed separately because
# they are different branches that could diverge.
TIER_MULTIPLIERS = {
    "cold": COLD_MULTIPLIER,
    "warm": WARM_MULTIPLIER,
    "hot": 1.0,
    "profile_bypass": 1.0,
}


@contextlib.contextmanager
def _out_of_contract_kind(kind: str, value: float):
    """Give one content_kind a magnitude the derivation would never produce.

    Used to prove the clamp is the enforcement rather than a formality. The
    derived magnitudes happen to compose within the bound today; that is not
    what makes the contract hold, and a test that only exercised them would
    pass just as well with no clamp at all.
    """
    original = dict(prior._KIND_FACTORS)
    prior._KIND_FACTORS[kind] = value
    try:
        yield
    finally:
        prior._KIND_FACTORS.clear()
        prior._KIND_FACTORS.update(original)


def _priors():
    for kind, length, bucket, is_reflection in itertools.product(
        CONTENT_KINDS, CONTENT_LENGTHS, RECENCY_BUCKETS, REFLECTION_FLAGS
    ):
        yield (
            kind,
            length,
            bucket,
            is_reflection,
            prior.assemble(
                content_kind=kind,
                content_length=length,
                recency_bucket=bucket,
                is_reflection=is_reflection,
            ),
        )


class TestTheBoundItself:
    def test_the_interval_matches_the_figures_the_adr_states(self):
        """0.8722 and 1.1278, to four places.

        Pinned against the literals so that changing COSINE_SPREAD without
        re-deriving the ADR's stated interval fails here rather than silently
        moving the contract.
        """
        assert prior.COMPOSED_MIN == pytest.approx(0.8722, abs=5e-5)
        assert prior.COMPOSED_MAX == pytest.approx(1.1278, abs=5e-5)

    def test_the_interval_is_derived_from_the_measured_spread(self):
        """B = 0.0815 / 0.6375, not a number someone liked.

        This is the assertion that makes the bound auditable: it ties the
        interval to the two measured quantities (#236) rather than to a
        constant that could drift away from them.
        """
        assert prior.COSINE_SPREAD == 0.0815
        assert prior.COSINE_MEAN_RANK1 == 0.6375
        assert prior.RELATIVE_SPREAD == pytest.approx(0.0815 / 0.6375)
        assert prior.COMPOSED_MIN == pytest.approx(1.0 - prior.RELATIVE_SPREAD)
        assert prior.COMPOSED_MAX == pytest.approx(1.0 + prior.RELATIVE_SPREAD)

    def test_the_budget_is_split_evenly_in_the_multiplicative_sense(self):
        """sqrt(COMPOSED_MIN) to each factor.

        An even split means the WORST CASE OF BOTH TOGETHER lands exactly on
        the bound -- which is the only split under which the composed
        assertion and the per-factor floors are the same statement. An
        arithmetic split would leave the product below the floor.
        """
        assert prior.PRIOR_MIN == pytest.approx(prior.COMPOSED_MIN ** 0.5)
        assert prior.TIER_MIN == pytest.approx(prior.COMPOSED_MIN ** 0.5)
        assert prior.PRIOR_MIN * prior.TIER_MIN == pytest.approx(prior.COMPOSED_MIN)

    def test_tier_carries_no_upward_half(self):
        """The prior may promote; tier may only discount.

        Asymmetric on purpose: a record should not be promoted for being hot,
        only spared the cold discount. So tier's ceiling is 1.0 while the
        prior reaches COMPOSED_MAX, and the composed maximum is therefore the
        prior's maximum alone.
        """
        assert max(TIER_MULTIPLIERS.values()) == 1.0
        assert prior.PRIOR_MAX == prior.COMPOSED_MAX

    @pytest.mark.parametrize("tier_name", sorted(TIER_MULTIPLIERS))
    def test_prior_times_tier_stays_inside_the_bound(self, tier_name):
        """The contract, over every combination the pipeline can produce."""
        tier = TIER_MULTIPLIERS[tier_name]
        checked = 0
        for kind, length, bucket, is_reflection, value in _priors():
            composed = value * tier
            assert prior.COMPOSED_MIN - 1e-12 <= composed <= prior.COMPOSED_MAX + 1e-12, (
                f"prior x tier left the bound: kind={kind!r} length={length} "
                f"bucket={bucket!r} reflection={is_reflection} tier={tier_name} "
                f"-> {composed}"
            )
            checked += 1
        assert checked == (
            len(CONTENT_KINDS) * len(CONTENT_LENGTHS)
            * len(RECENCY_BUCKETS) * len(REFLECTION_FLAGS)
        )

    def test_the_bound_is_reached_and_not_merely_respected(self):
        """Non-vacuity. A prior pinned at 1.0 would satisfy every test above.

        The floor must actually be attainable, or the contract is decorative
        and the reachability property below is unreachable for a different
        reason than the one it reports.
        """
        values = [v for *_rest, v in _priors()]
        assert min(values) == pytest.approx(prior.PRIOR_MIN), (
            "no combination reaches the prior's floor; the clamp is inert"
        )
        assert max(values) > 1.0, "no combination promotes a record"

    def test_a_term_cannot_escape_the_clamp(self):
        """The clamp is the enforcement, not the derivation.

        Derived magnitudes happen to compose within the bound today. That is
        not what makes the contract hold -- a future term, or a retune of an
        existing one, could push the product out. This asserts the clamp
        catches it, by handing assemble a deliberately out-of-contract term.
        """
        with _out_of_contract_kind("experience", 5.0):
            escaped = prior.assemble(
                content_kind="experience", content_length=600, recency_bucket="d7"
            )
        assert escaped == pytest.approx(prior.PRIOR_MAX)

    def test_the_clamp_catches_the_floor_side_too(self):
        with _out_of_contract_kind("experience", 0.01):
            escaped = prior.assemble(
                content_kind="experience", content_length=600, recency_bucket="d7"
            )
        assert escaped == pytest.approx(prior.PRIOR_MIN)


class TestReachability:
    """A rank-1 cold record outranking a rank-8 hot record.

    The property ADR-015's COLD_MULTIPLIER rationale claimed and did not
    deliver. It is arithmetic rather than empirical: the cold record finishes
    at c1 x COMPOSED_MIN in the worst case, the hot one at c8 x 1.0, so it
    holds exactly when

        (c1 - c8) / c1 > RELATIVE_SPREAD        (0.12784)

    equivalently when the cold record's cosine ADVANTAGE, c1/c8 - 1, exceeds
    1/COMPOSED_MIN - 1 = 14.7%.

    Baseline: 0 of 36. Under 0.3 x 0.10 = 0.03 an aged cold conversation
    record could not outrank a fresh hot one at ANY cosine value, because
    cosine is bounded by 1.0. Absorbing the temporal decay lifted the floor
    from 0.03 to 0.30 and left it at 0 of 36. Under the bound: 16 of 36.
    """

    def setup_method(self):
        self.ranker = ContextRanker()
        self.policy = ContextPolicy(name="test", memory_weight=1.0)

    def _finish(self, cosine: float, tier: str, content: str, kind: str | None) -> float:
        item = ContextItem(
            id=f"{tier}-{cosine}",
            content=content,
            source="conversation",
            item_type="conversation",
            memory_type="conversation",
            score=cosine,
            tier=tier,
            timestamp=None,
            metadata={"content_kind": kind} if kind else {},
        )
        self.ranker.apply_policy([item], self.policy)
        ranked, _ = self.ranker.rank([item], [])
        return float(ranked[0].score)

    def test_the_required_advantage_is_the_measured_relative_spread(self):
        required = 1.0 / prior.COMPOSED_MIN - 1.0
        assert required == pytest.approx(0.1466, abs=5e-5)
        # The same condition stated against rank-1 rather than as a ratio.
        assert 1.0 - prior.COMPOSED_MIN == pytest.approx(prior.RELATIVE_SPREAD)

    def test_a_rank_1_cold_record_outranks_a_rank_8_hot_record(self):
        """The demonstration, at the worst case for the cold record.

        Cold takes the prior's floor (short content, stale) and hot takes the
        neutral prior, so the cold record is carrying the entire composed
        discount. A spread above the threshold still leaves it on top.
        """
        short = "a short record"                       # < 50 chars: LEN_UNDER_50
        neutral = "a record body of a perfectly ordinary and unremarkable length"

        c1, c8 = 0.6375, 0.5200                        # relative spread 0.1843
        assert (c1 - c8) / c1 > prior.RELATIVE_SPREAD

        cold = self._finish(c1, "cold", short, "question")
        hot = self._finish(c8, "hot", neutral, None)

        assert cold > hot, (
            f"a rank-1 cold record at cosine {c1} lost to a rank-8 hot record "
            f"at {c8}, on a spread that clears the threshold: {cold} vs {hot}"
        )

    def test_below_the_threshold_the_cold_record_loses(self):
        """The negative case, so the test pins the THRESHOLD, not an outcome.

        Without this, the test above would pass for a contract that let cold
        records win everywhere, which is the opposite failure and equally bad.
        """
        short = "a short record"
        neutral = "a record body of a perfectly ordinary and unremarkable length"

        c1, c8 = 0.6375, 0.6000                        # relative spread 0.0588
        assert (c1 - c8) / c1 < prior.RELATIVE_SPREAD

        cold = self._finish(c1, "cold", short, "question")
        hot = self._finish(c8, "hot", neutral, None)

        assert cold < hot, (
            "a cold record won on a spread below the threshold; tier is not "
            "costing what the contract says it costs"
        )

    def test_the_old_multipliers_could_not_deliver_this_at_any_cosine(self):
        """The 0/36 baseline, as arithmetic rather than as a citation.

        0.3 (tier) x 0.10 (the ephemeral decay floor) = 0.03. Cosine is
        bounded by 1.0, so the best possible aged cold record finished at 0.03
        against a fresh hot record's cosine -- unreachable for every corpus,
        not merely for this one.
        """
        old_cold, old_decay_floor = 0.3, 0.10
        best_possible_cold = 1.0 * old_cold * old_decay_floor
        assert best_possible_cold == pytest.approx(0.03)

        # Any hot record above this cosine was unbeatable by any cold record.
        assert best_possible_cold < 0.0815, (
            "the old floor was not below the measured cosine spread; the "
            "unreachability argument does not hold as stated"
        )

        # And under the new contract the same comparison is winnable.
        assert 1.0 * prior.COMPOSED_MIN > 0.0815
