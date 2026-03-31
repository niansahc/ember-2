"""
tests/test_task_handler.py

Tests for task creation handler (explicit requests and offer/confirm flow).
"""

import pytest

from src.tasks.task_handler import (
    detect_explicit_task_request,
    create_task,
    store_pending_offer,
    check_pending_confirmation,
    clear_pending_offer,
    _pending_offers,
    TaskCreationResult,
)
from src.tasks.task_service import TaskService


class TestExplicitTaskDetection:
    """Detect explicit task creation requests in user messages."""

    # --- Basic patterns ---

    def test_create_a_task_for(self):
        titles = detect_explicit_task_request("Create a task for updating the README")
        assert titles == ["updating the README"]

    def test_add_a_task_called(self):
        titles = detect_explicit_task_request("Add a task called fix login bug")
        assert titles == ["fix login bug"]

    def test_make_a_task_to(self):
        titles = detect_explicit_task_request("Make a task to review the PR")
        assert titles == ["review the PR"]

    def test_track_as_a_task(self):
        titles = detect_explicit_task_request("Track deploy new version as a task")
        assert titles == ["deploy new version"]

    def test_new_task_for(self):
        titles = detect_explicit_task_request("New task for running the eval harness")
        assert titles == ["running the eval harness"]

    # --- Polite/natural variations ---

    def test_can_you_create(self):
        titles = detect_explicit_task_request("Can you create a task for updating the docs")
        assert titles == ["updating the docs"]

    def test_please_add(self):
        titles = detect_explicit_task_request("Please add a task for running tests")
        assert titles == ["running tests"]

    def test_can_you_add(self):
        titles = detect_explicit_task_request("Can you add a task for the migration")
        assert titles == ["the migration"]

    def test_i_need_a_task(self):
        titles = detect_explicit_task_request("I need a task for reviewing the architecture")
        assert titles == ["reviewing the architecture"]

    def test_add_to_task_list(self):
        titles = detect_explicit_task_request("Add weeding to my task list")
        assert titles == ["weeding"]

    def test_put_on_task_list(self):
        titles = detect_explicit_task_request("Put mowing on my task list")
        assert titles == ["mowing"]

    def test_remind_me_to(self):
        titles = detect_explicit_task_request("Remind me to water the plants")
        assert titles == ["water the plants"]

    def test_need_to_remember(self):
        titles = detect_explicit_task_request("I need to remember to call the dentist")
        assert titles == ["call the dentist"]

    # --- Multi-item lists ---

    def test_comma_separated_list(self):
        titles = detect_explicit_task_request("Create tasks for weeding, mowing, and picking up sticks")
        assert titles == ["weeding", "mowing", "picking up sticks"]

    def test_two_items_with_and(self):
        titles = detect_explicit_task_request("Create tasks for weeding and mowing")
        assert titles == ["weeding", "mowing"]

    def test_comma_no_and(self):
        titles = detect_explicit_task_request("Add tasks for laundry, dishes, vacuuming")
        assert titles == ["laundry", "dishes", "vacuuming"]

    # --- Non-matches ---

    def test_not_a_task_request(self):
        titles = detect_explicit_task_request("What tasks do I have?")
        assert titles == []

    def test_short_message_ignored(self):
        titles = detect_explicit_task_request("hi")
        assert titles == []

    def test_empty_message_ignored(self):
        titles = detect_explicit_task_request("")
        assert titles == []

    def test_strips_trailing_punctuation(self):
        titles = detect_explicit_task_request("Create a task for updating docs.")
        assert titles == ["updating docs"]


class TestCreateTask:
    """Task creation writes to vault."""

    def test_writes_task_record(self, tmp_path):
        result = create_task(
            title="Test task",
            source="user_input",
            session_id="sess-1",
            project_id="proj-1",
            vault_path=tmp_path,
        )
        assert result.created is True
        assert result.task_title == "Test task"

        # Verify it's in the vault
        service = TaskService(vault_path=tmp_path)
        records = service.read_all()
        assert len(records) == 1
        assert records[0].title == "Test task"
        assert records[0].source == "user_input"
        assert records[0].metadata["session_id"] == "sess-1"
        assert records[0].project_id == "proj-1"

    def test_returns_error_on_failure(self, tmp_path):
        # Make vault path a file to trigger write failure
        bad_path = tmp_path / "not_a_dir"
        bad_path.write_text("block", encoding="utf-8")
        result = create_task(title="Fail", vault_path=bad_path)
        assert result.created is False
        assert result.error is not None


class TestOfferConfirmFlow:
    """Offer/confirm flow stores pending offers and resolves on next turn."""

    def setup_method(self):
        """Clear pending offers between tests."""
        _pending_offers.clear()

    def test_store_and_confirm(self, tmp_path):
        store_pending_offer("sess-1", "Fix the retrieval bug")

        result = check_pending_confirmation(
            session_id="sess-1",
            user_message="yes",
            project_id="proj-1",
        )
        assert result is not None
        assert result.created is True
        assert result.task_title == "Fix the retrieval bug"

    def test_confirm_with_please(self, tmp_path):
        store_pending_offer("sess-1", "Run the eval")
        result = check_pending_confirmation("sess-1", "yes please")
        assert result is not None
        assert result.created is True

    def test_confirm_with_sure(self, tmp_path):
        store_pending_offer("sess-1", "Update docs")
        result = check_pending_confirmation("sess-1", "sure")
        assert result is not None
        assert result.created is True

    def test_confirm_with_add_it(self, tmp_path):
        store_pending_offer("sess-1", "Deploy v2")
        result = check_pending_confirmation("sess-1", "add it")
        assert result is not None
        assert result.created is True

    def test_decline_with_no(self, tmp_path):
        store_pending_offer("sess-1", "Some task")
        result = check_pending_confirmation("sess-1", "no")
        assert result is not None
        assert result.created is False
        assert result.task_title == "Some task"

    def test_decline_with_skip(self, tmp_path):
        store_pending_offer("sess-1", "Some task")
        result = check_pending_confirmation("sess-1", "skip")
        assert result is not None
        assert result.created is False

    def test_no_pending_returns_none(self):
        result = check_pending_confirmation("sess-1", "yes")
        assert result is None

    def test_ambiguous_clears_offer(self):
        store_pending_offer("sess-1", "Ambiguous task")
        result = check_pending_confirmation("sess-1", "tell me more about that")
        assert result is None
        assert "sess-1" not in _pending_offers

    def test_offer_consumed_after_check(self):
        store_pending_offer("sess-1", "One-time offer")
        check_pending_confirmation("sess-1", "yes")
        # Second check should find nothing
        result = check_pending_confirmation("sess-1", "yes")
        assert result is None

    def test_clear_pending_offer(self):
        store_pending_offer("sess-1", "Clear me")
        clear_pending_offer("sess-1")
        assert "sess-1" not in _pending_offers


class TestConfirmWritesToVault:
    """Confirm that the offer/confirm path actually writes a TaskRecord."""

    def setup_method(self):
        _pending_offers.clear()

    def test_confirmed_task_in_vault(self, tmp_path):
        store_pending_offer("sess-1", "Review the architecture")

        # Patch create_task to use tmp_path
        from unittest.mock import patch
        with patch("src.tasks.task_handler.TaskService") as MockService:
            mock_instance = MockService.return_value
            mock_instance.write.return_value = tmp_path / "test.json"

            result = check_pending_confirmation(
                session_id="sess-1",
                user_message="yes",
                project_id=None,
            )

        assert result.created is True
        # Verify make_record was called with correct source
        MockService.make_record.assert_called_once()
        call_kwargs = MockService.make_record.call_args[1]
        assert call_kwargs["source"] == "task_detector"
        assert call_kwargs["title"] == "Review the architecture"

    def test_declined_task_not_in_vault(self, tmp_path):
        store_pending_offer("sess-1", "Skip this one")

        from unittest.mock import patch
        with patch("src.tasks.task_handler.TaskService") as MockService:
            result = check_pending_confirmation("sess-1", "no")

        assert result.created is False
        MockService.return_value.write.assert_not_called()
