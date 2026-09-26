"""
tests/test_incident_reproduction.py

Permanent regression suite for the three retrieval incidents that have real
production provenance, plus the arm comparison that ADR-044 deferred.

Why this exists
---------------
ADR-044 settled the score composition contract but explicitly did not settle
role. Role is the one capability that survived a second-owner audit -- on a
corpus that is almost entirely conversation records with role metadata on
every row and roughly half of them assistant turns, the combined -0.45 role
penalty is the only mechanism that demotes a plain assistant turn, because the
hard filters in semantic_search match literal meta-markers only. It is also the
term with the worst evidence: the only measurement anyone has says removing its
retrieval-stage half IMPROVED ranked nDCG.

So the question is not whether the capability is real. It is whether it needs
to live in score space at all, or whether a hard predicate does the same job at
zero scoring-budget cost. That is what the four arms below measure.

What makes this measurable without relevance labels
---------------------------------------------------
Correctness here is a CONSTRUCTION FACT, not a graded judgement. Each fixture
set is authored so that exactly one record contains the answer and exactly one
is the decoy the incident was about. "Did the wrong record reach the model" and
"did the right one" are then yes/no questions with no annotator in the loop,
which is why this suite is immune to every labelling objection raised against
the 42-fixture ablation corpus.

Measured at the model-visible window
------------------------------------
Not at the service layer. `PromptBuilder._build_context_section` slices
non-profile memory to four (`prompt_builder.py:979`) after the service layer
has already applied its own limit, so a record can win retrieval and still
never reach the model. The endpoint here is the rendered section text: a
fixture "reached the model" if its marker appears in what the prompt builder
produced.

Both scoring stages are exercised, in the shipped order. The retrieval stage
is reproduced by calling the same four adjustment functions on each fixture
in the same sequence `semantic_search` uses (`semantic_search.py:79-84`)
rather than by seeding a store and issuing a real search: a real search would
require live embeddings, and the cosine it returned would be a property of the
embedder rather than a controlled input. Fixture cosines are stipulated so the
decoy starts ahead by construction, which is the condition each incident
needs. Everything downstream of that -- the authorship gate, the ranker, the
decay, the slice, the label rendering -- is the shipped code.

The first version of this runner skipped the retrieval stage and constructed
scored items directly. Three of the four constant piles and the entity boost
live at that stage, so they never fired and the arm patches were inert. That
is recorded here because the failure mode was silent: the suite ran green on
the arms it could not actually distinguish.

Vault privacy
-------------
Every fixture is synthetic and generic. The incidents involved a family
relationship and several named pets; none of that appears here, and none of it
needs to. The mechanisms are about record authorship and entity matching, not
about any particular subject, so the fixtures use invented entity tokens and
neutral domain content. No vault text, no real names (CLAUDE.md Vault Privacy
Rule).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from unittest.mock import patch

import pytest

import src.retrieval.semantic_search as ss
from src.context.models import ContextItem, ContextPacket
from src.context.ranker import ContextRanker
from src.llm.prompt_builder import PromptBuilder

# ---------------------------------------------------------------------------
# Fixtures. Markers are what the endpoint searches for, so they must be
# distinctive and must not collide across records.
# ---------------------------------------------------------------------------

MARK_RIGHT = "ZZRIGHTZZ"
MARK_WRONG = "ZZWRONGZZ"


def _item(
    item_id: str,
    content: str,
    *,
    memory_type: str = "conversation",
    role: str | None = None,
    content_kind: str | None = None,
    authorship: str = "first_person",
    score: float = 0.5,
    timestamp: str = "2026-09-01T12-00-00",
) -> ContextItem:
    metadata: dict = {"raw_score": score}
    if role is not None:
        metadata["role"] = role
    if content_kind is not None:
        metadata["content_kind"] = content_kind
    return ContextItem(
        id=item_id,
        store_id=item_id,
        content=content,
        source="chat",
        item_type=memory_type,
        memory_type=memory_type,
        score=score,
        timestamp=timestamp,
        tags=[],
        metadata=metadata,
        authorship=authorship,
    )


@dataclass(frozen=True)
class Incident:
    key: str
    provenance: str
    query: str
    items: tuple
    note: str
    endpoint: str  # "presence" or "order" -- see below


def _self_echo() -> Incident:
    """CHANGELOG v0.10.1, commit 8672ce0.

    Ember attributed her own prior responses back to the user as things they
    had said. The structural condition: an assistant turn restates the user's
    question in denser, more on-topic vocabulary, so it beats the user's own
    record on raw cosine. The decoy is the assistant turn; the answer is in the
    user's own words.
    """
    query = "what did i decide about the deployment cadence"
    return Incident(
        key="self_echo",
        provenance="8672ce0 / CHANGELOG v0.10.1",
        query=query,
        items=(
            # Decoy: assistant restatement. Higher cosine by construction --
            # it repeats the query's vocabulary more densely than the user did.
            _item(
                "echo_assistant",
                f"{MARK_WRONG} You decided about the deployment cadence that the "
                "deployment cadence should be weekly, and we discussed the "
                "deployment cadence at length including cadence tradeoffs.",
                role="assistant",
                content_kind="answer",
                authorship="mixed",
                score=0.72,
            ),
            # The answer, in the user's own words, phrased less densely.
            _item(
                "echo_user",
                f"{MARK_RIGHT} i think we should ship on a two week rhythm "
                "instead, it gives room to actually finish the testing pass "
                "before anything goes out.",
                role="user",
                content_kind="user_content",
                score=0.58,
            ),
            *[
                _item(f"echo_filler_{i}", f"an unrelated working note number {i} "
                      "with enough length to clear the short-content floor",
                      role="user", content_kind="user_content", score=0.50 - i * 0.01)
                for i in range(5)
            ],
        ),
        note="assistant turn outranks the user's own record on cosine",
        # Presence is the harm. A retrieved assistant turn is rendered
        # "[Ember said ...]" and is available for the model to attribute back
        # to the user regardless of where it sits in the window.
        endpoint="presence",
    )


def _relational_contamination() -> Incident:
    """UAT-005, commit f9f5dda.

    A relational query grounded against third-party imported content instead of
    the user's own records, and the model synthesised a relationship from it.
    The structural condition: a possessive kinship query where imported
    third-party content carries the kinship phrase and outscores the personal
    record.
    """
    query = "what does my sibling usually bring to these things"
    return Incident(
        key="relational_contamination",
        provenance="f9f5dda / UAT-005",
        query=query,
        items=(
            # Decoy: imported content carrying the kinship phrase, not about
            # this user. Tagged mixed, which is what classify_authorship
            # actually assigns to an imported assistant turn -- multiplier
            # 0.3. It was third_party (0.0) until #218 retired that class,
            # and while it was, this fixture exercised a value with zero rows
            # in production: the decoy was held out by a mechanism that could
            # not have fired on real data.
            _item(
                "rel_imported",
                f"{MARK_WRONG} my sibling usually brings a casserole to these "
                "things, and my sibling always says that is what my sibling is "
                "known for at every gathering.",
                role="assistant",
                authorship="mixed",
                score=0.74,
            ),
            # The answer: the user's own record, lower cosine.
            _item(
                "rel_personal",
                f"{MARK_RIGHT} they turned up with the folding chairs again, "
                "which is honestly more useful than anything anyone else "
                "carried in that afternoon.",
                role="user",
                content_kind="user_content",
                authorship="first_person",
                score=0.55,
            ),
            *[
                _item(f"rel_filler_{i}", f"an unrelated note number {i} about "
                      "scheduling that has nothing to do with the question",
                      role="user", content_kind="user_content",
                      score=0.50 - i * 0.01)
                for i in range(5)
            ],
        ),
        note="imported third-party content answers a possessive kinship query",
        # Presence is the harm. UAT-005 was the model synthesising a
        # relationship out of third-party content that was in the packet at
        # all; it did not need to be first.
        endpoint="presence",
    )


def _entity_confusion() -> Incident:
    """B-NAMED-001, commit a89c7d0.

    A query naming one entity returned a different entity's record, because
    the named token carried only a small term-hit bonus against much larger
    embedding variance. Entity tokens here are invented.
    """
    query = "what did Corvin turn out to be"
    return Incident(
        key="entity_confusion",
        provenance="a89c7d0 / B-NAMED-001",
        query=query,
        items=(
            # Decoy: a different entity, higher cosine. Emotionally weighted
            # content scored above the name match in the original incident.
            _item(
                "ent_wrong",
                f"{MARK_WRONG} Tamsin turned out to be the difficult one that "
                "year and it was genuinely hard to work out what was going on "
                "for a long stretch of months.",
                role="user",
                content_kind="user_content",
                score=0.76,
            ),
            # The answer: names the queried entity, lower cosine.
            _item(
                "ent_right",
                f"{MARK_RIGHT} Corvin turned out to be a fairly straightforward "
                "case in the end, once the right people had actually looked at "
                "the whole thing properly.",
                role="user",
                content_kind="user_content",
                score=0.61,
            ),
            *[
                _item(f"ent_filler_{i}", f"an unrelated record number {i} with "
                      "no entity name in it at all, just ordinary content",
                      role="user", content_kind="user_content",
                      score=0.55 - i * 0.01)
                for i in range(5)
            ],
        ),
        note="a different entity's record outranks the named one on cosine",
        # Order is the harm, and presence would be the wrong endpoint. The
        # incident was a query about one entity RETURNING another entity's
        # record -- displacement, not contamination. Both records legitimately
        # belong in a window about that topic; what went wrong is which one
        # came first. Using presence here would mark every arm FAIL and
        # measure the window size rather than the mechanism.
        endpoint="order",
    )


INCIDENTS = (_self_echo(), _relational_contamination(), _entity_confusion())
INCIDENTS_BY_KEY = {i.key: i for i in INCIDENTS}


# ---------------------------------------------------------------------------
# Arms
# ---------------------------------------------------------------------------
#
# A_REDUCED strips the query-independent constant piles AND the entity boost,
# so the two arms built on it each add back exactly one mechanism and the
# comparison is clean. Documented here because "reduced" is ambiguous on its
# own and a later reader should not have to infer the scope:
#
#   removed : memory_type_adjustment, source_quality_adjustment (which carries
#             the -0.20 role term), query_intent_adjustment,
#             _score_memory_item's additive pile (which carries the -0.25 role
#             term), and the named-entity boost
#   kept    : raw cosine, the lexical term-hit bonus, hard content filters,
#             ADR-018 type gating, the authorship multiplier, tier, decay
#
# The authorship multiplier is kept in every arm. It is a gate rather than a
# class constant, ADR-044 scopes it out of the reduction, and keeping it is
# what lets the relational incident measure the authorship regression rather
# than the role pile.


@contextlib.contextmanager
def _no_constant_piles():
    with patch.object(ss, "memory_type_adjustment", lambda *a, **k: 0.0), \
         patch.object(ss, "source_quality_adjustment", lambda *a, **k: 0.0), \
         patch.object(ss, "query_intent_adjustment", lambda *a, **k: 0.0), \
         patch.object(ContextRanker, "_score_memory_item", lambda self, item: item):
        yield


@contextlib.contextmanager
def _no_entity_boost():
    with patch.object(ss, "_extract_entity_names", lambda *a, **k: []):
        yield


@contextlib.contextmanager
def _arm(name: str):
    """Yield a role-predicate flag alongside the patched scoring context.

    The predicate is applied by the runner rather than patched in, because
    `should_exclude_result` takes content only (`semantic_search.py:465`) and
    has no metadata to test. A real implementation would be a WHERE clause on
    the `authorship` column, which exists; filtering the candidate list stands
    in for that here.
    """
    if name == "shipped":
        yield False
    elif name == "reduced":
        with _no_constant_piles(), _no_entity_boost():
            yield False
    elif name == "reduced+role_predicate":
        with _no_constant_piles(), _no_entity_boost():
            yield True
    elif name == "reduced+entity_boost":
        with _no_constant_piles():
            yield False
    else:  # pragma: no cover
        raise ValueError(name)


ARMS = ("shipped", "reduced", "reduced+role_predicate", "reduced+entity_boost")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _model_visible_text(incident: Incident, arm: str) -> str:
    """Render what the model would actually see, for one incident under one arm.

    Deliberately exercises the real ranker, the real authorship multiplier and
    the real prompt builder, so the [:4] slice and the label rendering are the
    shipped ones rather than a copy that can drift.
    """
    with _arm(arm) as role_predicate:
        normalized_query = ss.normalize_text(incident.query)
        query_terms = ss.extract_query_terms(normalized_query)

        items = []
        for i in incident.items:
            metadata = i.metadata or {}
            raw_score = float(metadata.get("raw_score", i.score))
            normalized_content = ss.normalize_text(i.content)

            # semantic_search.py:79-84, same functions, same order. Patched
            # per arm, so this is where the constant piles and the entity
            # boost are actually exercised.
            score = raw_score
            score += ss.lexical_relevance_bonus(
                normalized_query, query_terms, normalized_content,
                raw_query=incident.query,
            )
            score += ss.memory_type_adjustment(i.memory_type)
            score += ss.source_quality_adjustment(normalized_content, metadata)
            score += ss.query_intent_adjustment(
                normalized_query, i.memory_type, normalized_content,
            )

            item = _item(
                i.id, i.content,
                memory_type=i.memory_type,
                role=metadata.get("role"),
                content_kind=metadata.get("content_kind"),
                authorship=i.authorship,
                score=score,
                timestamp=i.timestamp,
            )
            item.metadata["raw_score"] = raw_score
            items.append(item)

        if role_predicate:
            # Stand-in for a WHERE clause: assistant-authored conversation
            # never becomes a candidate at all.
            items = [
                i for i in items
                if not (i.memory_type == "conversation"
                        and (i.metadata or {}).get("role") == "assistant")
            ]

        ranker = ContextRanker()

        # Apply the two stages that carry the mechanisms under test, in the
        # shipped order: authorship gate, then rank.
        items = ranker.apply_authorship_scoring(items, incident.query)
        ranked, _ = ranker.rank(items, [])

        packet = ContextPacket(
            user_message=incident.query,
            memory_items=ranked,
            reflection_items=[],
            state_items=[],
            task_items=[],
            web_items=[],
        )
        builder = PromptBuilder()
        return builder._build_context_section(packet)


def _reached(incident: Incident, arm: str) -> tuple[bool, bool]:
    """(right record reached the model, wrong record reached the model)."""
    text = _model_visible_text(incident, arm)
    return (MARK_RIGHT in text, MARK_WRONG in text)


def _verdict(incident: Incident, arm: str) -> tuple[bool, bool, bool]:
    """(answer reached, decoy reached, passed).

    The pass condition depends on what the incident actually was. For the two
    contamination incidents the decoy must not reach the model at all. For the
    displacement incident the decoy may appear -- it must not appear FIRST.
    Forcing one endpoint across all three would measure the slice width rather
    than the mechanism under test.
    """
    text = _model_visible_text(incident, arm)
    right_at = text.find(MARK_RIGHT)
    wrong_at = text.find(MARK_WRONG)
    right, wrong = right_at != -1, wrong_at != -1

    if incident.endpoint == "presence":
        return right, wrong, (right and not wrong)
    if incident.endpoint == "order":
        ordered = right and (not wrong or right_at < wrong_at)
        return right, wrong, ordered
    raise ValueError(incident.endpoint)  # pragma: no cover


# ---------------------------------------------------------------------------
# The shipped arm must pass all three. These are the regression guards.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", [i.key for i in INCIDENTS])
def test_shipped_suppresses_the_decoy(key: str) -> None:
    """The incident does not recur on the shipped pipeline.

    If one of these starts failing, the corresponding production incident has
    regressed. Each names its own commit so the failure is traceable.
    """
    incident = INCIDENTS_BY_KEY[key]
    right, _wrong, passed = _verdict(incident, "shipped")
    assert right, (
        f"{incident.provenance}: the correct record did not reach the "
        "model-visible window on the shipped pipeline"
    )
    assert passed, (
        f"{incident.provenance}: incident recurs on the shipped pipeline "
        f"({incident.endpoint} endpoint) -- {incident.note}"
    )


# ---------------------------------------------------------------------------
# Construction guards. A fixture set that does not reproduce the condition
# would make every arm result meaningless.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", [i.key for i in INCIDENTS])
def test_decoy_outranks_the_answer_on_raw_cosine(key: str) -> None:
    incident = INCIDENTS_BY_KEY[key]
    decoy = next(i for i in incident.items if MARK_WRONG in i.content)
    answer = next(i for i in incident.items if MARK_RIGHT in i.content)
    assert decoy.score > answer.score, (
        "the fixture does not reproduce the incident: the decoy must start "
        "ahead of the answer on similarity, or there is nothing to fix"
    )


@pytest.mark.parametrize("key", [i.key for i in INCIDENTS])
def test_more_candidates_than_the_model_visible_window(key: str) -> None:
    # Four is the slice at prompt_builder.py:979. With four or fewer
    # candidates every record reaches the model and the endpoint is vacuous.
    assert len(INCIDENTS_BY_KEY[key].items) > 4


def test_relational_query_is_recognised_as_relational():
    # The authorship multiplier is a no-op on non-relational queries, so if
    # this classifier stops matching, the relational incident silently stops
    # testing anything.
    from src.context.policies import _matches_relational_query

    assert _matches_relational_query(INCIDENTS_BY_KEY["relational_contamination"].query)


def test_entity_token_is_extractable_from_the_query():
    # Same reasoning for the entity incident: the boost only fires when the
    # query yields a capitalized non-sentence-initial token.
    names = ss._extract_entity_names(INCIDENTS_BY_KEY["entity_confusion"].query)
    assert names, "the entity fixture's query yields no entity token"


# ---------------------------------------------------------------------------
# The arm comparison. This is the measurement ADR-044 deferred.
# ---------------------------------------------------------------------------

def test_role_predicate_suppresses_self_echo_without_the_role_pile() -> None:
    """The load-bearing result.

    If the predicate arm passes an incident that the reduced arm fails, the
    -0.45 role pile is replaceable by a hard predicate at zero scoring-budget
    cost, and ADR-044's deferred decision resolves toward selection rather
    than scoring.
    """
    incident = INCIDENTS_BY_KEY["self_echo"]

    _r, wrong_reduced, _p = _verdict(incident, "reduced")
    right_pred, wrong_pred, _pp = _verdict(incident, "reduced+role_predicate")

    assert wrong_reduced, (
        "removing the role pile no longer lets the assistant turn through; "
        "the self-echo capability has acquired a second owner and this "
        "comparison needs rebuilding"
    )
    assert not wrong_pred, "the role predicate failed to suppress the decoy"
    assert right_pred, "the role predicate suppressed the answer as well"


def test_entity_boost_alone_resolves_entity_confusion() -> None:
    incident = INCIDENTS_BY_KEY["entity_confusion"]

    _r, _w, passed_reduced = _verdict(incident, "reduced")
    right_ent, _we, passed_ent = _verdict(incident, "reduced+entity_boost")

    assert not passed_reduced, (
        "the entity fixture no longer reproduces without the boost"
    )
    assert passed_ent, "the entity boost failed to rank the named entity first"
    assert right_ent


# ---------------------------------------------------------------------------
# The authorship regression. Not an arm question -- a live defect.
# ---------------------------------------------------------------------------

def test_imported_assistant_turns_classify_as_mixed_not_third_party() -> None:
    """Pins the #187 regression against f9f5dda so it cannot be forgotten.

    f9f5dda established third_party (multiplier 0.0) as a hard exclusion for
    imported assistant content on relational queries. The prior-substrate
    reclassification recomputed authorship through classify_authorship, which
    has no third_party branch at all -- it returns mixed (0.3) for
    conversation + role=assistant. The exclusion became a soft discount.

    This test asserts the CURRENT behaviour, so it fails the day someone
    restores the exclusion, at which point the assertion flips and this
    docstring is the record of why.
    """
    from src.memory.authorship import classify_authorship

    assert classify_authorship("conversation", "chatgpt", {"role": "assistant"}) == "mixed"


def test_mixed_authorship_does_not_exclude_on_a_relational_query() -> None:
    """The consequence of the above, at the ranker.

    0.3 rather than 0.0 means imported assistant content still competes on a
    relational query -- exactly the class of query UAT-005 was about.
    """
    ranker = ContextRanker()
    items = [
        _item("imported", "some imported assistant content about my sibling "
              "that is long enough to clear the content floor",
              role="assistant", authorship="mixed", score=0.8),
    ]
    scored = ranker.apply_authorship_scoring(
        items, INCIDENTS_BY_KEY["relational_contamination"].query
    )
    assert scored[0].score > 0.0, (
        "authorship=mixed is no longer competing; if third_party has been "
        "restored for imported assistant turns, update this test and #211"
    )
    assert scored[0].score == pytest.approx(0.8 * 0.3)


def test_authorship_regression_reaches_the_model_visible_window() -> None:
    """The regression measured where it matters, not arithmetically.

    The relational fixture declares its decoy `third_party`, which is what
    f9f5dda assigned imported assistant content and what the incident was
    fixed with. Production no longer has that value anywhere: the
    prior-substrate reclassification recomputed authorship through
    `classify_authorship`, which has no third_party branch, so every imported
    assistant turn is now `mixed` and the multiplier moved 0.0 -> 0.3.

    Re-runs the same incident with the decoy at production's current value.
    This is the one to watch. Today the rest of the stack still holds the
    line, so the decoy is suppressed anyway and this passes. The day that
    stops being true, UAT-005 is live again and this fails first.
    """
    incident = INCIDENTS_BY_KEY["relational_contamination"]
    assert _verdict(incident, "shipped")[2], "the pre-reclassification fixture should pass"

    production_shape = Incident(
        key=incident.key,
        provenance=incident.provenance,
        query=incident.query,
        items=tuple(
            _item(
                i.id, i.content,
                memory_type=i.memory_type,
                role=(i.metadata or {}).get("role"),
                content_kind=(i.metadata or {}).get("content_kind"),
                # The only change: what the reclassification left behind.
                authorship=("mixed" if i.authorship == "third_party" else i.authorship),
                score=(i.metadata or {}).get("raw_score", i.score),
                timestamp=i.timestamp,
            )
            for i in incident.items
        ),
        note=incident.note,
        endpoint=incident.endpoint,
    )

    assert _verdict(production_shape, "shipped")[2], (
        "UAT-005 recurs at the model-visible window with authorship=mixed. "
        "The 0.0 -> 0.3 change is no longer masked by the rest of the stack; "
        "the hard exclusion needs restoring."
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def test_emit_arm_matrix(capsys) -> None:
    """Print the full arm x incident matrix. Asserts nothing on its own.

    Kept as a test rather than a script so the numbers in any write-up can be
    regenerated with pytest -s and cannot drift from the suite silently.
    """
    rows = []
    for incident in INCIDENTS:
        for arm in ARMS:
            right, wrong, passed = _verdict(incident, arm)
            rows.append((incident.key, arm, right, wrong,
                         "PASS" if passed else "FAIL"))

    with capsys.disabled():
        print("\n\nINCIDENT REPRODUCTION -- model-visible window (after the [:4] slice)")
        print("endpoint  presence = decoy must not reach the model at all")
        print("          order    = decoy may reach it, must not rank ahead")
        print()
        hdr = (f"{'incident':26} {'endpoint':9} {'arm':24} "
               f"{'answer':>7} {'decoy':>7}  verdict")
        print(hdr)
        print("-" * len(hdr))
        last = None
        for key, arm, right, wrong, verdict in rows:
            if last and last != key:
                print()
            last = key
            ep = INCIDENTS_BY_KEY[key].endpoint
            print(f"{key:26} {ep:9} {arm:24} "
                  f"{str(right):>7} {str(wrong):>7}  {verdict}")

    assert rows
