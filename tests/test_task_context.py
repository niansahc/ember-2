"""
tests/test_task_context.py

Integration tests for task context injection (tasks appearing in ContextPacket).
"""

import pytest
from unittest.mock import patch, MagicMock

from src.context.models import ContextPacket
from src.context.retriever import ContextRetriever
from src.tasks.models import TaskItem
from src.tasks.task_resolver import TaskResolver
from src.tasks.task_service import TaskService


class TestTaskContextInjection:
    """Verify that active tasks appear in context retrieval."""

    def test_active_tasks_in_retrieve(self, tmp_path):
        service = TaskService(vault_path=tmp_path)
        record = TaskService.make_record(title="Fix retrieval bug", status="active")
        service.write(record)

        resolver = TaskResolver(service=service)

        with patch("src.context.retriever._semantic_search", return_value=[]), \
             patch.object(ContextRetriever, "get_memory_items", return_value=[]), \
             patch.object(ContextRetriever, "get_reflection_items", return_value=[]), \
             patch.object(ContextRetriever, "get_state_items", return_value=[]):

            retriever = ContextRetriever(task_resolver=resolver)
            state_items, task_items, memory_items, reflection_items, _ = retriever.retrieve("test")

        assert len(task_items) == 1
        assert task_items[0].title == "Fix retrieval bug"
        assert task_items[0].status == "active"

    def test_done_tasks_excluded_from_context(self, tmp_path):
        service = TaskService(vault_path=tmp_path)
        service.write(TaskService.make_record(title="Done task", status="done"))
        service.write(TaskService.make_record(title="Active task", status="active"))

        resolver = TaskResolver(service=service)

        with patch("src.context.retriever._semantic_search", return_value=[]), \
             patch.object(ContextRetriever, "get_memory_items", return_value=[]), \
             patch.object(ContextRetriever, "get_reflection_items", return_value=[]), \
             patch.object(ContextRetriever, "get_state_items", return_value=[]):

            retriever = ContextRetriever(task_resolver=resolver)
            _, task_items, _, _, _ = retriever.retrieve("test")

        assert len(task_items) == 1
        assert task_items[0].title == "Active task"

    def test_task_retrieval_failure_does_not_crash(self, tmp_path):
        """Task retrieval errors should not crash context building."""
        broken_resolver = MagicMock(spec=TaskResolver)
        broken_resolver.get_active_tasks.side_effect = Exception("Vault corrupted")

        with patch("src.context.retriever._semantic_search", return_value=[]), \
             patch.object(ContextRetriever, "get_memory_items", return_value=[]), \
             patch.object(ContextRetriever, "get_reflection_items", return_value=[]), \
             patch.object(ContextRetriever, "get_state_items", return_value=[]):

            retriever = ContextRetriever(task_resolver=broken_resolver)
            with pytest.warns(match="Task retrieval failed"):
                _, task_items, _, _, _ = retriever.retrieve("test")

        assert task_items == []


class TestContextPacketTaskItems:
    """Verify ContextPacket includes task_items field."""

    def test_context_packet_has_task_items(self):
        packet = ContextPacket(
            user_message="test",
            task_items=[
                TaskItem(id="t1", title="Fix bug", status="active"),
            ],
        )
        assert len(packet.task_items) == 1
        assert packet.task_items[0].title == "Fix bug"

    def test_context_packet_default_empty_tasks(self):
        packet = ContextPacket(user_message="test")
        assert packet.task_items == []
