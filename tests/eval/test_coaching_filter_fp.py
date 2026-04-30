"""tests/eval/test_coaching_filter_fp.py

Coaching filter false-positive regression suite.

Runs clean responses through filter_coaching_frame() and asserts the
returned text is unmodified. Uses intent_class="default" so the
internal is_emotional gate evaluates True (default is in
_EMOTIONAL_INTENTS), which stress-tests the filter on emotional
intent. A well-scoped filter must let clean responses through even
under that condition.

Stage 0.5 (semantic identity collapse) calls Ollama and is mocked to
return False so the test is deterministic regardless of whether
Ollama is reachable. Stage 0 (pattern-based identity collapse) is
pure regex and would only fire on identity-override phrases that none
of these fixtures contain, so it is left untouched.

NOTE: this file lives in tests/eval/ but is NOT marked
@pytest.mark.eval. The default pytest invocation excludes
@pytest.mark.eval tests via the addopts setting in
tests/pytest.ini ('not eval'). Unmarked tests in tests/eval/ run
as part of the default suite.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.llm.coaching_filter import filter_coaching_frame


# ---------------------------------------------------------------------------
# Category 1: factual responses with caring language
# ---------------------------------------------------------------------------


class TestFactualResponsesPassThrough:
    """Plain factual content with light caring language. Nothing in
    these matches any pattern in _COACHING_CLOSINGS,
    _THERAPEUTIC_OPENERS, _THERAPEUTIC_MID_RESPONSE, or
    _SYCOPHANTIC_OPENERS. They must pass through unmodified."""

    @pytest.mark.parametrize(
        "text",
        [
            "This is an important distinction to note when configuring the system.",
            "The migration will affect all records created before the cutoff date. Handle this carefully.",
            "You'll want to back up the vault before running the script.",
        ],
    )
    def test_factual_response_unchanged(self, text):
        with patch(
            "src.llm.coaching_filter._check_semantic_identity_collapse",
            return_value=False,
        ):
            result = filter_coaching_frame(
                text, intent_class="default", is_conversational=False,
            )
        assert result == text


# ---------------------------------------------------------------------------
# Category 2: technical responses using filter-vocabulary words in non-coaching contexts
# ---------------------------------------------------------------------------


class TestTechnicalResponsesPassThrough:
    """Technical responses that share lexical material with the filter
    vocabulary ('I'm here', 'fix that', 'okay to') but in unambiguous
    technical contexts. Pattern scope must be narrow enough that these
    pass through.

    NOTE on the second fixture: the original spec was
    "Let's fix that by updating the config file. The current value is
    wrong." which collides with the v0.18.0 pattern
    r"let(?:'s| us) fix (?:that|this)" added to _THERAPEUTIC_MID_RESPONSE
    in commit cd9e0a0 (PR #35). The replacement phrase below is
    unambiguously technical and avoids the collision until the pattern
    is refined or the fixture is reconciled at PR #35 merge time.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "I'm here to help debug this -- the stack trace points to line 47.",
            "The config file has a wrong value. Update it to match the documented default.",
            "It's okay to run this in production -- the flag is safe.",
        ],
    )
    def test_technical_response_unchanged(self, text):
        with patch(
            "src.llm.coaching_filter._check_semantic_identity_collapse",
            return_value=False,
        ):
            result = filter_coaching_frame(
                text, intent_class="default", is_conversational=False,
            )
        assert result == text


# ---------------------------------------------------------------------------
# Category 3: direct responses containing warmth without coaching frame
# ---------------------------------------------------------------------------


class TestDirectResponsesWithWarmthPassThrough:
    """Direct, substantive answers that include human warmth phrasing
    without the structural markers of a coaching closing or
    therapeutic opener. They must pass through unmodified."""

    @pytest.mark.parametrize(
        "text",
        [
            "That sounds like a hard week. The deployment is blocked on the auth fix.",
            "Makes sense. The pattern you're describing usually means a cache miss.",
        ],
    )
    def test_direct_warm_response_unchanged(self, text):
        with patch(
            "src.llm.coaching_filter._check_semantic_identity_collapse",
            return_value=False,
        ):
            result = filter_coaching_frame(
                text, intent_class="default", is_conversational=False,
            )
        assert result == text
