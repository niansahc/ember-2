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


# ---------------------------------------------------------------------------
# Layer 1: Entity-type triggers (volatile entity + state query pattern)
# ---------------------------------------------------------------------------

class TestEntityTypeTriggers:
    """Dual-condition: volatile entity signal AND state query pattern."""

    # --- Finance ---
    def test_bitcoin_price(self):
        p = classify_query("What is the current price of Bitcoin?")
        assert p.name == "web_search"

    def test_sp500_performance(self):
        p = classify_query("How did the S&P 500 perform this week?")
        assert p.name == "web_search"

    def test_interest_rate(self):
        p = classify_query("What is the current US federal interest rate?")
        assert p.name == "web_search"

    def test_stock_trading(self):
        p = classify_query("Is Tesla stock trading above 200?")
        assert p.name == "web_search"

    # --- Current roles ---
    def test_who_is_ceo(self):
        p = classify_query("Who is the CEO of OpenAI?")
        assert p.name == "web_search"

    def test_who_runs(self):
        p = classify_query("Who runs Google now?")
        assert p.name == "web_search"

    # --- Culture / entertainment ---
    def test_box_office(self):
        p = classify_query("What movies are currently number one at the box office?")
        assert p.name == "web_search"

    def test_billboard(self):
        p = classify_query("What are the most popular songs on the Billboard Hot 100?")
        assert p.name == "web_search"

    def test_streaming_show(self):
        p = classify_query("What's the most popular show on Netflix right now?")
        assert p.name == "web_search"

    def test_game_release(self):
        p = classify_query("What major video games were released recently?")
        assert p.name == "web_search"

    # --- Sports ---
    def test_nba_standings(self):
        p = classify_query("What are the current NBA playoff standings?")
        assert p.name == "web_search"

    def test_f1_winner(self):
        p = classify_query("Who won the most recent Formula 1 race?")
        assert p.name == "web_search"

    def test_tennis_ranking(self):
        p = classify_query("Who is currently ranked number one in men's tennis?")
        assert p.name == "web_search"

    def test_premier_league_results(self):
        p = classify_query("What are the latest Premier League results?")
        assert p.name == "web_search"

    # --- Weather / events ---
    def test_weather_forecast(self):
        p = classify_query("What is the current weather forecast for New York City?")
        assert p.name == "web_search"

    # --- Negative cases: entity without state query pattern ---
    def test_vault_question_about_stock(self):
        """'Tell me about my stock portfolio' has entity signal but no
        state query pattern — should NOT trigger web search."""
        p = classify_query("Tell me about my stock portfolio")
        assert p.name != "web_search"

    def test_personal_question_with_entity_word(self):
        """'I'm worried about the election' has entity signal but
        doesn't match state query pattern (not a question)."""
        p = classify_query("I'm worried about the election")
        assert p.name != "web_search"

    def test_explain_concept_not_web(self):
        """'Explain how the stock market works' has entity signal
        but 'explain how' is not a state query pattern."""
        p = classify_query("Explain how the stock market works")
        assert p.name != "web_search"


class TestLayerOneHelpers:
    """Direct tests on the pattern matching helpers."""

    def test_volatile_entity_detects_finance(self):
        from src.context.policies import _matches_volatile_entity
        assert _matches_volatile_entity("bitcoin price today") is True
        assert _matches_volatile_entity("s&p 500 performance") is True

    def test_volatile_entity_detects_sports(self):
        from src.context.policies import _matches_volatile_entity
        assert _matches_volatile_entity("nba playoff standings") is True

    def test_volatile_entity_misses_personal(self):
        from src.context.policies import _matches_volatile_entity
        assert _matches_volatile_entity("my favorite recipe") is False

    def test_state_query_matches_what_is(self):
        from src.context.policies import _matches_state_query
        assert _matches_state_query("what is the current price") is True

    def test_state_query_matches_who_is(self):
        from src.context.policies import _matches_state_query
        assert _matches_state_query("who is the ceo of apple") is True

    def test_state_query_matches_is_opening(self):
        from src.context.policies import _matches_state_query
        assert _matches_state_query("is tesla stock above 200") is True

    def test_state_query_misses_imperative(self):
        from src.context.policies import _matches_state_query
        assert _matches_state_query("tell me about stocks") is False

    def test_state_query_misses_statement(self):
        from src.context.policies import _matches_state_query
        assert _matches_state_query("i'm worried about bitcoin") is False
