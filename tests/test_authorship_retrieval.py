"""tests/test_authorship_retrieval.py — cluster 8 / task #24."""

from __future__ import annotations

import pytest

from src.context.models import ContextItem
from src.context.policies import _matches_relational_query
from src.context.ranker import ContextRanker
from scripts.rebuild_authorship_index import _classify


# ---------------------------------------------------------------------------
# Authorship inference (migration logic)
# ---------------------------------------------------------------------------


class TestClassify:
    def test_chatgpt_user_prefix_is_first_person(self):
        assert _classify("chatgpt_export", "user: I'm thinking about X", None) == "first_person"

    def test_chatgpt_assistant_prefix_is_third_party(self):
        assert _classify("chatgpt_export", "assistant: Here's how X works", None) == "third_party"

    def test_chatgpt_metadata_role_user_is_first_person(self):
        assert _classify("chatgpt_export", "plain body", '{"role": "user"}') == "first_person"

    def test_chatgpt_metadata_role_assistant_is_third_party(self):
        assert _classify("chatgpt_export", "plain body", '{"role": "assistant"}') == "third_party"

    def test_chatgpt_no_prefix_no_metadata_is_unknown(self):
        assert _classify("chatgpt_export", "no prefix here", None) == "unknown"

    def test_obsidian_export_is_first_person(self):
        assert _classify("obsidian_export", "some note", None) == "first_person"

    def test_journal_is_first_person(self):
        assert _classify("journal", "entry", None) == "first_person"

    def test_book_is_third_party(self):
        assert _classify("book", "prose", None) == "third_party"

    def test_pdf_is_third_party(self):
        assert _classify("pdf", "extracted text", None) == "third_party"

    def test_unknown_source_is_unknown(self):
        assert _classify("novel_format_7", "body", None) == "unknown"

    def test_none_source_is_unknown(self):
        assert _classify(None, "body", None) == "unknown"


# ---------------------------------------------------------------------------
# Relational query classifier
# ---------------------------------------------------------------------------


class TestRelationalQueryClassifier:
    @pytest.mark.parametrize(
        "q",
        [
            "what is my son's name",
            "tell me about my daughter",
            "how is my partner doing",
            "who is my mother",
            "remind me about my husband's birthday",
            "my kids — what do you know",
            "my health — any patterns",
            "how is my job going",
            "update me on my relationship",
        ],
    )
    def test_relational_true(self, q):
        assert _matches_relational_query(q) is True

    @pytest.mark.parametrize(
        "q",
        [
            "what is the current stock price of NVIDIA",
            "who won the most recent Nobel Prize",
            "how does python dataclasses work",
            "tell me about the French Revolution",
            "what is the son of god concept in Christianity",  # "son" without "my"
            "weather today",
            "",
        ],
    )
    def test_relational_false(self, q):
        assert _matches_relational_query(q) is False


# ---------------------------------------------------------------------------
# Authorship multiplier
# ---------------------------------------------------------------------------


def _make_item(authorship: str, score: float = 1.0) -> ContextItem:
    return ContextItem(
        id=f"id_{authorship}",
        content="body",
        source="ingested",
        item_type="ingested",
        memory_type="ingested",
        score=score,
        authorship=authorship,
    )


class TestAuthorshipScoring:
    def test_multipliers_applied_on_relational_query(self):
        ranker = ContextRanker()
        items = [
            _make_item("first_person", 1.0),
            _make_item("mixed", 1.0),
            _make_item("third_party", 1.0),
            _make_item("unknown", 1.0),
        ]
        ranker.apply_authorship_scoring(items, "what is my son's name")
        assert items[0].score == pytest.approx(1.0)
        assert items[1].score == pytest.approx(0.3)
        assert items[2].score == pytest.approx(0.0)
        assert items[3].score == pytest.approx(0.5)

    def test_no_op_on_non_relational_query(self):
        ranker = ContextRanker()
        items = [
            _make_item("third_party", 1.0),
            _make_item("unknown", 1.0),
        ]
        ranker.apply_authorship_scoring(items, "what is the current stock price of NVIDIA")
        # Scores untouched — third-party ingested content is useful for
        # knowledge questions.
        assert items[0].score == pytest.approx(1.0)
        assert items[1].score == pytest.approx(1.0)

    def test_missing_authorship_falls_back_to_unknown(self):
        ranker = ContextRanker()
        item = ContextItem(
            id="no_authorship",
            content="body",
            source="ingested",
            item_type="ingested",
            memory_type="ingested",
            score=1.0,
        )
        # Construct with default authorship, but clear it to simulate a
        # pre-migration record.
        item.authorship = ""
        ranker.apply_authorship_scoring([item], "tell me about my partner")
        assert item.score == pytest.approx(0.5)

    def test_metadata_authorship_fallback(self):
        """When the authorship attribute isn't set, metadata['authorship']
        is consulted as a fallback — useful for paths that build
        ContextItems without the field."""
        ranker = ContextRanker()
        item = ContextItem(
            id="meta_only",
            content="body",
            source="ingested",
            item_type="ingested",
            memory_type="ingested",
            score=1.0,
            metadata={"authorship": "third_party"},
        )
        item.authorship = ""  # simulate attribute not populated
        ranker.apply_authorship_scoring([item], "tell me about my child")
        assert item.score == pytest.approx(0.0)
