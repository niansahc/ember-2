"""
tests/test_session_reflection.py

Tests for session reflection mode (ADR-009).
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.reflection.session_reflection import (
    generate_session_reflection,
    MIN_TURNS_FOR_REFLECTION,
)


MOCK_BUFFER = [
    {"user": "What should I focus on today?", "assistant": "You've been working on the retrieval pipeline."},
    {"user": "Let's fix the profile bug.", "assistant": "I'll walk you through it step by step."},
    {"user": "That worked. What's next?", "assistant": "We should run the eval harness to confirm."},
    {"user": "Good idea. Run it.", "assistant": "Running now. Results will take a few minutes."},
]

MOCK_SHORT_BUFFER = [
    {"user": "Hi", "assistant": "Hello!"},
]


class TestSessionReflectionGenerator:
    """Unit tests for generate_session_reflection."""

    def test_generates_output_from_buffer(self, tmp_path):
        with patch("src.reflection.session_reflection.ollama") as mock_ollama, \
             patch("src.reflection.session_reflection.write_memory") as mock_write:
            mock_ollama.chat.return_value = {
                "message": {"content": "The session focused on fixing the profile retrieval bug and running the eval harness."}
            }

            result = generate_session_reflection(MOCK_BUFFER, session_id="test-sess")

            assert result is not None
            assert "profile" in result.lower() or "eval" in result.lower() or "session" in result.lower()
            mock_write.assert_called_once()
            call_kwargs = mock_write.call_args[1]
            assert call_kwargs["memory_type"] == "reflection"
            assert call_kwargs["source"] == "session_reflection"
            assert call_kwargs["metadata"]["cadence"] == "session"
            assert call_kwargs["metadata"]["session_id"] == "test-sess"

    def test_skips_empty_buffer(self):
        result = generate_session_reflection([], session_id="test")
        assert result is None

    def test_skips_short_buffer(self):
        result = generate_session_reflection(MOCK_SHORT_BUFFER, session_id="test")
        assert result is None

    def test_minimum_turns_threshold(self):
        # Exactly at threshold should proceed
        buffer = [
            {"user": f"Message {i}", "assistant": f"Reply {i}"}
            for i in range(MIN_TURNS_FOR_REFLECTION)
        ]
        with patch("src.reflection.session_reflection.ollama") as mock_ollama, \
             patch("src.reflection.session_reflection.write_memory"):
            mock_ollama.chat.return_value = {
                "message": {"content": "Session summary."}
            }
            result = generate_session_reflection(buffer)
            assert result is not None

    def test_handles_llm_failure_gracefully(self):
        with patch("src.reflection.session_reflection.ollama") as mock_ollama, \
             patch("src.reflection.session_reflection.get_ember_model", return_value="qwen3:8b"):
            mock_ollama.chat.side_effect = Exception("Connection refused")
            result = generate_session_reflection(MOCK_BUFFER)
            assert result is None

    def test_session_id_optional(self):
        with patch("src.reflection.session_reflection.ollama") as mock_ollama, \
             patch("src.reflection.session_reflection.write_memory") as mock_write:
            mock_ollama.chat.return_value = {
                "message": {"content": "Session summary without ID."}
            }
            result = generate_session_reflection(MOCK_BUFFER)
            assert result is not None
            call_kwargs = mock_write.call_args[1]
            assert "session_id" not in call_kwargs["metadata"]
