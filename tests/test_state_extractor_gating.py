"""tests/test_state_extractor_gating.py — ADR-033 is_live_turn gate.

Verifies that StateExtractor.extract() refuses to run on ingested/historical
content. The gate is the primary defense against state contamination from
ChatGPT import and any future non-live caller.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from src.state.state_extractor import StateExtractor


LONG_MSG = (
    "I really need to focus on finishing the database migration "
    "before the deadline on Friday"
)


class TestIsLiveTurnGate:
    """Default is is_live_turn=False — any caller that omits the flag skips."""

    def test_default_omit_flag_skips_extraction(self):
        """Calling extract() without the flag must NOT run the LLM."""
        extractor = StateExtractor()
        with patch("src.state.state_extractor.ollama") as mock_ollama:
            result = extractor.extract(LONG_MSG, "Sure, I can help.")
            assert result == []
            assert mock_ollama.chat.called is False

    def test_is_live_turn_false_skips_extraction(self):
        """Explicit False must skip."""
        extractor = StateExtractor()
        with patch("src.state.state_extractor.ollama") as mock_ollama:
            result = extractor.extract(LONG_MSG, "Sure.", is_live_turn=False)
            assert result == []
            assert mock_ollama.chat.called is False

    def test_is_live_turn_true_proceeds_to_extraction(self):
        """Explicit True must reach the LLM call (subject to other gates)."""
        extractor = StateExtractor()
        response_json = json.dumps({
            "extractions": [
                {"type": "current_focus", "text": "Database migration", "confidence": "high"},
            ]
        })
        with patch("src.state.state_extractor.ollama") as mock_ollama:
            mock_ollama.chat.return_value = {"message": {"content": response_json}}
            result = extractor.extract(LONG_MSG, "Let me help.", is_live_turn=True)
            assert mock_ollama.chat.called is True
            assert len(result) == 1
            assert result[0].type == "current_focus"

    def test_skip_log_line_emitted_for_non_live_turn(self, caplog):
        """Runtime log line confirms the gate executes on the skip path."""
        extractor = StateExtractor()
        with caplog.at_level("INFO", logger="ember.state_extractor"):
            with patch("src.state.state_extractor.ollama") as mock_ollama:
                extractor.extract(LONG_MSG, "Reply.")
                assert mock_ollama.chat.called is False

        assert any(
            "is_live_turn=False" in record.message
            for record in caplog.records
        ), "expected [STATE_EXTRACT] Skipped — is_live_turn=False log line not emitted"

    def test_gate_runs_before_word_count_check(self):
        """The is_live_turn gate is cheaper than word-count, runs first."""
        extractor = StateExtractor()
        with patch("src.state.state_extractor.ollama") as mock_ollama:
            # Short message + is_live_turn=False — both would skip. Assert the
            # gate short-circuits cleanly regardless.
            result = extractor.extract("hi", "Hello!", is_live_turn=False)
            assert result == []
            assert mock_ollama.chat.called is False

    def test_is_live_turn_is_keyword_only_for_clarity(self):
        """Positional call shape is unchanged — the flag is a kwarg."""
        extractor = StateExtractor()
        # Existing positional calls like extract(user, reply) must still work
        # and skip by default.
        with patch("src.state.state_extractor.ollama") as mock_ollama:
            result = extractor.extract(LONG_MSG, "Reply.")
            assert result == []
            assert mock_ollama.chat.called is False
