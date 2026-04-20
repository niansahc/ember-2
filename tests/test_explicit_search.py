"""tests/test_explicit_search.py — explicit search invocation bypasses ask-first."""

from __future__ import annotations

import httpx
import pytest

from src.context.policies import classify_query


def _ollama_reachable() -> bool:
    """Return True if a local Ollama instance responds to /api/version.

    The ask-first classifier has three stages: structural rules (fast),
    embedding similarity via nomic-embed-text (Ollama), and LLM fallback
    via qwen3:8b (Ollama). Implicit-web-search queries like "what is the
    current population of Tokyo" require stages 2/3. When Ollama is not
    reachable (CI), classify_query falls back to stage 1 and those queries
    classify as "default" instead of "web_search".
    """
    try:
        httpx.get("http://localhost:11434/api/version", timeout=1.0)
        return True
    except Exception:
        return False


_NEEDS_OLLAMA = pytest.mark.skipif(
    not _ollama_reachable(),
    reason="stage 2/3 classifier needs Ollama (nomic-embed-text + qwen3:8b)",
)


class TestExplicitSearchClassification:
    """Explicit search phrases set explicit_search_request=True on the policy."""

    @pytest.mark.parametrize("query", [
        "search the web for tokyo population",
        "google that",
        "look it up",
        "look this up for me",
        "search online for the latest news",
        # ADR-034 tightening: "can you find" alone is too broad — it was
        # misclassifying vault lookups like "can you find my current focus"
        # as explicit web requests. The marker now requires "online" to
        # scope it to actual web requests.
        "can you find online the latest inflation data",
        "can you find it online please",
        "find this online please",
        "web search for NVIDIA stock price",
    ])
    def test_explicit_phrases_set_flag(self, query):
        policy = classify_query(query)
        assert policy.name == "web_search"
        assert policy.use_web_search is True
        assert policy.explicit_search_request is True

    @_NEEDS_OLLAMA
    @pytest.mark.parametrize("query", [
        "what is the current population of Tokyo",
        "who won the most recent Nobel Prize",
        "what happened in the news today",
    ])
    def test_non_explicit_triggers_do_not_set_flag(self, query):
        policy = classify_query(query)
        assert policy.name == "web_search"
        assert policy.use_web_search is True
        assert policy.explicit_search_request is False

    @pytest.mark.parametrize("query", [
        "how are you today",
        "what am I working on",
        "tell me about myself",
    ])
    def test_non_search_queries(self, query):
        policy = classify_query(query)
        assert policy.name != "web_search"
        assert policy.explicit_search_request is False


class TestExplicitSearchBypassesAskFirst:
    """When explicit_search_request is True, ask-first should not activate."""

    def test_ask_first_inactive_on_explicit_search(self):
        policy = classify_query("google the latest NVIDIA stock price")
        assert policy.explicit_search_request is True
        # Simulate the _ask_first_active computation
        _web_autonomous = False  # ask-first mode would normally be on
        _ask_first_active = (
            policy.name == "web_search"
            and not _web_autonomous
            and not policy.explicit_search_request
        )
        assert _ask_first_active is False

    @_NEEDS_OLLAMA
    def test_ask_first_active_on_implicit_search(self):
        policy = classify_query("what is the current stock price of NVIDIA")
        assert policy.explicit_search_request is False
        _web_autonomous = False
        _ask_first_active = (
            policy.name == "web_search"
            and not _web_autonomous
            and not policy.explicit_search_request
        )
        assert _ask_first_active is True
