"""
tests/test_project_boost.py

Tests for project-scoped retrieval boost (ADR-007).

The boost is a BOUNDED MULTIPLIER applied in the prior, not a +0.15 added
here. ADR-044's 2026-09-30 amendment moved it: at +0.15 additive it was 1.8x
the entire measured cosine spread, and it landed AFTER the tier multiply, so
tier could not attenuate it -- the last surviving instance of the ordering
defect ADR-044 decision 1 names.

So apply_project_boost no longer changes a score. It records the match on the
item, and rank() applies prior.PROJECT_MATCH with every other bounded factor
in one multiply. These tests assert the marker and the multiplier separately,
because they are now two different jobs.
"""

import pytest

from src.context import prior
from src.context.models import ContextItem
from src.context.ranker import ContextRanker


def make_item(content="test content", score=0.5, metadata=None):
    """Create a ContextItem for testing."""
    return ContextItem(
        id="test-id",
        content=content,
        source="test",
        item_type="conversation",
        score=score,
        metadata=metadata or {},
    )


class TestTheMarker:
    """Which items apply_project_boost marks, and that it marks only."""

    def test_matching_project_id_is_marked(self):
        ranker = ContextRanker()
        item = make_item(metadata={"project_id": "proj_abc"})
        result = ranker.apply_project_boost([item], "proj_abc")
        assert result[0].project_match is True

    @pytest.mark.parametrize(
        "case, metadata, project_id",
        [
            ("a different project", {"project_id": "proj_other"}, "proj_abc"),
            ("no project_id key", {"role": "user"}, "proj_abc"),
            ("empty metadata", {}, "proj_abc"),
            ("no active project", {"project_id": "proj_abc"}, None),
            ("empty active project", {"project_id": "proj_abc"}, ""),
        ],
    )
    def test_everything_else_is_unmarked(self, case, metadata, project_id):
        ranker = ContextRanker()
        item = make_item(metadata=metadata)
        result = ranker.apply_project_boost([item], project_id)
        assert result[0].project_match is False, case

    @pytest.mark.parametrize("score", [0.0, 0.4, 0.8])
    def test_marking_never_moves_the_score(self, score):
        """The whole point of the amendment: this stage is score-neutral now.

        Parametrised over the score because an additive term and a
        multiplicative one agree at exactly one value -- 0.0 for the first,
        1.0 for the second -- and a single-value assertion could not tell
        "unchanged" from "changed by a term that happens to vanish here".
        """
        ranker = ContextRanker()
        item = make_item(score=score, metadata={"project_id": "proj_x"})
        result = ranker.apply_project_boost([item], "proj_x")
        assert result[0].project_match is True
        assert result[0].score == pytest.approx(score)

    def test_mixed_items_mark_only_the_match(self):
        ranker = ContextRanker()
        items = [
            make_item(content="in project", score=0.4, metadata={"project_id": "proj_abc"}),
            make_item(content="not in project", score=0.6, metadata={"project_id": "proj_other"}),
            make_item(content="no project", score=0.5, metadata={}),
        ]
        result = ranker.apply_project_boost(items, "proj_abc")
        assert [i.project_match for i in result] == [True, False, False]
        assert [i.score for i in result] == pytest.approx([0.4, 0.6, 0.5])

    def test_empty_items_returns_empty(self):
        ranker = ContextRanker()
        assert ranker.apply_project_boost([], "proj_abc") == []


class TestTheMultiplier:
    """The boost itself, where it now lives."""

    def test_a_marked_item_is_boosted_by_the_prior(self):
        ranker = ContextRanker()
        matched = make_item(score=0.5, metadata={"project_id": "proj_abc"})
        unmatched = make_item(score=0.5, metadata={"project_id": "proj_other"})
        ranker.apply_project_boost([matched, unmatched], "proj_abc")

        ranked, _ = ranker.rank([matched, unmatched], [])
        by_match = {i.project_match: i.score for i in ranked}

        assert by_match[True] > by_match[False]
        assert by_match[True] / by_match[False] == pytest.approx(
            prior.PROJECT_MATCH, rel=1e-9
        )

    def test_the_boost_is_inside_the_composed_bound(self):
        """The reason it moved. At +0.15 it was 1.8x the cosine spread."""
        assert prior.PRIOR_MIN <= prior.PROJECT_MATCH <= prior.PRIOR_MAX
        assert prior.PROJECT_MATCH - 1.0 < prior.COSINE_SPREAD
