"""
src/tasks/task_resolver.py

TaskResolver computes the active task list for context injection.

Reads all task records via TaskService and filters to proposed + active
status. Returns lightweight TaskItem objects suitable for ContextPacket.

Capped at MAX_ACTIVE_TASKS to prevent prompt bloat.
"""

from __future__ import annotations

from src.tasks.models import TaskItem, TaskRecord
from src.tasks.task_service import TaskService


# Maximum number of active tasks injected into context.
# Beyond this, oldest tasks are dropped.
MAX_ACTIVE_TASKS = 10


class TaskResolver:
    """
    Resolves active tasks from vault records for the context layer.

    Usage
    -----
    resolver = TaskResolver()
    items = resolver.get_active_tasks()
    """

    def __init__(self, service: TaskService | None = None) -> None:
        """
        Parameters
        ----------
        service : TaskService | None
            The TaskService to use for reading vault records. If None, a
            default TaskService is created. Pass an explicit instance in
            tests to control the vault path.
        """
        self._service = service or TaskService()

    def _record_to_item(self, record: TaskRecord) -> TaskItem:
        """Convert a TaskRecord into a TaskItem for context injection."""
        priority = record.metadata.get("priority") if record.metadata else None
        if priority is not None:
            priority = str(priority)

        return TaskItem(
            id=record.id,
            title=record.title,
            status=record.status,
            project_id=record.project_id,
            priority=priority,
        )

    def get_active_tasks(self) -> list[TaskItem]:
        """
        Return all proposed and active tasks as TaskItems.

        Results are capped at MAX_ACTIVE_TASKS, newest first. Tasks with
        status done or cancelled are excluded.

        Returns
        -------
        list[TaskItem]
            Active task items for context injection.
        """
        records = self._service.read_active()
        # read_active() returns newest first, cap at limit
        return [self._record_to_item(r) for r in records[:MAX_ACTIVE_TASKS]]

    def get_tasks_by_project(self, project_id: str) -> list[TaskItem]:
        """
        Return all tasks for a project as TaskItems.

        Includes all statuses (proposed, active, done, cancelled) for
        project-scoped views. Newest first, capped at MAX_ACTIVE_TASKS.

        Returns
        -------
        list[TaskItem]
            Task items for the specified project.
        """
        records = self._service.read_by_project(project_id)
        return [self._record_to_item(r) for r in records[:MAX_ACTIVE_TASKS]]
