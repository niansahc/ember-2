"""
tests/test_retrieval_trace.py

Coverage for the per-term trace capture and replay harness.

Three things are under test, in order of how badly they matter.

1. THE DEFAULTS ARE THE SHIPPED CONSTANTS. params.py is a second copy of
   numbers that live inline in src/. A copy that drifts is worse than no
   copy: every sensitivity number computed from it would be confidently
   wrong. Each default is pinned by calling the shipped function and
   reading the value back, so a retune in src/ fails here.

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
from src.context.ranker import COLD_MULTIPLIER, ContextRanker
from src.context.service import ContextService
from src.core.config import get_private_vault_path
from src.memory.write_memory import write_memory
from src.retrieval.semantic_search import (
    lexical_relevance_bonus,
    memory_type_adjustment,
    query_intent_adjustment,
    source_quality_adjustment,
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


@pytest.mark.parametrize(
    "mem_type,param",
    [
        ("conversation", "ret.type.conversation"),
        ("reflection", "ret.type.reflection"),
        ("memory", "ret.type.memory"),
        ("ingested", "ret.type.ingested"),
        ("journal", "ret.type.other"),
    ],
)
def test_retrieval_type_defaults_match_shipped(mem_type, param):
    assert memory_type_adjustment(mem_type) == P[param]


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


def _quality(content: str, metadata: dict | None = None) -> float:
    return source_quality_adjustment(content, metadata)


def test_source_quality_defaults_match_shipped():
    neutral = "a plain statement of fact with no markers of any kind at all"
    base = _quality(neutral)
    assert base == pytest.approx(P["ret.quality.not_question"])
    assert _quality(neutral, {"role": "user"}) - base == pytest.approx(
        P["ret.quality.role_user"]
    )
    assert _quality(neutral, {"role": "assistant"}) - base == pytest.approx(
        P["ret.quality.role_assistant"]
    )
    question = "why does the plain statement of fact carry no markers at all"
    assert _quality(question) == pytest.approx(P["ret.quality.question"])
    # Both sides are question-like on purpose: "could you clarify" trips the
    # question ladder as well, so a non-question baseline would measure the
    # clarification term plus the question term and call it one number.
    clarify = "why is this, and could you clarify the rest of it for me please"
    plain_question = "why is this, and where is the rest of it written out"
    assert _quality(clarify) - _quality(plain_question) == pytest.approx(
        P["ret.quality.clarification"]
    )
    assert _quality("today the plain statement carried no markers") - base == pytest.approx(
        P["ret.quality.experience"]
    )
    assert _quality("here's a summary of the plain statement") - _quality(
        "a restatement of the plain statement"
    ) == pytest.approx(P["ret.quality.summary"])


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


def test_policy_preference_defaults_match_shipped():
    ranker = ContextRanker()
    plain = "a record body long enough to avoid every length penalty there is"

    item = _item(score=0.0, content="today i noticed something", tier="hot")
    ranker.apply_policy([item], ContextPolicy(name="x", prefer_experiences=True))
    assert item.score == pytest.approx(P["pol.prefer_experience"])

    item = _item(score=0.0, content="working on the next step", tier="hot")
    ranker.apply_policy([item], ContextPolicy(name="x", prefer_active_work=True))
    assert item.score == pytest.approx(P["pol.prefer_active_work"])

    item = _item(score=0.0, content=plain, metadata={"content_kind": "question"})
    ranker.apply_policy([item], ContextPolicy(name="x", prefer_exact_matches=True))
    assert item.score == pytest.approx(P["pol.exact.question"])

    item = _item(score=0.0, content=plain, metadata={"content_kind": "user_content"})
    ranker.apply_policy([item], ContextPolicy(name="x", prefer_exact_matches=True))
    assert item.score == pytest.approx(P["pol.exact.other"])


def test_authorship_and_project_defaults_match_shipped():
    ranker = ContextRanker()
    relational = "tell me about my partner"
    for branch in ("first_person", "mixed", "third_party", "unknown"):
        item = _item(score=1.0, authorship=branch)
        ranker.apply_authorship_scoring([item], relational)
        assert item.score == pytest.approx(P[f"auth.{branch}"])

    item = _item(score=0.0, metadata={"project_id": "p1"})
    ranker.apply_project_boost([item], "p1")
    assert item.score == pytest.approx(P["proj.boost"])


@pytest.mark.parametrize(
    "item_type,param",
    [
        ("conversation", "rank.type.conversation"),
        ("reflection", "rank.type.reflection"),
        ("memory", "rank.type.memory"),
        ("ingested", "rank.type.ingested"),
    ],
)
def test_rank_type_defaults_match_shipped(item_type, param):
    ranker = ContextRanker()
    item = _item(score=0.0, item_type=item_type)
    ranker._score_memory_item(item)
    assert item.score == pytest.approx(P[param])


@pytest.mark.parametrize(
    "role,param",
    [("user", "rank.role.user"), ("assistant", "rank.role.assistant"),
     ("tool", "rank.role.tool_system"), ("system", "rank.role.tool_system")],
)
def test_rank_role_defaults_match_shipped(role, param):
    ranker = ContextRanker()
    item = _item(score=0.0, metadata={"role": role})
    ranker._score_memory_item(item)
    assert item.score == pytest.approx(P[param])


@pytest.mark.parametrize(
    "kind,param",
    [("experience", "rank.kind.experience"), ("user_content", "rank.kind.user_content"),
     ("answer", "rank.kind.answer"), ("question", "rank.kind.question")],
)
def test_rank_kind_defaults_match_shipped(kind, param):
    ranker = ContextRanker()
    item = _item(score=0.0, metadata={"content_kind": kind})
    ranker._score_memory_item(item)
    assert item.score == pytest.approx(P[param])


def test_rank_length_and_token_defaults_match_shipped():
    ranker = ContextRanker()
    # Under 20 characters also trips the token floor, so the two are read
    # together rather than pretending they separate.
    short = _item(score=0.0, content="tiny")
    ranker._score_memory_item(short)
    assert short.score == pytest.approx(P["rank.len.lt20"] + P["rank.tokens_lt5"])

    mid = _item(score=0.0, content="one two three four five six seven")
    ranker._score_memory_item(mid)
    assert mid.score == pytest.approx(P["rank.len.lt50"])

    long_item = _item(score=0.0, content="word " * 300)
    ranker._score_memory_item(long_item)
    assert long_item.score == pytest.approx(P["rank.len.gt1200"])


def test_rank_user_prefix_default_matches_shipped():
    ranker = ContextRanker()
    plain = "a record body long enough to avoid every length penalty there is"
    with_prefix = _item(score=0.0, content=f"user: {plain}")
    without = _item(score=0.0, content=plain)
    ranker._score_memory_item(with_prefix)
    ranker._score_memory_item(without)
    assert with_prefix.score - without.score == pytest.approx(P["rank.user_prefix"])


@pytest.mark.parametrize(
    "age_days,param",
    [(0, "recency.d7"), (7, "recency.d7"), (8, "recency.d30"), (30, "recency.d30"),
     (31, "recency.d90"), (90, "recency.d90"), (91, "recency.d365"),
     (365, "recency.d365"), (366, "recency.older")],
)
def test_recency_defaults_match_shipped(age_days, param):
    ranker = ContextRanker()
    with patch.object(ContextRanker, "_parse_age_days", return_value=age_days):
        assert ranker._recency_boost("ignored") == pytest.approx(P[param])


def test_unparsed_recency_default_matches_shipped():
    assert ContextRanker()._recency_boost(None) == P["recency.unparsed"]


@pytest.mark.parametrize(
    "mem_type,age_days,param",
    [
        ("profile", 1000, "decay.none"),
        ("reference", 1000, "decay.none"),
        ("ingested", 1000, "decay.none"),
        ("reflection", 7, "decay.reflection.d7"),
        ("reflection", 30, "decay.reflection.d30"),
        ("reflection", 90, "decay.reflection.d90"),
        ("reflection", 91, "decay.reflection.older"),
        ("conversation", 3, "decay.ephemeral.d3"),
        ("conversation", 7, "decay.ephemeral.d7"),
        ("conversation", 14, "decay.ephemeral.d14"),
        ("conversation", 30, "decay.ephemeral.d30"),
        ("conversation", 31, "decay.ephemeral.older"),
        ("state", 3, "decay.default.d3"),
        ("state", 7, "decay.default.d7"),
        ("state", 14, "decay.default.d14"),
        ("state", 30, "decay.default.d30"),
        ("state", 90, "decay.default.d90"),
        ("state", 91, "decay.default.older"),
    ],
)
def test_decay_defaults_match_shipped(mem_type, age_days, param):
    ranker = ContextRanker()
    item = _item(memory_type=mem_type, timestamp="whatever")
    with patch.object(ContextRanker, "_parse_age_days", return_value=age_days):
        assert ranker._temporal_decay_weight(item) == pytest.approx(P[param])


def test_reflection_scoring_defaults_match_shipped():
    ranker = ContextRanker()
    plain = "a reflection body long enough to clear the short-reflection floor"
    item = _item(score=1.0, content=plain, item_type="reflection", memory_type="reflection")
    ranker._score_reflection_item(item)
    assert item.score == pytest.approx(P["refl.base_discount"])

    short = _item(score=1.0, content="brief", item_type="reflection", memory_type="reflection")
    ranker._score_reflection_item(short)
    assert short.score == pytest.approx(P["refl.base_discount"] + P["refl.short"])

    dated = _item(score=0.0, content=plain, item_type="reflection", memory_type="reflection")
    with patch.object(ContextRanker, "_parse_age_days", return_value=0):
        ranker._score_reflection_item(dated)
    assert dated.score == pytest.approx(P["recency.d7"] * P["refl.recency_scale"])


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
    with patch("src.retrieval.semantic_search.embed_text", return_value=seeded_vault):
        yield


@pytest.fixture
def traced_run(stub_query_embedding):
    """One captured run over the synthetic vault, policy pinned.

    The policy is pinned rather than classified because classify_query
    reaches the ADR-034 intent classifier, which is an Ollama call and not
    something a unit test should depend on.
    """
    policy = ContextPolicy(name="reflective", memory_weight=0.7, reflection_weight=1.4,
                           recency_bias=0.2, diversity=True, prefer_experiences=True)
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


def test_replay_reproduces_the_delivered_set(traced_run):
    report = check_fidelity(traced_run)
    assert report.delivery_mismatches == []


def test_capture_leaves_the_database_unchanged(traced_run):
    assert traced_run.db_digest_before == traced_run.db_digest_after
    assert traced_run.digest_unchanged


def test_the_same_pipeline_unguarded_does_write(stub_query_embedding):
    """Control for the test above: without the guard, a build writes."""
    service = ContextService()
    service.build_context(QUERY)
    before = _stats_digest()
    service.build_context(QUERY)
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
    assert any(name.startswith("rank.") for name in result.terms)
    assert "decay" in result.terms


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
    assert "ret.type.ingested" in inert
    assert "proj.boost" in inert


def test_sweep_reports_delivery_changes(traced_run):
    results = sweep(traced_run, "tier.cold", [0.3, 0.0])
    assert results[0] == (0.3, 0)  # the shipped value cannot change anything


# ---------------------------------------------------------------------------
# Serialization and the privacy guard
# ---------------------------------------------------------------------------

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
    broken["rank.type.conversation"] = 99.0

    with patch("tools.retrieval_trace.capture.classify_query", return_value=policy), \
         patch("src.context.service.classify_query", return_value=policy), \
         patch("tools.retrieval_trace.capture.default_params", return_value=broken):
        with pytest.raises(capture_mod.TraceValidationError):
            capture_mod.capture_run([("q1", QUERY)], include_content=True)
