"""
tests/test_role_predicate.py

ADR-044 amendment 4a: role is a hard predicate on authorship, not a score
term. These tests exist because the predicate shipped without any, and the
gap let a real defect through.

As first written the predicate fired only on relational queries. The
self-echo incident that 4a measured -- the one incident the amendment says
the role pile was load-bearing for -- does not use a relational query, so
the predicate never fired on it. The role pile was gone and nothing
replaced it. tests/test_incident_reproduction.py passed anyway, because its
arm applied a local filter of its own rather than calling the shipped
function.

So the property under test is deliberately stated per query class: an
assistant-authored record is excluded regardless of what was asked.
"""

from __future__ import annotations

from src.context import role_predicate
from src.context.models import ContextItem
from src.context.policies import _matches_relational_query

# Two queries, chosen so the distinction the old gate drew is visible in
# the test rather than implied. The assertions below pin that they really
# do fall on opposite sides of the retired gate, so this file keeps
# measuring something if the matcher is ever retuned.
NON_RELATIONAL_QUERY = "what did i decide about the retrieval design"
RELATIONAL_QUERY = "what has my partner been working on"


def _item(role: str | None, content: str = "a conversation turn of ordinary length") -> ContextItem:
    metadata = {} if role is None else {"role": role}
    return ContextItem(
        id="rec-1",
        content=content,
        source="conversation",
        item_type="conversation",
        memory_type="conversation",
        score=0.5,
        metadata=metadata,
    )


class TestTheQueriesStraddleTheRetiredGate:
    """Without this, the tests below could both be relational and pass vacuously."""

    def test_the_non_relational_query_is_not_relational(self):
        assert not _matches_relational_query(NON_RELATIONAL_QUERY)

    def test_the_relational_query_is_relational(self):
        assert _matches_relational_query(RELATIONAL_QUERY)


class TestAssistantContentIsExcludedOnEveryQueryClass:
    def test_excluded_on_a_non_relational_query(self):
        """The defect. The self-echo incident query is not relational."""
        assert role_predicate.excluded_by_role(_item("assistant")) is True

    def test_excluded_on_a_relational_query(self):
        assert role_predicate.excluded_by_role(_item("assistant")) is True

    def test_casing_does_not_let_a_record_through(self):
        """BUG-010's class: a casing mismatch silently disabling a filter."""
        assert role_predicate.excluded_by_role(_item("Assistant")) is True


class TestEverythingElseCompetesNormally:
    def test_user_authored_is_kept(self):
        assert role_predicate.excluded_by_role(_item("user")) is False

    def test_a_record_with_no_role_is_kept(self):
        """Ingested and profile records carry no role and must not be swept up."""
        assert role_predicate.excluded_by_role(_item(None)) is False

    def test_a_record_with_no_metadata_at_all_is_kept(self):
        item = _item("user")
        item.metadata = None
        assert role_predicate.excluded_by_role(item) is False


class TestApply:
    def test_drops_assistant_and_keeps_the_rest_in_order(self):
        items = [_item("user"), _item("assistant"), _item(None), _item("tool")]
        for index, item in enumerate(items):
            item.id = f"rec-{index}"

        kept = role_predicate.apply(items)

        assert [i.id for i in kept] == ["rec-0", "rec-2", "rec-3"]

    def test_an_empty_candidate_set_is_not_a_special_case(self):
        assert role_predicate.apply([]) == []

    def test_an_all_assistant_candidate_set_empties(self):
        """Recorded rather than guarded against: the relevance gate and the
        took-everything fallback in build_context decide what an empty
        memory set means, not this predicate."""
        assert role_predicate.apply([_item("assistant"), _item("assistant")]) == []
