"""
tests/test_selection_bound.py

ADR-044 / #255: the bound on the SELECTION stage, as a test rather than a comment.

The composed bound in tests/test_composition_bound.py governs the metadata prior,
a per-candidate multiplier. This governs `ContextService._diversity_score`, which
is a different animal: it is relational (a candidate's penalty depends on which
candidates were already selected), it accumulates, and it decides which records are
DELIVERED on four of ten policies rather than how they are ordered.

THE BOUND is that the selection objective may move a record by at most
SELECTION_BAND of that record's own composed score. Derived, not chosen: a
tie-breaker's legitimate authority is tie-breaking among records of COMPARABLE
relevance, and the measured width of "comparable" is the embedder's own top-k
spread -- the same 0.0815 / 0.6375 the prior's bound comes from (#236). Two records
inside that band are comparable and diversity may reorder them; two further apart
are not, and diversity may not overturn the similarity signal.

Before this bound the three per-neighbour terms were SUMMED and the similarity term
was absolute, so at a limit of 6 the objective could subtract 2.45 from a composed
score of roughly 0.4 to 0.8 -- up to six times the whole record.

WHY A CAP RATHER THAN FOUR DERIVED MAGNITUDES. Only one of the four terms is
measurable on the production corpus: `div.similarity_share` resolves at ST 0.1180
+/- 0.0078 on the delivery endpoint, while `same_type`, `same_doc` and `same_title`
are solo-flat -- sweeping each to both ends of its declared range moves zero
rendered refs. Deriving magnitudes for those three would be inventing numbers for
terms this corpus cannot exercise, which is the error ADR-044 records against #232
pointed the other way. The cap makes their sizes moot instead of guessed.

Fixtures are synthetic (CLAUDE.md Vault Privacy Rule).
"""

from __future__ import annotations

import inspect
import itertools

import pytest

from src.context import prior
from src.context.models import ContextItem
from src.context.service import (
    DIVERSITY_WEIGHTS,
    SELECTION_BAND,
    ContextService,
    DiversityWeights,
)

# The state space `_diversity_score` can occupy, enumerated rather than sampled.
# Small enough to cover exhaustively, and a sampled bound test can miss exactly
# the corner where the cap is load-bearing.
SCORES = (0.0, 0.05, 0.4, 0.6375, 0.8, 1.0)
SHARED_DOC = (False, True)
SHARED_TITLE = (False, True)
SHARED_TYPE = (False, True)
NEIGHBOURS = (1, 2, 3, 5)  # |selected| at limits 4 through 6


def _service() -> ContextService:
    return ContextService.__new__(ContextService)


def _item(content: str, score: float, *, item_type="conversation", doc=None, title=None):
    metadata = {}
    if doc is not None:
        metadata["doc_id"] = doc
    if title is not None:
        metadata["title"] = title
    return ContextItem(
        id="x", content=content, source="test", item_type=item_type,
        memory_type=item_type, score=score, metadata=metadata,
    )


# Two bodies with no shared vocabulary, so Jaccard is 0 and the per-neighbour
# terms can be measured without the similarity term confounding them.
DISJOINT_A = "alpha beta gamma delta epsilon zeta"
DISJOINT_B = "one two three four five six seven"


def _cases():
    """Every combination of the relational facts, at several |selected|.

    Yields (label, score, candidate, selected). The label carries the
    combination for a failure message, so no caller has to unpack and discard
    the four relational flags to get at the candidates.
    """
    for score, doc, title, same_type, neighbours in itertools.product(
        SCORES, SHARED_DOC, SHARED_TITLE, SHARED_TYPE, NEIGHBOURS
    ):
        candidate = _item(
            DISJOINT_A, score,
            item_type="conversation",
            doc="d" if doc else None,
            title="t" if title else None,
        )
        selected = [
            _item(
                DISJOINT_B, score,
                item_type="conversation" if same_type else "ingested",
                doc="d" if doc else None,
                title="t" if title else None,
            )
            for _ in range(neighbours)
        ]
        label = (
            f"score={score} shared_doc={doc} shared_title={title} "
            f"same_type={same_type} neighbours={neighbours}"
        )
        yield label, score, candidate, selected


class TestTheBoundItself:
    def test_the_band_is_derived_from_the_measured_spread(self):
        """Not a literal. The relationship is what must hold.

        Deliberately not a pin on 0.1278: ADR-044 requires the spread be
        re-measured per embedder, so a literal here would fail on a legitimate
        re-measurement while asserting nothing about behaviour -- and would be
        "fixed" by editing the number, which is the habit the contract exists to
        break. The claim is that the selection band IS the relative spread, which
        is the whole argument for why a tie-breaker may have that much authority
        and no more.
        """
        assert SELECTION_BAND == prior.RELATIVE_SPREAD
        assert prior.RELATIVE_SPREAD == pytest.approx(
            prior.COSINE_SPREAD / prior.COSINE_MEAN_RANK1
        )

    def test_no_combination_moves_a_record_further_than_the_band(self):
        """The bound, over the whole relational state space."""
        service = _service()
        escaped = []
        for label, score, candidate, selected in _cases():
            result = service._diversity_score(candidate, selected)
            moved = score - result
            if moved > abs(score) * SELECTION_BAND + 1e-12:
                escaped.append(f"{label} moved={moved}")
        assert not escaped, (
            f"{len(escaped)} combination(s) move a record further than the band "
            f"allows. First: {escaped[0]}"
        )

    def test_the_worst_case_lands_exactly_on_the_band(self):
        """Tight, not merely inside -- otherwise the budget is being wasted.

        A conservative cap would satisfy the test above while giving the
        objective less authority than the contract permits, so near-duplicates
        would not be separated at all. The worst case has to EQUAL the band.

        The worst case is restated here rather than read off the implementation,
        for the same reason test_composition_bound restates its worst arms:
        deriving it from the thing under test makes the equality true by
        construction.
        """
        service = _service()
        score = 0.6375  # the measured mean rank-1 cosine
        # Identical bodies -> Jaccard 1.0, plus every per-neighbour term.
        candidate = _item("the very same words appear in both", score, doc="d", title="t")
        selected = [
            _item("the very same words appear in both", score, doc="d", title="t")
            for _ in range(5)
        ]
        moved = score - service._diversity_score(candidate, selected)
        assert moved == pytest.approx(score * SELECTION_BAND, rel=1e-9)

    def test_the_band_is_reached_and_not_merely_respected(self):
        """Non-vacuity. A cap of zero would pass every test above."""
        service = _service()
        moves = []
        for _label, score, candidate, selected in _cases():
            if score == 0.0:
                continue
            moved = score - service._diversity_score(candidate, selected)
            moves.append(moved / score)
        assert max(moves) == pytest.approx(SELECTION_BAND, rel=1e-9), (
            "no combination reaches the band, so the objective has less "
            "authority than the contract grants it and near-duplicates will not "
            "be separated"
        )

    def test_an_out_of_contract_weight_cannot_escape_the_cap(self):
        """The cap is the enforcement, proved against a magnitude the
        derivation would never produce.

        The shipped weights happen to compose within the band on most
        combinations; that is not what makes the contract hold. This hands the
        selector a same_doc of 5.0 -- more than six times any score in the
        system -- and asserts the record still loses no more than the band.
        """
        service = _service()
        absurd = DiversityWeights(similarity_share=50.0, same_doc=5.0,
                                  same_title=5.0, same_type=5.0)
        score = 0.6375
        candidate = _item(DISJOINT_A, score, doc="d", title="t")
        selected = [_item(DISJOINT_B, score, doc="d", title="t") for _ in range(5)]

        moved = score - service._diversity_score(candidate, selected, absurd)
        assert moved == pytest.approx(score * SELECTION_BAND, rel=1e-9)

    def test_a_fifth_penalty_term_cannot_be_added_unnoticed(self):
        """Adding a term without putting it inside the cap must fail here.

        A source-text assertion, which is ordinarily a bad idea. It is the right
        one here because what is being guarded is that a human put a new term
        INSIDE the `penalty` sum rather than subtracting it separately from the
        return value -- and no value can show that. A term subtracted outside the
        cap would pass every other test in this file while leaving the bound
        false.
        """
        source = inspect.getsource(ContextService._diversity_score)
        body = source.split('"""')[-1]  # past the docstring

        returns = [l.strip() for l in body.splitlines() if l.strip().startswith("return")]
        assert returns == ["return relevance - penalty"], (
            f"_diversity_score returns {returns}; the bound holds only if every "
            f"penalty goes through the capped `penalty` total, so a new term must "
            f"be added to that sum rather than subtracted here"
        )
        # Every weight the dataclass declares is read inside the function.
        # `weights.` prefixed deliberately: the bare field names are substrings
        # of the function's own locals (`same_doc_penalty` contains `same_doc`),
        # so matching on those alone would pass with every weight read deleted.
        for field_name in DIVERSITY_WEIGHTS.__dataclass_fields__:
            assert f"weights.{field_name}" in body, (
                f"weights.{field_name} is declared but never read by "
                f"_diversity_score"
            )


class TestWhatTheBoundBuys:
    """The behaviour the bound is FOR, with its negative twin.

    A bound test alone would pass on a contract that permits nothing, so these
    pin the threshold rather than an outcome: a near-duplicate must lose, and a
    merely-similar record must not.
    """

    def test_a_near_duplicate_is_demoted(self):
        service = _service()
        body = "the state layer landed today and the tests are green"
        selected = [_item(body, 0.60)]
        duplicate = _item(body, 0.60)          # Jaccard 1.0
        distinct = _item(DISJOINT_B, 0.58)     # lower score, no overlap

        assert service._diversity_score(duplicate, selected) < service._diversity_score(
            distinct, selected
        ), "an identical record outranks a distinct one; diversity does nothing"

    def test_but_not_at_any_relevance_gap(self):
        """The negative twin, and the reason the band is the band.

        Diversity may reorder records of COMPARABLE relevance. A duplicate that
        is far enough ahead on similarity must still win, or the objective is
        overturning the signal it is supposed to tie-break. "Far enough" is the
        band, which is what makes this test the threshold rather than an outcome.
        """
        service = _service()
        body = "the state layer landed today and the tests are green"
        selected = [_item(body, 0.60)]

        # A duplicate ahead by more than the band keeps its place.
        clear_lead = 0.60 * (1 + SELECTION_BAND * 1.5)
        duplicate = _item(body, clear_lead)
        distinct = _item(DISJOINT_B, 0.60)
        assert service._diversity_score(duplicate, selected) > service._diversity_score(
            distinct, selected
        ), (
            "a duplicate leading by more than the band was still demoted, so the "
            "objective is overriding relevance rather than tie-breaking it"
        )

    def test_the_cap_firing_is_observable(self):
        """The counters that would surface a saturating objective must exist.

        Same requirement test_composition_bound puts on the clamp: a bound that
        holds by construction still needs something to notice when a future term
        breaks the construction.
        """
        import sys
        from pathlib import Path

        tools = str(Path(__file__).resolve().parents[1] / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import guard_counter_sites as sites

        declared = {row["site"] for row in sites.declared_sites()}
        assert "diversity.penalty_capped" in declared
        for term in ("same_type", "same_doc", "same_title", "similarity"):
            assert f"diversity.penalty.{term}" in declared, (
                f"diversity.penalty.{term} is not declared, so a term that never "
                f"fires on a corpus cannot be told apart from one that fires and "
                f"does nothing"
            )
