"""tests/test_clarification_next_turn.py

After a B2 clarification short-circuit, the assistant conversation
record carries metadata.awaiting_search_content=True. When the user
sends their follow-up turn, the request handler must:

  1. Detect the prior clarification by reading the most recent
     assistant conversation record for the session.
  2. Bypass the intent classifier on this turn.
  3. Dispatch the user's full new message to web_search() as the
     search query.

Without this, the follow-up "iPhone 16 release date" would be
classified in isolation; if the classifier mis-routes (e.g., vault
because of no temporal anchor), the user gets a vault-empty answer
after explicitly responding to the clarification prompt.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with patch("src.api.main.get_ember_api_key", return_value=None):
        from src.api.main import app
        yield TestClient(app)


def _payload(text: str) -> dict:
    return {
        "model": "ember",
        "stream": False,
        "messages": [{"role": "user", "content": text}],
    }


def _fake_recent_conversations(session_id: str) -> list[dict]:
    """Return what read_memories('conversation', limit=N) would return
    after a clarification short-circuit fired in this session."""
    return [
        {
            "id": "2026-05-13T05-30-00",
            "timestamp": "2026-05-13T05-30-00",
            "type": "conversation",
            "text": "What would you like me to search for?",
            "source": "chat",
            "tags": ["conversation", "clarification"],
            "metadata": {
                "role": "assistant",
                "content_kind": "answer",
                "session_id": session_id,
                "source": "clarification",
                "awaiting_search_content": True,
            },
        },
        {
            "id": "2026-05-13T05-29-59",
            "timestamp": "2026-05-13T05-29-59",
            "type": "conversation",
            "text": "google please",
            "source": "chat",
            "tags": ["conversation"],
            "metadata": {
                "role": "user",
                "content_kind": "user_content",
                "session_id": session_id,
            },
        },
    ]


def test_next_turn_after_clarification_dispatches_user_message_to_web_search(client):
    """When the prior assistant turn is a clarification, the user's
    next message goes directly to web_search() bypassing the classifier."""
    session_id = "sess_b2_followup"

    with patch("src.api.openai_adapter.read_memories") as _read_conv, \
         patch("src.api.openai_adapter.context_service") as _ctx, \
         patch("src.api.openai_adapter.llm_adapter") as _llm, \
         patch("src.api.openai_adapter.write_memory"), \
         patch("src.api.openai_adapter._background_state_extraction"), \
         patch("src.api.openai_adapter._detect_and_write_commitment"), \
         patch("src.api.openai_adapter._detect_task_in_response"), \
         patch("src.api.openai_adapter.onboarding_service") as _onb, \
         patch("src.api.openai_adapter._ensure_session"), \
         patch("src.api.openai_adapter.get_session") as _get_session:
        _onb.is_active.return_value = False
        _read_conv.return_value = _fake_recent_conversations(session_id)
        _get_session.return_value = {"id": session_id, "metadata": {}}

        from src.context.models import ContextPacket
        # Pretend build_context returned a packet with web results to
        # confirm the request did go through the dispatch path.
        empty_packet = ContextPacket(
            user_message="iPhone 16 release date",
            web_items=[],
        )
        _ctx.build_context.return_value = empty_packet
        _llm.generate_response.return_value = "iPhone 16 was released..."

        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "ember",
                "stream": False,
                "messages": [
                    {"role": "user", "content": "iPhone 16 release date"},
                ],
            },
            headers={"X-Session-Id": session_id},
        )

        assert resp.status_code == 200

        # build_context must have run with skip_web_search=False so the
        # dispatch through context_service.web_search executes.
        assert _ctx.build_context.called
        call_kwargs = _ctx.build_context.call_args.kwargs
        assert call_kwargs.get("skip_web_search") is False, (
            f"After clarification, follow-up turn must dispatch to web_search; "
            f"got skip_web_search={call_kwargs.get('skip_web_search')!r}"
        )


def test_no_clarification_in_history_does_not_force_web_search(client):
    """Sanity check: when the prior assistant turn is NOT a clarification,
    the next-turn handler must not fire. The normal classifier path runs."""
    session_id = "sess_no_clarification"

    # Recent conversations: a normal assistant turn (no
    # awaiting_search_content).
    normal_history = [
        {
            "id": "2026-05-13T06-00-00",
            "timestamp": "2026-05-13T06-00-00",
            "type": "conversation",
            "text": "I noticed you mentioned the docs earlier.",
            "source": "chat",
            "tags": ["conversation"],
            "metadata": {
                "role": "assistant",
                "content_kind": "answer",
                "session_id": session_id,
                # No source=clarification, no awaiting_search_content.
            },
        },
    ]

    with patch("src.api.openai_adapter.read_memories") as _read_conv, \
         patch("src.api.openai_adapter.context_service") as _ctx, \
         patch("src.api.openai_adapter.llm_adapter") as _llm, \
         patch("src.api.openai_adapter.write_memory"), \
         patch("src.api.openai_adapter._background_state_extraction"), \
         patch("src.api.openai_adapter._detect_and_write_commitment"), \
         patch("src.api.openai_adapter._detect_task_in_response"), \
         patch("src.api.openai_adapter.onboarding_service") as _onb, \
         patch("src.api.openai_adapter._ensure_session"), \
         patch("src.api.openai_adapter.get_session") as _get_session, \
         patch("src.core.preferences.get", return_value=False):
        _onb.is_active.return_value = False
        _read_conv.return_value = normal_history
        _get_session.return_value = {"id": session_id, "metadata": {}}

        from src.context.models import ContextPacket
        empty_packet = ContextPacket(user_message="hello there", web_items=[])
        _ctx.build_context.return_value = empty_packet
        _llm.generate_response.return_value = "Hi."

        resp = client.post(
            "/v1/chat/completions",
            json=_payload("hello there"),
            headers={"X-Session-Id": session_id},
        )

        assert resp.status_code == 200
        # No clarification flag means normal flow: web_autonomous=False
        # means ask-first is active and skip_web_search=True.
        call_kwargs = _ctx.build_context.call_args.kwargs
        assert call_kwargs.get("skip_web_search") is True
