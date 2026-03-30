"""
tests/test_task_resolver.py

Tests for TaskResolver (active task resolution for context injection).
"""

import pytest

from src.tasks.models import TaskRecord
from src.tasks.task_resolver import MAX_ACTIVE_TASKS, TaskResolver
from src.tasks.task_service import TaskService


def _write_task(
    service: TaskService,
    title: str,
    status: str = "active",
    project_id: str | None = None,
    timestamp: str | None = None,
    priority: str | None = None,
) -> TaskRecord:
    """Write a task record and return it."""
    metadata = {}
    if priority:
        metadata["priority"] = priority

    if timestamp:
        record = TaskRecord(
            id=timestamp,
            timestamp=timestamp,
            type="task",
            title=title,
            status=status,
            text=title,
            source="test",
            project_id=project_id,
            metadata=metadata,
        )
    else:
        record = TaskService.make_record(
            title=title,
            status=status,
            source="test",
            project_id=project_id,
            metadata=metadata,
        )
    service.write(record)
    return record


class TestTaskResolverActiveTasks:
    """Tests for get_active_tasks()."""

    def test_returns_active_and_proposed(self, tmp_path):
        service = TaskService(vault_path=tmp_path)
        resolver = TaskResolver(service=service)

        _write_task(service, "Active task", status="active")
        _write_task(service, "Proposed task", status="proposed")
        _write_task(service, "Done task", status="done")
        _write_task(service, "Cancelled task", status="cancelled")

        items = resolver.get_active_tasks()
        assert len(items) == 2
        titles = {i.title for i in items}
        assert titles == {"Active task", "Proposed task"}

    def test_empty_vault_returns_empty(self, tmp_path):
        service = TaskService(vault_path=tmp_path)
        resolver = TaskResolver(service=service)
        assert resolver.get_active_tasks() == []

    def test_capped_at_max(self, tmp_path):
        service = TaskService(vault_path=tmp_path)
        resolver = TaskResolver(service=service)

        for i in range(MAX_ACTIVE_TASKS + 5):
            _write_task(
                service,
                f"Task {i}",
                status="active",
                timestamp=f"2026-03-30T{10+i:02d}-00-00-000000",
            )

        items = resolver.get_active_tasks()
        assert len(items) == MAX_ACTIVE_TASKS

    def test_returns_task_items_with_correct_fields(self, tmp_path):
        service = TaskService(vault_path=tmp_path)
        resolver = TaskResolver(service=service)

        _write_task(
            service,
            "Important task",
            status="active",
            project_id="proj-1",
            priority="high",
        )

        items = resolver.get_active_tasks()
        assert len(items) == 1
        item = items[0]
        assert item.title == "Important task"
        assert item.status == "active"
        assert item.project_id == "proj-1"
        assert item.priority == "high"


class TestTaskResolverByProject:
    """Tests for get_tasks_by_project()."""

    def test_returns_project_tasks(self, tmp_path):
        service = TaskService(vault_path=tmp_path)
        resolver = TaskResolver(service=service)

        _write_task(service, "Proj task", project_id="proj-1")
        _write_task(service, "General task")
        _write_task(service, "Other proj", project_id="proj-2")

        items = resolver.get_tasks_by_project("proj-1")
        assert len(items) == 1
        assert items[0].title == "Proj task"

    def test_includes_all_statuses_for_project(self, tmp_path):
        service = TaskService(vault_path=tmp_path)
        resolver = TaskResolver(service=service)

        _write_task(service, "Active", status="active", project_id="proj-1")
        _write_task(service, "Done", status="done", project_id="proj-1")

        items = resolver.get_tasks_by_project("proj-1")
        assert len(items) == 2

    def test_empty_project_returns_empty(self, tmp_path):
        service = TaskService(vault_path=tmp_path)
        resolver = TaskResolver(service=service)
        assert resolver.get_tasks_by_project("nonexistent") == []
