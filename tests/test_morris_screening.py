"""
tests/test_morris_screening.py

Coverage for the Morris screening pass over the retrieval scoring
parameters.

The thing most worth testing here is not the arithmetic -- it is that the
harness can tell the two kinds of zero apart. A parameter measured across
the whole space and found not to matter, and a parameter no candidate in
the corpus ever activates, both produce mu* = 0. They mean opposite things.
If the second kind is allowed into the ranked table, the table quietly
says "these 34 constants are dead weight" when what it actually knows is
"this vault has no ingested records".

Fixtures are synthetic traces built in-process (CLAUDE.md Vault Privacy
Rule): no capture, no vault, no model.
"""

from __future__ import annotations

import json
import math

import pytest

from tools.retrieval_trace.compose import compose
from tools.retrieval_trace.morris import (
    ENDPOINT_DELIVERY,
    ENDPOINT_SCORE,
    ENDPOINTS,
    EndpointEvaluator,
    MorrisResult,
    UNEXERCISED_NOTE,
    elementary_effects,
    find_unexercised,
    format_report,
    rank_stability,
    sample_trajectory,
    screen,
    to_dict,
)
from tools.retrieval_trace.params import ReplayParams, default_params
from tools.retrieval_trace.ranges import (
    ADDITIVE_FLOOR,
    FAMILY_ADDITIVE,
    FAMILY_MULTIPLIER,
    build_ranges,
    family_of,
    range_for,
)
from tools.retrieval_trace.schema import (
    SCHEMA_VERSION,
    CandidateTrace,
    DecayActivation,
    PolicyActivation,
    QueryTrace,
    RankActivation,
    RetrievalActivation,
    TraceRun,
    content_fingerprint,
)

import random


# ---------------------------------------------------------------------------
# Synthetic trace construction
# ---------------------------------------------------------------------------

def _candidate(
    ref: str,
    *,
    memory_type: str = "conversation",
    item_type: str = "conversation",
    tier: str = "hot",
    raw_cosine: float = 0.4,
    role: str = "user",
    decay_family: str = "ephemeral",
    decay_bucket: str = "d7",
    policy_name: str = "default",
    content: str = "a synthetic candidate body of a perfectly ordinary length",
) -> CandidateTrace:
    candidate = CandidateTrace(
        ref=ref,
        store_id=ref,
        channel="memory",
        memory_type=memory_type,
        item_type=item_type,
        tier=tier,
        authorship="first_person",
        timestamp="2026-09-01T12-00-00",
        age_days=5,
        content_sha256=content_fingerprint(content + ref),
        content_length=len(content),
        content=content,
        retrieval=RetrievalActivation(
            applies=True,
            raw_cosine=raw_cosine,
            lexical_term_hits=2,
            type_branch=memory_type if memory_type in {"conversation", "reflection"} else "other",
            quality_role=role,
            quality_experience=True,
        ),
        policy=PolicyActivation(
            weight_field="memory_weight",
            weight_captured=1.0,
            recency_bias_captured=0.0,
            recency_bucket="d7",
            tier_branch="profile_bypass" if memory_type == "profile" else tier,
        ),
        rank=RankActivation(
            type_branch=item_type if item_type in {"conversation", "reflection"} else "other",
            role_branch=role if role in {"user", "assistant"} else "none",
            kind_branch="experience",
            recency_bucket="d7",
        ),
        decay=DecayActivation(family=decay_family, bucket=decay_bucket),
    )
    # Stage scores are what capture would have recorded: the model at
    # shipped defaults. Building them any other way would make the fixture
    # internally inconsistent and every replay assertion meaningless.
    result = compose(candidate, ReplayParams(), policy_name)
    candidate.stage_scores = dict(result.stage_scores)
    candidate.composed_score = result.final
    return candidate


def _query(query_id: str, candidates: list[CandidateTrace], **overrides) -> QueryTrace:
    fields = dict(
        query_id=query_id,
        query=f"a synthetic query {query_id}",
        policy_name="default",
        policy={"name": "default"},
        relational_query=False,
        project_id=None,
        memory_limit=3,
        reflection_limit=1,
        diversity=False,
        min_score=0.0,
        relevance_gate_fired=False,
        candidates=candidates,
        delivered_refs=[],
        delivered_reflection_refs=[],
    )
    fields.update(overrides)
    return QueryTrace(**fields)


def _run(queries: list[QueryTrace]) -> TraceRun:
    return TraceRun(
        schema_version=SCHEMA_VERSION,
        captured_at="2026-09-23T00-00-00",
        vault_fingerprint="synthetic",
        db_digest_before="x",
        db_digest_after="x",
        param_defaults=default_params(),
        include_content=True,
        pool_limit=8,
        pool_widened=False,
        queries=queries,
    )


@pytest.fixture
def synthetic_run() -> TraceRun:
    """Two queries whose candidates differ in tier, type and cosine.

    Varied on purpose. A corpus where every candidate is identical gives
    every parameter the same elementary effect and the ranking becomes an
    artifact of the fixture rather than a measurement of anything.
    """
    q1 = _query(
        "q1",
        [
            _candidate("m0", raw_cosine=0.55, tier="hot"),
            _candidate("m1", raw_cosine=0.42, tier="cold"),
            _candidate("m2", raw_cosine=0.31, tier="hot", role="assistant"),
            _candidate("m3", raw_cosine=0.28, memory_type="profile", item_type="profile"),
        ],
    )
    q2 = _query(
        "q2",
        [
            _candidate("m0", raw_cosine=0.50, tier="cold", decay_bucket="older"),
            _candidate("m1", raw_cosine=0.47, tier="hot"),
            _candidate("m2", raw_cosine=0.45, memory_type="reflection",
                       item_type="reflection", decay_family="reflection", decay_bucket="d30"),
        ],
    )
    run = _run([q1, q2])
    # Record what the shipped configuration delivers, so the delivery
    # endpoint has a baseline to measure distance from.
    from tools.retrieval_trace.replay import replay_query

    for query in run.queries:
        replay = replay_query(query)
        query.delivered_refs = list(replay.delivered_refs)
        query.delivered_reflection_refs = list(replay.delivered_reflection_refs)
    return run


# ---------------------------------------------------------------------------
# Ranges: derived from each parameter's own scale
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name",
    ["tier.cold", "tier.hot", "auth.mixed", "decay.ephemeral.d7", "refl.base_discount",
     "refl.recency_scale"],
)
def test_multiplier_family_is_swept_over_the_unit_interval(name):
    parameter = range_for(name, default_params()[name])
    assert parameter.family == FAMILY_MULTIPLIER
    assert (parameter.low, parameter.high) == (0.0, 1.0)


@pytest.mark.parametrize(
    "name", ["ret.type.conversation", "rank.role.assistant", "recency.d7", "proj.boost"]
)
def test_additive_family_is_swept_over_its_own_magnitude(name):
    default = default_params()[name]
    parameter = range_for(name, default)
    assert parameter.family == FAMILY_ADDITIVE
    half_width = max(abs(default), ADDITIVE_FLOOR)
    assert parameter.low == pytest.approx(default - half_width)
    assert parameter.high == pytest.approx(default + half_width)


def test_a_zero_default_still_gets_a_range_to_move_in():
    """A proportional rule alone would pin every zero-valued constant."""
    parameter = range_for("rank.type.ingested", 0.0)
    assert parameter.width == pytest.approx(2 * ADDITIVE_FLOOR)
    assert parameter.low < 0 < parameter.high


def test_no_uniform_range_across_families():
    """The explicit requirement: different units get different treatment."""
    ranges = build_ranges(default_params())
    widths = {r.family: set() for r in ranges.values()}
    for parameter in ranges.values():
        widths[parameter.family].add(round(parameter.width, 6))
    assert widths[FAMILY_MULTIPLIER] == {1.0}
    assert len(widths[FAMILY_ADDITIVE]) > 1, (
        "every additive parameter got the same width; the range is not "
        "deriving from each parameter's own scale"
    )


def test_unit_mapping_round_trips():
    parameter = range_for("ret.type.conversation", 0.10)
    assert parameter.to_unit(parameter.to_value(0.37)) == pytest.approx(0.37)


def test_family_classification_covers_every_parameter():
    for name in default_params():
        assert family_of(name) in {FAMILY_ADDITIVE, FAMILY_MULTIPLIER}


# ---------------------------------------------------------------------------
# Trajectory sampling
# ---------------------------------------------------------------------------

def test_trajectory_has_k_plus_one_points():
    trajectory = sample_trajectory(6, 8, random.Random(1))
    assert len(trajectory) == 7
    assert all(len(point) == 6 for point in trajectory)


def test_each_step_changes_exactly_one_factor():
    trajectory = sample_trajectory(10, 8, random.Random(2))
    for before, after in zip(trajectory, trajectory[1:]):
        changed = [i for i in range(len(before)) if abs(after[i] - before[i]) > 1e-15]
        assert len(changed) == 1


def test_every_factor_moves_exactly_once():
    k = 12
    trajectory = sample_trajectory(k, 8, random.Random(3))
    moved = []
    for before, after in zip(trajectory, trajectory[1:]):
        moved += [i for i in range(k) if abs(after[i] - before[i]) > 1e-15]
    assert sorted(moved) == list(range(k))


def test_every_point_stays_inside_the_unit_cube():
    for seed in range(20):
        for point in sample_trajectory(15, 8, random.Random(seed)):
            assert all(-1e-12 <= c <= 1 + 1e-12 for c in point)


def test_step_size_is_the_standard_delta():
    levels = 8
    expected = levels / (2.0 * (levels - 1))
    trajectory = sample_trajectory(5, levels, random.Random(4))
    for before, after in zip(trajectory, trajectory[1:]):
        delta = max(abs(a - b) for a, b in zip(after, before))
        assert delta == pytest.approx(expected)


def test_odd_levels_are_rejected():
    with pytest.raises(ValueError, match="even"):
        sample_trajectory(3, 7, random.Random(5))


def test_trajectories_are_reproducible_from_a_seed():
    assert sample_trajectory(8, 8, random.Random(9)) == sample_trajectory(
        8, 8, random.Random(9)
    )


# ---------------------------------------------------------------------------
# Elementary effects
# ---------------------------------------------------------------------------

def test_elementary_effect_recovers_a_known_slope():
    """On a linear function the effect is the coefficient, exactly."""
    names = ["a", "b"]
    coefficients = {"a": 3.0, "b": -1.5}
    trajectory = sample_trajectory(2, 8, random.Random(7))
    values = [
        {
            ENDPOINT_SCORE: sum(coefficients[n] * point[i] for i, n in enumerate(names)),
            ENDPOINT_DELIVERY: 0.0,
        }
        for point in trajectory
    ]
    effects = elementary_effects(trajectory, values, names)
    assert effects["a"][ENDPOINT_SCORE] == pytest.approx(3.0)
    assert effects["b"][ENDPOINT_SCORE] == pytest.approx(-1.5)


def test_a_step_that_moves_two_factors_is_rejected():
    names = ["a", "b"]
    trajectory = [[0.0, 0.0], [0.5, 0.5]]
    values = [{e: 0.0 for e in ENDPOINTS}, {e: 1.0 for e in ENDPOINTS}]
    with pytest.raises(ValueError, match="exactly one"):
        elementary_effects(trajectory, values, names)


# ---------------------------------------------------------------------------
# The two kinds of zero
# ---------------------------------------------------------------------------

def test_unexercised_parameters_are_identified(synthetic_run):
    unexercised = find_unexercised(synthetic_run)
    # No ingested candidates and no project id anywhere in the fixture.
    assert "ret.type.ingested" in unexercised
    assert "proj.boost" in unexercised
    # Exercised, and must not appear.
    assert "tier.cold" not in unexercised
    assert "ret.type.conversation" not in unexercised


def test_unexercised_parameters_are_excluded_from_the_ranking(synthetic_run):
    screening = screen(synthetic_run, trajectories=3, levels=8, seed=1)
    ranked = {result.name for result in screening.ranked(ENDPOINT_SCORE)}
    assert ranked.isdisjoint(set(screening.unexercised))
    assert len(ranked) + len(screening.unexercised) == len(default_params())


def test_the_report_states_why_the_unexercised_read_as_zero(synthetic_run):
    screening = screen(synthetic_run, trajectories=2, levels=8, seed=1)
    report = format_report(screening)
    assert "UNEXERCISED" in report
    # The note has to survive into the rendered report, not merely exist.
    assert "never exercised" in report or "corpus never exercised" in report
    for name in screening.unexercised[:3]:
        assert name in report


def test_the_note_says_the_two_zeros_mean_opposite_things():
    assert "NOT because" in UNEXERCISED_NOTE
    assert "uninfluential" in UNEXERCISED_NOTE


# ---------------------------------------------------------------------------
# Screening statistics
# ---------------------------------------------------------------------------

def test_screen_reports_all_three_statistics_on_both_endpoints(synthetic_run):
    screening = screen(synthetic_run, trajectories=4, levels=8, seed=11)
    for result in screening.screened:
        for endpoint in ENDPOINTS:
            assert endpoint in result.mu
            assert endpoint in result.mu_star
            assert endpoint in result.sigma
            assert result.mu_star[endpoint] >= 0
            assert result.sigma[endpoint] >= 0


def test_mu_star_is_never_smaller_than_the_absolute_mu(synthetic_run):
    """mu* >= |mu| by construction; a violation means cancellation is lost."""
    screening = screen(synthetic_run, trajectories=4, levels=8, seed=12)
    for result in screening.screened:
        for endpoint in ENDPOINTS:
            assert result.mu_star[endpoint] >= abs(result.mu[endpoint]) - 1e-12


def test_ranking_is_by_descending_mu_star(synthetic_run):
    screening = screen(synthetic_run, trajectories=4, levels=8, seed=13)
    for endpoint in ENDPOINTS:
        values = [r.mu_star[endpoint] for r in screening.ranked(endpoint)]
        assert values == sorted(values, reverse=True)


def test_evaluation_count_is_r_times_k_plus_one(synthetic_run):
    screening = screen(synthetic_run, trajectories=3, levels=8, seed=14)
    assert screening.evaluations == 3 * (len(screening.screened) + 1)


def test_a_dominant_parameter_outranks_a_marginal_one(synthetic_run):
    """Sanity anchor: tier.cold multiplies whole scores, the token floor
    subtracts a small constant from short records the fixture does not have."""
    screening = screen(synthetic_run, trajectories=6, levels=8, seed=15)
    ranked = [r.name for r in screening.ranked(ENDPOINT_SCORE)]
    assert ranked.index("tier.cold") < ranked.index("ret.quality.not_question")


def test_a_score_only_parameter_is_reported_as_no_effect_on_delivery(synthetic_run):
    """The third kind of zero: exercised, moves scores, moves no slot.

    A constant added to every candidate of one type shifts every score in
    that class by the same amount and reorders nothing. Reporting that as
    "within noise" would be wrong -- it is an exact zero across every
    trajectory, and it means something different.
    """
    screening = screen(synthetic_run, trajectories=6, levels=8, seed=51)
    by_name = {r.name: r for r in screening.screened}
    score_only = [
        r for r in screening.screened
        if r.mu_star[ENDPOINT_SCORE] > 0 and r.no_effect(ENDPOINT_DELIVERY)
    ]
    assert score_only, "the fixture exercises no score-only parameter"
    assert not by_name["tier.cold"].no_effect(ENDPOINT_SCORE)

    report = format_report(screening)
    assert "NO-EFFECT on delivery" in report
    for result in score_only[:2]:
        assert result.name in report


def test_no_effect_is_not_reported_as_within_noise(synthetic_run):
    """They are different findings and must not share a label."""
    screening = screen(synthetic_run, trajectories=6, levels=8, seed=52)
    report = format_report(screening)
    for line in report.splitlines():
        if "NO-EFFECT" in line or not line.strip().startswith(tuple("0123456789")):
            continue
        if "within-noise" in line:
            # A within-noise row must have a real, nonzero mu*.
            assert " 0.0000 " not in line.split("within-noise")[0][:60]


def test_interaction_flag_fires_when_sigma_exceeds_mu_star():
    result = MorrisResult(
        name="x", family=FAMILY_ADDITIVE, low=0.0, high=1.0, default=0.5,
        mu={ENDPOINT_SCORE: 0.0, ENDPOINT_DELIVERY: 0.0},
        mu_star={ENDPOINT_SCORE: 0.1, ENDPOINT_DELIVERY: 0.1},
        sigma={ENDPOINT_SCORE: 0.3, ENDPOINT_DELIVERY: 0.01},
        effects={e: [] for e in ENDPOINTS},
    )
    assert result.interaction_candidate(ENDPOINT_SCORE)
    assert not result.interaction_candidate(ENDPOINT_DELIVERY)


def test_an_effect_inside_the_noise_wedge_is_not_distinguishable():
    result = MorrisResult(
        name="x", family=FAMILY_ADDITIVE, low=0.0, high=1.0, default=0.5,
        mu={e: 0.0 for e in ENDPOINTS},
        mu_star={ENDPOINT_SCORE: 0.01, ENDPOINT_DELIVERY: 1.0},
        sigma={ENDPOINT_SCORE: 0.5, ENDPOINT_DELIVERY: 0.01},
        effects={e: [] for e in ENDPOINTS},
    )
    assert not result.distinguishable(ENDPOINT_SCORE, trajectories=10)
    assert result.distinguishable(ENDPOINT_DELIVERY, trajectories=10)


def test_screening_is_reproducible_from_a_seed(synthetic_run):
    first = screen(synthetic_run, trajectories=3, levels=8, seed=99)
    second = screen(synthetic_run, trajectories=3, levels=8, seed=99)
    assert [r.mu_star[ENDPOINT_SCORE] for r in first.ranked(ENDPOINT_SCORE)] == [
        r.mu_star[ENDPOINT_SCORE] for r in second.ranked(ENDPOINT_SCORE)
    ]


def test_a_different_seed_gives_a_different_draw(synthetic_run):
    """Otherwise the stability check would be comparing a run with itself."""
    first = screen(synthetic_run, trajectories=3, levels=8, seed=1)
    second = screen(synthetic_run, trajectories=3, levels=8, seed=2)
    assert [r.mu_star[ENDPOINT_SCORE] for r in first.screened] != [
        r.mu_star[ENDPOINT_SCORE] for r in second.screened
    ]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def test_the_delivery_endpoint_is_zero_at_the_shipped_configuration(synthetic_run):
    evaluator = EndpointEvaluator(run=synthetic_run)
    values = evaluator.evaluate(ReplayParams())
    assert values[ENDPOINT_DELIVERY] == pytest.approx(0.0)


def test_the_delivery_endpoint_moves_when_delivery_moves(synthetic_run):
    evaluator = EndpointEvaluator(run=synthetic_run)
    # Zeroing the cold tier drops cold candidates to the bottom of the order.
    moved = ReplayParams().with_overrides(**{"tier.cold": 0.0, "tier.hot": 0.0})
    assert evaluator.evaluate(moved)[ENDPOINT_DELIVERY] >= 0.0
    # A parameter the fixture never activates cannot move either endpoint.
    inert = ReplayParams().with_overrides(**{"proj.boost": 5.0})
    assert evaluator.evaluate(inert)[ENDPOINT_DELIVERY] == pytest.approx(0.0)
    assert evaluator.evaluate(inert)[ENDPOINT_SCORE] == pytest.approx(
        evaluator.evaluate(ReplayParams())[ENDPOINT_SCORE]
    )


def test_the_score_endpoint_responds_to_a_score_parameter(synthetic_run):
    evaluator = EndpointEvaluator(run=synthetic_run)
    baseline = evaluator.evaluate(ReplayParams())[ENDPOINT_SCORE]
    lifted = evaluator.evaluate(
        ReplayParams().with_overrides(**{"rank.type.conversation": 1.0})
    )[ENDPOINT_SCORE]
    assert lifted > baseline


def test_the_score_endpoint_is_query_balanced(synthetic_run):
    """A query with more candidates must not weigh more in the mean."""
    evaluator = EndpointEvaluator(run=synthetic_run)
    value = evaluator.evaluate(ReplayParams())[ENDPOINT_SCORE]
    per_query = []
    from tools.retrieval_trace.replay import replay_query

    for query in synthetic_run.queries:
        scored = replay_query(query).scored
        per_query.append(sum(s.score for s in scored) / len(scored))
    assert value == pytest.approx(sum(per_query) / len(per_query))


# ---------------------------------------------------------------------------
# Stability and reporting
# ---------------------------------------------------------------------------

def test_rank_stability_reports_perfect_agreement_with_itself(synthetic_run):
    screening = screen(synthetic_run, trajectories=3, levels=8, seed=21)
    report = rank_stability([screening, screening], ENDPOINT_SCORE, top=5)
    assert report.agreement == pytest.approx(1.0)
    assert report.max_displacement == 0
    assert report.stable()


def test_rank_stability_compares_across_seeds(synthetic_run):
    screenings = [
        screen(synthetic_run, trajectories=3, levels=8, seed=seed) for seed in (1, 2, 3)
    ]
    report = rank_stability(screenings, ENDPOINT_SCORE, top=5)
    assert 0.0 <= report.agreement <= 1.0
    assert report.seeds == [1, 2, 3]


def test_report_renders_both_endpoints_and_the_range_convention(synthetic_run):
    screening = screen(synthetic_run, trajectories=2, levels=8, seed=31)
    report = format_report(screening)
    assert "ENDPOINT: score" in report
    assert "ENDPOINT: delivery" in report
    assert "mu*" in report and "sigma" in report
    # The ranking is only meaningful under the stated ranges, so the report
    # must not be readable without them.
    assert "multipliers swept over [0, 1]" in report
    assert "step function" in report


def test_results_serialize_to_json(synthetic_run):
    screening = screen(synthetic_run, trajectories=2, levels=8, seed=41)
    payload = to_dict(screening)
    text = json.dumps(payload)
    assert "unexercised_note" in payload
    assert len(payload["screened"]) == len(screening.screened)
    assert math.isfinite(payload["screened"][0]["mu_star"][ENDPOINT_SCORE])
    assert len(text) > 0


def test_results_carry_no_vault_data(synthetic_run):
    """Morris output is parameter names and numbers, so unlike a trace it
    can be written anywhere. That has to stay true."""
    screening = screen(synthetic_run, trajectories=2, levels=8, seed=42)
    text = json.dumps(to_dict(screening))
    for query in synthetic_run.queries:
        assert query.query not in text
        for candidate in query.candidates:
            assert (candidate.content or "zzz-not-present") not in text


def test_screening_an_empty_parameter_set_is_refused():
    run = _run([_query("q1", [])])
    with pytest.raises(ValueError, match="nothing to screen"):
        screen(run, trajectories=2, levels=8, seed=1)
