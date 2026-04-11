"""
tests/test_web_search_triggers.py

Tests for broadened web search trigger logic in policies.py.
Covers explicit markers (existing), temporal currency compound
markers, and factual uncertainty markers.
"""

from src.context.policies import classify_query


# ---------------------------------------------------------------------------
# Existing explicit markers (regression — should still work)
# ---------------------------------------------------------------------------

class TestExplicitWebSearchMarkers:

    def test_search_the_web(self):
        p = classify_query("search the web for Python tutorials")
        assert p.name == "web_search"
        assert p.use_web_search is True

    def test_google(self):
        p = classify_query("can you google the weather forecast")
        assert p.name == "web_search"

    def test_whats_the_latest(self):
        p = classify_query("what's the latest on the Rust release")
        assert p.name == "web_search"


# ---------------------------------------------------------------------------
# Temporal currency compound markers (new)
# ---------------------------------------------------------------------------

class TestTemporalCurrencyTriggers:

    def test_yesterday_plus_happened(self):
        p = classify_query("What happened yesterday in the election?")
        assert p.name == "web_search"
        assert p.use_web_search is True

    def test_last_night_plus_game(self):
        p = classify_query("Who won the game last night?")
        assert p.name == "web_search"

    def test_this_morning_plus_news(self):
        p = classify_query("Was there any news this morning about the storm?")
        assert p.name == "web_search"

    def test_this_month_plus_released(self):
        p = classify_query("Has anything been released this month by Anthropic?")
        assert p.name == "web_search"

    def test_yesterday_alone_is_not_web_search(self):
        """Temporal word alone (no event context) should NOT trigger web search.
        'What did I eat yesterday' is a personal/vault query."""
        p = classify_query("What did I eat yesterday?")
        assert p.name != "web_search"

    def test_weather_plus_temporal(self):
        p = classify_query("What's the weather like this morning?")
        assert p.name == "web_search"


# ---------------------------------------------------------------------------
# Factual uncertainty markers (new)
# ---------------------------------------------------------------------------

class TestFactualUncertaintyTriggers:

    def test_is_it_true_that(self):
        p = classify_query("Is it true that OpenAI released a new model?")
        assert p.name == "web_search"
        assert p.use_web_search is True

    def test_has_there_been(self):
        p = classify_query("Has there been an update to the Python packaging spec?")
        assert p.name == "web_search"

    def test_did_they_announce(self):
        p = classify_query("Did they announce the conference dates yet?")
        assert p.name == "web_search"

    def test_whats_happening_with(self):
        p = classify_query("What's happening with the EU AI Act?")
        assert p.name == "web_search"


# ---------------------------------------------------------------------------
# Non-web queries (should NOT trigger web search)
# ---------------------------------------------------------------------------

class TestNonWebQueries:

    def test_personal_today_query_not_web(self):
        """'What did I work on today' is a personal vault query, not web."""
        p = classify_query("What did I work on today?")
        assert p.name != "web_search"

    def test_reflective_query_not_web(self):
        p = classify_query("What patterns have you noticed in my work?")
        assert p.name == "reflective"

    def test_state_query_not_web(self):
        p = classify_query("What am I focused on right now?")
        assert p.name == "status_state"

    def test_general_question_not_web(self):
        p = classify_query("Explain how photosynthesis works")
        assert p.name == "default"
