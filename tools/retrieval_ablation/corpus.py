"""
tools/retrieval_ablation/corpus.py

The labelled fixture corpus for the retrieval-architecture ablation.

Shape: ONE shared pool of 40 fixtures, with per-query graded labels. Each of
the 8 policy strata contributes 5 constructed fixtures; every query is graded
0-3 against the whole pool, so a top-6 cut ranks against 40 candidates and the
other strata's fixtures act as natural cross-domain distractors. A per-stratum
pool of 5 would make k<=6 retrieve almost everything and leave nDCG nowhere to
move.

Grades are ground truth assigned by construction. Cosine is the OBSERVABLE
signal, and it is MEASURED, not stipulated: `MEASURED_COSINES` holds the real
nomic-embed-text similarity of every fixture text against every query, frozen
into this file so the run stays offline and deterministic.

That distinction is the whole validity of the eval. The first build stipulated
cosines by hand and, without meaning to, made them agree with the grades
(Kendall tau +0.64, cosine-only nDCG@6 0.937). A naive baseline handed the
answer key wins by construction, every typed stage can only subtract, and the
result says nothing about Ember. Measuring instead means the base signal is the
embedder's opinion rather than the author's, so the headroom the pipeline has to
work in is a real property of the corpus. See `regenerate_cosines.py`.

Headroom is built deliberately and asserted by the tests:

  HELP CASES  -- a correctly-graded record phrased WITHOUT the query's
                 vocabulary, so dense similarity ranks it below the cut and
                 only the mechanism under test can recover it. Marked
                 "BURIED HELP CASE" at each fixture.
  HARM CASES  -- a grade-0 distractor carrying the query's vocabulary AND the
                 metadata that baits one lever, so it floats above the cut
                 and only that lever's removal drops it.

Without help cases the pipeline has nothing to rescue and every ablation scores
as an improvement; without harm cases nothing can leak and the leakage metric
is vacuous. Both failure modes are pinned in the test file.

Every fixture that lands in a query's contested zone is graded explicitly.
Leaving a plausible record unjudged would score it 0 by omission and penalise
the retriever for being right about it.

Everything here is synthetic. No vault text, no real names, no record ids
(CLAUDE.md vault privacy rule).

Four pipeline constraints the fixtures are built to survive, all in
src/context/service.py:
  - `_is_low_value_memory` drops normalized content under 40 chars, and drops
    `content_kind == "question"` under 120 chars.
  - `_is_echo_or_meta_memory` drops content containing "user asked:",
    "question:", "answer:" and similar, and drops anything whose token Jaccard
    against the query exceeds 0.55. That is why the lexical-bait fixtures are
    LONG: they need high raw term overlap to bait
    `lexical_relevance_bonus` while keeping Jaccard below the echo threshold.
  - `_deduplicate` is exact-match on normalized text, so every text is distinct.
  - `_apply_type_gate` drops anything scoring below 0.25, so every fixture
    carries a cosine above that floor and the gate is not silently doing the
    selection work.

Types are restricted to what `get_memory_items` and `get_profile_items`
actually produce -- conversation, journal, reflection, ingested, profile.
`state`, `task`, `project` and `reference` appear in some policies'
`eligible_memory_types` lists but are not retrievable through the memory
channel, so including them would test a path that does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

# Every fixture competes in every stratum, at its real measured similarity.
# The first build gave unjudged fixtures a flat stipulated baseline, which left
# 32 of the 40 inert and made the top-6 a contest between 8 candidates for 6
# slots -- at most two could ever be excluded, so there was nowhere for a
# ranking decision to show up. The full pool competing is what gives k=6 a real
# selection problem.

# Distractor classes, one per scoring lever. A non-zero leakage count against
# one of these names the mechanism that failed rather than reporting
# undifferentiated noise.
TYPE_BOOST_BAIT = "type_boost_bait"
DECAY_BAIT = "decay_bait"
RECENCY_BAIT = "recency_bait"
LEXICAL_BAIT = "lexical_bait"
ENTITY_BAIT = "entity_bait"
TIER_BAIT = "tier_bait"
REFLECTION_WEIGHT_BAIT = "reflection_weight_bait"
EXPERIENCE_BAIT = "experience_bait"

DISTRACTOR_CLASSES = (
    TYPE_BOOST_BAIT,
    DECAY_BAIT,
    RECENCY_BAIT,
    LEXICAL_BAIT,
    ENTITY_BAIT,
    TIER_BAIT,
    REFLECTION_WEIGHT_BAIT,
    EXPERIENCE_BAIT,
)


@dataclass(frozen=True)
class Fixture:
    """One candidate record. `age_days` is the invariant, not the timestamp:
    the timestamp is derived at build time so decay and recency buckets stay
    fixed regardless of the day the eval runs."""

    id: str
    text: str
    memory_type: str
    tier: str = "hot"
    age_days: int = 30
    role: str | None = None
    content_kind: str | None = None
    distractor_class: str | None = None

    def timestamp(self, now: datetime | None = None) -> str:
        """Vault canonical hyphenated form, which ranker._parse_age_days
        handles directly."""
        base = now or datetime.now()
        return (base - timedelta(days=self.age_days)).strftime("%Y-%m-%dT%H-%M-%S")


@dataclass(frozen=True)
class Stratum:
    """One query, with its policy pinned.

    The policy is pinned rather than classified: `classify_query` calls the
    LLM intent classifier (policies.py:282), which would make the eval
    non-deterministic and Ollama-dependent, and intent classification is not
    the variable under test.

    `judgments` maps fixture id -> grade 0-3. Cosine is not stored here: it is
    measured, and lives in MEASURED_COSINES. An omitted fixture is grade 0,
    which is only sound because every fixture in a query's contested zone is
    graded explicitly -- see the module docstring.
    """

    name: str
    policy_name: str
    query: str
    judgments: dict[str, int] = field(default_factory=dict)
    abstain: bool = False

    def grade(self, fixture_id: str) -> int:
        return self.judgments.get(fixture_id, 0)

    def cosine(self, fixture_id: str) -> float:
        return MEASURED_COSINES[self.name][fixture_id]


# ---------------------------------------------------------------------------
# The pool: 40 fixtures
# ---------------------------------------------------------------------------

FIXTURES: tuple[Fixture, ...] = (
    # -- Work / retrieval pipeline -----------------------------------------
    # Mentions the Halloway codename legitimately: the entity boost should
    # promote this one, and d08 is the test of whether it also promotes an
    # irrelevant record that merely shares the name.
    # BURIED HELP CASE (activity stratum). Keeps the Halloway codename, which
    # the entity boost is worth 0.20 for, but drops the query's topic
    # vocabulary so dense similarity alone will not lift it into the window.
    # Only the lexical/entity term can rescue it.
    Fixture(
        id="f01_work_decay_trace",
        text=(
            "Spent the afternoon on why older notes drop out of the Halloway stage "
            "once they pass a certain age, and found the weight is applied per "
            "record type rather than evenly across everything being compared."
        ),
        memory_type="conversation", tier="hot", age_days=2,
        role="user", content_kind="experience",
    ),
    Fixture(
        id="f02_work_index_backlog",
        text=(
            "The indexing job finished overnight and the backlog finally cleared, "
            "so the search path is no longer timing out on the larger imports."
        ),
        memory_type="conversation", tier="warm", age_days=9, role="user",
    ),
    Fixture(
        id="f03_work_scoring_journal",
        text=(
            "Worked through the scoring pipeline again today. The ordering still "
            "feels arbitrary once a record passes about a month old, even when it "
            "is clearly the better match."
        ),
        memory_type="journal", tier="hot", age_days=4, content_kind="experience",
    ),
    Fixture(
        id="f04_work_design_note",
        text=(
            "Design note: the ranking service applies a multiplicative age weight "
            "after the additive boosts, which compresses older records toward zero "
            "regardless of how well they match."
        ),
        memory_type="ingested", tier="cold", age_days=120,
    ),
    Fixture(
        id="f05_work_assistant_summary",
        text=(
            "The ranking stage weights recent records more heavily and then applies "
            "a type-specific decay curve, so two equally relevant records can end up "
            "far apart in the final order."
        ),
        memory_type="conversation", tier="warm", age_days=6,
        role="assistant", content_kind="answer",
    ),
    # BURIED HELP CASE (task_status stratum). Says what is outstanding without
    # naming the thing the query names, so only recency and the hot tier can
    # bring it back into the window.
    Fixture(
        id="f06_work_migration_progress",
        text=(
            "The backfill still needs doing and the import path wants one more pass "
            "before the freeze. Everything else in that stream is done and closed."
        ),
        memory_type="journal", tier="hot", age_days=3, content_kind="experience",
    ),

    # -- Health / energy ----------------------------------------------------
    Fixture(
        id="f07_health_sleep_slip",
        text=(
            "Slept badly again and it showed by the middle of the afternoon. That is "
            "the third time this week the late evening has pushed everything back."
        ),
        memory_type="journal", tier="hot", age_days=1, content_kind="experience",
    ),
    Fixture(
        id="f08_health_morning_pattern",
        text=(
            "Noticed the harder work gets done before noon and then the day stalls "
            "out. That is one of the patterns that has held for most of this month "
            "without much variation in how the hours land."
        ),
        memory_type="journal", tier="warm", age_days=12, content_kind="experience",
    ),
    # BURIED HELP CASE (reflective stratum). This IS the synthesized answer,
    # but stated without the query's vocabulary, so dense similarity ranks it
    # below the lexical bait. The reflective policy's reflection weight (x1.4)
    # is the only thing that can recover it.
    Fixture(
        id="f09_health_reflection_thread",
        text=(
            "Energy runs highest in the first few hours after waking, and anything "
            "left until after nine in the evening tends to cost the following "
            "morning as well, almost without exception."
        ),
        memory_type="reflection", tier="hot", age_days=3,
    ),
    Fixture(
        id="f10_health_routine_intent",
        text=(
            "Keep meaning to shift the evening routine earlier, but it slides every "
            "time something overruns and the whole plan goes with it."
        ),
        memory_type="conversation", tier="warm", age_days=15, role="user",
    ),
    Fixture(
        id="f11_health_older_reflection",
        text=(
            "Looking back over the quarter, the productive stretches line up with the "
            "weeks where the evenings stayed short and nothing ran past midnight."
        ),
        memory_type="reflection", tier="cold", age_days=95,
    ),

    # -- Scheduling / current state ----------------------------------------
    # BURIED HELP CASE (status_state stratum). States the focus without using
    # the word, so the recency bias (0.8) and the hot tier have to do the work.
    Fixture(
        id="f12_state_current_focus",
        text=(
            "Everything else is parked until the migration lands and has held for a "
            "full cycle. Nothing else gets picked up before that is true."
        ),
        memory_type="conversation", tier="hot", age_days=1, role="user",
    ),
    Fixture(
        id="f13_state_open_threads",
        text=(
            "Still open: the import path needs a second pass, and the older records "
            "have not been backfilled yet. Neither is blocked, both are unfinished."
        ),
        memory_type="conversation", tier="hot", age_days=2, role="user",
    ),
    Fixture(
        id="f14_state_parked_cleanup",
        text=(
            "Parked the secondary cleanup for now. It is not blocking anything and it "
            "can wait until after the freeze without causing a problem."
        ),
        memory_type="journal", tier="warm", age_days=8,
    ),
    Fixture(
        id="f15_state_freeze_window",
        text=(
            "The freeze window starts at the end of next week, so anything not merged "
            "by then waits for the following cycle regardless of how small it is."
        ),
        memory_type="conversation", tier="hot", age_days=5, role="user",
    ),
    Fixture(
        id="f16_state_old_priority",
        text=(
            "Priorities from earlier in the year were the ingest rewrite and the "
            "reporting cleanup, both of which have since been finished and closed out."
        ),
        memory_type="journal", tier="cold", age_days=150,
    ),

    # -- Learning / reference material -------------------------------------
    # BURIED HELP CASE (factual_recall stratum). The correct answer, phrased
    # around the terms the query uses rather than with them, so the naive
    # ranking puts the decay baits above it.
    Fixture(
        id="f17_ref_hybrid_retrieval",
        text=(
            "Two methods are often run side by side, one comparing meaning through "
            "vectors and one counting words the texts share, with the two output "
            "orderings merged into one list before anything is returned."
        ),
        memory_type="ingested", tier="cold", age_days=200,
    ),
    Fixture(
        id="f18_ref_graded_metric",
        text=(
            "A graded relevance metric discounts each position logarithmically, so an "
            "error at the first rank costs considerably more than the same error at "
            "the sixth."
        ),
        memory_type="ingested", tier="cold", age_days=180,
    ),
    Fixture(
        id="f19_ref_cosine_definition",
        text=(
            "Cosine similarity measures the angle between two embedding vectors and is "
            "independent of their magnitude, which is why it is stable across documents "
            "of very different length."
        ),
        memory_type="ingested", tier="cold", age_days=300,
    ),
    Fixture(
        id="f20_ref_tiering_theory",
        text=(
            "Activation-based memory models score a trace on both how recently and how "
            "often it was used, rather than on elapsed calendar time by itself."
        ),
        memory_type="ingested", tier="cold", age_days=160,
    ),
    Fixture(
        id="f21_ref_chunking_note",
        text=(
            "Chunk size trades recall against precision: longer passages retrieve more "
            "reliably but dilute the specific claim that made them worth retrieving."
        ),
        memory_type="ingested", tier="warm", age_days=60,
    ),

    # -- Tooling / setup ----------------------------------------------------
    Fixture(
        id="f22_tool_editor_setup",
        text=(
            "Reconfigured the editor and the shell prompt so the two machines behave "
            "the same way, which removes a small daily friction that had been adding up."
        ),
        memory_type="journal", tier="warm", age_days=20, content_kind="experience",
    ),
    Fixture(
        id="f23_tool_backup_routine",
        text=(
            "The backup routine now runs on a schedule instead of by hand, and the last "
            "three runs completed without needing any intervention at all."
        ),
        memory_type="conversation", tier="hot", age_days=8, role="user",
    ),
    # BURIED HELP CASE (recent stratum). Describes the change without saying
    # "changed", "recently" or "setup", so recency and the hot tier carry it.
    Fixture(
        id="f24_tool_local_model",
        text=(
            "Put the smaller model back for day to day use after the larger one "
            "turned out to be noticeably slower to answer anything substantial."
        ),
        memory_type="conversation", tier="hot", age_days=7, role="user",
    ),
    Fixture(
        id="f25_tool_old_migration",
        text=(
            "The storage migration from last spring took two weekends and mostly "
            "involved rewriting the loader rather than moving the data itself."
        ),
        memory_type="journal", tier="cold", age_days=210,
    ),

    # -- Identity / profile -------------------------------------------------
    # Profile fixtures carry tier="hot" because get_profile_items builds items
    # without a tier field, so profile always arrives hot in production
    # regardless of what the tiering job assigned.
    Fixture(
        id="f26_profile_role",
        text=(
            "Works as a systems analyst and spends most of the week on data pipelines, "
            "retrieval tooling, and the reporting that sits on top of them."
        ),
        memory_type="profile", tier="hot", age_days=40,
    ),
    Fixture(
        id="f27_profile_preference",
        text=(
            "Prefers direct feedback over encouragement, and would rather be told the "
            "problem plainly than be asked how the problem made them feel."
        ),
        memory_type="profile", tier="hot", age_days=55,
    ),
    Fixture(
        id="f28_profile_working_style",
        text=(
            "Works best in long uninterrupted stretches early in the day and treats "
            "meetings after mid afternoon as a cost rather than a neutral event."
        ),
        memory_type="profile", tier="hot", age_days=70,
    ),
    Fixture(
        id="f29_profile_tooling_pref",
        text=(
            "Keeps tooling deliberately simple and local, and is sceptical of anything "
            "that requires a hosted service to do work that runs fine on one machine."
        ),
        memory_type="profile", tier="hot", age_days=90,
    ),

    # -- Mechanism-targeted distractors -------------------------------------
    # Each is irrelevant (grade 0 everywhere) but carries the metadata that
    # baits exactly one scoring lever, so leakage names the lever.
    Fixture(
        id="d01_typeboost_grocery",
        text=(
            "Picked up the shopping on the way back and finally replaced the kettle, "
            "which had been making an alarming noise for about a fortnight."
        ),
        # Matched to the activity stratum's victim (f03: journal, hot, 4d), so
        # the type ladder is the only difference. The task_status stratum's
        # victim is warm and older, so that pair gets d13 instead.
        memory_type="conversation", tier="hot", age_days=4,
        role="user", content_kind="experience", distractor_class=TYPE_BOOST_BAIT,
    ),
    Fixture(
        id="d13_typeboost_kitchen",
        text=(
            "Finally got round to descaling the kettle and clearing out the cupboard "
            "above it, which had accumulated three opened bags of the same rice and "
            "a jar of something with no label left on it at all."
        ),
        memory_type="conversation", tier="warm", age_days=9,
        role="user", content_kind="experience", distractor_class=TYPE_BOOST_BAIT,
    ),
    Fixture(
        id="d02_typeboost_weather",
        text=(
            "What changed recently is the weather: it turned overnight, the walk in was "
            "colder than expected, and the heavier coat has come back out of storage a "
            "good few weeks earlier than it usually does."
        ),
        # Age matched to its victim f06 (hot, 3d): the type ladder is then
        # the only difference between them.
        memory_type="conversation", tier="hot", age_days=3,
        role="user", content_kind="experience", distractor_class=TYPE_BOOST_BAIT,
    ),
    # The decay baits are `ingested`, which _NO_DECAY_TYPES exempts from
    # temporal decay entirely, so age costs them nothing however old they get.
    # They carry the factual query's vocabulary ("dense", "combine", "signals")
    # in a sense that has nothing to do with retrieval.
    Fixture(
        id="d03_decay_old_recipe",
        text=(
            "The bread recipe combines a dense flour with a lighter one and scores much "
            "better on a long cold rise. The hybrid loaf keeps its structure for days "
            "rather than going heavy and dense by the second morning."
        ),
        memory_type="ingested", tier="cold", age_days=400, distractor_class=DECAY_BAIT,
    ),
    Fixture(
        id="d04_decay_old_travel",
        text=(
            "The coastal route adds an hour but avoids the tunnel. The signals on that "
            "stretch combine badly with weekend engineering work, so the score for "
            "taking it is dense with caveats and depends on the day chosen."
        ),
        memory_type="ingested", tier="cold", age_days=365, distractor_class=DECAY_BAIT,
    ),
    # The recency baits carry the query's time vocabulary ("past few days",
    # "currently open") so they float ABOVE the buried relevant records on
    # similarity alone. Nothing in them answers anything: that is the point.
    Fixture(
        id="d05_recency_today_errand",
        text=(
            "Over the past few days the errands have piled up. The parcel went to the "
            "collection point this morning and the queue was shorter than it has been, "
            "so at least that one is finished and no longer open."
        ),
        memory_type="conversation", tier="hot", age_days=0,
        role="user", distractor_class=RECENCY_BAIT,
    ),
    Fixture(
        id="d06_recency_today_plant",
        text=(
            "Currently the plant on the windowsill is the thing still open: it has "
            "outgrown the pot and wants moving somewhere with more light, which is "
            "what I have actually been doing with the last hour of the day."
        ),
        memory_type="journal", tier="hot", age_days=0, distractor_class=RECENCY_BAIT,
    ),
    # Long by necessity: it needs high raw term overlap to bait
    # lexical_relevance_bonus while keeping its token Jaccard against the query
    # under the 0.55 echo threshold in _is_echo_or_meta_memory. A short text
    # dense in query terms would be dropped as an echo before it could bait
    # anything.
    Fixture(
        id="d07_lexical_overlap_bait",
        text=(
            "A general note on patterns: patterns in working practice, patterns in "
            "working hours, and how the working week is shown to arrange itself "
            "lately. Patterns show up in scheduling and patterns show up in energy. "
            "The same goes for ranking and scoring generally, where a pipeline of "
            "steps is itself a pattern worth noting and the ranking of the steps "
            "matters as much as the scoring of them. None of this describes any "
            "particular week, any particular task, any particular pipeline, or "
            "anything that actually happened on any given day to anyone."
        ),
        # Matched to its victim in the reflective stratum (f08: journal, warm,
        # 12d). The activity stratum's victim is a different age, so that one
        # gets its own variant below rather than sharing this fixture -- a bait
        # can only be a minimal pair with one victim at a time.
        memory_type="journal", tier="warm", age_days=12, distractor_class=LEXICAL_BAIT,
    ),
    # Same vocabulary-stuffing trick as d07, matched instead to the activity
    # stratum's victim (f05: conversation, warm, 6d).
    Fixture(
        id="d12_lexical_overlap_bait_recent",
        text=(
            "A short note on ranking and scoring in general: the ranking of steps "
            "in any pipeline matters as much as the scoring of them, and a scoring "
            "pipeline that ranks badly is a ranking pipeline that scores badly. "
            "Ranking, scoring, pipeline: three words that travel together and mean "
            "very little on their own. None of this refers to any particular "
            "pipeline, any particular ranking, or any work actually carried out."
        ),
        memory_type="conversation", tier="warm", age_days=6,
        role="user", distractor_class=LEXICAL_BAIT,
    ),
    # The entity boost is worth 0.20 per matched proper noun up to 0.40 -- the
    # single largest lexical term in the system. It only fires when the QUERY
    # carries a proper noun, so the activity stratum names a synthetic project
    # codename that this fixture mentions in an unrelated context.
    Fixture(
        id="d08_entity_name_bait",
        text=(
            "Halloway came up again in the reading, mostly as a footnote about early "
            "cataloguing practice, and the argument made there has not aged especially "
            "well in the decades since."
        ),
        # Matched to its victim f05 (conversation, warm, 6d) so only the
        # entity term distinguishes them.
        memory_type="conversation", tier="warm", age_days=6,
        distractor_class=ENTITY_BAIT,
    ),
    Fixture(
        id="d09_tier_hot_irrelevant",
        text=(
            "The hallway light is still open as a job: the fitting needs replacing, the "
            "spare bulbs do not match the socket, and what is left to finish there is a "
            "trip to the shop that has not happened yet."
        ),
        memory_type="conversation", tier="hot", age_days=8,
        role="user", distractor_class=TIER_BAIT,
    ),
    Fixture(
        id="d10_reflection_weight_bait",
        text=(
            "Across the last stretch there is a general sense of things moving along "
            "steadily, without any single thread standing out as the defining one."
        ),
        memory_type="reflection", tier="hot", age_days=5,
        distractor_class=REFLECTION_WEIGHT_BAIT,
    ),
    Fixture(
        id="d11_experience_kind_bait",
        text=(
            "The kind of work I do on a Sunday is sorting through the box of cables in "
            "the cupboard, and I would prefer to be told beforehand which of them still "
            "connect to anything, rather than finding out one plug at a time."
        ),
        # Age and tier matched to its victim (f11: cold, 95d) so decay and
        # tiering are neutral between them and the experience bonus is the
        # only mechanism that separates the pair.
        memory_type="journal", tier="cold", age_days=95,
        content_kind="experience", distractor_class=EXPERIENCE_BAIT,
    ),
)

FIXTURES_BY_ID: dict[str, Fixture] = {f.id: f for f in FIXTURES}


# ---------------------------------------------------------------------------
# Measured cosines -- frozen
# ---------------------------------------------------------------------------
# Real nomic-embed-text similarity of every fixture text against every query.
# Frozen so the eval runs offline and deterministically; regenerate with
# `python -m tools.retrieval_ablation.regenerate_cosines` after ANY text edit.
# CORPUS_TEXT_DIGEST below is what catches a text edit that forgot to.

MEASURED_COSINES: dict[str, dict[str, float]] = {
    "reflective_work_patterns": {
        "f01_work_decay_trace": 0.533,
        "f02_work_index_backlog": 0.460,
        "f03_work_scoring_journal": 0.517,
        "f04_work_design_note": 0.423,
        "f05_work_assistant_summary": 0.522,
        "f06_work_migration_progress": 0.429,
        "f07_health_sleep_slip": 0.509,
        "f08_health_morning_pattern": 0.613,
        "f09_health_reflection_thread": 0.411,
        "f10_health_routine_intent": 0.465,
        "f11_health_older_reflection": 0.479,
        "f12_state_current_focus": 0.432,
        "f13_state_open_threads": 0.478,
        "f14_state_parked_cleanup": 0.444,
        "f15_state_freeze_window": 0.465,
        "f16_state_old_priority": 0.439,
        "f17_ref_hybrid_retrieval": 0.551,
        "f18_ref_graded_metric": 0.426,
        "f19_ref_cosine_definition": 0.473,
        "f20_ref_tiering_theory": 0.532,
        "f21_ref_chunking_note": 0.438,
        "f22_tool_editor_setup": 0.519,
        "f23_tool_backup_routine": 0.516,
        "f24_tool_local_model": 0.430,
        "f25_tool_old_migration": 0.417,
        "f26_profile_role": 0.476,
        "f27_profile_preference": 0.439,
        "f28_profile_working_style": 0.446,
        "f29_profile_tooling_pref": 0.469,
        "d01_typeboost_grocery": 0.438,
        "d13_typeboost_kitchen": 0.455,
        "d02_typeboost_weather": 0.573,
        "d03_decay_old_recipe": 0.499,
        "d04_decay_old_travel": 0.439,
        "d05_recency_today_errand": 0.490,
        "d06_recency_today_plant": 0.525,
        "d07_lexical_overlap_bait": 0.720,
        "d12_lexical_overlap_bait_recent": 0.449,
        "d08_entity_name_bait": 0.458,
        "d09_tier_hot_irrelevant": 0.517,
        "d10_reflection_weight_bait": 0.502,
        "d11_experience_kind_bait": 0.495,
    },
    "factual_recall_retrieval_theory": {
        "f01_work_decay_trace": 0.478,
        "f02_work_index_backlog": 0.463,
        "f03_work_scoring_journal": 0.434,
        "f04_work_design_note": 0.511,
        "f05_work_assistant_summary": 0.496,
        "f06_work_migration_progress": 0.417,
        "f07_health_sleep_slip": 0.323,
        "f08_health_morning_pattern": 0.316,
        "f09_health_reflection_thread": 0.341,
        "f10_health_routine_intent": 0.271,
        "f11_health_older_reflection": 0.306,
        "f12_state_current_focus": 0.395,
        "f13_state_open_threads": 0.375,
        "f14_state_parked_cleanup": 0.384,
        "f15_state_freeze_window": 0.416,
        "f16_state_old_priority": 0.360,
        "f17_ref_hybrid_retrieval": 0.560,
        "f18_ref_graded_metric": 0.478,
        "f19_ref_cosine_definition": 0.465,
        "f20_ref_tiering_theory": 0.483,
        "f21_ref_chunking_note": 0.487,
        "f22_tool_editor_setup": 0.348,
        "f23_tool_backup_routine": 0.303,
        "f24_tool_local_model": 0.353,
        "f25_tool_old_migration": 0.388,
        "f26_profile_role": 0.452,
        "f27_profile_preference": 0.344,
        "f28_profile_working_style": 0.372,
        "f29_profile_tooling_pref": 0.275,
        "d01_typeboost_grocery": 0.268,
        "d13_typeboost_kitchen": 0.298,
        "d02_typeboost_weather": 0.293,
        "d03_decay_old_recipe": 0.507,
        "d04_decay_old_travel": 0.442,
        "d05_recency_today_errand": 0.298,
        "d06_recency_today_plant": 0.240,
        "d07_lexical_overlap_bait": 0.391,
        "d12_lexical_overlap_bait_recent": 0.460,
        "d08_entity_name_bait": 0.347,
        "d09_tier_hot_irrelevant": 0.244,
        "d10_reflection_weight_bait": 0.302,
        "d11_experience_kind_bait": 0.279,
    },
    "status_state_current_focus": {
        "f01_work_decay_trace": 0.454,
        "f02_work_index_backlog": 0.516,
        "f03_work_scoring_journal": 0.402,
        "f04_work_design_note": 0.364,
        "f05_work_assistant_summary": 0.410,
        "f06_work_migration_progress": 0.496,
        "f07_health_sleep_slip": 0.380,
        "f08_health_morning_pattern": 0.415,
        "f09_health_reflection_thread": 0.417,
        "f10_health_routine_intent": 0.395,
        "f11_health_older_reflection": 0.440,
        "f12_state_current_focus": 0.424,
        "f13_state_open_threads": 0.596,
        "f14_state_parked_cleanup": 0.492,
        "f15_state_freeze_window": 0.470,
        "f16_state_old_priority": 0.537,
        "f17_ref_hybrid_retrieval": 0.378,
        "f18_ref_graded_metric": 0.350,
        "f19_ref_cosine_definition": 0.344,
        "f20_ref_tiering_theory": 0.406,
        "f21_ref_chunking_note": 0.377,
        "f22_tool_editor_setup": 0.408,
        "f23_tool_backup_routine": 0.385,
        "f24_tool_local_model": 0.433,
        "f25_tool_old_migration": 0.411,
        "f26_profile_role": 0.433,
        "f27_profile_preference": 0.365,
        "f28_profile_working_style": 0.452,
        "f29_profile_tooling_pref": 0.385,
        "d01_typeboost_grocery": 0.374,
        "d13_typeboost_kitchen": 0.368,
        "d02_typeboost_weather": 0.446,
        "d03_decay_old_recipe": 0.375,
        "d04_decay_old_travel": 0.371,
        "d05_recency_today_errand": 0.498,
        "d06_recency_today_plant": 0.581,
        "d07_lexical_overlap_bait": 0.389,
        "d12_lexical_overlap_bait_recent": 0.345,
        "d08_entity_name_bait": 0.424,
        "d09_tier_hot_irrelevant": 0.486,
        "d10_reflection_weight_bait": 0.419,
        "d11_experience_kind_bait": 0.442,
    },
    "task_status_migration": {
        "f01_work_decay_trace": 0.437,
        "f02_work_index_backlog": 0.592,
        "f03_work_scoring_journal": 0.412,
        "f04_work_design_note": 0.371,
        "f05_work_assistant_summary": 0.414,
        "f06_work_migration_progress": 0.591,
        "f07_health_sleep_slip": 0.370,
        "f08_health_morning_pattern": 0.471,
        "f09_health_reflection_thread": 0.401,
        "f10_health_routine_intent": 0.417,
        "f11_health_older_reflection": 0.461,
        "f12_state_current_focus": 0.711,
        "f13_state_open_threads": 0.559,
        "f14_state_parked_cleanup": 0.482,
        "f15_state_freeze_window": 0.463,
        "f16_state_old_priority": 0.554,
        "f17_ref_hybrid_retrieval": 0.435,
        "f18_ref_graded_metric": 0.375,
        "f19_ref_cosine_definition": 0.345,
        "f20_ref_tiering_theory": 0.380,
        "f21_ref_chunking_note": 0.359,
        "f22_tool_editor_setup": 0.426,
        "f23_tool_backup_routine": 0.475,
        "f24_tool_local_model": 0.411,
        "f25_tool_old_migration": 0.630,
        "f26_profile_role": 0.375,
        "f27_profile_preference": 0.361,
        "f28_profile_working_style": 0.384,
        "f29_profile_tooling_pref": 0.377,
        "d01_typeboost_grocery": 0.419,
        "d13_typeboost_kitchen": 0.442,
        "d02_typeboost_weather": 0.483,
        "d03_decay_old_recipe": 0.390,
        "d04_decay_old_travel": 0.419,
        "d05_recency_today_errand": 0.549,
        "d06_recency_today_plant": 0.475,
        "d07_lexical_overlap_bait": 0.413,
        "d12_lexical_overlap_bait_recent": 0.390,
        "d08_entity_name_bait": 0.432,
        "d09_tier_hot_irrelevant": 0.514,
        "d10_reflection_weight_bait": 0.502,
        "d11_experience_kind_bait": 0.357,
    },
    "recent_activity_this_week": {
        "f01_work_decay_trace": 0.412,
        "f02_work_index_backlog": 0.427,
        "f03_work_scoring_journal": 0.437,
        "f04_work_design_note": 0.313,
        "f05_work_assistant_summary": 0.400,
        "f06_work_migration_progress": 0.415,
        "f07_health_sleep_slip": 0.477,
        "f08_health_morning_pattern": 0.488,
        "f09_health_reflection_thread": 0.412,
        "f10_health_routine_intent": 0.448,
        "f11_health_older_reflection": 0.455,
        "f12_state_current_focus": 0.402,
        "f13_state_open_threads": 0.425,
        "f14_state_parked_cleanup": 0.462,
        "f15_state_freeze_window": 0.411,
        "f16_state_old_priority": 0.464,
        "f17_ref_hybrid_retrieval": 0.393,
        "f18_ref_graded_metric": 0.314,
        "f19_ref_cosine_definition": 0.314,
        "f20_ref_tiering_theory": 0.428,
        "f21_ref_chunking_note": 0.336,
        "f22_tool_editor_setup": 0.473,
        "f23_tool_backup_routine": 0.527,
        "f24_tool_local_model": 0.382,
        "f25_tool_old_migration": 0.434,
        "f26_profile_role": 0.454,
        "f27_profile_preference": 0.374,
        "f28_profile_working_style": 0.406,
        "f29_profile_tooling_pref": 0.413,
        "d01_typeboost_grocery": 0.439,
        "d13_typeboost_kitchen": 0.410,
        "d02_typeboost_weather": 0.504,
        "d03_decay_old_recipe": 0.392,
        "d04_decay_old_travel": 0.390,
        "d05_recency_today_errand": 0.498,
        "d06_recency_today_plant": 0.565,
        "d07_lexical_overlap_bait": 0.519,
        "d12_lexical_overlap_bait_recent": 0.403,
        "d08_entity_name_bait": 0.393,
        "d09_tier_hot_irrelevant": 0.451,
        "d10_reflection_weight_bait": 0.483,
        "d11_experience_kind_bait": 0.437,
    },
    "recent_tooling_change": {
        "f01_work_decay_trace": 0.491,
        "f02_work_index_backlog": 0.506,
        "f03_work_scoring_journal": 0.472,
        "f04_work_design_note": 0.422,
        "f05_work_assistant_summary": 0.464,
        "f06_work_migration_progress": 0.490,
        "f07_health_sleep_slip": 0.461,
        "f08_health_morning_pattern": 0.477,
        "f09_health_reflection_thread": 0.398,
        "f10_health_routine_intent": 0.411,
        "f11_health_older_reflection": 0.401,
        "f12_state_current_focus": 0.435,
        "f13_state_open_threads": 0.525,
        "f14_state_parked_cleanup": 0.468,
        "f15_state_freeze_window": 0.459,
        "f16_state_old_priority": 0.492,
        "f17_ref_hybrid_retrieval": 0.390,
        "f18_ref_graded_metric": 0.390,
        "f19_ref_cosine_definition": 0.407,
        "f20_ref_tiering_theory": 0.426,
        "f21_ref_chunking_note": 0.400,
        "f22_tool_editor_setup": 0.553,
        "f23_tool_backup_routine": 0.472,
        "f24_tool_local_model": 0.479,
        "f25_tool_old_migration": 0.546,
        "f26_profile_role": 0.423,
        "f27_profile_preference": 0.397,
        "f28_profile_working_style": 0.418,
        "f29_profile_tooling_pref": 0.645,
        "d01_typeboost_grocery": 0.427,
        "d13_typeboost_kitchen": 0.387,
        "d02_typeboost_weather": 0.593,
        "d03_decay_old_recipe": 0.431,
        "d04_decay_old_travel": 0.434,
        "d05_recency_today_errand": 0.503,
        "d06_recency_today_plant": 0.487,
        "d07_lexical_overlap_bait": 0.470,
        "d12_lexical_overlap_bait_recent": 0.397,
        "d08_entity_name_bait": 0.444,
        "d09_tier_hot_irrelevant": 0.469,
        "d10_reflection_weight_bait": 0.437,
        "d11_experience_kind_bait": 0.388,
    },
    "activity_pipeline_work": {
        "f01_work_decay_trace": 0.644,
        "f02_work_index_backlog": 0.493,
        "f03_work_scoring_journal": 0.629,
        "f04_work_design_note": 0.531,
        "f05_work_assistant_summary": 0.622,
        "f06_work_migration_progress": 0.422,
        "f07_health_sleep_slip": 0.384,
        "f08_health_morning_pattern": 0.457,
        "f09_health_reflection_thread": 0.419,
        "f10_health_routine_intent": 0.390,
        "f11_health_older_reflection": 0.477,
        "f12_state_current_focus": 0.412,
        "f13_state_open_threads": 0.417,
        "f14_state_parked_cleanup": 0.400,
        "f15_state_freeze_window": 0.405,
        "f16_state_old_priority": 0.535,
        "f17_ref_hybrid_retrieval": 0.514,
        "f18_ref_graded_metric": 0.565,
        "f19_ref_cosine_definition": 0.413,
        "f20_ref_tiering_theory": 0.476,
        "f21_ref_chunking_note": 0.468,
        "f22_tool_editor_setup": 0.400,
        "f23_tool_backup_routine": 0.434,
        "f24_tool_local_model": 0.415,
        "f25_tool_old_migration": 0.423,
        "f26_profile_role": 0.533,
        "f27_profile_preference": 0.425,
        "f28_profile_working_style": 0.396,
        "f29_profile_tooling_pref": 0.355,
        "d01_typeboost_grocery": 0.359,
        "d13_typeboost_kitchen": 0.438,
        "d02_typeboost_weather": 0.430,
        "d03_decay_old_recipe": 0.408,
        "d04_decay_old_travel": 0.450,
        "d05_recency_today_errand": 0.413,
        "d06_recency_today_plant": 0.331,
        "d07_lexical_overlap_bait": 0.602,
        "d12_lexical_overlap_bait_recent": 0.762,
        "d08_entity_name_bait": 0.692,
        "d09_tier_hot_irrelevant": 0.402,
        "d10_reflection_weight_bait": 0.459,
        "d11_experience_kind_bait": 0.401,
    },
    "default_identity_question": {
        "f01_work_decay_trace": 0.417,
        "f02_work_index_backlog": 0.294,
        "f03_work_scoring_journal": 0.373,
        "f04_work_design_note": 0.322,
        "f05_work_assistant_summary": 0.373,
        "f06_work_migration_progress": 0.348,
        "f07_health_sleep_slip": 0.294,
        "f08_health_morning_pattern": 0.419,
        "f09_health_reflection_thread": 0.322,
        "f10_health_routine_intent": 0.409,
        "f11_health_older_reflection": 0.298,
        "f12_state_current_focus": 0.302,
        "f13_state_open_threads": 0.350,
        "f14_state_parked_cleanup": 0.313,
        "f15_state_freeze_window": 0.299,
        "f16_state_old_priority": 0.383,
        "f17_ref_hybrid_retrieval": 0.410,
        "f18_ref_graded_metric": 0.318,
        "f19_ref_cosine_definition": 0.346,
        "f20_ref_tiering_theory": 0.293,
        "f21_ref_chunking_note": 0.379,
        "f22_tool_editor_setup": 0.401,
        "f23_tool_backup_routine": 0.341,
        "f24_tool_local_model": 0.363,
        "f25_tool_old_migration": 0.293,
        "f26_profile_role": 0.506,
        "f27_profile_preference": 0.567,
        "f28_profile_working_style": 0.449,
        "f29_profile_tooling_pref": 0.464,
        "d01_typeboost_grocery": 0.283,
        "d13_typeboost_kitchen": 0.307,
        "d02_typeboost_weather": 0.344,
        "d03_decay_old_recipe": 0.387,
        "d04_decay_old_travel": 0.410,
        "d05_recency_today_errand": 0.339,
        "d06_recency_today_plant": 0.415,
        "d07_lexical_overlap_bait": 0.483,
        "d12_lexical_overlap_bait_recent": 0.428,
        "d08_entity_name_bait": 0.378,
        "d09_tier_hot_irrelevant": 0.400,
        "d10_reflection_weight_bait": 0.334,
        "d11_experience_kind_bait": 0.523,
    },
}


# ---------------------------------------------------------------------------
# The 8 policy strata
# ---------------------------------------------------------------------------
# Grades only. Cosine is measured, in MEASURED_COSINES below.
#
# Every fixture in a query's contested zone carries an explicit grade, including
# the ones that are genuinely irrelevant. Grading only the items the stratum was
# built around would score every plausible neighbour 0 by omission and punish
# the retriever for surfacing something reasonable.

STRATA: tuple[Stratum, ...] = (
    Stratum(
        name="reflective_work_patterns",
        policy_name="reflective",
        query="what patterns have shown up in how I have been working lately",
        judgments={
            # f09 is the BURIED help case: the synthesized answer, phrased
            # without the query's words, so only the reflective policy's
            # reflection weight (x1.4) can recover it.
            "f09_health_reflection_thread": 3,
            "f08_health_morning_pattern": 3,
            "f03_work_scoring_journal": 2,
            "f11_health_older_reflection": 2,
            "f07_health_sleep_slip": 1,
            "f06_work_migration_progress": 1,
            "f22_tool_editor_setup": 1,
            "f01_work_decay_trace": 1,
            # Baits: reflection weight, lexical overlap on "patterns"/
            # "working", and the experience bonus `prefer_experiences` adds.
            "d10_reflection_weight_bait": 0,
            "d07_lexical_overlap_bait": 0,
            "d11_experience_kind_bait": 0,
            "d05_recency_today_errand": 0,
            "d02_typeboost_weather": 0,
            "d06_recency_today_plant": 0,
            "d09_tier_hot_irrelevant": 0,
            "f17_ref_hybrid_retrieval": 0,
            "f20_ref_tiering_theory": 0,
            "f05_work_assistant_summary": 0,
        },
    ),
    Stratum(
        name="factual_recall_retrieval_theory",
        policy_name="factual_recall",
        query="how does a hybrid retrieval score combine dense and lexical signals",
        judgments={
            "f17_ref_hybrid_retrieval": 3,   # BURIED help case
            "f19_ref_cosine_definition": 2,
            "f18_ref_graded_metric": 2,
            "f21_ref_chunking_note": 1,
            "f04_work_design_note": 1,
            "f05_work_assistant_summary": 1,
            "f20_ref_tiering_theory": 1,
            # Decay bait matters most here: ingested is exempt from temporal
            # decay, so an ancient irrelevant chunk keeps its full weight.
            "d03_decay_old_recipe": 0,
            "d04_decay_old_travel": 0,
            "d08_entity_name_bait": 0,
            "f01_work_decay_trace": 0,
            "f02_work_index_backlog": 0,
            "f26_profile_role": 0,
        },
    ),
    Stratum(
        name="status_state_current_focus",
        policy_name="status_state",
        query="what is my current focus and what is still open",
        judgments={
            "f12_state_current_focus": 3,    # BURIED help case
            "f13_state_open_threads": 3,
            "f15_state_freeze_window": 2,
            "f06_work_migration_progress": 2,
            "f14_state_parked_cleanup": 1,
            "f16_state_old_priority": 0,
            # Recency bait: this policy carries recency_bias 0.8, so a
            # same-day irrelevant record gets a large amplified boost.
            "d05_recency_today_errand": 0,
            "d06_recency_today_plant": 0,
            "d09_tier_hot_irrelevant": 0,
            "f02_work_index_backlog": 0,
            "f01_work_decay_trace": 0,
            "f28_profile_working_style": 0,
        },
    ),
    Stratum(
        name="task_status_migration",
        policy_name="task_status",
        query="where did the migration work get to and what is left to finish",
        judgments={
            "f06_work_migration_progress": 3,   # BURIED help case
            "f13_state_open_threads": 3,
            "f02_work_index_backlog": 2,
            "f12_state_current_focus": 2,
            "f25_tool_old_migration": 1,
            "f14_state_parked_cleanup": 1,
            "d09_tier_hot_irrelevant": 0,
            "d01_typeboost_grocery": 0,
            "d13_typeboost_kitchen": 0,
            "f16_state_old_priority": 0,
            "d05_recency_today_errand": 0,
            "d10_reflection_weight_bait": 0,
            "d02_typeboost_weather": 0,
        },
    ),
    Stratum(
        name="recent_activity_this_week",
        policy_name="recent_activity",
        query="what have I actually been doing over the past few days",
        judgments={
            "f07_health_sleep_slip": 3,
            "f06_work_migration_progress": 3,
            "f01_work_decay_trace": 2,
            "f24_tool_local_model": 2,      # BURIED help case
            "f23_tool_backup_routine": 2,
            "f12_state_current_focus": 1,
            "f08_health_morning_pattern": 1,
            "f22_tool_editor_setup": 1,
            "f14_state_parked_cleanup": 1,
            # Recency and type-boost baits both bite hardest here: this policy
            # has the highest recency_bias (1.2) in the system.
            "d05_recency_today_errand": 0,
            "d06_recency_today_plant": 0,
            "d02_typeboost_weather": 0,
            "d07_lexical_overlap_bait": 0,
            "d10_reflection_weight_bait": 0,
            "f16_state_old_priority": 0,
        },
    ),
    Stratum(
        name="recent_tooling_change",
        policy_name="recent",
        query="what changed recently in the local setup and tooling",
        judgments={
            "f24_tool_local_model": 3,      # BURIED help case
            "f23_tool_backup_routine": 2,
            "f22_tool_editor_setup": 2,
            "f02_work_index_backlog": 1,
            "f25_tool_old_migration": 1,
            "f29_profile_tooling_pref": 1,
            "d02_typeboost_weather": 0,
            "d06_recency_today_plant": 0,
            "f13_state_open_threads": 0,
            "d05_recency_today_errand": 0,
            "f16_state_old_priority": 0,
            "f01_work_decay_trace": 0,
            "f06_work_migration_progress": 0,
        },
    ),
    Stratum(
        name="activity_pipeline_work",
        policy_name="activity",
        # Carries a proper noun deliberately: the entity boost (0.20 each,
        # capped at 0.40) only fires when the query contains one, and it is
        # the largest single lexical term in the system. The name is not at
        # the start of the query because _extract_entity_names skips a match
        # in sentence-initial position.
        query="in the Halloway work, what went into the ranking and scoring pipeline",
        judgments={
            "f01_work_decay_trace": 3,      # BURIED help case, entity-rescued
            "f03_work_scoring_journal": 3,
            "f04_work_design_note": 2,
            "f05_work_assistant_summary": 2,
            "f02_work_index_backlog": 1,
            "f18_ref_graded_metric": 1,
            "f26_profile_role": 1,
            "f17_ref_hybrid_retrieval": 1,
            # d08 shares the codename in an unrelated context: the test of
            # whether the entity boost promotes a mere name match.
            "d08_entity_name_bait": 0,
            "d07_lexical_overlap_bait": 0,
            "d01_typeboost_grocery": 0,
            "d12_lexical_overlap_bait_recent": 0,
            "f16_state_old_priority": 0,
        },
    ),
    Stratum(
        name="default_identity_question",
        policy_name="default",
        query="what kind of work do I do and how do I prefer to be told things",
        judgments={
            "f26_profile_role": 3,
            "f27_profile_preference": 3,
            "f28_profile_working_style": 2,
            "f29_profile_tooling_pref": 2,
            "f08_health_morning_pattern": 1,
            "d11_experience_kind_bait": 0,
            "d08_entity_name_bait": 0,
            "d07_lexical_overlap_bait": 0,
            "f01_work_decay_trace": 0,
            "d06_recency_today_plant": 0,
            "f17_ref_hybrid_retrieval": 0,
            "d04_decay_old_travel": 0,
        },
    ),
)

STRATA_BY_NAME: dict[str, Stratum] = {s.name: s for s in STRATA}


# ---------------------------------------------------------------------------
# Designed bait / stratum pairs
# ---------------------------------------------------------------------------
# Which baits each stratum is actually built to test. Declared rather than
# inferred from the judgments, because the two are different questions: a grade
# says whether a record is relevant, this says whether the corpus claims the
# pair is a test of a lever. Every fixture competes in every stratum, so a bait
# turns up in strata it was never designed for; that is a result about the
# pipeline, not a construction error, and the reachability test must not force
# it to be tuned away.
#
# Deliberately absent: recency baits in the recency-biased strata
# (recent_activity, recent_tooling, status_state). When the query IS "what
# happened lately", freshness is the correct signal, so a fresh irrelevant
# record has to be excluded on content -- ablating recency there removes as
# much from the right answer as from the bait, and the measured differential is
# near zero. Baiting recency in those strata would be asking the lever to do
# something it is not for.
BAIT_TARGETS: dict[str, tuple[str, ...]] = {
    "reflective_work_patterns": (
        "d10_reflection_weight_bait",
        "d07_lexical_overlap_bait",
        "d11_experience_kind_bait",
    ),
    "factual_recall_retrieval_theory": (
        "d03_decay_old_recipe",
        "d04_decay_old_travel",
    ),
    "status_state_current_focus": (
        "d09_tier_hot_irrelevant",
    ),
    "task_status_migration": (
        "d09_tier_hot_irrelevant",
        "d13_typeboost_kitchen",
    ),
    "recent_activity_this_week": (
        "d02_typeboost_weather",
    ),
    "recent_tooling_change": (
        "d06_recency_today_plant",
    ),
    "activity_pipeline_work": (
        "d12_lexical_overlap_bait_recent",
        "d08_entity_name_bait",
        "d01_typeboost_grocery",
    ),
    "default_identity_question": (
        "d11_experience_kind_bait",
        "d08_entity_name_bait",
    ),
}


# ---------------------------------------------------------------------------
# Text digest
# ---------------------------------------------------------------------------

def corpus_text_digest() -> str:
    """Digest of every fixture text and stratum query.

    MEASURED_COSINES is frozen, so editing a text without re-running
    `regenerate_cosines` would leave the corpus scoring the OLD wording while
    the eval reports the new one -- a silent, invisible divergence between what
    the fixtures say and what the numbers mean. The test suite pins this digest
    against the constant below, so that edit fails loudly instead.
    """
    import hashlib

    parts: list[str] = []
    for fixture in FIXTURES:
        parts.append(f"{fixture.id}\x00{fixture.text}")
    for stratum in STRATA:
        parts.append(f"{stratum.name}\x00{stratum.query}")
    return hashlib.sha256("\x1e".join(parts).encode("utf-8")).hexdigest()


CORPUS_TEXT_DIGEST = "d94e46737d3123e029113252328a064f7cabd30b361c80d86111edda3a6fa219"
