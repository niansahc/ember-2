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

            result = _check_pending_confirmation("sess_test_010", "yes please")

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

            result = _check_pending_confirmation("sess_test_010", "Sure, go ahead")

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

            result = _check_pending_confirmation("sess_test_010", "Nah, skip it")

        assert result is not None
        assert result["confirmed"] is False
        assert result["action"] == "web_search"

    # -------------------------------------------------------------------
    # 6. Writes a resolution record regardless of outcome
    # -------------------------------------------------------------------

    def test_marks_original_resolved_on_yes(self, svc, vault):
        """Confirming marks the ORIGINAL pending record as resolved."""
        self._seed_pending(svc, query="weather forecast")

        mock_response = {"message": {"content": "YES"}}

        with (
            patch("src.api.openai_adapter.state_service", svc),
            patch("ollama.chat", return_value=mock_response),
        ):
            from src.api.openai_adapter import _check_pending_confirmation

            result = _check_pending_confirmation("sess_test_010", "Yes please")

        assert result is not None
        assert result["confirmed"] is True
        # The original record should now be marked resolved on disk
        records = svc.read_by_category("pending_confirmation")
        unresolved = [r for r in records if not (r.metadata or {}).get("resolved")]
        assert len(unresolved) == 0

    def test_marks_original_resolved_on_no(self, svc, vault):
        """Declining also marks the ORIGINAL pending record as resolved."""
        self._seed_pending(svc, query="weather forecast")

        mock_response = {"message": {"content": "NO"}}

        with (
            patch("src.api.openai_adapter.state_service", svc),
            patch("ollama.chat", return_value=mock_response),
        ):
            from src.api.openai_adapter import _check_pending_confirmation

            result = _check_pending_confirmation("sess_test_010", "No thanks")

        assert result is not None
        assert result["confirmed"] is False
        records = svc.read_by_category("pending_confirmation")
        unresolved = [r for r in records if not (r.metadata or {}).get("resolved")]
        assert len(unresolved) == 0


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
# 9. pending_confirmation exempt from staleness filtering
# ---------------------------------------------------------------------------

class TestStalenessExemption:

    def test_staleness_exempt_set_includes_pending_confirmation(self, vault):
        """StateResolver's _STALENESS_EXEMPT set includes pending_confirmation.

        We verify this by writing a very old pending_confirmation record to
        the vault and confirming it still appears in resolved state items.
        A non-exempt category with the same timestamp would be filtered out.
        """
        from src.state.state_resolver import StateResolver
        from src.state.models import StateRecord

        service = StateService(vault_path=vault)

        # Write a very old pending_confirmation record — should survive
        # staleness for exempt categories but not for normal ones.
        old_pc = StateRecord(
            id="2020-01-01T00-00-00",
            timestamp="2020-01-01T00-00-00",
            type="pending_confirmation",
            text="Want me to search for that?",
            source="ask_first_detector",
            metadata={"action": "web_search", "resolved": False},
        )
        service.write(old_pc)

        # Also write an equally old non-exempt record for contrast.
        old_focus = StateRecord(
            id="2020-01-01T00-00-01",
            timestamp="2020-01-01T00-00-01",
            type="current_focus",
            text="Old focus item",
            source="test",
        )
        service.write(old_focus)

        resolver = StateResolver(service=service)

        with patch(
            "src.core.config.get_state_staleness_days", return_value=7
        ):
            items = resolver.get_current_state()

        # The old pending_confirmation should survive staleness filtering
        pc_items = [i for i in items if i.category == "pending_confirmation"]
        assert len(pc_items) == 1
        assert "search" in pc_items[0].text.lower()

        # The old current_focus should be filtered out by staleness
        focus_items = [i for i in items if i.category == "current_focus"]
        assert len(focus_items) == 0
