"""
tests/test_ask_first_confirmation.py

Tests for the ask-first confirmation routing feature:
  - _write_pending_confirmation (pattern detection, state record creation)
  - _check_pending_confirmation (LLM-based yes/no interpretation, resolution)
  - pending_confirmation category membership in models, extractor, resolver
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.state.models import VALID_STATE_CATEGORIES
from src.state.state_service import StateService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def vault(tmp_path):
    """Provide a tmp_path vault with state directory pre-created."""
    state_dir = tmp_path / "memory" / "state"
    state_dir.mkdir(parents=True)
    return tmp_path


@pytest.fixture()
def svc(vault):
    """StateService pointed at the tmp vault."""
    return StateService(vault_path=vault)


# ---------------------------------------------------------------------------
# 1. _write_pending_confirmation detects ask-first pattern and writes record
# ---------------------------------------------------------------------------

class TestWritePendingConfirmation:

    def test_detects_want_me_to_search(self, svc, vault):
        """Response containing 'want me to search' writes a pending_confirmation record."""
        with patch("src.api.openai_adapter.state_service", svc):
            from src.api.openai_adapter import _write_pending_confirmation

            _write_pending_confirmation(
                reply="I don't have recent info on that. Want me to search the web for you?",
                user_message="What happened in the news today?",
                session_id="sess_test_001",
            )

        records = svc.read_by_category("pending_confirmation")
        assert len(records) == 1
        rec = records[0]
        assert rec.source == "ask_first_detector"
        assert rec.metadata["action"] == "web_search"
        assert rec.metadata["query"] == "What happened in the news today?"
        assert rec.metadata["session_id"] == "sess_test_001"
        assert rec.metadata["resolved"] is False

    def test_detects_shall_i_search(self, svc, vault):
        """Other ask-first patterns also trigger detection."""
        with patch("src.api.openai_adapter.state_service", svc):
            from src.api.openai_adapter import _write_pending_confirmation

            _write_pending_confirmation(
                reply="I could look that up for you if you like.",
                user_message="Who won the game last night?",
                session_id="sess_test_002",
            )

        records = svc.read_by_category("pending_confirmation")
        assert len(records) == 1

    # -------------------------------------------------------------------
    # 2. _write_pending_confirmation does NOT fire without ask-first pattern
    # -------------------------------------------------------------------

    def test_no_pattern_no_record(self, svc, vault):
        """Response without any ask-first phrase writes nothing."""
        with patch("src.api.openai_adapter.state_service", svc):
            from src.api.openai_adapter import _write_pending_confirmation

            _write_pending_confirmation(
                reply="Here is what I know about that topic based on your vault.",
                user_message="Tell me about my project priorities.",
                session_id="sess_test_003",
            )

        records = svc.read_by_category("pending_confirmation")
        assert len(records) == 0


# ---------------------------------------------------------------------------
# 3-6. _check_pending_confirmation
# ---------------------------------------------------------------------------

class TestCheckPendingConfirmation:

    def _seed_pending(self, svc, text="Want me to search for that?", query="test query"):
        """Write an unresolved pending_confirmation record into the vault."""
        rec = StateService.make_record(
            state_type="pending_confirmation",
            text=text,
            source="ask_first_detector",
            metadata={
                "action": "web_search",
                "query": query,
                "session_id": "sess_test_010",
                "resolved": False,
            },
        )
        svc.write(rec)
        return rec

    # -------------------------------------------------------------------
    # 3. Returns None when no pending confirmation exists
    # -------------------------------------------------------------------

    def test_returns_none_when_empty(self, svc, vault):
        """No pending_confirmation records means None is returned."""
        with patch("src.api.openai_adapter.state_service", svc):
            from src.api.openai_adapter import _check_pending_confirmation

            result, _ = _check_pending_confirmation("sess_test_010", "yes please")

        assert result is None

    # -------------------------------------------------------------------
    # 4. Returns confirmed=True when user says yes
    # -------------------------------------------------------------------

    def test_confirmed_true_on_yes(self, svc, vault):
        """When LLM interprets user response as YES, returns confirmed=True."""
        self._seed_pending(svc, query="latest news")

        mock_response = {"message": {"content": "YES"}}

        with (
            patch("src.api.openai_adapter.state_service", svc),
            patch("ollama.chat", return_value=mock_response),
        ):
            from src.api.openai_adapter import _check_pending_confirmation

            result, _ = _check_pending_confirmation("sess_test_010", "Sure, go ahead")

        assert result is not None
        assert result["confirmed"] is True
        assert result["action"] == "web_search"
        assert result["query"] == "latest news"

    # -------------------------------------------------------------------
    # 5. Returns confirmed=False when user says no
    # -------------------------------------------------------------------

    def test_confirmed_false_on_no(self, svc, vault):
        """When LLM interprets user response as NO, returns confirmed=False."""
        self._seed_pending(svc, query="game scores")

        mock_response = {"message": {"content": "NO"}}

        with (
            patch("src.api.openai_adapter.state_service", svc),
            patch("ollama.chat", return_value=mock_response),
        ):
            from src.api.openai_adapter import _check_pending_confirmation

            result, _ = _check_pending_confirmation("sess_test_010", "Nah, skip it")

        assert result is not None
        assert result["confirmed"] is False
        assert result["action"] == "web_search"

    # -------------------------------------------------------------------
    # 6. Resolution is append-only and derived (ADR-038)
    # -------------------------------------------------------------------

    def test_resolves_pending_append_only_on_yes(self, svc, vault):
        """Confirming resolves the pending append-only: the original file is
        unchanged, a tombstone is written, the derived resolved-set marks the
        original, and the pending is not re-found on the next turn."""
        rec = self._seed_pending(svc, query="weather forecast")
        original_path = svc._get_state_dir() / svc._filename_for(rec)
        original_bytes = original_path.read_bytes()

        with patch("src.api.openai_adapter.state_service", svc):
            from src.api.openai_adapter import _check_pending_confirmation

            result, _ = _check_pending_confirmation("sess_test_010", "Yes please")

            assert result is not None
            assert result["confirmed"] is True
            # Append-only: the original record file is byte-for-byte unchanged.
            assert original_path.read_bytes() == original_bytes
            # A tombstone resolves the original via the derived resolved-set.
            records = svc.read_by_category("pending_confirmation")
            assert rec.id in StateService.resolved_ids(records)
            assert any(
                (r.metadata or {}).get("original_id") == rec.id for r in records
            )
            # Derived suppression: the resolved pending is not re-found.
            again, _ = _check_pending_confirmation("sess_test_010", "anything else")
            assert again is None

    def test_resolves_pending_append_only_on_no(self, svc, vault):
        """Declining also resolves the pending append-only (same contract)."""
        rec = self._seed_pending(svc, query="weather forecast")
        original_path = svc._get_state_dir() / svc._filename_for(rec)
        original_bytes = original_path.read_bytes()

        with patch("src.api.openai_adapter.state_service", svc):
            from src.api.openai_adapter import _check_pending_confirmation

            result, _ = _check_pending_confirmation("sess_test_010", "No thanks")

            assert result is not None
            assert result["confirmed"] is False
            assert original_path.read_bytes() == original_bytes
            records = svc.read_by_category("pending_confirmation")
            assert rec.id in StateService.resolved_ids(records)
            again, _ = _check_pending_confirmation("sess_test_010", "ok then")
            assert again is None


# ---------------------------------------------------------------------------
# 7. pending_confirmation is in VALID_STATE_CATEGORIES
# ---------------------------------------------------------------------------

class TestPendingConfirmationCategory:

    def test_in_valid_state_categories(self):
        assert "pending_confirmation" in VALID_STATE_CATEGORIES

    def test_state_record_accepts_pending_confirmation(self):
        """StateRecord can be constructed with type='pending_confirmation'."""
        from src.state.models import StateRecord

        rec = StateRecord(
            id="2026-04-13T10-00-00",
            timestamp="2026-04-13T10-00-00",
            type="pending_confirmation",
            text="Want me to search?",
            source="test",
        )
        assert rec.type == "pending_confirmation"


# ---------------------------------------------------------------------------
# 8. pending_confirmation excluded from EXTRACTABLE_CATEGORIES
# ---------------------------------------------------------------------------

class TestExtractorExclusion:

    def test_not_in_extractable_categories(self):
        from src.state.state_extractor import EXTRACTABLE_CATEGORIES

        assert "pending_confirmation" not in EXTRACTABLE_CATEGORIES


# ---------------------------------------------------------------------------
# 9. pending_confirmation is internal control flow, never surfaced (ADR-038)
# ---------------------------------------------------------------------------

class TestPendingConfirmationNotSurfaced:

    def test_pending_confirmation_excluded_from_current_state(self, vault):
        """pending_confirmation is ask-first control flow consumed directly by
        the chat endpoint; the resolver never surfaces it as current state
        (ADR-038). This is what lets pending resolution be append-only without
        leaking resolved-but-flag-False pendings into the prompt. A normal
        category still resolves, proving the exclusion is specific.
        """
        from src.state.state_resolver import StateResolver

        service = StateService(vault_path=vault)

        # An unresolved, recent pending would have surfaced under the old
        # contract; it must not surface now.
        pc = StateService.make_record(
            state_type="pending_confirmation",
            text="Want me to search for that?",
            source="ask_first_detector",
            metadata={"action": "web_search", "resolved": False},
        )
        service.write(pc)

        # A normal recent state record still resolves, for contrast.
        focus = StateService.make_record(
            state_type="current_focus",
            text="Active focus item",
            source="test",
        )
        service.write(focus)

        items = StateResolver(service=service).get_current_state()

        assert all(i.category != "pending_confirmation" for i in items)
        assert any(i.category == "current_focus" for i in items)


# ---------------------------------------------------------------------------
# 10. Regression: resolved pending does not re-trigger (infinite-loop guard)
# ---------------------------------------------------------------------------

class TestNoInfiniteConfirmationLoop:

    def _seed(self, svc, query="weather"):
        rec = StateService.make_record(
            state_type="pending_confirmation",
            text="Want me to search for that?",
            source="ask_first_detector",
            metadata={
                "action": "web_search", "query": query,
                "session_id": "sess_loop", "resolved": False,
            },
        )
        svc.write(rec)
        return rec

    def test_resolved_pending_does_not_retrigger(self, svc, vault):
        """After a pending is resolved append-only, subsequent turns in the
        same session never re-find it, so the confirmation prompt cannot loop
        (the regression the in-place mark_resolved used to guard against).
        Exactly one resolution tombstone is written - no per-turn accumulation.
        """
        rec = self._seed(svc)

        with patch("src.api.openai_adapter.state_service", svc):
            from src.api.openai_adapter import _check_pending_confirmation

            first, _ = _check_pending_confirmation("sess_loop", "yes")
            assert first is not None and first["confirmed"] is True

            for msg in ("tell me a joke", "what's the time", "hello"):
                again, _ = _check_pending_confirmation("sess_loop", msg)
                assert again is None

        records = svc.read_by_category("pending_confirmation")
        tombstones = [
            r for r in records if (r.metadata or {}).get("original_id") == rec.id
        ]
        assert len(tombstones) == 1
