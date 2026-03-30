"""
tests/test_commitment_detector.py

Tests for the commitment detector (ADR-014).
"""

import pytest
from pathlib import Path
from unittest.mock import patch

from src.state.commitment_detector import detect_commitment, CommitmentDetectionResult


class TestCommitmentDetection:
    """Core detection logic."""

    def test_detects_walk_through_commitment(self):
        result = detect_commitment("I'll walk you through this step by step.")
        assert result.detected is True
        assert "walk you through" in result.commitment_text.lower()

    def test_detects_follow_up_commitment(self):
        result = detect_commitment("I'll follow up on that tomorrow when we have more data.")
        assert result.detected is True

    def test_detects_collaborative_plan(self):
        result = detect_commitment("Let's go through this together. Here's the plan.")
        assert result.detected is True

    def test_detects_structured_commitment(self):
        result = detect_commitment("Here's your plan for the day: focus on the eval harness.")
        assert result.detected is True

    def test_does_not_detect_casual_offer(self):
        result = detect_commitment("I can help with that if you'd like.")
        assert result.detected is False

    def test_does_not_detect_information(self):
        result = detect_commitment("Here's some information about how retrieval works.")
        assert result.detected is False

    def test_does_not_detect_observation(self):
        result = detect_commitment("The architecture is designed for this exact use case.")
        assert result.detected is False

    def test_empty_string_handled(self):
        result = detect_commitment("")
        assert result.detected is False

    def test_short_string_handled(self):
        result = detect_commitment("OK")
        assert result.detected is False

    def test_none_commitment_text_when_not_detected(self):
        result = detect_commitment("That sounds good.")
        assert result.detected is False
        assert result.commitment_text is None

    def test_commitment_text_is_sentence_not_full_response(self):
        result = detect_commitment(
            "Sure, that makes sense. I'll walk you through the retrieval pipeline. "
            "It has several stages including intent classification and ranking."
        )
        assert result.detected is True
        assert len(result.commitment_text) <= 120


class TestCommitmentIntegration:
    """Integration: commitment writes open_loop to vault."""

    def test_commitment_writes_open_loop(self, tmp_path):
        from src.state.state_service import StateService

        service = StateService(vault_path=tmp_path)
        response = "I'll walk you through the state layer step by step."

        result = detect_commitment(response)
        assert result.detected is True

        record = service.make_record(
            state_type="open_loop",
            text=result.commitment_text,
            source="commitment_detector",
            metadata={"session_id": "test-session", "resolved": False},
        )
        service.write(record)

        all_records = service.read_all()
        assert len(all_records) == 1
        assert all_records[0].type == "open_loop"
        assert all_records[0].source == "commitment_detector"
        assert "walk you through" in all_records[0].text.lower()
