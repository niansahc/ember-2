"""
tests/test_retrieval_trace.py

Coverage for the per-term trace capture and replay harness.

Three things are under test, in order of how badly they matter.

1. THE DEFAULTS ARE THE SHIPPED CONSTANTS. params.py is a second copy of
   the retrieval, policy and authorship constants that live inline in src/.
   A copy that drifts is worse than no copy: every sensitivity number
   computed from it would be confidently wrong. Each of those is pinned by
   calling the shipped function and reading the value back, so a retune in
   src/ fails here.

   The prior and tier parameters are handled differently, because after
   ADR-044 their values are derived rather than authored -- params.py
   IMPORTS them, so there is no copy to guard and a pinning test would only
   assert an identity. What is tested instead is coverage (every factor the
   prior applies is in the vector), absence (no retired parameter lingers),
   and expressibility (each bounded parameter's shipped value lies inside
   its own sweep range).

2. REPLAY REPRODUCES CAPTURE EXACTLY. Over a synthetic vault, with the
   score checked to 1e-12 and the delivered set checked by identity.

3. CAPTURE IS READ-ONLY. Paired with a control that the unguarded path
   does write, so the assertion cannot pass vacuously.

All fixtures are synthetic (CLAUDE.md Vault Privacy Rule).
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from src.context.models import ContextItem
from src.context.policies import ContextPolicy
from src.context.ranker import COLD_MULTIPLIER, WARM_MULTIPLIER, ContextRanker
from src.context.service import ContextService
from tests.conftest import deliver_packet, stub_both_embed_bindings
from src.core.config import get_private_vault_path
from src.memory.write_memory import write_memory
from src.context import prior
from src.retrieval.semantic_search import (
    lexical_relevance_bonus,
    query_intent_adjustment,
)
from tools.retrieval_trace import capture as capture_mod
from tools.retrieval_trace.params import PARAM_NAMES, ReplayParams, default_params
from tools.retrieval_trace.queries import EXPECTED_POLICIES, QUERY_SET
from tools.retrieval_trace.replay import check_fidelity, replay_query, sweep
from tools.retrieval_trace.schema import SCHEMA_VERSION, TraceRun, load_run

EMBED_DIM = 768
QUERY = "what have i been reading about lately"


# ---------------------------------------------------------------------------
# 1. The defaults are the shipped constants
# ---------------------------------------------------------------------------

P = default_params()


def test_no_duplicate_parameter_names():
    assert len(PARAM_NAMES) == len(set(PARAM_NAMES))


def test_lexical_defaults_match_shipped():
    # Substring only: the query appears verbatim, and its single term is
    # short enough to be dropped by extract_query_terms, so no term hits.
    assert lexical_relevance_bonus("ab", [], "xx ab xx") == P["ret.lexical.substring"]
    # One term hit, no substring.
    assert lexical_relevance_bonus("zzz", ["alpha"], "alpha beta") == pytest.approx(
        P["ret.lexical.term_hit"]
    )
    # The cap binds well before ten hits would.
    many = [f"term{i}" for i in range(10)]
    content = " ".join(many)
    assert lexical_relevance_bonus("zzz", many, content) == pytest.approx(
        P["ret.lexical.term_cap"]
    )
    # One entity, then enough entities to bind the cap.
    one = lexical_relevance_bonus("zzz", [], "a story about alpha", raw_query="x Alpha")
    assert one == pytest.approx(P["ret.lexical.entity_hit"])
    three = lexical_relevance_bonus(
        "zzz", [], "alpha beta gamma", raw_query="x Alpha Beta Gamma"
    )
    assert three == pytest.approx(P["ret.lexical.entity_cap"])


def test_query_intent_defaults_match_shipped():
    reflective = "what patterns do you see"
    neutral_content = "a plain record body"
    assert query_intent_adjustment(reflective, "conversation", neutral_content) == pytest.approx(
        P["ret.intent.reflective_conversation"]
    )
    assert query_intent_adjustment(reflective, "reflection", neutral_content) == pytest.approx(
        P["ret.intent.reflective_reflection"]
    )
    assert query_intent_adjustment(reflective, "ingested", neutral_content) == pytest.approx(
        P["ret.intent.reflective_ingested"]
    )
    assert query_intent_adjustment(reflective, "journal", "user: a body") == pytest.approx(
        P["ret.intent.reflective_user_prefix"]
    )
    assert query_intent_adjustment(reflective, "journal", "assistant: a body") == pytest.approx(
        P["ret.intent.reflective_assistant_prefix"]
    )
    task = "fix the pipeline"
    assert query_intent_adjustment(task, "ingested", neutral_content) == pytest.approx(
        P["ret.intent.task_ingested"]
    )
    assert query_intent_adjustment(task, "conversation", neutral_content) == pytest.approx(
        P["ret.intent.task_conversation"]
    )


def _item(**kwargs) -> ContextItem:
    base = dict(
        id="x",
        content="a record body long enough to avoid every length penalty there is",
        source="chat",
        item_type="journal",
        memory_type="journal",
        score=0.0,
        timestamp=None,
        tags=[],
        metadata={},
    )
    base.update(kwargs)
    return ContextItem(**base)


def test_tier_defaults_match_shipped():
    ranker = ContextRanker()
    policy = ContextPolicy(name="default")
    assert COLD_MULTIPLIER == P["tier.cold"]
    for tier, param in (("cold", "tier.cold"), ("warm", "tier.warm"), ("hot", "tier.hot")):
        item = _item(score=1.0, tier=tier)
        ranker.apply_policy([item], policy)
        assert item.score == pytest.approx(P[param])
    # Profile bypasses the ladder entirely, even when tagged cold.
    profile = _item(score=1.0, tier="cold", memory_type="profile", item_type="profile")
    ranker.apply_policy([profile], policy)
    assert profile.score == pytest.approx(P["tier.profile_bypass"])


def test_the_policy_arm_is_multiplicative_and_matches_the_vector():
    """The preference terms are prior arms now, not additive terms in apply_policy.

    They were pinned here as `score == P["pol.prefer_experience"]` on an item
    seeded at 0.0 -- which only reads as a magnitude while the term is additive.
    As multipliers they are pinned as a RATIO against a neutral record, which is
    what a multiplier means and what survives a re-derivation of the value.
    """
    ranker = ContextRanker()
    plain = "a record body long enough to avoid every length penalty there is"

    cases = [
        ("today i noticed something", {}, ContextPolicy(name="x", prefer_experiences=True),
         "prior.policy.prefer_experience"),
        ("working on the next step", {}, ContextPolicy(name="x", prefer_active_work=True),
         "prior.policy.prefer_active_work"),
        (plain, {"content_kind": "question"},
         ContextPolicy(name="x", prefer_exact_matches=True),
         "prior.policy.exact_question"),
        (plain, {"content_kind": "user_content"},
         ContextPolicy(name="x", prefer_exact_matches=True),
         "prior.policy.exact_other"),
    ]
    for content, metadata, policy, param in cases:
        armed = _item(score=1.0, content=content, tier="hot", metadata=dict(metadata))
        bare = _item(score=1.0, content=content, tier="hot", metadata=dict(metadata))
        ranker.rank([armed], [], policy)
        ranker.rank([bare], [], None)      # no policy: the arm is the identity
        assert armed.score / bare.score == pytest.approx(P[param], rel=1e-9), param


def test_authorship_and_project_defaults_match_shipped():
    ranker = ContextRanker()
    relational = "tell me about my partner"
    for branch in ("first_person", "mixed", "unknown"):
        item = _item(score=1.0, authorship=branch)
        ranker.apply_authorship_scoring([item], relational)
        assert item.score == pytest.approx(P[f"auth.{branch}"])

    # The project boost is a prior arm too, so it is a ratio against an
    # unmatched record rather than an additive magnitude.
    matched = _item(score=1.0, metadata={"project_id": "p1"})
    unmatched = _item(score=1.0, metadata={"project_id": "other"})
    ranker.apply_project_boost([matched, unmatched], "p1")
    ranker.rank([matched, unmatched], [], None)
    assert matched.score / unmatched.score == pytest.approx(
        P["prior.project.match"], rel=1e-9
    )


def test_the_prior_vector_covers_every_factor_the_prior_applies():
    """No prior factor may be missing from the parameter vector.

    The prior's magnitudes are DERIVED (from the measured cosine spread and
    from Sobol ST on delivery), so params.py imports them instead of copying
    them, and the pinning tests that guarded the old additive terms were
    deleted rather than migrated: asserting that an imported value equals
    itself is not a test.

    What still needs guarding is COVERAGE. A factor added to prior.py that
    nobody adds to the vector would be swept by nothing and report a
    sensitivity of exactly zero -- indistinguishable from a term the corpus
    never exercised, which is the confusion parameter_coverage exists to
    prevent.
    """
    expected = (
        {f"prior.kind.{k}" for k in prior._KIND_FACTORS if k != "none"}
        | {f"prior.len.{k}" for k in prior._LENGTH_FACTORS if k != "none"}
        | {f"prior.recency.{b}" for b in prior.RECENCY}
        | {f"prior.policy.{k}" for k in prior._POLICY_FACTORS if k != "none"}
        | {"prior.project.match", "prior.reflection_discount"}
    )
    actual = {n for n in PARAM_NAMES if n.startswith("prior.")}
    assert actual == expected


def test_no_retired_parameter_lingers_in_the_vector():
    """The parameters ADR-044 retired are gone, not left at their old values.

    A retired parameter kept in the table is the worst outcome available: it
    sweeps, moves nothing, and reports as an inert parameter -- so a reader
    concludes the TERM does not matter, when the truth is that the term has no
    call site at all. That is the reading this test exists to make impossible.
    """
    retired_prefixes = (
        "ret.type.",       # memory_type_adjustment, no longer called
        "ret.quality.",    # source_quality_adjustment, no longer called
        "rank.",           # the additive pile
        "refl.",           # the reflection discount and its recency scale
        "decay.",          # _temporal_decay_weight, absorbed into tiering
        "recency.",        # the additive ladder; now prior.recency.*
    )
    lingering = sorted(n for n in PARAM_NAMES if n.startswith(retired_prefixes))
    assert not lingering, f"retired parameters still in the vector: {lingering}"


def test_the_bound_is_expressible_by_every_bounded_parameter():
    """Each bounded parameter's shipped value lies inside its sweep range.

    The failure this prevents is concrete and was live before ADR-044's range
    family was added: the multiplier family swept [0, 1], which cannot express
    prior.kind.experience at 1.0581. A parameter screened over an interval
    that excludes its own shipped value reports sensitivity for a system
    nobody runs.
    """
    from tools.retrieval_trace.ranges import FAMILY_BOUNDED, build_ranges

    ranges = build_ranges(P)
    bounded = [r for r in ranges.values() if r.family == FAMILY_BOUNDED]
    assert bounded, "no bounded parameters; the family is not wired up"
    for r in bounded:
        assert r.low <= r.default <= r.high, (
            f"{r.name} default {r.default} outside sweep range "
            f"[{r.low}, {r.high}]"
        )


# ---------------------------------------------------------------------------
# The parameter vector
# ---------------------------------------------------------------------------

def test_unknown_parameter_is_rejected():
    with pytest.raises(ValueError):
        ReplayParams(values={"ret.lexical.nope": 1.0})


def test_partial_override_keeps_every_other_default():
    params = ReplayParams().with_overrides(**{"tier.cold": 0.9})
    assert params["tier.cold"] == 0.9
    assert params["tier.warm"] == P["tier.warm"]
    assert set(params.values) == set(PARAM_NAMES)


def test_policy_override_falls_back_to_the_captured_value():
    params = ReplayParams(policy_overrides={"reflective": {"memory_weight": 0.5}})
    assert params.policy_field("reflective", "memory_weight", 0.7) == 0.5
    assert params.policy_field("default", "memory_weight", 0.7) == 0.7


# ---------------------------------------------------------------------------
# The query set
# ---------------------------------------------------------------------------

def test_query_set_intends_to_cover_every_vault_policy():
    intended = {policy for _id, _q, policy in QUERY_SET}
    assert EXPECTED_POLICIES <= intended


def test_query_ids_are_unique():
    ids = [query_id for query_id, _q, _p in QUERY_SET]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# 2 and 3. Capture and replay over a synthetic vault
# ---------------------------------------------------------------------------

def _memory_db() -> Path:
    return get_private_vault_path() / "embeddings" / "memory.db"


def _stats_digest() -> str:
    path = _memory_db()
    if not path.exists():
        return "no-database"
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT id, last_retrieved_at, frequency_score FROM vectors ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return hashlib.sha256(repr(rows).encode()).hexdigest()


@pytest.fixture
def seeded_vault():
    """A corpus varied enough that the branches under test actually fire."""
    vector = [0.1] * EMBED_DIM
    rows = [
        ("today i was reading about retrieval scoring and it clarified a lot for me",
         "conversation", {"role": "user", "content_kind": "experience"}),
        ("assistant: here's a summary of what you have been reading about lately",
         "conversation", {"role": "assistant", "content_kind": "answer"}),
        ("user: what have i been reading about, can you tell me more about it",
         "conversation", {"role": "user", "content_kind": "question"}),
        ("a journal entry about working through the reading list this week",
         "journal", {"role": "user", "content_kind": "user_content"}),
        ("a reflection on the reading patterns visible across the last month",
         "reflection", {"content_kind": "user_content"}),
        ("the user prefers dense technical reading over summaries and skimming",
         "profile", {"content_kind": "user_content"}),
    ]
    with patch("src.memory.write_memory.embed_text", return_value=vector):
        for text, memory_type, metadata in rows:
            write_memory(text=text, memory_type=memory_type, source="chat", metadata=metadata)
    yield vector


@pytest.fixture
def stub_query_embedding(seeded_vault):
    """Both embedding bindings. See conftest.stub_both_embed_bindings."""
    with stub_both_embed_bindings(seeded_vault):
        yield seeded_vault


@pytest.fixture
def traced_run(stub_query_embedding):
    """One captured run over the synthetic vault, policy pinned.

    The policy is pinned rather than classified because classify_query
    reaches the ADR-034 intent classifier, which is an Ollama call and not
    something a unit test should depend on.
    """
    policy = ContextPolicy(name="reflective", memory_weight=0.7, reflection_weight=1.4,
                           diversity=True, prefer_experiences=True)
    with patch("tools.retrieval_trace.capture.classify_query", return_value=policy), \
         patch("src.context.service.classify_query", return_value=policy):
        yield capture_mod.capture_run(
            [("q1", QUERY), ("q2", "what patterns have you noticed lately")],
            include_content=True,
        )


def test_capture_produces_candidates(traced_run):
    assert len(traced_run.queries) == 2
    assert all(q.candidates for q in traced_run.queries)


def test_capture_records_every_stage_for_every_candidate(traced_run):
    from tools.retrieval_trace.compose import STAGES

    for query in traced_run.queries:
        for candidate in query.candidates:
            assert set(candidate.stage_scores) == set(STAGES)


def test_replay_reproduces_every_captured_score_exactly(traced_run):
    report = check_fidelity(traced_run)
    assert report.candidates_checked > 0
    assert report.score_mismatches == []


@pytest.mark.xfail(
    strict=False,
    reason="#244: the harness models the reflection channel as having no "
           "retrieval stage -- true under Jaccard scoring, stale since "
           "#241 routed reflections through semantic_search. Non-strict "
           "because the outcome is order-dependent, and the passing case "
           "is the vacuous one: the two models agree whenever the shared "
           "session vault is large enough that the channel delivers "
           "nothing in both. Remove with the #244 fix, not before.",
)
def test_replay_reproduces_the_delivered_set(traced_run):
    report = check_fidelity(traced_run)
    assert report.delivery_mismatches == []


def test_capture_leaves_the_database_unchanged(traced_run):
    assert traced_run.db_digest_before == traced_run.db_digest_after
    assert traced_run.digest_unchanged


def test_the_same_pipeline_unguarded_does_write(stub_query_embedding):
    """Control for the test above: without the guard, a build writes."""
    service = ContextService()
    deliver_packet(service.build_context(QUERY))
    before = _stats_digest()
    deliver_packet(service.build_context(QUERY))
    assert _stats_digest() != before


def test_something_was_actually_delivered(traced_run):
    delivered = sum(len(q.delivered_refs) for q in traced_run.queries)
    assert delivered > 0, "nothing delivered; the delivery replay check is vacuous"


def test_terms_are_individually_attributable(traced_run):
    """Per-term granularity is the requirement, not an aggregate score."""
    from tools.retrieval_trace.compose import compose

    candidate = traced_run.queries[0].candidates[0]
    result = compose(candidate, ReplayParams(), traced_run.queries[0].policy_name)
    assert len(result.terms) >= 6
    assert "raw_cosine" in result.terms

    # The prior's FACTORS, not just its product. Consolidating the additive
    # pile into one multiplier put per-term attribution at risk: recording
    # only the composed "prior" would make every metadata term
    # indistinguishable from every other, which is precisely the granularity
    # this test exists to defend. So both are required.
    assert "prior" in result.terms
    assert any(name.startswith("prior.") for name in result.terms)

    # And the product really is the product of the factors it reports, so the
    # attribution is not decorative.
    factors = [v for k, v in result.terms.items() if k.startswith("prior.")]
    assert factors
    product = 1.0
    for value in factors:
        product *= value
    assert result.terms["prior"] == pytest.approx(prior.clamp(product))


def test_perturbing_a_parameter_moves_the_score(traced_run):
    """The harness has to be able to fail, or it is measuring nothing."""
    query = traced_run.queries[0]
    baseline = {s.ref: s.score for s in replay_query(query).scored}
    # tier.hot, because every non-profile candidate carries a tier and the
    # shipped value is the identity element -- a parameter that cannot move
    # anything is exactly the kind a sensitivity pass would wrongly report
    # as uninfluential.
    moved = ReplayParams().with_overrides(**{"tier.hot": 2.0})
    perturbed = {s.ref: s.score for s in replay_query(query, moved).scored}
    assert any(
        abs(baseline[ref] - perturbed[ref]) > 1e-9 for ref in baseline
    ), "no candidate moved; the corpus does not exercise this parameter"


def test_parameter_coverage_separates_inert_from_exercised(traced_run):
    """A zero Sobol index has two causes and they are not the same finding."""
    from tools.retrieval_trace.replay import parameter_coverage

    results = parameter_coverage(traced_run)
    assert len(results) == len(PARAM_NAMES)
    exercised = [name for name, moved in results if moved]
    inert = [name for name, moved in results if not moved]
    assert exercised, "no parameter moves anything; the harness is inert"
    # The synthetic corpus has no ingested records and no project, so these
    # cannot be exercised. Asserting that they come back inert is what
    # proves the report distinguishes the two cases rather than reporting
    # every parameter as live.
    #
    # ret.type.ingested used to be the ingested-record probe here. It is
    # retired (semantic_search no longer calls memory_type_adjustment), and a
    # retired parameter is inert for a THIRD reason -- no call site -- which
    # would have made this assertion pass while testing nothing. The
    # ingested-specific parameter that survives is the intent term.
    assert "ret.intent.reflective_ingested" in inert
    assert "prior.project.match" in inert


def test_sweep_reports_delivery_changes(traced_run):
    results = sweep(traced_run, "tier.cold", [0.3, 0.0])
    assert results[0] == (0.3, 0)  # the shipped value cannot change anything


# ---------------------------------------------------------------------------
# Serialization and the privacy guard
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    strict=False,
    reason="#244: the harness models the reflection channel as having no "
           "retrieval stage -- true under Jaccard scoring, stale since "
           "#241 routed reflections through semantic_search. Non-strict "
           "because the outcome is order-dependent, and the passing case "
           "is the vacuous one: the two models agree whenever the shared "
           "session vault is large enough that the channel delivers "
           "nothing in both. Remove with the #244 fix, not before.",
)
def test_round_trip_through_disk(traced_run, tmp_path):
    path = traced_run.write(tmp_path / "trace.json")
    reloaded = load_run(path)
    assert reloaded.schema_version == SCHEMA_VERSION
    assert len(reloaded.queries) == len(traced_run.queries)
    assert check_fidelity(reloaded).exact


def test_writing_inside_the_repository_is_refused(traced_run):
    repo_root = Path(__file__).resolve().parents[1]
    with pytest.raises(ValueError, match="refusing to write a trace inside"):
        traced_run.write(repo_root / "logs" / "trace.json")


def test_writing_inside_any_git_work_tree_is_refused(traced_run, tmp_path):
    """Not just ember-2. Any work tree, however unrelated.

    The gap this closes was live on the reference machine: the documented
    default output directory is `~/.ember_traces`, the home directory there is
    itself the work tree of an unrelated repository, and `.ember_traces` is
    matched by none of that repository's ignore rules. A trace written to the
    default location sat untracked-but-unignored inside someone else's repo,
    which is precisely the state `git add .` sweeps up -- and the old guard,
    which compared against ember-2's root alone, permitted it.

    Untracked is not safe. Unignored-and-untracked is the dangerous state, and
    a trace carries real query text and real content.
    """
    somebody_elses_repo = tmp_path / "unrelated_project"
    (somebody_elses_repo / ".git").mkdir(parents=True)
    nested = somebody_elses_repo / "data" / "traces"

    with pytest.raises(ValueError, match="inside a git work tree"):
        traced_run.write(nested / "trace.json")


def test_the_guard_names_the_nearest_work_tree_not_a_blanket_refusal(
    traced_run, tmp_path
):
    """Non-vacuity, in the only form this machine can express.

    A guard that refused every path would pass the test above and make the tool
    unusable -- the failure mode of a guard written from the refusal side only.
    The obvious check is "a path outside every work tree is allowed", and it
    cannot be written here: pytest's tmp_path lives under the home directory,
    and on this machine the home directory IS a work tree. That is not a defect
    in the test, it is the finding the guard exists for, and it is why the plain
    positive case is unavailable.

    What is machine-independent is WHICH ancestor the refusal names. The walk
    goes from the path upward, so a target nested inside a nearer repository
    must name that one rather than the outer one. Two paths that differ only in
    whether a nearer .git exists must therefore produce different messages --
    which cannot be true of a blanket refusal.
    """
    nearer_repo = tmp_path / "unrelated_project"
    (nearer_repo / ".git").mkdir(parents=True)

    with pytest.raises(ValueError) as nested:
        traced_run.write(nearer_repo / "data" / "trace.json")
    with pytest.raises(ValueError) as plain:
        traced_run.write(tmp_path / "data" / "trace.json")

    assert str(nearer_repo) in str(nested.value)
    assert str(nearer_repo) not in str(plain.value), (
        "both paths produced the same refusal; the guard is not walking to the "
        "nearest work tree, it is refusing unconditionally"
    )


def test_content_is_omitted_when_not_requested(stub_query_embedding):
    policy = ContextPolicy(name="default")
    with patch("tools.retrieval_trace.capture.classify_query", return_value=policy), \
         patch("src.context.service.classify_query", return_value=policy):
        run = capture_mod.capture_run([("q1", QUERY)], include_content=False)
    for query in run.queries:
        for candidate in query.candidates:
            assert candidate.content is None
            assert candidate.content_sha256
            assert candidate.content_length > 0


def test_an_older_schema_is_refused_rather_than_guessed(tmp_path):
    path = tmp_path / "old.json"
    path.write_text('{"schema_version": 0, "queries": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="recapture"):
        load_run(path)


def test_capture_raises_when_the_model_disagrees_with_the_pipeline(stub_query_embedding):
    """The check that makes every other result trustworthy.

    A wrong parameter is made to look right by breaking the model: if the
    stage check can be fooled, nothing else here is evidence.
    """
    policy = ContextPolicy(name="default")
    broken = dict(default_params())
    # A live parameter every candidate activates. Was rank.type.conversation,
    # which ADR-044 retired -- poisoning a parameter with no call site would
    # perturb nothing, so the meta-test would pass by failing to detect a
    # disagreement that never occurred.
    broken["prior.recency.unparsed"] = 99.0

    with patch("tools.retrieval_trace.capture.classify_query", return_value=policy), \
         patch("src.context.service.classify_query", return_value=policy), \
         patch("tools.retrieval_trace.capture.default_params", return_value=broken):
        with pytest.raises(capture_mod.TraceValidationError):
            capture_mod.capture_run([("q1", QUERY)], include_content=True)
