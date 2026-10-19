"""
tests/test_ask_first_offer_phrasing.py

Every offer Ember can emit is acceptable (2026-10-18 review, findings 1 and
1b). OFFER_PATTERNS in src/llm/ask_first_validator.py is the one list of
offer phrasings: the ask-first validator accepts a reply on it, the post-gen
retry offer matches it, and _write_pending_confirmation writes a pending on
it. Before this, the writer kept its own list, so the retry offer and some
accepted model offers wrote no pending and the user's "yes" did nothing.

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

QUERY = "latest widget price"

# One example per OFFER_PATTERNS entry (and per alternative inside it), plus
# the two fixed texts the pipeline can substitute.
OFFERS = [
    SCRIPTED_ASK_FIRST_RESPONSE,
    RETRY_OFFER_RESPONSE,
    "I don't have that. Want me to search for it?",
    "Do you want me to look into it?",
    "Want me to find the current figure?",
    "Want me to try the search again?",
    "Let me know if you want me to search.",
    "Should I search the web?",
    "Shall I look that up?",
    "I don't have that yet. Would you like me to search for it?",
    "Would you like me to find out?",
    "Would you like me to try again?",
    "I can search for that, interested?",
    "I can look that up for you?",
    "I could look it up for you if you like.",
    "I could search for that if you'd like.",
]

NON_OFFERS = [
    "I don't have access to live data.",
    "Check Google for the latest.",
    "I can search but I won't.",
    "Here's what I know from memory.",
    "You might want to visit the official site.",
]


def _active_pendings(session_id: str) -> list:
    from src.api.openai_adapter import state_service
    records = state_service.read_by_category("pending_confirmation")
    resolved = state_service.resolved_ids(records)
    return [r for r in records
            if r.id not in resolved
            and (r.metadata or {}).get("session_id") == session_id]


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
    _write_pending_confirmation(offer, QUERY, sid, existing_pending=[])
    pendings = _active_pendings(sid)
    assert len(pendings) == 1
    assert pendings[0].metadata["query"] == QUERY
    assert pendings[0].text == find_offer_sentence(offer)


@pytest.mark.parametrize("text", NON_OFFERS)
def test_non_offers_are_not_accepted_and_write_nothing(text):
    """Control for the two tests above: the shared list is not a match-all."""
    from src.api.openai_adapter import _write_pending_confirmation
    assert has_search_offer(text) is False
    assert is_substitutable(text) is True
    sid = _sid("nonoffer", text)
    _write_pending_confirmation(text, QUERY, sid, existing_pending=[])
    assert _active_pendings(sid) == []


def test_retry_offer_writes_a_pending_for_the_original_query():
    """Repro F1/H1 (unit): the retry offer used to write nothing."""
    from src.api.openai_adapter import _write_pending_confirmation
    sid = "sess_test_retry_offer_unit"
    _write_pending_confirmation(RETRY_OFFER_RESPONSE, QUERY, sid, existing_pending=[])
    pendings = _active_pendings(sid)
    assert [p.metadata["query"] for p in pendings] == [QUERY]
    assert pendings[0].text == "Want me to try again?"


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
    literal_lists = [n for n in ast.walk(tree) if isinstance(n, (ast.List, ast.Tuple))
                     and n.elts and all(isinstance(e, ast.Constant)
                                        and isinstance(e.value, str) for e in n.elts)]
    assert literal_lists == []


def test_structural_check_flags_a_writer_with_its_own_list():
    """Positive control for the structural test above."""
    src = "def w(reply):\n    ask_patterns = ['want me to search', 'shall i look']\n"
    tree = ast.parse(src)
    literal_lists = [n for n in ast.walk(tree) if isinstance(n, (ast.List, ast.Tuple))
                     and n.elts and all(isinstance(e, ast.Constant)
                                        and isinstance(e.value, str) for e in n.elts)]
    assert len(literal_lists) == 1
