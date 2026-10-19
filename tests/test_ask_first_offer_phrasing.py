"""
tests/test_ask_first_offer_phrasing.py

Every offer Ember can emit is acceptable, and only an offer to look
something up on the web counts as one. OFFER_PATTERNS in
src/llm/ask_first_validator.py is the one list of offer phrasings: the
ask-first validator accepts a reply on it, the post-gen retry offer matches
it, and _write_pending_confirmation writes a pending on it.

A pending is a web search waiting for the user's "yes", and that search
sends the user's previous message to the search engines. So "look" and
"find" offers count only with a web object ("look it up", "look up X
online"), a retry offer must name the search, and an offer that names one
of the user's own sources ("look it up in your notes") is not a web offer.

The writer runs for real against the conftest throwaway test vault.
Synthetic session ids only.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from src.llm.ask_first_validator import (
    RETRY_OFFER_RESPONSE,
    SCRIPTED_ASK_FIRST_RESPONSE,
    find_offer_sentence,
    has_search_offer,
    is_substitutable,
)
from tests.conftest import active_pendings as _active_pendings

QUERY = "latest widget price"

# Examples across the OFFER_PATTERNS lead-ins and web objects, plus the two
# fixed texts the pipeline can substitute.
OFFERS = [
    SCRIPTED_ASK_FIRST_RESPONSE,
    RETRY_OFFER_RESPONSE,
    "I don't have that. Want me to search for it?",
    "Do you want me to look it up?",
    "Want me to look up the score online?",
    "Want me to try the search again?",
    "Want me to try searching again?",
    "Let me know if you want me to search.",
    "Should I search the web?",
    "Shall I look that up?",
    "I don't have that yet. Would you like me to search for it?",
    "Would you like me to look online?",
    "Would you like me to find out the price on the web?",
    "I can search for that, interested?",
    "I can look that up for you?",
    "I could look it up for you if you like.",
    "I could search for that if you'd like.",
    # Curly apostrophe, as a mobile keyboard sends it.
    "I could search for that if you\u2019d like.",
    "Want me to search your notes and the web?",
]

NON_OFFERS = [
    "I don't have access to live data.",
    "Check Google for the latest.",
    "I can search but I won't.",
    "Here's what I know from memory.",
    "You might want to visit the official site.",
    # Look and find without a web object: not a web search.
    "Would you like me to look over the draft for typos?",
    "Want me to find the file?",
    "Should I look at your notes?",
    "Do you want me to look into it?",
    "Would you like me to find out?",
    "Want me to find the current figure?",
    # A bare retry does not name the search.
    "Want me to try again?",
    # One of the user's own sources, not the web.
    "I could look it up in your notes if you like.",
    "Want me to search your notes?",
]


def _string_literal_lists(tree: ast.AST) -> list:
    """List or tuple literals made only of strings: an offer phrase list."""
    return [n for n in ast.walk(tree) if isinstance(n, (ast.List, ast.Tuple))
            and n.elts and all(isinstance(e, ast.Constant)
                               and isinstance(e.value, str) for e in n.elts)]


def _sid(prefix: str, text: str) -> str:
    return f"sess_test_{prefix}_{abs(hash(text)) % 100000}"


@pytest.mark.parametrize("offer", OFFERS)
def test_every_offer_is_accepted_by_the_validator(offer):
    assert has_search_offer(offer) is True
    assert is_substitutable(offer) is False


@pytest.mark.parametrize("offer", OFFERS)
def test_every_offer_writes_a_pending(offer):
    """The writer recognises every offer the validator accepts or the
    pipeline substitutes, so the user's "yes" has something to confirm."""
    from src.api.openai_adapter import _write_pending_confirmation
    sid = _sid("offer", offer)
    _write_pending_confirmation(offer, QUERY, sid)
    pendings = _active_pendings(sid)
    assert len(pendings) == 1
    assert pendings[0].metadata["query"] == QUERY
    assert pendings[0].text == find_offer_sentence(offer)


@pytest.mark.parametrize("text", NON_OFFERS)
def test_non_offers_are_not_accepted_and_write_nothing(text):
    """Control for the two tests above: the shared list is not a match-all,
    and a non-web offer leaves nothing a "yes" could turn into a search."""
    from src.api.openai_adapter import _write_pending_confirmation
    assert has_search_offer(text) is False
    assert is_substitutable(text) is True
    sid = _sid("nonoffer", text)
    _write_pending_confirmation(text, QUERY, sid)
    assert _active_pendings(sid) == []


def test_curly_offer_sentence_is_returned_as_written():
    reply = "I don't have it. I could search for that if you\u2019d like."
    assert find_offer_sentence(reply) == "I could search for that if you\u2019d like."


def test_retry_offer_writes_a_pending_for_the_original_query():
    """The retry offer is an offer: a "yes" to it re-runs the search."""
    from src.api.openai_adapter import _write_pending_confirmation
    sid = "sess_test_retry_offer_unit"
    _write_pending_confirmation(RETRY_OFFER_RESPONSE, QUERY, sid)
    pendings = _active_pendings(sid)
    assert [p.metadata["query"] for p in pendings] == [QUERY]
    assert pendings[0].text == "Want me to try the search again?"


def test_offer_sentence_is_the_sentence_with_the_offer():
    reply = "Prices change daily. I don't have today's. Want me to search for it? Happy to."
    assert find_offer_sentence(reply) == "Want me to search for it?"
    assert find_offer_sentence("No offer here.") is None


def test_post_gen_retry_offer_is_the_shared_constant():
    from src.llm.post_gen_pipeline import run_post_gen_pipeline
    result = run_post_gen_pipeline(
        "I'm working on it.", intent_class="web_search",
        web_search_autonomous=False, used_web_search=False, used_vault=False,
        used_vision=False, confirmation_search_failed=True,
    )
    assert result.reply == RETRY_OFFER_RESPONSE


def test_pending_writer_keeps_no_phrase_list_of_its_own():
    """Structural: the writer reads OFFER_PATTERNS through find_offer_sentence
    and holds no string literal list of offer phrases."""
    from src.api import openai_adapter
    tree = ast.parse(inspect.getsource(openai_adapter._write_pending_confirmation))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "find_offer_sentence" in names
    assert _string_literal_lists(tree) == []


def test_structural_check_flags_a_writer_with_its_own_list():
    """Positive control for the structural test above."""
    src = "def w(reply):\n    ask_patterns = ['want me to search', 'shall i look']\n"
    assert len(_string_literal_lists(ast.parse(src))) == 1
