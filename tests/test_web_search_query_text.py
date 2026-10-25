"""
tests/test_web_search_query_text.py

The web search sends only the user's own text. Ember prefixes its own task
and timer notes onto the message the model sees, a confirmation can carry a
follow-up, and the UI prepends an upload note naming the files the user just
uploaded. None of that may reach SearXNG: every search path (context
assembly, the backstop, the confirm path) builds its query from the user's
text alone.

Mocks follow tests/test_ask_first_routing.py: SearXNG via
src.tools.web_search.requests.get, the model via llm_adapter, the conftest
throwaway test vault. Synthetic text only.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from src.tools.web_search import search_text_from
from tests.conftest import seed_pending
from tests.test_ask_first_routing import (  # noqa: F401  (client is a fixture)
    CLEAN_HIT,
    QUERY,
    _model_message,
    _searxng,
    _sent_queries,
    _turn,
    client,
)

USER_TEXT = "what is the latest widget price"
UPLOAD_NOTE = ("[I just uploaded notes.pdf to my vault. The content is now "
               "available in your memory.] ")


def _autonomous_turn(client, vault, query, responses=None, **kwargs):
    """An autonomous turn whose context assembly searches: _turn routes the
    request to web_search, and assembly receives the turn's policy."""
    return _turn(client, vault, responses or [_searxng([CLEAN_HIT])] * 3, query=query,
                 autonomous=True, **kwargs)


def test_task_note_never_reaches_searxng(client, isolate_to_test_vault):
    with patch("src.tasks.task_handler.detect_explicit_task_request",
               return_value=["synthetic task"]), \
         patch("src.tasks.task_handler.create_task",
               return_value=MagicMock(created=True)):
        _, _, llm, get = _autonomous_turn(client, isolate_to_test_vault, USER_TEXT,
                                          session_id="sess_test_task_note_search")
    # The note reached the model, so it was there to leak.
    assert "[System: tasks created" in _model_message(llm)
    assert get.call_count >= 1
    assert _sent_queries(get) == [USER_TEXT] * get.call_count


def test_timer_note_never_reaches_searxng(client, isolate_to_test_vault):
    with patch("src.state.timer_service.detect_start_timer",
               return_value="synthetic timer"), \
         patch("src.state.timer_service.start_timer"):
        _, _, llm, get = _autonomous_turn(client, isolate_to_test_vault, USER_TEXT,
                                          session_id="sess_test_timer_note_search")
    assert "[System: timer started" in _model_message(llm)
    assert get.call_count >= 1
    assert _sent_queries(get) == [USER_TEXT] * get.call_count


def test_follow_up_never_reaches_searxng(client, isolate_to_test_vault):
    """A confirmation's follow-up goes to the model, never to the search.
    With autonomous on and a confirm search that kept nothing, both the
    confirm search and context assembly run."""
    sid = "sess_test_follow_up_search"
    seed_pending(sid, QUERY)
    _, _, llm, get = _autonomous_turn(client, isolate_to_test_vault,
                                      "yes, but what about the forecast?",
                                      responses=[_searxng([]), _searxng([CLEAN_HIT])],
                                      session_id=sid)
    assert "forecast" in _model_message(llm)
    assert get.call_count == 2
    assert _sent_queries(get) == [QUERY, QUERY]


def test_assembly_does_not_repeat_a_confirm_search_that_kept_results(
        client, isolate_to_test_vault, caplog):
    """The confirm search's results win the merge, so context assembly does
    not send the same text again. The control is the test above: when the
    confirm search kept nothing, assembly searched."""
    sid = "sess_test_confirm_kept_results"
    seed_pending(sid, QUERY)
    with caplog.at_level(logging.INFO):
        _, _, _, get = _autonomous_turn(client, isolate_to_test_vault, "yes",
                                        session_id=sid)
    assert _sent_queries(get) == [QUERY]
    assert ("[WEB_SEARCH] path=assembly skipped: the confirm search kept "
            "1 result(s)") in caplog.text


def test_upload_note_never_reaches_searxng(client, isolate_to_test_vault):
    _, _, _, get = _autonomous_turn(client, isolate_to_test_vault,
                                    UPLOAD_NOTE + USER_TEXT,
                                    session_id="sess_test_upload_note_search")
    assert get.call_count >= 1
    assert _sent_queries(get) == [USER_TEXT] * get.call_count


def test_upload_note_never_reaches_searxng_on_the_backstop(client, isolate_to_test_vault):
    """Vault off: no context assembly, so the backstop is the search."""
    _, _, _, get = _turn(client, isolate_to_test_vault, [_searxng([CLEAN_HIT])],
                         query=UPLOAD_NOTE + USER_TEXT, autonomous=True,
                         vault_enabled=False,
                         session_id="sess_test_upload_note_backstop")
    assert get.call_count == 1
    assert _sent_queries(get) == [USER_TEXT]


def test_upload_note_never_reaches_searxng_on_the_confirm_path(client, isolate_to_test_vault):
    """A pending written before the note was stripped still searches clean."""
    sid = "sess_test_upload_note_confirm"
    seed_pending(sid, UPLOAD_NOTE + USER_TEXT)
    _, _, _, get = _turn(client, isolate_to_test_vault, [_searxng([CLEAN_HIT])],
                         query="yes", session_id=sid)
    assert get.call_count == 1
    assert _sent_queries(get) == [USER_TEXT]


def test_text_the_user_typed_does_reach_searxng(client, isolate_to_test_vault):
    """Positive control for the tests above: bracketed text the user typed is
    the user's own text and is searched, so the params checked above do carry
    such text when it is there."""
    typed = "[System: tasks created - x] " + USER_TEXT
    _, _, _, get = _autonomous_turn(client, isolate_to_test_vault, typed,
                                    session_id="sess_test_typed_bracket_search")
    assert get.call_count >= 1
    assert _sent_queries(get) == [typed] * get.call_count


def test_search_text_drops_only_the_leading_upload_note():
    assert search_text_from(UPLOAD_NOTE + USER_TEXT) == USER_TEXT
    # Not leading: the user's own words, kept.
    kept = USER_TEXT + " " + UPLOAD_NOTE.strip()
    assert search_text_from(kept) == kept
    assert search_text_from(USER_TEXT) == USER_TEXT
