"""
tests/test_sobol_indices.py

Coverage for the Sobol variance decomposition.

The estimators are validated against functions whose indices are known in
closed form -- Ishigami, a pure additive function, and a pure product --
rather than against the retrieval model, whose indices are the thing being
measured and cannot serve as their own reference. If the transcription of
Saltelli 2010 or Jansen 1999 is wrong, Ishigami says so immediately; the
retrieval model would just return confident numbers nobody could check.

Everything else here is about the reporting discipline: that an index which
could not be measured never appears in a ranked table beside one that was,
that intervals are reported rather than point estimates alone, and that the
convergence loop stops on a criterion rather than at a number chosen in
advance.

Fixtures are synthetic and in-process (CLAUDE.md Vault Privacy Rule).
"""

from __future__ import annotations

import json
import math

from unittest.mock import patch

import numpy as np
import pytest

from tools.retrieval_trace.compose import compose
from tools.retrieval_trace.endpoints import (
    ENDPOINT_DELIVERY,
    ENDPOINT_SCORE,
    ENDPOINTS,
)
from tools.retrieval_trace.params import ReplayParams, default_params
from tools.retrieval_trace.sobol import (
    BOOTSTRAP_RESAMPLES,
    CONFIDENCE,
    UNEXERCISED_NOTE,
    Interval,
    PairIndex,
    ParameterIndices,
    SampleStream,
    SobolAnalysis,
    analyse,
    check_pair,
    first_order,
    format_report,
    saltelli_matrices,
    second_order,
    to_dict,
    total_order,
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


# ---------------------------------------------------------------------------
# A harness for analytic test functions
# ---------------------------------------------------------------------------

# The analytic validations pin the plain-numpy sampler rather than taking
# whatever the machine offers. They used to take "auto", which made their
# accuracy a property of the environment: calibrated here against a
# scrambled Sobol' sequence, they failed in CI, where scipy is absent and
# the same N buys plain Monte Carlo precision. A test whose tolerance
# depends on an optional import is not testing the estimator.
#
# Tolerances below are therefore Monte Carlo tolerances, measured over six
# seeds at MC_SAMPLES and set with margin. They are loose -- +/-0.05 on an
# index of 0.31 -- and that is fine for what they are for: a transcription
# error in Saltelli 2010 or Jansen 1999 produces a grossly wrong number,
# not a 0.03 one.
TEST_SAMPLER = "random"
MC_SAMPLES = 16384


def _indices_for(function, k: int, n: int, seed: int = 7, low=0.0, high=1.0,
                 sampler: str = TEST_SAMPLER):
    """Run the real estimators over an analytic function."""
    stream = SampleStream(dimensions=2 * k, seed=seed, sampler=sampler)
    points = stream.take(n)
    design = saltelli_matrices(points, k)

    def evaluate(matrix):
        scaled = low + matrix * (high - low)
        return np.apply_along_axis(function, 1, scaled)

    fa = evaluate(design["A"])
    fb = evaluate(design["B"])
    fab = [evaluate(m) for m in design["AB"]]
    fba = [evaluate(m) for m in design["BA"]]
    variance = float(np.var(np.concatenate([fa, fb]), ddof=1))

    s1 = [first_order(fa, fb, fab[i], variance) for i in range(k)]
    st = [total_order(fa, fab[i], variance) for i in range(k)]
    s2 = {
        (i, j): second_order(fa, fb, fab[i], fab[j], fba[i], variance, s1[i], s1[j])
        for i in range(k)
        for j in range(i + 1, k)
    }
    return s1, st, s2


def _ishigami(x, a=7.0, b=0.1):
    return math.sin(x[0]) + a * math.sin(x[1]) ** 2 + b * x[2] ** 4 * math.sin(x[0])


# Analytic Sobol indices for Ishigami with a=7, b=0.1 over [-pi, pi]^3.
ISHIGAMI_S1 = (0.3139, 0.4424, 0.0)
ISHIGAMI_ST = (0.5576, 0.4424, 0.2437)


def test_ishigami_first_order_matches_the_analytic_values():
    s1, _st, _s2 = _indices_for(_ishigami, 3, MC_SAMPLES, low=-math.pi, high=math.pi)
    for index, expected in enumerate(ISHIGAMI_S1):
        assert s1[index] == pytest.approx(expected, abs=0.05)


def test_ishigami_total_order_matches_the_analytic_values():
    _s1, st, _s2 = _indices_for(_ishigami, 3, MC_SAMPLES, low=-math.pi, high=math.pi)
    for index, expected in enumerate(ISHIGAMI_ST):
        assert st[index] == pytest.approx(expected, abs=0.05)


def test_ishigami_recovers_the_x1_x3_interaction():
    """x3 has S1 = 0 and ST = 0.24: its entire influence is through x1.

    The single most useful property of the decomposition, and the one a
    first-order-only analysis would get exactly backwards.
    """
    s1, st, s2 = _indices_for(_ishigami, 3, MC_SAMPLES, low=-math.pi, high=math.pi)
    assert s1[2] == pytest.approx(0.0, abs=0.05)
    assert st[2] > 0.2
    assert s2[(0, 2)] == pytest.approx(ISHIGAMI_ST[0] - ISHIGAMI_S1[0], abs=0.07)
    # x2 interacts with nothing.
    assert s2[(0, 1)] == pytest.approx(0.0, abs=0.07)
    assert s2[(1, 2)] == pytest.approx(0.0, abs=0.07)


def test_a_purely_additive_function_has_st_equal_to_s1():
    def additive(x):
        return 3.0 * x[0] + 1.0 * x[1] + 0.5 * x[2]

    s1, st, s2 = _indices_for(additive, 3, MC_SAMPLES)
    for index in range(3):
        assert st[index] == pytest.approx(s1[index], abs=0.05)
    assert sum(s1) == pytest.approx(1.0, abs=0.06)
    for value in s2.values():
        assert value == pytest.approx(0.0, abs=0.08)


def test_a_pure_product_is_all_interaction():
    """f = (x1 - 0.5)(x2 - 0.5): both S1 are zero, S2 carries everything."""

    def product(x):
        return (x[0] - 0.5) * (x[1] - 0.5)

    s1, st, s2 = _indices_for(product, 2, MC_SAMPLES)
    assert s1[0] == pytest.approx(0.0, abs=0.03)
    assert s1[1] == pytest.approx(0.0, abs=0.03)
    assert st[0] == pytest.approx(1.0, abs=0.06)
    assert s2[(0, 1)] == pytest.approx(1.0, abs=0.06)


def test_first_order_indices_sum_to_at_most_one():
    s1, _st, _s2 = _indices_for(_ishigami, 3, MC_SAMPLES, low=-math.pi, high=math.pi)
    assert sum(s1) <= 1.0 + 0.05


def test_total_order_is_at_least_first_order():
    s1, st, _s2 = _indices_for(_ishigami, 3, MC_SAMPLES, low=-math.pi, high=math.pi)
    for index in range(3):
        assert st[index] >= s1[index] - 0.05


def test_zero_variance_output_does_not_divide_by_zero():
    assert first_order(np.ones(4), np.ones(4), np.ones(4), 0.0) == 0.0
    assert total_order(np.ones(4), np.ones(4), 0.0) == 0.0
    assert second_order(np.ones(4), np.ones(4), np.ones(4), np.ones(4), np.ones(4), 0.0, 0, 0) == 0.0


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

def test_sampler_is_selected_not_inferred():
    """The regression this file exists to prevent a second time.

    Accuracy used to depend on whether scipy happened to be installed, so
    tolerances calibrated on one machine failed on another. The sampler is
    now named by the caller and recorded on the stream.
    """
    assert SampleStream(4, 1, sampler="random").sampler == "numpy.default_rng"
    with pytest.raises(ValueError, match="unknown sampler"):
        SampleStream(4, 1, sampler="nonsense")


def test_requiring_sobol_without_scipy_raises_rather_than_downgrading():
    """A run whose numbers will be quoted must not silently get MC quality."""
    import builtins

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith("scipy"):
            raise ImportError("scipy blocked for this test")
        return real_import(name, *args, **kwargs)

    with patch.object(builtins, "__import__", blocked):
        with pytest.raises(RuntimeError, match="requires scipy"):
            SampleStream(4, 1, sampler="sobol")
        # auto downgrades instead, and says which sampler it ended up with.
        assert SampleStream(4, 1, sampler="auto").sampler == "numpy.default_rng"


def test_the_fallback_sampler_is_called_out_in_the_report(synthetic_run):
    sobol = analyse(
        synthetic_run, NAMES, UNEXERCISED,
        start_samples=16, max_samples=16, st_ci_target=1.0, sampler="random",
    )
    report = format_report(sobol)
    assert "numpy.default_rng" in report
    assert "plain Monte Carlo" in report


def test_sample_stream_is_extensible():
    """The convergence loop reuses evaluations, which requires the first N
    rows of the 2N sample to BE the N sample."""
    stream = SampleStream(dimensions=4, seed=3)
    first = stream.take(16).copy()
    second = stream.take(32)
    assert np.allclose(second[:16], first)


def test_sample_stream_is_reproducible_from_a_seed():
    a = SampleStream(dimensions=6, seed=5).take(32)
    b = SampleStream(dimensions=6, seed=5).take(32)
    assert np.allclose(a, b)


def test_sample_stream_stays_in_the_unit_cube():
    points = SampleStream(dimensions=8, seed=11).take(64)
    assert points.min() >= 0.0
    assert points.max() <= 1.0


def test_saltelli_matrices_differ_in_exactly_one_column():
    points = SampleStream(dimensions=6, seed=13).take(8)
    design = saltelli_matrices(points, 3)
    for i in range(3):
        differing = [
            column
            for column in range(3)
            if not np.allclose(design["AB"][i][:, column], design["A"][:, column])
        ]
        assert differing == [i]
        # BA_i is B with column i taken from A: the mirror of AB_i.
        assert np.allclose(design["BA"][i][:, i], design["A"][:, i])


# ---------------------------------------------------------------------------
# Synthetic trace
# ---------------------------------------------------------------------------

def _candidate(ref: str, *, memory_type="conversation", tier="hot", raw_cosine=0.4,
               role="user", decay_family="ephemeral", decay_bucket="d7",
               content="a synthetic candidate body of perfectly ordinary length"):
    candidate = CandidateTrace(
        ref=ref,
        store_id=ref,
        channel="memory",
        memory_type=memory_type,
        item_type=memory_type,
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
            type_branch=memory_type if memory_type in {"conversation", "reflection"} else "other",
            role_branch=role if role in {"user", "assistant"} else "none",
            kind_branch="experience",
            recency_bucket="d7",
        ),
        decay=DecayActivation(family=decay_family, bucket=decay_bucket),
    )
    result = compose(candidate, ReplayParams(), "default")
    candidate.stage_scores = dict(result.stage_scores)
    candidate.composed_score = result.final
    return candidate


def _query(query_id: str, candidates) -> QueryTrace:
    return QueryTrace(
        query_id=query_id,
        query=f"a synthetic query {query_id}",
        policy_name="default",
        policy={"name": "default"},
        relational_query=False,
        project_id=None,
        memory_limit=2,
        reflection_limit=1,
        diversity=False,
        min_score=0.0,
        relevance_gate_fired=False,
        candidates=candidates,
        delivered_refs=[],
        delivered_reflection_refs=[],
    )


@pytest.fixture
def synthetic_run() -> TraceRun:
    q1 = _query("q1", [
        _candidate("m0", raw_cosine=0.55, tier="hot"),
        _candidate("m1", raw_cosine=0.42, tier="cold"),
        _candidate("m2", raw_cosine=0.31, tier="hot", role="assistant"),
    ])
    q2 = _query("q2", [
        _candidate("m0", raw_cosine=0.50, tier="cold", decay_bucket="older"),
        _candidate("m1", raw_cosine=0.47, tier="hot"),
    ])
    run = TraceRun(
        schema_version=SCHEMA_VERSION,
        captured_at="2026-09-23T00-00-00",
        vault_fingerprint="synthetic",
        db_digest_before="x",
        db_digest_after="x",
        param_defaults=default_params(),
        include_content=True,
        pool_limit=8,
        pool_widened=False,
        queries=[q1, q2],
    )
    from tools.retrieval_trace.replay import replay_query

    for query in run.queries:
        replay = replay_query(query)
        query.delivered_refs = list(replay.delivered_refs)
        query.delivered_reflection_refs = list(replay.delivered_reflection_refs)
    return run


NAMES = ["tier.cold", "tier.hot", "decay.ephemeral.d7", "rank.role.user"]
UNEXERCISED = ["ret.type.ingested", "proj.boost"]


# ---------------------------------------------------------------------------
# The pass over a trace
# ---------------------------------------------------------------------------

def test_analysis_reports_all_three_index_families(synthetic_run):
    sobol = analyse(
        synthetic_run, NAMES, UNEXERCISED,
        start_samples=32, max_samples=32, st_ci_target=1.0,
    )
    for endpoint in ENDPOINTS:
        indices = sobol.endpoints[endpoint]
        assert len(indices.parameters) == len(NAMES)
        assert len(indices.pairs) == len(NAMES) * (len(NAMES) - 1) // 2
        for parameter in indices.parameters:
            assert isinstance(parameter.st_ci, Interval)
            assert parameter.st_ci.low <= parameter.st_ci.high


def test_evaluation_count_is_n_times_two_k_plus_two(synthetic_run):
    sobol = analyse(
        synthetic_run, NAMES, UNEXERCISED,
        start_samples=32, max_samples=32, st_ci_target=1.0,
    )
    assert sobol.evaluations == 32 * (2 * len(NAMES) + 2)


def test_doubling_reuses_every_earlier_evaluation(synthetic_run):
    """The convergence loop is only affordable because nothing is re-run."""
    analysis = SobolAnalysis(synthetic_run, NAMES, seed=1)
    analysis.indices(16)
    after_first = analysis.evaluations
    analysis.indices(32)
    after_second = analysis.evaluations
    assert after_first == 16 * (2 * len(NAMES) + 2)
    # Doubling N doubles the total; it does not triple it, which is what
    # re-evaluating the first half would cost.
    assert after_second == 32 * (2 * len(NAMES) + 2)


def test_ranked_by_total_order(synthetic_run):
    sobol = analyse(
        synthetic_run, NAMES, UNEXERCISED,
        start_samples=32, max_samples=32, st_ci_target=1.0,
    )
    for endpoint in ENDPOINTS:
        values = [p.st for p in sobol.endpoints[endpoint].ranked()]
        assert values == sorted(values, reverse=True)


def test_interaction_share_is_st_minus_s1():
    parameter = ParameterIndices(
        name="x", s1=0.2, st=0.5,
        s1_ci=Interval(0.1, 0.3), st_ci=Interval(0.4, 0.6),
    )
    assert parameter.interaction_share == pytest.approx(0.3)
    assert not parameter.additive()
    assert ParameterIndices(
        name="y", s1=0.4, st=0.404,
        s1_ci=Interval(0, 1), st_ci=Interval(0, 1),
    ).additive()


def test_interaction_share_is_not_clipped():
    """A negative share is information about the sample size, not an error."""
    parameter = ParameterIndices(
        name="x", s1=0.3, st=0.29,
        s1_ci=Interval(0, 1), st_ci=Interval(0, 1),
    )
    assert parameter.interaction_share < 0


# ---------------------------------------------------------------------------
# The two kinds of zero, again
# ---------------------------------------------------------------------------

def test_unexercised_parameters_are_carried_not_ranked(synthetic_run):
    sobol = analyse(
        synthetic_run, NAMES, UNEXERCISED,
        start_samples=16, max_samples=16, st_ci_target=1.0,
    )
    ranked = {p.name for p in sobol.endpoints[ENDPOINT_SCORE].ranked()}
    assert ranked.isdisjoint(set(UNEXERCISED))
    assert sobol.unexercised == UNEXERCISED
    report = format_report(sobol)
    assert "UNEXERCISED" in report
    for name in UNEXERCISED:
        assert name in report


def test_the_note_distinguishes_measured_zero_from_unmeasurable(synthetic_run):
    assert "NOT as" in UNEXERCISED_NOTE
    assert "opposite things" in UNEXERCISED_NOTE


# ---------------------------------------------------------------------------
# Convergence
# ---------------------------------------------------------------------------

def test_convergence_records_every_doubling(synthetic_run):
    sobol = analyse(
        synthetic_run, NAMES, UNEXERCISED,
        start_samples=16, max_samples=64, st_ci_target=0.0,
    )
    samples = [step["samples"] for step in sobol.convergence]
    assert samples == [16, 32, 64]
    for step in sobol.convergence:
        assert step["widest_st_ci_half_width_top_k"] >= 0


def test_convergence_stops_when_the_target_is_met(synthetic_run):
    """The stopping rule is the criterion, not a sample count."""
    sobol = analyse(
        synthetic_run, NAMES, UNEXERCISED,
        start_samples=16, max_samples=4096, st_ci_target=10.0,
    )
    # A target that is trivially satisfiable stops at the second step: the
    # first has no previous top-k to compare membership against.
    assert sobol.samples <= 32
    assert sobol.convergence[-1]["met_target"]


def test_required_samples_extrapolates_from_the_measured_curve():
    """A run that stops at its ceiling still has to answer "how many would
    it take" -- from the curve it measured, not from a guess."""
    from tools.retrieval_trace.sobol import required_samples

    # Textbook Monte Carlo: half-width halves every four-fold increase in N.
    convergence = [
        {"samples": 128, "widest_st_ci_half_width_top_k": 0.16},
        {"samples": 512, "widest_st_ci_half_width_top_k": 0.08},
        {"samples": 2048, "widest_st_ci_half_width_top_k": 0.04},
    ]
    needed, slope = required_samples(convergence, 0.02)
    assert slope == pytest.approx(-0.5, abs=0.05)
    assert needed == pytest.approx(8192, rel=0.05)


def test_required_samples_declines_to_extrapolate_from_too_little():
    from tools.retrieval_trace.sobol import required_samples

    assert required_samples([], 0.02) is None
    assert required_samples(
        [{"samples": 128, "widest_st_ci_half_width_top_k": 0.1}], 0.02
    ) is None
    # Widths that did not shrink cannot be extrapolated to a narrower one.
    assert required_samples(
        [
            {"samples": 128, "widest_st_ci_half_width_top_k": 0.1},
            {"samples": 256, "widest_st_ci_half_width_top_k": 0.2},
        ],
        0.02,
    ) is None


def test_a_run_that_misses_its_target_says_so(synthetic_run):
    sobol = analyse(
        synthetic_run, NAMES, UNEXERCISED,
        start_samples=16, max_samples=32, st_ci_target=1e-9,
    )
    report = format_report(sobol)
    assert "TARGET NOT MET" in report
    assert str(sobol.samples) in report


def test_a_shrinking_curve_gets_an_extrapolated_sample_size(synthetic_run):
    sobol = analyse(
        synthetic_run, NAMES, UNEXERCISED,
        start_samples=16, max_samples=16, st_ci_target=1e-9,
    )
    # Substitute a clean shrinking curve: the extrapolation is a property of
    # the curve, and a 4-parameter toy does not reliably produce a monotone
    # one at these sample sizes.
    sobol.convergence = [
        {"samples": 128, "evaluations": 1280, "widest_st_ci_half_width_top_k": 0.16,
         "top_k_membership_unchanged": False, "met_target": False},
        {"samples": 512, "evaluations": 5120, "widest_st_ci_half_width_top_k": 0.08,
         "top_k_membership_unchanged": True, "met_target": False},
        {"samples": 2048, "evaluations": 20480, "widest_st_ci_half_width_top_k": 0.04,
         "top_k_membership_unchanged": True, "met_target": False},
    ]
    sobol.st_ci_target = 0.02
    report = format_report(sobol)
    assert "extrapolated N" in report
    assert "model evaluations" in report


def test_no_false_extrapolation_from_a_flat_curve(synthetic_run):
    """Declining to extrapolate is the right answer when the curve has not
    started shrinking; inventing a number would be worse than silence."""
    sobol = analyse(
        synthetic_run, NAMES, UNEXERCISED,
        start_samples=16, max_samples=16, st_ci_target=1e-9,
    )
    sobol.convergence = [
        {"samples": 128, "evaluations": 1280, "widest_st_ci_half_width_top_k": 0.1,
         "top_k_membership_unchanged": False, "met_target": False},
        {"samples": 256, "evaluations": 2560, "widest_st_ci_half_width_top_k": 0.1,
         "top_k_membership_unchanged": True, "met_target": False},
    ]
    sobol.st_ci_target = 0.02
    report = format_report(sobol)
    assert "TARGET NOT MET" in report
    assert "extrapolated N" not in report


def test_intervals_narrow_as_samples_grow(synthetic_run):
    analysis = SobolAnalysis(synthetic_run, NAMES, seed=2)
    small = analysis.indices(32)[ENDPOINT_SCORE]
    large = analysis.indices(256)[ENDPOINT_SCORE]
    small_width = max(p.st_ci.half_width for p in small.parameters)
    large_width = max(p.st_ci.half_width for p in large.parameters)
    assert large_width < small_width


# ---------------------------------------------------------------------------
# Pairs
# ---------------------------------------------------------------------------

def test_pairs_cover_every_combination(synthetic_run):
    sobol = analyse(
        synthetic_run, NAMES, UNEXERCISED,
        start_samples=16, max_samples=16, st_ci_target=1.0,
    )
    pairs = sobol.endpoints[ENDPOINT_SCORE].pairs
    seen = {frozenset((p.first, p.second)) for p in pairs}
    assert len(seen) == len(NAMES) * (len(NAMES) - 1) // 2


def test_pair_lookup_is_order_independent(synthetic_run):
    sobol = analyse(
        synthetic_run, NAMES, UNEXERCISED,
        start_samples=16, max_samples=16, st_ci_target=1.0,
    )
    indices = sobol.endpoints[ENDPOINT_SCORE]
    assert indices.pair("tier.cold", "tier.hot") is indices.pair("tier.hot", "tier.cold")


def test_check_pair_reports_whether_the_interval_excludes_zero(synthetic_run):
    sobol = analyse(
        synthetic_run, NAMES, UNEXERCISED,
        start_samples=32, max_samples=32, st_ci_target=1.0,
    )
    value, decided, reason = check_pair(
        sobol, ENDPOINT_SCORE, "tier.cold", "decay.ephemeral.d7"
    )
    assert isinstance(value, float)
    assert isinstance(decided, bool)
    assert reason
    _v, decided_missing, reason_missing = check_pair(
        sobol, ENDPOINT_SCORE, "tier.cold", "auth.mixed"
    )
    assert not decided_missing
    assert "not in the analysed set" in reason_missing


def test_significance_requires_an_interval_clear_of_zero():
    assert PairIndex("a", "b", 0.2, Interval(0.1, 0.3)).significant()
    assert not PairIndex("a", "b", 0.2, Interval(-0.1, 0.3)).significant()
    assert not PairIndex("a", "b", 0.2, None).significant()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def test_report_states_the_range_convention_and_the_intervals(synthetic_run):
    sobol = analyse(
        synthetic_run, NAMES, UNEXERCISED,
        start_samples=16, max_samples=16, st_ci_target=1.0,
    )
    report = format_report(sobol)
    assert "multipliers swept over [0, 1]" in report
    assert f"{int(CONFIDENCE * 100)}% bootstrap percentile" in report
    assert str(BOOTSTRAP_RESAMPLES) in report
    assert "CONVERGENCE" in report
    assert "ENDPOINT: score" in report
    assert "ENDPOINT: delivery" in report
    assert "ST-S1" in report
    assert "largest S2 pairs" in report


def test_report_records_the_sampler(synthetic_run):
    """Two samplers converge to the same indices at different N; a reader
    comparing runs has to know which was used."""
    sobol = analyse(
        synthetic_run, NAMES, UNEXERCISED,
        start_samples=16, max_samples=16, st_ci_target=1.0,
    )
    assert sobol.sampler in format_report(sobol)


def test_saved_results_can_be_re_rendered_without_re_running(synthetic_run):
    """A pass costs hours; re-reading it must not cost them again."""
    from tools.retrieval_trace.sobol import load_results

    sobol = analyse(
        synthetic_run, NAMES, UNEXERCISED,
        start_samples=32, max_samples=32, st_ci_target=1.0,
    )
    reloaded = load_results(json.loads(json.dumps(to_dict(sobol))))

    assert reloaded.samples == sobol.samples
    assert reloaded.evaluations == sobol.evaluations
    assert reloaded.sampler == sobol.sampler
    assert reloaded.unexercised == sobol.unexercised
    assert reloaded.no_solo_delivery_effect == sobol.no_solo_delivery_effect
    for endpoint in ENDPOINTS:
        original = sobol.endpoints[endpoint].ranked()
        restored = reloaded.endpoints[endpoint].ranked()
        assert [p.name for p in restored] == [p.name for p in original]
        for a, b in zip(original, restored):
            assert b.st == pytest.approx(a.st)
            assert b.s1 == pytest.approx(a.s1)
            assert b.st_ci.low == pytest.approx(a.st_ci.low)
    assert format_report(reloaded)


def test_the_target_can_be_asked_of_saved_results(synthetic_run):
    """The target belongs to the question, not to the numbers: the same run
    answers "resolved to 0.02?" and "resolved to 0.05?"."""
    from tools.retrieval_trace.sobol import load_results

    sobol = analyse(
        synthetic_run, NAMES, UNEXERCISED,
        start_samples=32, max_samples=32, st_ci_target=1.0,
    )
    payload = json.loads(json.dumps(to_dict(sobol)))
    payload["st_ci_target"] = None
    assert load_results(payload, st_ci_target=0.05).st_ci_target == 0.05
    assert load_results(payload, st_ci_target=0.01).st_ci_target == 0.01


def test_pairs_involving_queries_saved_results(synthetic_run):
    """Asking a finished run a new question must not cost another run."""
    from tools.retrieval_trace.sobol import pairs_involving

    sobol = analyse(
        synthetic_run, NAMES, UNEXERCISED,
        start_samples=16, max_samples=16, st_ci_target=1.0,
    )
    results = json.loads(json.dumps(to_dict(sobol)))
    rows = pairs_involving(results, ENDPOINT_SCORE, "tier.cold")
    assert rows
    assert all("tier.cold" in (r["first"], r["second"]) for r in rows)
    # Ordered by magnitude, so the first row is the strongest partner.
    magnitudes = [abs(r["s2"]) for r in rows]
    assert magnitudes == sorted(magnitudes, reverse=True)
    assert pairs_involving(results, ENDPOINT_SCORE, "not.a.parameter") == []


def test_two_additive_terms_cannot_interact_with_each_other():
    """The structural claim behind the "counted twice" question.

    Double counting a signal at two points in a linear composition does
    NOT produce an S2 between the two constants: f is linear in each, so
    the cross partial is identically zero. What it produces is an
    asymmetry in what each interacts with. Pinning this here means the
    retrieval result is read correctly rather than as "the double counting
    did not show up".
    """

    def double_counted(x):
        # a enters inside the multiply, b after it -- the same arrangement
        # as ret.type.* versus rank.type.* around the tier multiply. The
        # factor of 10 gives the multiplicative part enough of the variance
        # that the three claims below separate under plain Monte Carlo; at
        # unit scale they sit inside the sampling noise and the test would
        # be asserting its own luck.
        a, b, multiplier = x
        return 10.0 * a * multiplier + b

    s1, st, s2 = _indices_for(double_counted, 3, MC_SAMPLES)
    assert s2[(0, 1)] == pytest.approx(0.0, abs=0.06), "a and b must not interact"
    assert s2[(0, 2)] > 0.05, "the term inside the multiply must interact with it"
    assert s2[(1, 2)] == pytest.approx(0.0, abs=0.03), "the term outside must not"
    assert st[1] == pytest.approx(s1[1], abs=0.03)


def test_results_serialize_and_carry_no_vault_data(synthetic_run):
    sobol = analyse(
        synthetic_run, NAMES, UNEXERCISED,
        start_samples=16, max_samples=16, st_ci_target=1.0,
    )
    payload = to_dict(sobol)
    text = json.dumps(payload)
    assert payload["samples"] == 16
    assert payload["unexercised"] == UNEXERCISED
    assert len(payload["endpoints"]["score"]["parameters"]) == len(NAMES)
    for query in synthetic_run.queries:
        assert query.query not in text
        for candidate in query.candidates:
            assert (candidate.content or "zzz-absent") not in text
