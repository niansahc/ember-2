"""
tests/test_state_extractor.py

Tests for automatic state extraction from conversation turns.
"""

import json
import pytest
from unittest.mock import patch, MagicMock

from src.state.state_extractor import (
    StateExtractor,
    EXTRACTABLE_CATEGORIES,
    MIN_WORDS_FOR_EXTRACTION,
)
from src.state.models import VALID_STATE_CATEGORIES


class TestShortMessageSkipping:
    """Short messages should be skipped entirely."""

    def test_very_short_message_returns_empty(self):
        extractor = StateExtractor()
        result = extractor.extract("hi", "Hello!")
        assert result == []

    def test_under_threshold_returns_empty(self):
        extractor = StateExtractor()
        short = " ".join(["word"] * (MIN_WORDS_FOR_EXTRACTION - 1))
        result = extractor.extract(short, "Some reply.")
        assert result == []

    def test_exactly_at_threshold_attempts_extraction(self):
        """At exactly MIN_WORDS, extraction should be attempted (not skipped)."""
        at_threshold = " ".join(["word"] * MIN_WORDS_FOR_EXTRACTION)
        extractor = StateExtractor()
        with patch("src.state.state_extractor.ollama") as mock_ollama:
            mock_ollama.chat.return_value = {
                "message": {"content": '{"extractions": []}'}
            }
            result = extractor.extract(at_threshold, "Reply.")
            assert mock_ollama.chat.called
            assert result == []


class TestErrorHandling:
    """Extraction errors should never raise — always return empty list."""

    def test_llm_call_error_returns_empty(self):
        extractor = StateExtractor()
        long_msg = "I need to work on the authentication system for the new project this week"
        with patch("src.state.state_extractor.ollama") as mock_ollama:
            mock_ollama.chat.side_effect = Exception("Connection refused")
            result = extractor.extract(long_msg, "Sure, I can help.")
            assert result == []

    def test_invalid_json_returns_empty(self):
        extractor = StateExtractor()
        long_msg = "I need to work on the authentication system for the new project this week"
        with patch("src.state.state_extractor.ollama") as mock_ollama:
            mock_ollama.chat.return_value = {
                "message": {"content": "This is not JSON at all!"}
            }
            result = extractor.extract(long_msg, "Sure thing.")
            assert result == []

    def test_malformed_json_returns_empty(self):
        extractor = StateExtractor()
        long_msg = "I need to work on the authentication system for the new project this week"
        with patch("src.state.state_extractor.ollama") as mock_ollama:
            mock_ollama.chat.return_value = {
                "message": {"content": '{"extractions": "not a list"}'}
            }
            result = extractor.extract(long_msg, "Sure thing.")
            assert result == []


class TestConfidenceFiltering:
    """Low confidence extractions should be filtered out."""

    def test_low_confidence_filtered(self):
        extractor = StateExtractor()
        long_msg = "I really need to focus on finishing the database migration before the deadline on Friday"
        response_json = json.dumps({
            "extractions": [
                {"type": "current_focus", "text": "Database migration", "confidence": "high"},
                {"type": "open_loop", "text": "Something vague", "confidence": "low"},
            ]
        })
        with patch("src.state.state_extractor.ollama") as mock_ollama:
            mock_ollama.chat.return_value = {
                "message": {"content": response_json}
            }
            result = extractor.extract(long_msg, "Let me help with that.")
            assert len(result) == 1
            assert result[0].type == "current_focus"
            assert result[0].text == "Database migration"

    def test_medium_confidence_kept(self):
        extractor = StateExtractor()
        long_msg = "I really need to focus on finishing the database migration before the deadline on Friday"
        response_json = json.dumps({
            "extractions": [
                {"type": "priority", "text": "Friday deadline", "confidence": "medium"},
            ]
        })
        with patch("src.state.state_extractor.ollama") as mock_ollama:
            mock_ollama.chat.return_value = {
                "message": {"content": response_json}
            }
            result = extractor.extract(long_msg, "Got it.")
            assert len(result) == 1
            assert result[0].type == "priority"

    def test_all_low_confidence_returns_empty(self):
        extractor = StateExtractor()
        long_msg = "I really need to focus on finishing the database migration before the deadline on Friday"
        response_json = json.dumps({
            "extractions": [
                {"type": "current_focus", "text": "Something", "confidence": "low"},
                {"type": "blocker", "text": "Another thing", "confidence": "low"},
            ]
        })
        with patch("src.state.state_extractor.ollama") as mock_ollama:
            mock_ollama.chat.return_value = {
                "message": {"content": response_json}
            }
            result = extractor.extract(long_msg, "Sure.")
            assert result == []


class TestCategoryValidation:
    """Invalid categories should be skipped."""

    def test_invalid_category_skipped(self):
        extractor = StateExtractor()
        long_msg = "I really need to focus on finishing the database migration before the deadline on Friday"
        response_json = json.dumps({
            "extractions": [
                {"type": "invalid_type", "text": "Something", "confidence": "high"},
                {"type": "current_focus", "text": "Database migration", "confidence": "high"},
            ]
        })
        with patch("src.state.state_extractor.ollama") as mock_ollama:
            mock_ollama.chat.return_value = {
                "message": {"content": response_json}
            }
            result = extractor.extract(long_msg, "Let me help.")
            assert len(result) == 1
            assert result[0].type == "current_focus"

    def test_onboarding_category_excluded(self):
        """Onboarding is a valid state category but not extractable from conversations."""
        assert "onboarding" in VALID_STATE_CATEGORIES
        assert "onboarding" not in EXTRACTABLE_CATEGORIES


class TestRecordStructure:
    """Extracted records should have the correct structure."""

    def test_record_has_correct_fields(self):
        extractor = StateExtractor()
        long_msg = "I really need to focus on finishing the database migration before the deadline on Friday"
        response_json = json.dumps({
            "extractions": [
                {"type": "blocker", "text": "Waiting on API credentials", "confidence": "high"},
            ]
        })
        with patch("src.state.state_extractor.ollama") as mock_ollama:
            mock_ollama.chat.return_value = {
                "message": {"content": response_json}
            }
            result = extractor.extract(long_msg, "I see.")
            assert len(result) == 1
            record = result[0]
            assert record.type == "blocker"
            assert record.text == "Waiting on API credentials"
            assert record.source == "state_extractor"
            assert "auto_extracted" in record.tags
            assert record.metadata["confidence"] == "high"
            assert record.metadata["extraction_source"] == "conversation"

    def test_empty_extractions_returns_empty(self):
        extractor = StateExtractor()
        long_msg = "I really need to focus on finishing the database migration before the deadline on Friday"
        response_json = json.dumps({"extractions": []})
        with patch("src.state.state_extractor.ollama") as mock_ollama:
            mock_ollama.chat.return_value = {
                "message": {"content": response_json}
            }
            result = extractor.extract(long_msg, "Okay.")
            assert result == []
