"""tests/test_intent_classifier.py

Unit tests for the three-tier intent classifier (ADR-034).

Stage 1 (commit 1) tests cover: definite internet signals, the compound
first-person guard, and escalation when no signal matches.

Stages 2 and 3 tests are added in their respective commits on the same
branch.
"""

from __future__ import annotations

import pytest

from src.llm.intent_classifier import (
    _stage1_classify,
    classify_intent,
)


class TestStage1DefiniteInternetSignals:
    """Stage 1 routes clear external-world queries to needs_internet."""

    @pytest.mark.parametrize(
        "query",
        [
            "what's the weather today",
            "forecast for this weekend",
            "current temperature in Richmond",
            "bitcoin price right now",
            "stock price of NVDA",
            "crypto price today",
            "today's news",
            "current headlines",
            "latest updates on the election",
            "live score of the game",
            "standings after last night's match",
            "who won the championship",
        ],
    )
    def test_external_queries_route_to_needs_internet(self, query):
        assert _stage1_classify(query) == "needs_internet"


class TestStage1CompoundGuardAnchorOverrides:
    """Signal + first-person + external anchor => internet still wins.

    The compound guard only blocks to vault when first-person is present
    AND no external-world anchor word is present. If an anchor word appears,
    the query is asking about the external thing even though it uses
    first-person framing.
    """

    @pytest.mark.parametrize(
        "query",
        [
            "I'm checking today's news",
            "my stock price alerts fire often",
            "I mentioned the weather last week",
            "I've been watching live score updates",
            "my bitcoin price alerts keep firing",
            "I said today's news was grim",
        ],
    )
    def test_signal_with_anchor_routes_to_internet_despite_first_person(self, query):
        result = _stage1_classify(query)
        assert result == "needs_internet", (
            f"{query!r}: first-person with external anchor should route to "
            f"internet, got {result!r}"
        )


class TestStage1CompoundGuardBlocksToVault:
    """Signal + first-person + NO anchor => guard blocks to vault.

    These queries trigger a definite-internet signal pattern but lack any
    external-world anchor word. The compound guard treats them as personal
    framing of the signal word, not actual requests for external info.
    """

    @pytest.mark.parametrize(
        "query",
        [
            "my latest forecast was off",
            "I mentioned the temperature readings",
            "my standings list is growing",
            "I've been checking my who won predictions",
        ],
    )
    def test_signal_without_anchor_blocks_to_vault(self, query):
        assert _stage1_classify(query) == "vault_answerable"


class TestStage1Escalation:
    """Queries with no definite signal escalate (return None)."""

    @pytest.mark.parametrize(
        "query",
        [
            "what am i working on",
            "what did i say yesterday",
            "remind me about my current focus",
            "how have I been feeling",
            "tell me about my projects",
            "what's on my plate this week",
            # ADR §Stage 1 example: first-person + "currently" but no signal
            "what is my doctor currently recommending for my condition",
            # ADR §Stage 1 example: first-person subject, external verification
            # need but no Stage 1 signal — will resolve at Stage 2 or 3.
            "I've been reading that inflation is rising, is that still true",
        ],
    )
    def test_no_signal_escalates(self, query):
        assert _stage1_classify(query) is None

    def test_empty_query_escalates(self):
        assert _stage1_classify("") is None


class TestClassifyIntentPublicAPI:
    """classify_intent() always returns a valid label and logs once."""

    def test_stage1_resolves_to_needs_internet(self):
        assert classify_intent("what's the weather today") == "needs_internet"

    def test_stage1_resolves_to_vault_answerable(self):
        assert (
            classify_intent("my standings list is growing")
            == "vault_answerable"
        )

    def test_no_signal_falls_back_to_safe_default(self):
        """With Stages 2+3 not yet implemented, escalation falls to vault."""
        assert classify_intent("what am i working on") == "vault_answerable"

    def test_empty_query_falls_back_to_safe_default(self):
        assert classify_intent("") == "vault_answerable"

    def test_stage1_log_line_emitted(self, caplog):
        with caplog.at_level("INFO", logger="ember.intent_classifier"):
            classify_intent("what's the weather today")
        matches = [r for r in caplog.records if "[INTENT_CLASSIFY]" in r.message]
        assert len(matches) == 1
        assert "stage=stage1" in matches[0].message
        assert "label=needs_internet" in matches[0].message
        assert "confidence=none" in matches[0].message

    def test_fallback_log_line_emitted(self, caplog):
        with caplog.at_level("INFO", logger="ember.intent_classifier"):
            classify_intent("what am i working on")
        matches = [r for r in caplog.records if "[INTENT_CLASSIFY]" in r.message]
        assert len(matches) == 1
        assert "stage=fallback" in matches[0].message
        assert "label=vault_answerable" in matches[0].message


class TestLogLineIsAsciiOnly:
    """CLAUDE.md rule 7: diagnostic logs must be ASCII-only on Windows cp1252."""

    def test_log_line_is_pure_ascii(self, caplog):
        with caplog.at_level("INFO", logger="ember.intent_classifier"):
            classify_intent("what's the weather today")
        for record in caplog.records:
            if "[INTENT_CLASSIFY]" in record.message:
                # Encode to ASCII must succeed without replacement chars.
                record.message.encode("ascii")


class TestAdrRequiredCasesStage1:
    """Cases from the kickoff that Stage 1 alone can resolve.

    Cases requiring Stages 2 or 3 are covered in their respective commits
    on this branch. This class documents which kickoff cases land at
    Stage 1 vs later stages.
    """

    # Stage 1 positive cases — covered by the weather/stock/news signal
    # patterns.
    def test_whats_the_weather_today(self):
        assert classify_intent("What's the weather today?") == "needs_internet"

    # Cases requiring Stages 2+ — until those land, they fall back to the
    # safe vault_answerable default. Tests here assert only that Stage 1
    # escalates (returns None) so later commits can tighten them.
    @pytest.mark.parametrize(
        "query",
        [
            "I'm currently working on a project",
            "I've been watching a show",
            "What are my latest projects?",
            "What did I say about the migration?",
            # Stage 1 does not catch this specific Bitcoin phrasing
            # ("current price of Bitcoin" — current+news pattern wants
            # news/headlines/updates after current, not "price"). Stage 2
            # will cover it.
            "What's the current price of Bitcoin?",
            # First-person + external verification — Stage 3 territory.
            "I've been reading that inflation is rising, is that still true",
        ],
    )
    def test_stage1_escalates_on_cases_needing_later_stages(self, query):
        assert _stage1_classify(query) is None
