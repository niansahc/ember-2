"""
tests/test_web_search_eval.py

Tests for the web search accuracy evaluation harness. Covers question
battery structure, grade validation, and utility functions. Does NOT
test actual API calls (those require a running Ember API + Ollama).
"""

import pytest

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from eval_web_search import (
    TEST_QUESTIONS,
    GRADE_LABELS,
    CATEGORY_DISPLAY,
)


class TestQuestionBattery:
    """Validate the structure and coverage of the 30-question battery."""

    def test_total_question_count(self):
        assert len(TEST_QUESTIONS) == 30

    def test_six_per_category(self):
        counts = {}
        for q in TEST_QUESTIONS:
            cat = q["category"]
            counts[cat] = counts.get(cat, 0) + 1
        for cat, count in counts.items():
            assert count == 6, f"Category '{cat}' has {count} questions, expected 6"

    def test_five_categories(self):
        categories = {q["category"] for q in TEST_QUESTIONS}
        expected = {"current_events", "science_tech", "sports", "business", "culture"}
        assert categories == expected

    def test_all_categories_have_display_names(self):
        for q in TEST_QUESTIONS:
            assert q["category"] in CATEGORY_DISPLAY

    def test_question_schema(self):
        required_keys = {"question", "expected_answer_hint", "as_of", "category"}
        for i, q in enumerate(TEST_QUESTIONS):
            assert required_keys.issubset(q.keys()), f"Question {i} missing keys: {required_keys - q.keys()}"
            assert isinstance(q["question"], str) and q["question"].strip()
            assert isinstance(q["expected_answer_hint"], str) and q["expected_answer_hint"].strip()
            assert isinstance(q["as_of"], str)
            assert isinstance(q["category"], str)

    def test_no_duplicate_questions(self):
        questions = [q["question"] for q in TEST_QUESTIONS]
        assert len(questions) == len(set(questions))


class TestGradeLabels:
    """Validate grade label consistency."""

    def test_four_grade_labels(self):
        assert len(GRADE_LABELS) == 4

    def test_expected_labels(self):
        assert set(GRADE_LABELS) == {"accurate", "partial", "hallucination", "search_not_triggered"}


class TestCategoryDisplay:
    """Validate category display name mapping."""

    def test_all_categories_mapped(self):
        categories = {q["category"] for q in TEST_QUESTIONS}
        for cat in categories:
            assert cat in CATEGORY_DISPLAY
            assert isinstance(CATEGORY_DISPLAY[cat], str)
            assert CATEGORY_DISPLAY[cat].strip()


class TestQuestionTimeRelevance:
    """All questions should require live web data (time-sensitive)."""

    @pytest.mark.parametrize("question", [q for q in TEST_QUESTIONS])
    def test_question_is_time_sensitive(self, question):
        """Questions should contain temporal markers that indicate they
        need current data, not just static knowledge."""
        q = question["question"].lower()
        temporal_markers = (
            "current", "today", "right now", "this week", "this month",
            "recently", "latest", "most recent", "currently", "happening",
            "new", "forecast",
        )
        assert any(m in q for m in temporal_markers), (
            f"Question may not require web search: {question['question']}"
        )


class TestNoAnthropicDependency:
    """The eval must not import or reference the Anthropic SDK."""

    def test_no_anthropic_import(self):
        import importlib
        source_path = TOOLS_DIR / "eval_web_search.py"
        source = source_path.read_text(encoding="utf-8")
        assert "import anthropic" not in source
        assert "ANTHROPIC_API_KEY" not in source
        assert "claude" not in source.lower().split("# ")[0]  # ignore comments
