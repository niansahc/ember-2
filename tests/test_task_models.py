"""
tests/test_task_models.py

Tests for task layer models (TaskRecord, TaskItem).
"""

import pytest

from src.tasks.models import (
    VALID_TASK_STATUSES,
    TaskItem,
    TaskRecord,
)


class TestTaskRecord:
    """Validation and serialization tests for TaskRecord."""

    def test_valid_record_creates_successfully(self):
        record = TaskRecord(
            id="2026-03-30T14-00-00-000000",
            timestamp="2026-03-30T14-00-00-000000",
            type="task",
            title="Fix the retrieval bug",
            status="active",
            text="Fix the retrieval bug in the context pipeline",
            source="user_input",
        )
        assert record.title == "Fix the retrieval bug"
        assert record.status == "active"
        assert record.type == "task"

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match="type must be 'task'"):
            TaskRecord(
                id="t1",
                timestamp="t1",
                type="state",
                title="Bad type",
                status="active",
                text="Bad",
                source="test",
            )

    def test_invalid_status_raises(self):
        with pytest.raises(ValueError, match="Invalid task status"):
            TaskRecord(
                id="t1",
                timestamp="t1",
                type="task",
                title="Bad status",
                status="blocked",
                text="Bad",
                source="test",
            )

    def test_all_valid_statuses_accepted(self):
        for status in VALID_TASK_STATUSES:
            record = TaskRecord(
                id="t1",
                timestamp="t1",
                type="task",
                title="Test",
                status=status,
                text="Test",
                source="test",
            )
            assert record.status == status

    def test_optional_fields_default(self):
        record = TaskRecord(
            id="t1",
            timestamp="t1",
            type="task",
            title="Defaults",
            status="active",
            text="Defaults",
            source="test",
        )
        assert record.project_id is None
        assert record.tags == []
        assert record.metadata == {}

    def test_project_id_stored(self):
        record = TaskRecord(
            id="t1",
            timestamp="t1",
            type="task",
            title="Project task",
            status="active",
            text="In project",
            source="test",
            project_id="proj-123",
        )
        assert record.project_id == "proj-123"


class TestTaskItem:
    """Tests for the lightweight TaskItem context object."""

    def test_basic_creation(self):
        item = TaskItem(
            id="t1",
            title="Fix bug",
            status="active",
        )
        assert item.id == "t1"
        assert item.title == "Fix bug"
        assert item.status == "active"
        assert item.project_id is None
        assert item.priority is None

    def test_with_priority_and_project(self):
        item = TaskItem(
            id="t1",
            title="Ship v0.12",
            status="proposed",
            project_id="ember-2",
            priority="high",
        )
        assert item.project_id == "ember-2"
        assert item.priority == "high"


class TestValidTaskStatuses:
    """Verify the status taxonomy."""

    def test_expected_statuses_present(self):
        expected = {"proposed", "active", "done", "cancelled"}
        assert VALID_TASK_STATUSES == expected

    def test_statuses_are_frozenset(self):
        assert isinstance(VALID_TASK_STATUSES, frozenset)
