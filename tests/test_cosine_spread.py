"""
tests/test_cosine_spread.py

Coverage for the top-k cosine spread measurement.

ADR-044 bounds the metadata prior against this number, so the number has
to mean what the ADR says it means. Two ways it could quietly not:

  * measuring the composed score instead of raw cosine, which would
    compare the prior against itself and always look reasonable
  * writing retrieval stats while measuring, which promotes records for
    a delivery that was a measurement

Both are asserted here rather than left to the caller.

Fixtures are synthetic (CLAUDE.md Vault Privacy Rule).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import cosine_spread  # noqa: E402


def _results(raw_scores):
    """Search results carrying a raw cosine and a composed score.

    The two differ on purpose: a measurement that reads `score` would
    pass every other assertion here and still be the wrong quantity.
    """
    return [
        {"raw_score": raw, "score": raw + 10.0, "content": f"synthetic {i}"}
        for i, raw in enumerate(raw_scores)
    ]


@pytest.fixture
def stub_search(monkeypatch):
    calls = {"queries": []}
    plan = {}

    def _embed(text):
        calls["queries"].append(text)
        return [0.1] * 768

    def _search(query, limit=None, query_embedding=None, **kwargs):
        return _results(plan.get(query, [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2]))

    monkeypatch.setattr("src.retrieval.embed_memory.embed_text", _embed)
    monkeypatch.setattr("src.retrieval.semantic_search.semantic_search", _search)
    return plan, calls


class TestDefinition:
    def test_spread_is_max_minus_min_of_the_top_k_raw_cosines(self, stub_search):
        plan, _ = stub_search
        plan["q"] = [0.9, 0.85, 0.8, 0.75, 0.7, 0.65, 0.6, 0.5]
        result = cosine_spread.measure(["q"], k=8)
        assert result["mean_spread"] == pytest.approx(0.4)
        assert result["mean_top1"] == pytest.approx(0.9)
        assert result["mean_topk"] == pytest.approx(0.5)

    def test_it_measures_raw_cosine_not_the_composed_score(self, stub_search):
        """The composed score in the stub is raw + 10.

        Its spread is identical, so an implementation reading the wrong
        field is invisible in the spread alone -- the rank-1 value is what
        catches it.
        """
        plan, _ = stub_search
        plan["q"] = [0.4, 0.3, 0.2, 0.1]
        result = cosine_spread.measure(["q"], k=4)
        assert result["mean_top1"] == pytest.approx(0.4)
        assert result["mean_top1"] < 1.0

    def test_k_truncates_a_longer_result_list(self, stub_search):
        plan, _ = stub_search
        plan["q"] = [0.9, 0.8, 0.7, 0.6, 0.5]
        assert cosine_spread.measure(["q"], k=2)["mean_spread"] == pytest.approx(0.1)

    def test_a_query_short_of_k_is_counted_and_still_measured(self, stub_search):
        plan, _ = stub_search
        plan["q"] = [0.9, 0.6]
        result = cosine_spread.measure(["q"], k=8)
        assert result["short_of_k"] == 1
        assert result["measured"] == 1
        assert result["mean_spread"] == pytest.approx(0.3)

    def test_a_query_with_one_result_has_no_spread_and_is_skipped(self, stub_search):
        plan, _ = stub_search
        plan["a"] = [0.9]
        plan["b"] = [0.9, 0.5]
        result = cosine_spread.measure(["a", "b"], k=8)
        assert result["queries"] == 2
        assert result["measured"] == 1

    def test_no_measurable_query_is_an_error_not_a_zero(self, stub_search):
        """Zero spread and no measurement are opposite findings."""
        plan, _ = stub_search
        plan["a"] = [0.9]
        with pytest.raises(RuntimeError, match="enough results"):
            cosine_spread.measure(["a"], k=8)

    def test_every_query_is_embedded_once(self, stub_search):
        plan, calls = stub_search
        cosine_spread.measure(["a", "b", "c"], k=4)
        assert calls["queries"] == ["a", "b", "c"]


class TestItDoesNotWriteStats:
    def test_the_search_runs_inside_a_suppressed_stats_scope(self, stub_search):
        from src.retrieval.retrieval_stats import retrieval_stats_disabled_now

        seen = []

        def _search(query, limit=None, query_embedding=None, **kwargs):
            seen.append(retrieval_stats_disabled_now())
            return _results([0.9, 0.5])

        import src.retrieval.semantic_search as module
        original = module.semantic_search
        module.semantic_search = _search
        try:
            cosine_spread.measure(["q"], k=2)
        finally:
            module.semantic_search = original

        assert seen == [True]

    def test_suppression_that_does_not_take_effect_refuses_to_measure(
        self, stub_search, monkeypatch
    ):
        """A no-op suppression must stop the run, not be assumed to work."""
        monkeypatch.setattr(
            "src.retrieval.retrieval_stats.retrieval_stats_disabled_now",
            lambda: False,
        )
        with pytest.raises(RuntimeError, match="did not take effect"):
            cosine_spread.measure(["q"], k=2)
