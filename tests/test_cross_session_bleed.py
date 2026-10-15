"""
tests/test_cross_session_bleed.py

Issue #133: conversation state from one session reaching another.

The ConversationBuffer is a process-level singleton on the module-level
llm_adapter. It resets only inside add_turn, which runs AFTER generation, so
the prompt for the first turn of a new session is built from the previous
session's turns and sticky flags. These tests pin the contract that every
read of conversation state is scoped to the session (and vault) of the turn
doing the reading.

Each absence assertion ships with a positive control (CLAUDE.md Testing
Discipline): the same fixture with the session held constant must surface
the content the absence test says is missing.

All fixtures are synthetic. No live API, no private vault.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from src.context.conversation_buffer import ConversationBufferStore
from src.context.models import ContextPacket
from src.llm.adapter import LLMAdapter
from src.safety.models import SafetyTriggerResult
from src.state.state_service import StateService
from tests.conftest import adapter_with_real_buffer, fill_buffer_past_threshold

SESSION_A = "sess_test_a"
SESSION_B = "sess_test_b"

# Distinctive markers so a substring check cannot pass by accident.
MARKER_A = "SYNTHETIC-MARKER-ALPHA trends outlined"
MARKER_B = "SYNTHETIC-MARKER-BRAVO reply"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _capturing_adapter(replies: list[str]) -> tuple[LLMAdapter, list[dict]]:
    """adapter_with_real_buffer() plus a capturing build_prompt.

    build_prompt is a side effect that snapshots the buffer it is handed at
    prompt-build time: the history turns and the session-sticky flags. That
    snapshot is what reaches the model.
    """
    adapter = adapter_with_real_buffer()
    snapshots: list[dict] = []

    def _build_prompt(packet, **kwargs):
        buf = kwargs["conversation_buffer"]
        snapshots.append({
            "user_message": packet.user_message,
            "history": buf.get_recent(),
            "question_suppressed": buf.question_suppressed,
            "declined_topics": list(buf.declined_topics),
        })
        return "system prompt"

    adapter.prompt_builder.build_prompt.side_effect = _build_prompt
    adapter.policy_service = MagicMock()
    adapter.policy_service.evaluate_trigger.return_value = SafetyTriggerResult(
        triggered=False
    )
    adapter.review_service = MagicMock()
    adapter.review_logger = MagicMock()
    adapter._chat = MagicMock(side_effect=list(replies))
    adapter._chat_stream = MagicMock(side_effect=[iter([r]) for r in replies])
    adapter._maybe_compress_buffer = MagicMock()
    # Cloud branch calls build_prompt once, directly. The guardrail branch
    # is exercised elsewhere; the buffer read is the same in both.
    adapter._is_cloud_model = MagicMock(return_value=True)
    return adapter, snapshots


def _turn(adapter, message: str, session_id: str, *, stream: bool = False,
          vault_path=None) -> None:
    packet = ContextPacket(user_message=message)
    if stream:
        list(adapter.generate_response_stream(
            packet, session_id=session_id, vault_path=vault_path,
        ))
    else:
        list(adapter.generate_response_iter(
            packet, session_id=session_id, vault_path=vault_path,
        ))


def _history_text(snapshot: dict) -> str:
    return "\n".join(
        f"{t.get('user', '')}\n{t.get('assistant', '')}" for t in snapshot["history"]
    )


# ---------------------------------------------------------------------------
# Observation 1: prior-session turns in the new session's prompt
# ---------------------------------------------------------------------------

class TestPromptHistoryIsSessionScoped:

    @pytest.mark.parametrize("stream", [False, True], ids=["iter", "stream"])
    def test_first_turn_of_new_session_has_no_prior_session_history(self, stream):
        """Session B's first prompt must not carry session A's reply."""
        adapter, snaps = _capturing_adapter([MARKER_A, MARKER_B])

        _turn(adapter, "list some AI trends", SESSION_A, stream=stream)
        _turn(adapter, "expand on the trends you outlined", SESSION_B, stream=stream)

        b_prompt = snaps[-1]
        assert b_prompt["user_message"] == "expand on the trends you outlined"
        assert MARKER_A not in _history_text(b_prompt)
        assert b_prompt["history"] == []

    @pytest.mark.parametrize("stream", [False, True], ids=["iter", "stream"])
    def test_positive_control_same_session_sees_its_own_history(self, stream):
        """Control: with the session held constant the reply IS in history,
        so the absence test above is not vacuous."""
        adapter, snaps = _capturing_adapter([MARKER_A, MARKER_B])

        _turn(adapter, "list some AI trends", SESSION_A, stream=stream)
        _turn(adapter, "expand on the trends you outlined", SESSION_A, stream=stream)

        assert MARKER_A in _history_text(snaps[-1])

    def test_interleaved_sessions_each_see_only_their_own_history(self):
        """Concurrent clients (e2e harness, two UI tabs) interleave turns.
        A2's prompt must contain A1 and must not contain B1."""
        adapter, snaps = _capturing_adapter([MARKER_A, MARKER_B, "a2 reply"])

        _turn(adapter, "a1", SESSION_A)
        _turn(adapter, "b1", SESSION_B)
        _turn(adapter, "a2", SESSION_A)

        a2_history = _history_text(snaps[-1])
        assert MARKER_B not in a2_history
        assert MARKER_A in a2_history

    def test_buffer_turn_log_reports_per_session_turn_counts(self, tmp_path, caplog):
        """Runtime confirmation line (Bug Fix Standard 6): each prompt build
        logs the session, vault folder and that session's own turn count."""
        adapter, _snaps = _capturing_adapter(["r1", "r2", "r3"])
        vault = tmp_path / "vault_one"

        with caplog.at_level(logging.INFO, logger="ember.llm"):
            _turn(adapter, "a1", SESSION_A, vault_path=vault)
            _turn(adapter, "b1", SESSION_B, vault_path=vault)
            _turn(adapter, "a2", SESSION_A, vault_path=vault)

        lines = [
            r.getMessage() for r in caplog.records
            if r.getMessage().startswith("[BUFFER] turn ")
        ]
        assert lines == [
            f"[BUFFER] turn session={SESSION_A} vault=vault_one turns=0",
            f"[BUFFER] turn session={SESSION_B} vault=vault_one turns=0",
            f"[BUFFER] turn session={SESSION_A} vault=vault_one turns=1",
        ]
        # No turn text in the log line.
        assert all("r1" not in line and "a1" not in line for line in lines)

    def test_same_session_after_vault_swap_has_no_prior_vault_history(self, tmp_path):
        """The buffer survives POST /v1/vault/swap. A turn served from a
        different vault must not render turns that came from the old one,
        even under the same session id."""
        vault_one = tmp_path / "vault_one"
        vault_two = tmp_path / "vault_two"
        adapter, snaps = _capturing_adapter([MARKER_A, MARKER_B])

        _turn(adapter, "first", SESSION_A, vault_path=vault_one)
        _turn(adapter, "second", SESSION_A, vault_path=vault_two)

        assert MARKER_A not in _history_text(snaps[-1])

    def test_positive_control_same_vault_same_session_keeps_history(self, tmp_path):
        vault_one = tmp_path / "vault_one"
        adapter, snaps = _capturing_adapter([MARKER_A, MARKER_B])

        _turn(adapter, "first", SESSION_A, vault_path=vault_one)
        _turn(adapter, "second", SESSION_A, vault_path=vault_one)

        assert MARKER_A in _history_text(snaps[-1])


class TestStickyFlagsAreSessionScoped:

    def test_question_objection_does_not_carry_into_new_session(self):
        """BUG-008 flag set in session A must not suppress questions in B."""
        adapter, snaps = _capturing_adapter(["ok", "fine"])

        _turn(adapter, "please stop asking questions", SESSION_A)
        _turn(adapter, "hello", SESSION_B)

        assert snaps[-1]["question_suppressed"] is False

    def test_positive_control_question_objection_holds_within_session(self):
        adapter, snaps = _capturing_adapter(["ok", "fine"])

        _turn(adapter, "please stop asking questions", SESSION_A)
        _turn(adapter, "hello", SESSION_A)

        assert snaps[-1]["question_suppressed"] is True

    def test_declined_topic_does_not_carry_into_new_session(self):
        adapter, snaps = _capturing_adapter(["ok", "fine"])

        _turn(adapter, "i don't want to talk about synthetic topic", SESSION_A)
        _turn(adapter, "hello", SESSION_B)

        assert snaps[-1]["declined_topics"] == []

    def test_positive_control_declined_topic_holds_within_session(self):
        adapter, snaps = _capturing_adapter(["ok", "fine"])

        _turn(adapter, "i don't want to talk about synthetic topic", SESSION_A)
        _turn(adapter, "hello", SESSION_A)

        assert snaps[-1]["declined_topics"] == ["synthetic topic"]


# ---------------------------------------------------------------------------
# Background compression crossing a session boundary
# ---------------------------------------------------------------------------

class TestCompressionDoesNotCrossSessions:

    def _adapter_mid_session_switch(self):
        """Session A's buffer is past the threshold. The summarization call
        (seconds in production) is where session B's first turn lands, through
        the same entry point a live turn uses."""
        adapter = adapter_with_real_buffer()
        buf_a = adapter.conversation_buffer_for(SESSION_A)
        fill_buffer_past_threshold(buf_a)

        def _summarize(_prompt):
            adapter.conversation_buffer_for(SESSION_B).add_turn("b user", MARKER_B)
            return MARKER_A

        adapter._summarize_with_plain_prompt = _summarize
        return adapter, buf_a

    def test_summary_of_session_a_not_injected_into_session_b(self):
        adapter, buf_a = self._adapter_mid_session_switch()

        adapter._maybe_compress_buffer(skip_vault_write=True, buffer=buf_a)

        buf_b = adapter.conversation_buffer_for(SESSION_B)
        assert not any(t.get("is_summary") for t in buf_b.get_recent())
        assert all(MARKER_A not in t["assistant"] for t in buf_b.get_recent())
        # The summary did land: in A's own buffer.
        assert buf_a.get_recent()[0]["assistant"] == MARKER_A

    def test_summary_of_session_a_not_written_under_session_b(self):
        adapter, buf_a = self._adapter_mid_session_switch()

        with patch("src.llm.adapter.write_session_summary") as write:
            adapter._maybe_compress_buffer(skip_vault_write=False, buffer=buf_a)

        assert write.call_count == 1
        assert write.call_args.kwargs["session_id"] == SESSION_A

    def test_positive_control_summary_injected_without_session_switch(self):
        adapter = adapter_with_real_buffer()
        buf = adapter.conversation_buffer_for(SESSION_A)
        fill_buffer_past_threshold(buf)
        adapter._summarize_with_plain_prompt = lambda _p: MARKER_A

        adapter._maybe_compress_buffer(skip_vault_write=True, buffer=buf)

        assert buf.buffer[0].get("is_summary") is True
        assert buf.buffer[0]["assistant"] == MARKER_A


# ---------------------------------------------------------------------------
# Session reflection on delete reads whatever session the buffer holds
# ---------------------------------------------------------------------------

class TestDeleteReflectionIsSessionScoped:

    def _delete_with_buffer_of(self, monkeypatch, buffer_session: str):
        import src.api.main as main
        from src.core.config import get_private_vault_path
        from src.reflection.session_reflection import (
            generate_session_reflection_in_background,
        )

        store = ConversationBufferStore()
        buf = store.get(get_private_vault_path(), buffer_session)
        for i in range(3):
            buf.add_turn(f"u{i}", f"{MARKER_B} {i}")
        monkeypatch.setattr(main.llm_adapter, "conversation_buffers", store)
        monkeypatch.setattr(main, "delete_session", lambda sid: {"id": sid, "deleted": True})

        spawned: list[tuple] = []
        monkeypatch.setattr(
            "src.core.config.spawn_vault_bound_thread",
            lambda target, args=(), vault=None: spawned.append((target, args)),
        )

        main.delete_conversation_endpoint(SESSION_A)
        return [
            args for target, args in spawned
            if target is generate_session_reflection_in_background
        ]

    def test_deleting_a_does_not_reflect_session_b_buffer_as_a(self, monkeypatch):
        reflections = self._delete_with_buffer_of(monkeypatch, SESSION_B)
        assert reflections == []

    def test_positive_control_deleting_a_reflects_a_buffer(self, monkeypatch):
        reflections = self._delete_with_buffer_of(monkeypatch, SESSION_A)
        assert len(reflections) == 1
        assert reflections[0][1] == SESSION_A


# ---------------------------------------------------------------------------
# Observation 2: another session's turn resolves this session's pending
# ---------------------------------------------------------------------------

@pytest.fixture()
def svc(tmp_path):
    (tmp_path / "memory" / "state").mkdir(parents=True)
    return StateService(vault_path=tmp_path)


def _seed_pending(svc: StateService, session_id: str) -> StateService:
    rec = StateService.make_record(
        state_type="pending_confirmation",
        text="Want me to search for that?",
        source="ask_first_detector",
        metadata={
            "action": "web_search",
            "query": "synthetic query",
            "session_id": session_id,
            "resolved": False,
        },
    )
    svc.write(rec)
    return rec


def _resolve_logs(caplog) -> list[str]:
    return [
        r.getMessage() for r in caplog.records
        if "[CONFIRM] Resolved original pending" in r.getMessage()
    ]


class TestPendingConfirmationIsSessionScoped:

    def test_other_session_turn_does_not_resolve_pending(self, svc, caplog):
        """Session B's turn must not touch session A's live offer: no
        tombstone, no [CONFIRM] Resolved log on B's turn."""
        rec = _seed_pending(svc, SESSION_A)

        with patch("src.api.openai_adapter.state_service", svc):
            from src.api.openai_adapter import _check_pending_confirmation

            with caplog.at_level(logging.INFO):
                result, _ = _check_pending_confirmation(SESSION_B, "unrelated question")

        assert result is None
        assert _resolve_logs(caplog) == []
        assert rec.id not in svc.resolved_ids(svc.read_by_category("pending_confirmation"))

    def test_pending_survives_interleaved_turn_and_confirms_in_owner(self, svc):
        """A offers search, B takes a turn, A says yes: A's search runs."""
        _seed_pending(svc, SESSION_A)

        with patch("src.api.openai_adapter.state_service", svc):
            from src.api.openai_adapter import _check_pending_confirmation

            _check_pending_confirmation(SESSION_B, "unrelated question")
            result, _ = _check_pending_confirmation(SESSION_A, "yes")

        assert result is not None
        assert result["confirmed"] is True

    def test_positive_control_owner_turn_resolves_and_logs(self, svc, caplog):
        """Control: the owning session's reply does resolve and log, so the
        absence assertions above can fail."""
        rec = _seed_pending(svc, SESSION_A)

        with patch("src.api.openai_adapter.state_service", svc):
            from src.api.openai_adapter import _check_pending_confirmation

            with caplog.at_level(logging.INFO):
                _check_pending_confirmation(SESSION_A, "yes")

        assert len(_resolve_logs(caplog)) == 1
        assert rec.id in svc.resolved_ids(svc.read_by_category("pending_confirmation"))
