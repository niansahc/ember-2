"""
tests/test_incident_reproduction.py

Permanent regression suite for the retrieval incidents that have real production
provenance, plus the arm comparison that ADR-044 deferred.

Three of them are closed defects and are guarded here. The fourth and fifth are
#207, which is OPEN, and it is different in three ways that are called out where
they bite rather than hidden behind the shared machinery.

Its provenance is an issue rather than a fix commit, because it had never been
reproduced. What the reproduction found is that #207's open question -- did the
successor lose on score or was it never a candidate -- has two answers depending
on one property of the successor, and neither is "it lost on score":

  user-authored successor:      delivered. Nothing between retrieval and the
                                render removes it, so it is a plain regression
                                guard and carries no xfail.
  assistant-authored successor: removed by the role predicate before selection,
                                and the freed slot is taken by an unrelated
                                filler. That one carries a strict xfail naming
                                the issue that owns it, because the removal is
                                shipped policy under ADR-044 4a rather than a
                                defect in this suite -- and strict means an
                                unexpected pass fails too, so the day that
                                policy changes this suite says so rather than
                                going quietly green.

It runs through the REAL `ContextService._build_context` over a stubbed
retriever, not the hand-assembled stage chain the other three use. #207's open
question is which stage removes the successor, and the stage chain does not run
the type gate, the relevance gate, the content filters, dedup, the policy limit
or diversity selection. The relevance gate cannot be called at all -- it is
inline in `_build_context` with an undeclared second threshold -- so a ladder
that reproduced it would be restating shipped logic, which is the mistake
recorded at the role-predicate call site below.

And it is measured twice, by two instruments that must agree: the shipped trace
harness `capture_query`, which emits CandidateTrace's per-stage flags and aborts
if its model of the stages disagrees with the real ones, and the rendered prompt
itself.

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
import copy
import datetime as dt
from dataclasses import dataclass
from unittest.mock import patch

import pytest

import src.context.role_predicate as role_predicate_module
import src.retrieval.semantic_search as ss
from src.context.models import ContextItem, ContextPacket
from src.context.policies import ContextPolicy
from src.context.ranker import ContextRanker
from src.context.service import ContextService
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


def _days_ago(days: int) -> str:
    """A vault-format timestamp, relative to now rather than a literal.

    The three original fixtures carry a fixed literal, which is fine for them:
    nothing they measure depends on an age. #207 is an age incident -- the
    predecessor's 74 weeks against the successor's few days is the condition --
    and the recency arm of the prior, the `[recorded ...]` label and the
    confidence band are all computed from the gap to now. A literal would let
    the fixture drift into a different recency bucket and a different confidence
    band as the repo ages, which is a condition-dependent test by construction.
    """
    return (dt.datetime.now() - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H-%M-%S")


@dataclass(frozen=True)
class Incident:
    key: str
    provenance: str
    query: str
    items: tuple
    note: str
    endpoint: str  # "presence" or "order" -- see below

    # Set when the shipped arm is NOT expected to pass, naming the issue that
    # owns the failure. The three original incidents leave it None: they are
    # closed defects and a failure there is a regression. #207 is open, so a
    # failure there is the measurement, and `_params` turns this into a strict
    # xfail so the suite reports it without going red on a known-open issue --
    # and fails loudly if it ever starts passing silently.
    open_issue: str | None = None

    # Which runner produces the model-visible text. "stage_chain" is the
    # original hand-assembled sequence; "pipeline" runs the real
    # ContextService._build_context over a stubbed retriever. See
    # `_pipeline_visible_text` for why #207 needs the second one.
    via: str = "stage_chain"


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


def _superseded_fact(successor_role: str) -> Incident:
    """#207, open. The first fixture here whose provenance is an issue.

    A query about a current biographical fact returned a record roughly 74 weeks
    old and the response asserted its content in the present tense. A later
    record in the same vault contradicts that fact and was not delivered. #207
    is explicit that it does not establish WHY: "Whether the contradicting
    record lost on score or never entered the candidate set is not established."
    This fixture is how that gets established.

    The structural condition: predecessor ahead on cosine and old, successor
    behind on cosine and recent, both in the candidate pool by construction, so
    whatever happens to the successor downstream is attributable to a stage
    rather than to retrieval.

    Three construction choices that are deductions rather than preferences.

    The predecessor is NOT a profile record. `_build_retrieval_confidence` is
    computed over non-profile items only, and a profile-only packet gets no
    confidence block at all, so #207's Failure 2 -- the low-confidence line
    firing -- could not have happened if the delivered record were profile.

    One unrelated profile record IS in the fixture. `has_profile` is computed
    over the whole packet, and ADR-046 measured `reserved_slots.profile_present`
    at 36 of 36 turns, so the profile authority-rules block was in the prompt on
    the incident turn. Without a profile record here the arm below measures
    nothing.

    The successor's authorship is an AXIS, not a stipulation. #266 measured that
    on the real vault every supersession-candidate pair was assistant-authored
    and all five removals were `excluded_by_role`. Stipulating that would decide
    "which stage removes it" by construction and then report the construction as
    a finding. Both variants run.
    """
    assert successor_role in {"user", "assistant"}
    # Cosines derived from the production measurement rather than invented.
    # prior.COSINE_MEAN_RANK1 is the measured mean top-1 raw cosine on this
    # vault and COSINE_SPREAD the measured top-8 spread (#236), so "the
    # predecessor is a rank-1-strength match and the successor is one spread
    # behind it" is a statement about the embedder rather than a number chosen
    # to make the fixture work. Invented cosines above the measured rank-1 mean
    # would put the whole fixture in territory the corpus does not produce,
    # which is the error ADR-044 records against the synthetic ablation corpus.
    from src.context import prior

    lead = prior.COSINE_MEAN_RANK1
    trail = prior.COSINE_MEAN_RANK1 - prior.COSINE_SPREAD
    return Incident(
        key=f"superseded_{successor_role}",
        provenance=f"#207 / successor_role={successor_role}",
        query="what is my current job title",
        items=(
            # The decoy: the superseded fact, ahead on cosine, 74 weeks old.
            _item(
                "sup_predecessor",
                f"{MARK_WRONG} my job title is systems analyst on the "
                "integrations team and it has been that for a good while now.",
                role="user",
                content_kind="user_content",
                score=lead,
                timestamp=_days_ago(518),
            ),
            # The answer: the successor, behind on cosine, recent.
            _item(
                "sup_successor",
                f"{MARK_RIGHT} i moved off integrations and my job title is "
                "platform lead as of the reorganisation last month.",
                role=successor_role,
                content_kind="user_content" if successor_role == "user" else "answer",
                authorship="first_person" if successor_role == "user" else "mixed",
                score=trail,
                timestamp=_days_ago(4),
            ),
            # Unrelated profile record, present for the reason in the docstring.
            _item(
                "sup_profile",
                "prefers direct answers with no preamble and no closing question",
                memory_type="profile",
                score=trail,
                timestamp=_days_ago(200),
            ),
            *[
                _item(f"sup_filler_{i}",
                      f"an unrelated record number {i} about something else "
                      "entirely, with enough length to clear the short-content "
                      "floor and no job title in it",
                      role="user", content_kind="user_content",
                      # Inside the measured spread, below the successor, so the
                      # fillers crowd the window without displacing it.
                      score=trail - 0.01 * (i + 1),
                      timestamp=_days_ago(60 + i * 10))
                for i in range(5)
            ],
        ),
        note=(
            "the successor reaches the model alongside the predecessor"
            if successor_role == "user"
            else "role exclusion removes the successor before selection"
        ),
        # See `_verdict` for why this is not `presence`.
        endpoint="delivery",
        # Measured, not predicted. The user variant PASSES on current main: with
        # the successor in the candidate pool, nothing between retrieval and the
        # render removes it. So it is a plain regression guard and carries no
        # xfail -- the day a stage starts eating it, this suite says so.
        #
        # The assistant variant fails, and the failure is shipped policy rather
        # than a defect in this suite: ADR-044 4a's role predicate drops
        # assistant-authored records on every query, before selection. Whether a
        # correction the assistant wrote may supersede a user-stated fact is the
        # question that owns it, and it is not this file's to answer.
        open_issue=None if successor_role == "user" else "#270",
        via="pipeline",
    )


INCIDENTS = (
    _self_echo(),
    _relational_contamination(),
    _entity_confusion(),
    _superseded_fact("user"),
    _superseded_fact("assistant"),
)
INCIDENTS_BY_KEY = {i.key: i for i in INCIDENTS}


def _params(keys=None):
    """Parametrize ids, carrying a strict xfail for any incident still open.

    Only `test_shipped_suppresses_the_decoy` uses this. The construction guards
    deliberately do not: a fixture that fails to reproduce its own condition is
    a defect in this file, not a known-open issue, and must fail outright.
    """
    incidents = INCIDENTS if keys is None else [INCIDENTS_BY_KEY[k] for k in keys]
    out = []
    for incident in incidents:
        if incident.open_issue is None:
            out.append(incident.key)
        else:
            out.append(pytest.param(incident.key, marks=pytest.mark.xfail(
                strict=True,
                reason=f"{incident.open_issue} open: {incident.note}",
            )))
    return out


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
         patch.object(ContextRanker, "_score_memory_item",
                      lambda self, item, policy=None: item):
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
    has no metadata to test. Filtering the candidate list is where a WHERE
    clause would land in this harness -- but the filter itself is the shipped
    `role_predicate.apply`, not a restatement of it. See the comment at the
    call site for what the restatement cost.
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


def _arms_for(incident: Incident) -> tuple[str, ...]:
    """Which arms mean anything for this incident.

    The four arms above all patch functions that live INSIDE semantic_search or
    the ranker's retrieval-stage pile. A pipeline-run incident stubs
    `retriever.retrieve`, which is downstream of every one of them, so all four
    arms would produce byte-identical output and the matrix would show four
    columns of the same number as though that were a result. The #207 arm that
    does mean something is the prompt-level one, and it has its own section at
    the end of this file because it cannot be measured on the presence endpoint.
    """
    return ("shipped",) if incident.via == "pipeline" else ARMS


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

PIPELINE_POLICY = ContextPolicy(name="default")


def _retrieved_items(incident: Incident) -> list:
    """What `semantic_search` would have returned for this fixture's candidates.

    The fixture stipulates a cosine. `ContextRetriever.retrieve` hands
    `_build_context` records whose `score` is that cosine PLUS the two
    retrieval-stage adjustments, with the cosine itself preserved in
    `metadata["raw_score"]` -- `semantic_search.py:105-110`, and the two
    functions in that order.

    Restating those two lines is unavoidable: the fixture exists to replace the
    embedding call, so something has to play its part, and the suite's docstring
    gives the reason -- a real search would return a cosine that is a property of
    the embedder rather than a controlled input.

    What makes it different from the restatement recorded at the role-predicate
    call site is that it is CHECKED rather than trusted. The trace harness
    recomputes every stage from its own model of the pipeline and aborts the
    capture if this sequence is wrong. The first version of this function omitted
    both adjustments, and `_validate` refused the capture with a 0.06 delta on
    every record carrying a query term. A restatement with a validator behind it
    is a different thing from one without.

    Fresh deep copies, because every downstream stage mutates `item.score` in
    place and a second run over the same objects would score scored items.
    """
    normalized_query = ss.normalize_text(incident.query)
    query_terms = ss.extract_query_terms(normalized_query)

    items = []
    for item in copy.deepcopy(list(incident.items)):
        normalized_content = ss.normalize_text(item.content)
        raw = float((item.metadata or {}).get("raw_score", item.score))
        score = raw
        score += ss.lexical_relevance_bonus(
            normalized_query, query_terms, normalized_content,
            raw_query=incident.query,
        )
        score += ss.query_intent_adjustment(
            normalized_query, item.memory_type, normalized_content,
        )
        item.score = score
        item.metadata = dict(item.metadata or {}, raw_score=raw)
        items.append(item)
    return items


def _run_pipeline(incident: Incident, *, suppress_profile_authority: bool = False):
    """One run of the fixture through the real ContextService._build_context.

    Returns (packet, prompt_text).

    Why this and not the stage chain below. The stage chain reproduces four
    retrieval-stage functions and then calls the ranker and the prompt builder;
    it does NOT run the ADR-018 type gate, the relevance gate, the content
    filters, dedup, the policy limit or diversity selection. For #207 the
    question IS which of those removes the successor, so a runner that skips
    most of them cannot answer it.

    The relevance gate in particular cannot be called at all: it is inline in
    `_build_context` with an undeclared second threshold for ingested records,
    so any ladder that reproduced it would be restating shipped logic -- which
    is precisely what the comment at the role-predicate call site above records
    as having cost this suite a real defect.

    Stubbing `retriever.retrieve` is the established seam for this
    (tests/test_profile_slot_budget.py does the same), and it is the same seam
    the shipped trace harness uses. `classify_query` is pinned because it calls
    the ADR-034 intent classifier, which reaches Ollama; the policy is `default`
    because that is the only policy whose relevance gate opens, and its memory
    limit of 6 against a render of 4 is the boundary #264 showed decides
    membership. A different policy changes the limit and the diversity flag, so
    the ladder prints the policy it ran under.

    Fresh deep copies per run, because every stage mutates `item.score` in
    place: a second run over the same objects would score already-scored items.
    """
    items = _retrieved_items(incident)
    service = ContextService()
    builder = PromptBuilder()

    with patch("src.context.service.classify_query", return_value=PIPELINE_POLICY), \
         patch.object(service.retriever, "retrieve",
                      return_value=([], [], items, [], None)):
        packet = service.build_context(
            incident.query, read_only=True, skip_web_search=True
        )

    # build_prompt calls begin_render itself (prompt_builder.py:371) and the
    # shipped renderer records what it rendered, so delivered_items after this
    # is the model-visible set rather than a restatement of the window.
    if suppress_profile_authority:
        with patch(
            "src.llm.prompt_builder._AUTHORITY_RULES_PROFILE_HEDGE_EXCLUSION", ""
        ):
            prompt = builder.build_prompt(packet)
    else:
        prompt = builder.build_prompt(packet)

    return packet, prompt


def _pipeline_visible_text(incident: Incident, arm: str) -> str:
    """The pipeline runner's answer to the same question `_model_visible_text` asks.

    The markers only ever occur in record content, so searching the whole prompt
    is equivalent to searching the memory section for a presence endpoint, and
    it is the prompt that carries the authority rules the arm at the end of this
    file varies.
    """
    if arm != "shipped":  # pragma: no cover
        raise ValueError(f"{incident.key} runs only the shipped arm; see _arms_for")
    _packet, prompt = _run_pipeline(incident)
    return prompt


def _model_visible_text(incident: Incident, arm: str) -> str:
    """Render what the model would actually see, for one incident under one arm.

    Deliberately exercises the real ranker, the real authorship multiplier and
    the real prompt builder, so the [:4] slice and the label rendering are the
    shipped ones rather than a copy that can drift.
    """
    if incident.via == "pipeline":
        return _pipeline_visible_text(incident, arm)

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
            # The SHIPPED predicate, not a local copy of it. This arm used to
            # apply its own filter here, and that is how a real defect stayed
            # invisible: the predicate as first shipped was gated to
            # relational queries, the self-echo incident's query is not
            # relational, so the shipped code did not fire on the incident
            # this arm reports a PASS for. A permanent regression suite that
            # reimplements the thing it protects protects nothing.
            items = role_predicate_module.apply(items)

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
    if incident.endpoint == "delivery":
        # #207. The answer must reach the model; the decoy's presence is not the
        # harm and is reported without being judged.
        #
        # Presence would be the wrong endpoint here, for the same reason it is
        # wrong for the entity incident and in the same words: it would mark the
        # arm FAIL and measure the window size rather than the mechanism. A
        # superseded fact delivered ALONGSIDE its successor, both honestly dated
        # with an age label, is the configuration the Memory Trust Gap paper
        # measures 8B stale-value following at 0.00 in -- so "both present" is
        # not the incident, it is the condition under which the model gets it
        # right. Suppressing the predecessor is what ADR-045 proposes; it is not
        # a property this pipeline has or a failure for lacking.
        #
        # What #207 is about is the successor's ABSENCE. That is what this
        # endpoint tests and nothing else.
        return right, wrong, right
    raise ValueError(incident.endpoint)  # pragma: no cover


# ---------------------------------------------------------------------------
# The shipped arm must pass all three. These are the regression guards.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", _params())
def test_shipped_suppresses_the_decoy(key: str) -> None:
    """The incident does not recur on the shipped pipeline.

    If one of these starts failing, the corresponding production incident has
    regressed. Each names its own commit so the failure is traceable.

    The two #207 cases carry a strict xfail from `_params` because the issue is
    open: there, a failure is the measurement rather than a regression, and
    `strict=True` makes an unexpected PASS fail too, so the day the pipeline
    starts delivering the successor this test says so instead of going quietly
    green.
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
        for arm in _arms_for(incident):
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


# ---------------------------------------------------------------------------
# #207: the stage ladder. Which stage removes the successor, or does it survive.
# ---------------------------------------------------------------------------

# The flags below are CandidateTrace's, named exactly as the schema names them.
# There is no `score_gated_out`: the min_score half of the ADR-018 gate is folded
# into `type_gated_out`, so `type_eligible=True` with `type_gated_out=True` reads
# as "the right type, dropped on score" (capture.py:371-374). The raw-cosine
# relevance gate is the separate `relevance_gated_out`.
#
# `in_packet` is DERIVED, not a trace flag, and it is here because capture has no
# flag for the two stages between the packet and the render: the policy limit and
# diversity selection. Both are visible only as `rendered=False`, so without this
# column "dropped by a gate" and "ranked out of the window" are indistinguishable.
LADDER_COLUMNS = (
    "store_id", "authorship", "age_days", "raw_cosine", "composed_score",
    "type_eligible", "type_gated_out", "relevance_gated_out",
    "filtered_echo_or_meta", "filtered_low_value", "excluded_by_role",
    "deduped_out", "in_packet", "rendered",
)


def _stage_ladder(incident: Incident) -> tuple[list[dict], list[str]]:
    """Per-candidate stage attribution, plus the delivered set in render order.

    Instrument: the shipped trace harness, `capture_query`, over the same stubbed
    retriever `_run_pipeline` uses. It is the right instrument for three reasons
    and one of them is the point of this file.

    First, it emits the flags above per candidate rather than requiring a
    restatement of each stage's predicate.

    Second, it self-validates. `_validate` recomputes every stage score from the
    recorded activations and raises `TraceValidationError` if the model and the
    pipeline disagree by more than 1e-12, and `_validate_render` raises if
    `render_window` disagrees with what `PromptBuilder` actually rendered. So a
    ladder that reported a stage attribution the pipeline does not produce would
    abort rather than print.

    Third, `rendered` comes from driving the real prompt builder, not from
    applying the window here.

    The successor's `rendered` flag is then cross-checked against the marker in
    the prompt from `_run_pipeline`, which is a second instrument reaching the
    same conclusion by a different route. Two instruments agreeing is the only
    reason to believe either.
    """
    from tools.retrieval_trace.capture import capture_query

    items = _retrieved_items(incident)
    service = ContextService()

    # Both bindings. capture_query calls classify_query itself, and that call
    # reaches the ADR-034 intent classifier, which reaches Ollama.
    with patch("tools.retrieval_trace.capture.classify_query",
               return_value=PIPELINE_POLICY), \
         patch("src.context.service.classify_query", return_value=PIPELINE_POLICY), \
         patch.object(service.retriever, "retrieve",
                      return_value=([], [], items, [], None)):
        trace = capture_query(
            incident.query, incident.key, service=service, include_content=True
        )

    packet, _prompt = _run_pipeline(incident)
    in_packet = {getattr(i, "store_id", None) for i in packet.memory_items}

    rows = []
    for candidate in trace.candidates:
        rows.append({
            "store_id": candidate.store_id,
            "authorship": candidate.authorship,
            "age_days": candidate.age_days,
            "raw_cosine": round(candidate.retrieval.raw_cosine, 4),
            "composed_score": round(candidate.composed_score, 4),
            "type_eligible": candidate.type_eligible,
            "type_gated_out": candidate.type_gated_out,
            "relevance_gated_out": candidate.relevance_gated_out,
            "filtered_echo_or_meta": candidate.filtered_echo_or_meta,
            "filtered_low_value": candidate.filtered_low_value,
            "excluded_by_role": candidate.excluded_by_role,
            "deduped_out": candidate.deduped_out,
            "in_packet": candidate.store_id in in_packet,
            "rendered": candidate.rendered,
        })
    return rows, list(trace.rendered_refs)


def _ladder_row(incident: Incident, store_id: str) -> dict:
    rows, _refs = _stage_ladder(incident)
    return next(r for r in rows if r["store_id"] == store_id)


@pytest.mark.parametrize("key", ["superseded_user", "superseded_assistant"])
def test_both_records_are_candidates_by_construction(key: str) -> None:
    """The construction guard that makes the ladder attributable.

    If either record were absent from the candidate pool, "which stage removed
    the successor" would have no answer and the fixture would be measuring
    retrieval, which it stubs. #207 declines to say whether the real successor
    was ever a candidate; this fixture stipulates that it was, so that whatever
    happens downstream is attributable to a stage.
    """
    rows, _refs = _stage_ladder(INCIDENTS_BY_KEY[key])
    ids = {r["store_id"] for r in rows}
    assert {"sup_predecessor", "sup_successor"} <= ids


@pytest.mark.parametrize("key", ["superseded_user", "superseded_assistant"])
def test_the_ladder_and_the_prompt_agree_on_the_successor(key: str) -> None:
    """Two instruments, one conclusion. Neither is trusted alone.

    `capture_query` reports `rendered` from the shipped prompt builder; the
    pipeline runner independently searches the prompt for the marker. If these
    ever disagree, the ladder is reporting something the model never saw and
    every number in it is suspect.
    """
    incident = INCIDENTS_BY_KEY[key]
    successor = _ladder_row(incident, "sup_successor")
    _packet, prompt = _run_pipeline(incident)
    assert successor["rendered"] == (MARK_RIGHT in prompt), (
        "the trace harness and the rendered prompt disagree about whether the "
        "successor reached the model"
    )


def test_emit_supersession_ladder(capsys) -> None:
    """Print the stage ladder for both variants. Asserts nothing on its own.

    This is the deliverable: #207 has never been reproduced against the
    corrected index, and the question it leaves open -- did the successor lose
    on score or was it never a candidate -- is answered per stage here.
    """
    with capsys.disabled():
        print("\n\n#207 SUPERSESSION -- stage ladder")
        print(f"policy={PIPELINE_POLICY.name}  "
              f"min_score={PIPELINE_POLICY.min_score}  "
              f"diversity={PIPELINE_POLICY.diversity}")
        print("predecessor = the superseded fact (74 weeks old, ahead on cosine)")
        print("successor   = the record that corrects it (recent, behind on cosine)")
        print("in_packet is DERIVED: in the packet but not rendered means the")
        print("policy limit or diversity selection dropped it, not a gate.")

        for incident in (INCIDENTS_BY_KEY["superseded_user"],
                         INCIDENTS_BY_KEY["superseded_assistant"]):
            rows, refs = _stage_ladder(incident)
            print(f"\n--- {incident.key} ({incident.provenance}) ---")
            widths = [max(len(c), 10) for c in LADDER_COLUMNS]
            print("  ".join(c[:w].ljust(w) for c, w in zip(LADDER_COLUMNS, widths)))
            for row in rows:
                print("  ".join(
                    str(row[c])[:w].ljust(w)
                    for c, w in zip(LADDER_COLUMNS, widths)
                ))
            print(f"rendered refs, in render order: {refs}")
            _packet, prompt = _run_pipeline(incident)
            print(f"confidence band: {_hedge_band(prompt) or 'high, or no block'}")
            print(f"answer reached: {MARK_RIGHT in prompt}   "
                  f"decoy reached: {MARK_WRONG in prompt}")

    assert True


# ---------------------------------------------------------------------------
# #207 Failure 2: the profile authority-rules arm.
#
# WHAT THIS ARM CAN AND CANNOT CLAIM, stated here because overclaiming it would
# repeat this suite's own recorded failure mode.
#
# Suppressing `_AUTHORITY_RULES_PROFILE_HEDGE_EXCLUSION` cannot change retrieval,
# the packet, or the rendered set. It is prompt text. What the model does with it
# is unmeasurable without calling the model, which this suite never does.
#
# What it CAN establish as a construction fact is whether the prompt handed to
# the model contains, at the same time, the low-confidence hedge line and an
# instruction never to hedge profile facts on that block. That co-occurrence is
# the contradiction #207's Failure 2 describes -- the hedge fired and the model
# asserted the stale fact in the present tense anyway -- and whether it is
# present, and disappears when the constant is suppressed, needs no model.
#
# The constant is NOT deleted. It is patched inside a `with` block for the
# duration of one arm.
# ---------------------------------------------------------------------------

# The confidence block's three bands, verbatim fragments.
#
# The band is NOT pinned to "low", and that is a finding rather than a
# convenience. #207 reports the low band firing on the incident turn, and
# ADR-044:95-99 says why: "Aged records finalize at 0.03-0.15x raw cosine, so
# the confidence block reads 'low -- records are old or weakly matched' on
# essentially every vault-grounded turn... Neither is reporting match quality;
# both are reporting the decay multipliers." Those multipliers are gone. The
# composed score is now cosine x [0.8722, 1.1278], so a fixture at the measured
# production cosines cannot reach the low band, and pinning it would mean
# inventing cosines the corpus does not produce to force a band the contract
# retired. What the contradiction needs is a band that instructs hedging at all,
# which both "low" and "moderate" do.
_HEDGE_BANDS = ("records are old or weakly matched", "hedge claims with temporal context")
_CONFIDENCE_HEADER = "[Retrieval confidence:]"


def _hedge_band(prompt: str) -> str | None:
    return next((band for band in _HEDGE_BANDS if band in prompt), None)


def test_the_prompt_carries_the_hedge_and_the_never_hedge_instruction() -> None:
    """The contradiction, as a construction fact on the shipped arm.

    #207's Failure 2 is that the confidence block instructed hedging and the
    model asserted the stale fact in the present tense anyway. The prompt it was
    given also told it never to hedge profile facts on that block. Whether both
    instructions are in the same prompt is checkable; what the model did with
    them is not, without a model.

    This is also the positive control for the absence assertion in the arm
    below, per CLAUDE.md: an assertion that the instruction is gone is satisfied
    by a fixture where it could never have been there.
    """
    from src.llm.prompt_builder import _AUTHORITY_RULES_PROFILE_HEDGE_EXCLUSION

    _packet, prompt = _run_pipeline(INCIDENTS_BY_KEY["superseded_user"])
    assert _AUTHORITY_RULES_PROFILE_HEDGE_EXCLUSION in prompt, (
        "the profile authority-rules block did not render, so this fixture "
        "cannot speak to #207 Failure 2 -- ADR-046 measures it present on 36 "
        "of 36 turns, so a fixture where it is absent is the wrong fixture"
    )
    assert _CONFIDENCE_HEADER in prompt, (
        "no confidence block rendered, so there is no hedge instruction for the "
        "authority rules to contradict"
    )
    assert _hedge_band(prompt) is not None, (
        "the confidence block fired on the high band, so it is not instructing "
        "the model to hedge and the contradiction is not reproduced"
    )


def test_suppressing_the_profile_authority_block_changes_only_the_prompt() -> None:
    """The arm, and the limit of what it measures.

    Three claims, in order of what they are worth.

    The instruction is gone from the prompt (the absence, controlled above).

    The two prompts differ by EXACTLY that constant and nothing else, which is
    the arm-liveness guard `tests/test_retrieval_ablation_arms.py` puts on every
    ablation arm: a patch that reached nothing would otherwise give a clean
    two-arm comparison measuring nothing.

    And the delivered set is byte-identical across the two arms. That is the
    honest answer to "does removing it change the answer": it cannot change
    which records the model receives, because it is an instruction about them.
    Anything further needs a model in the loop and is not measurable here.
    """
    from src.llm.prompt_builder import _AUTHORITY_RULES_PROFILE_HEDGE_EXCLUSION

    incident = INCIDENTS_BY_KEY["superseded_user"]
    shipped_packet, shipped_prompt = _run_pipeline(incident)
    armed_packet, armed_prompt = _run_pipeline(
        incident, suppress_profile_authority=True
    )

    assert _AUTHORITY_RULES_PROFILE_HEDGE_EXCLUSION not in armed_prompt
    assert shipped_prompt.replace(
        _AUTHORITY_RULES_PROFILE_HEDGE_EXCLUSION, ""
    ) == armed_prompt, (
        "the two arms differ by something other than the suppressed constant, "
        "so this arm is not measuring what it claims to"
    )

    assert [i.store_id for i in shipped_packet.delivered_items] == [
        i.store_id for i in armed_packet.delivered_items
    ], (
        "suppressing prompt text changed the delivered set, which would mean "
        "the arm is reaching retrieval and the measurement is confounded"
    )
    assert _hedge_band(armed_prompt) == _hedge_band(shipped_prompt), (
        "the hedge band moved with the exclusion block; the arm is supposed to "
        "leave the confidence block alone and remove only the instruction "
        "telling the model to ignore it"
    )
