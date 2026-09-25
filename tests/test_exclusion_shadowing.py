"""
tests/test_exclusion_shadowing.py

Phase 2 triage, findings 14 and 15: exclusion rules that existed twice.

`ContextRetriever._should_exclude_content` re-checked five rules that
`semantic_search.should_exclude_result` had already applied to every
result it returns. The copies were unreachable, not merely redundant,
and the hazard was drift: two implementations of one rule, either of
which could be edited without the other.

Deleting them is safe only while an invariant holds -- every result
reaching the retriever's filter has already passed the upstream one. The
docstring on that method argues it. These tests enforce it, because an
argument in a docstring does not fail when someone adds a branch.

Two properties:

  * the capability still exists, upstream, end to end
  * the structure the deletion depends on is still there

Fixtures are synthetic (CLAUDE.md Vault Privacy Rule).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.context.retriever import ContextRetriever

REPO_ROOT = Path(__file__).resolve().parents[1]
SEARCH_SOURCE = REPO_ROOT / "src" / "retrieval" / "semantic_search.py"

# Box-drawing characters by codepoint, so the source file stays ASCII
# (house rule) and the characters under test stay visible in a diff
# instead of rendering as whatever the reader's terminal does with them.
BOX_VERTICAL = chr(0x2502)
BOX_BRANCH = chr(0x251C)

# The rules that moved upstream. Their absence here is the change; their
# presence upstream is what makes it safe.
SHADOWED = (
    "retriever.exclude.empty",
    "retriever.exclude.under_40_chars",
    "retriever.exclude.meta_marker",
    "retriever.exclude.json_payload",
    "retriever.exclude.code_fence",
)

# The rules with no upstream equivalent.
UNIQUE = (
    "retriever.exclude.box_drawing",
    "retriever.exclude.recent_themes_prefix",
    "retriever.exclude.query_verbatim_in_content",
    "retriever.exclude.jaccard_over_0_60",
)


def _retriever() -> ContextRetriever:
    return ContextRetriever(memory_service=MagicMock())


class TestTheCapabilitySurvivedUpstream:
    """Deleting a duplicate must not delete the rule."""

    @pytest.mark.parametrize("content,rule", [
        ("", "empty"),
        ("too short", "under_40_chars"),
        ("user asked: something that is quite long enough to clear the floor",
         "meta_marker"),
        ('{"memory": "a json payload long enough to clear the length floor"}',
         "json_payload"),
        ("a record with a fence ``` in it, long enough to clear the floor",
         "code_fence"),
    ])
    def test_upstream_still_excludes(self, content, rule):
        from src.retrieval.semantic_search import should_exclude_result, normalize_text

        assert should_exclude_result(normalize_text(content)) is True, rule

    def test_a_clean_record_is_not_excluded_upstream(self):
        """Control: the rules above exclude for their own reason."""
        from src.retrieval.semantic_search import should_exclude_result, normalize_text

        clean = ("a perfectly ordinary conversation turn with enough length "
                 "to clear the forty character floor")
        assert should_exclude_result(normalize_text(clean)) is False


class TestTheRetrieverNoLongerDuplicates:
    def test_the_shadowed_rules_are_gone(self):
        source = inspect.getsource(ContextRetriever._should_exclude_content)
        for site in SHADOWED:
            assert site not in source, (
                f"{site} is back. It is unreachable -- semantic_search "
                "already applied it to every result that gets here -- and "
                "a second copy of a rule is a rule that can drift."
            )

    def test_the_unique_rules_remain(self):
        source = inspect.getsource(ContextRetriever._should_exclude_content)
        for site in UNIQUE:
            assert site in source, f"{site} has no upstream equivalent"

    @pytest.mark.parametrize("content", [
        f"some content with {BOX_VERTICAL} box drawing and {BOX_BRANCH} "
        "branches, long enough to clear the length floor",
        "recent themes: user complaints that go on for more than forty characters",
    ])
    def test_the_unique_rules_still_fire(self, content):
        assert _retriever()._should_exclude_content(content, "anything") is True


class TestTheInvariantTheDeletionDependsOn:
    """Every result semantic_search returns has passed its filter.

    Checked structurally: each `results.append` must sit in a function
    that also calls `should_exclude_result`. A new branch that collects
    results without filtering them would put unfiltered content in front
    of a retriever that no longer re-checks.
    """

    def _search_function(self) -> ast.FunctionDef:
        tree = ast.parse(SEARCH_SOURCE.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "semantic_search":
                return node
        raise AssertionError("semantic_search not found")

    def test_every_result_append_is_matched_by_a_filter_call(self):
        function = self._search_function()
        appends = sum(
            1 for n in ast.walk(function)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "append"
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "results"
        )
        filters = sum(
            1 for n in ast.walk(function)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "should_exclude_result"
        )
        assert appends > 0
        assert filters == appends, (
            f"{appends} result-collecting branches but {filters} filter "
            "calls. A branch that appends without filtering hands unfiltered "
            "content to a retriever that stopped re-checking (findings 14)."
        )

    def test_the_filter_callers_all_consume_semantic_search(self):
        """Both call sites must be fed by the upstream filter."""
        source = REPO_ROOT / "src" / "context" / "retriever.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))

        callers = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            calls = {
                n.func.attr for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            } | {
                n.func.id for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
            if "_should_exclude_content" in calls:
                callers.append((node.name, calls))

        assert callers, "no callers found; the filter may have been orphaned"
        for name, calls in callers:
            assert "semantic_search" in calls or "_semantic_search" in calls, (
                f"{name} calls _should_exclude_content but does not get its "
                "candidates from semantic_search, so the five rules deleted "
                "in findings 14 are load-bearing on that path"
            )

    def test_both_sides_normalize_identically(self):
        """The predicates matched only because the inputs matched."""
        from src.retrieval.semantic_search import normalize_text

        messy = "  A Record\twith   Mixed   Whitespace  "
        assert normalize_text(messy) == _retriever()._normalize_text(messy)


class TestSelectionDedupIsNotShadowedOnReflections:
    """Finding 15, which the control-flow check answers the other way.

    ContextRetriever.retrieve() deduplicates memory_items and does NOT
    deduplicate reflection_items, so ContextService._deduplicate is the
    only dedup the reflection channel gets. It reads as shadowed in the
    counters only because the channel delivered nothing before #241.
    """

    def test_the_retriever_does_not_deduplicate_reflections(self):
        source = inspect.getsource(ContextRetriever.retrieve)
        assert "_deduplicate_items(memory_items)" in source
        assert "_deduplicate_items(reflection_items)" not in source, (
            "if the retriever starts deduplicating reflections, the service "
            "call becomes shadowed on both channels and can be removed -- "
            "but moving it changes which duplicate survives, because the "
            "two run either side of ranking"
        )

    def test_the_service_deduplicates_both_channels(self):
        from src.context.service import ContextService

        source = inspect.getsource(ContextService._build_context)
        assert "self._deduplicate(filtered_memory)" in source
        assert "self._deduplicate(ranked_reflections)" in source
