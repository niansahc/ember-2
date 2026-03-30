"""
tests/test_task_detector.py

Tests for the task detector.
"""

import pytest

from src.tasks.task_detector import detect_task, TaskDetectionResult


class TestTaskDetection:
    """Core detection logic."""

    def test_detects_implementation_task(self):
        result = detect_task("I'll implement the caching layer for the retrieval pipeline.")
        assert result.detected is True
        assert "caching" in result.task_title.lower() or "implement" in result.task_title.lower()

    def test_detects_fix_task(self):
        result = detect_task("I'll fix the search bar focus issue in the sidebar.")
        assert result.detected is True

    def test_detects_create_task(self):
        result = detect_task("I'll create the migration script for the new schema.")
        assert result.detected is True

    def test_detects_user_directed_task(self):
        result = detect_task("You need to update the .env file with the new vault path.")
        assert result.detected is True

    def test_detects_reminder_task(self):
        result = detect_task("Don't forget to run the eval harness before merging.")
        assert result.detected is True

    def test_detects_plan_task(self):
        result = detect_task("Here's the plan: first we update the schema, then migrate.")
        assert result.detected is True

    def test_does_not_detect_explanation(self):
        result = detect_task("Here's how the retrieval pipeline works. It has three stages.")
        assert result.detected is False

    def test_does_not_detect_observation(self):
        result = detect_task("The system is running well. No issues detected.")
        assert result.detected is False

    def test_does_not_detect_conditional(self):
        result = detect_task("You could try restarting the server if that doesn't work.")
        assert result.detected is False

    def test_empty_string_handled(self):
        result = detect_task("")
        assert result.detected is False

    def test_short_string_handled(self):
        result = detect_task("OK")
        assert result.detected is False

    def test_none_fields_when_not_detected(self):
        result = detect_task("That sounds good.")
        assert result.detected is False
        assert result.task_title is None
        assert result.suggested_response is None

    def test_suggested_response_format(self):
        result = detect_task("I'll fix the authentication bug in the login flow.")
        assert result.detected is True
        assert result.suggested_response is not None
        assert "Want me to add" in result.suggested_response
        assert "as a task?" in result.suggested_response

    def test_task_title_is_concise(self):
        result = detect_task(
            "Sure, that makes sense. I'll implement the full caching layer for "
            "the retrieval pipeline including the invalidation logic and the "
            "warming strategy and all the edge cases we discussed."
        )
        assert result.detected is True
        assert len(result.task_title) <= 80
