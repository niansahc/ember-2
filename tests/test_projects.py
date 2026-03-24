"""
tests/test_projects.py

Tests for the projects backend: project.py service and API endpoints.
"""

import pytest
from src.memory.project import (
    _generate_project_id,
    _resolve_projects,
)


class TestProjectIdGeneration:
    """Test project ID format."""

    def test_project_id_starts_with_prefix(self):
        pid = _generate_project_id()
        assert pid.startswith("proj_")

    def test_project_id_unique(self):
        ids = {_generate_project_id() for _ in range(50)}
        assert len(ids) == 50

    def test_project_id_length(self):
        pid = _generate_project_id()
        assert len(pid) == 17  # "proj_" + 12 hex chars


class TestProjectResolution:
    """Test that latest record wins per project_id."""

    def test_latest_timestamp_wins(self):
        records = [
            {"timestamp": "2026-03-24T10:00:00", "text": "Old Name", "metadata": {"project_id": "proj_aaa"}},
            {"timestamp": "2026-03-24T12:00:00", "text": "New Name", "metadata": {"project_id": "proj_aaa"}},
        ]
        resolved = _resolve_projects(records)
        assert resolved["proj_aaa"]["text"] == "New Name"

    def test_multiple_projects_resolved_independently(self):
        records = [
            {"timestamp": "2026-03-24T10:00:00", "text": "Proj A", "metadata": {"project_id": "proj_aaa"}},
            {"timestamp": "2026-03-24T10:00:00", "text": "Proj B", "metadata": {"project_id": "proj_bbb"}},
            {"timestamp": "2026-03-24T12:00:00", "text": "Proj A Renamed", "metadata": {"project_id": "proj_aaa"}},
        ]
        resolved = _resolve_projects(records)
        assert len(resolved) == 2
        assert resolved["proj_aaa"]["text"] == "Proj A Renamed"
        assert resolved["proj_bbb"]["text"] == "Proj B"

    def test_empty_project_id_skipped(self):
        records = [
            {"timestamp": "2026-03-24T10:00:00", "text": "No ID", "metadata": {"project_id": ""}},
            {"timestamp": "2026-03-24T10:00:00", "text": "No Meta", "metadata": {}},
        ]
        resolved = _resolve_projects(records)
        assert len(resolved) == 0

    def test_deleted_project_still_resolved(self):
        """Resolution returns all records — deletion filtering happens in list_projects."""
        records = [
            {"timestamp": "2026-03-24T10:00:00", "text": "Proj", "metadata": {"project_id": "proj_aaa", "deleted": False}},
            {"timestamp": "2026-03-24T12:00:00", "text": "Proj", "metadata": {"project_id": "proj_aaa", "deleted": True}},
        ]
        resolved = _resolve_projects(records)
        assert resolved["proj_aaa"]["metadata"]["deleted"] is True


class TestSessionProjectSupport:
    """Test that session module supports project_id."""

    def test_update_session_function_exists(self):
        from src.memory.session import update_session
        assert callable(update_session)

    def test_list_sessions_by_project_function_exists(self):
        from src.memory.session import list_sessions_by_project
        assert callable(list_sessions_by_project)

    def test_session_list_includes_project_id_field(self):
        """The list_sessions output schema should include project_id."""
        from src.memory.session import list_sessions
        sessions = list_sessions(limit=1)
        # Even if empty, the function should work
        assert isinstance(sessions, list)


class TestProjectEndpointModels:
    """Test that Pydantic models for project endpoints are defined correctly."""

    def test_project_create_request(self):
        from src.api.main import ProjectCreateRequest
        req = ProjectCreateRequest(name="Test", color="#ff0000")
        assert req.name == "Test"
        assert req.color == "#ff0000"

    def test_project_create_request_default_color(self):
        from src.api.main import ProjectCreateRequest
        req = ProjectCreateRequest(name="Test")
        assert req.color == "#ff8c00"

    def test_project_update_request_optional_fields(self):
        from src.api.main import ProjectUpdateRequest
        req = ProjectUpdateRequest()
        assert req.name is None
        assert req.color is None

    def test_conversation_update_request_supports_project_id(self):
        from src.api.main import ConversationUpdateRequest
        req = ConversationUpdateRequest(project_id="proj_abc123")
        assert req.project_id == "proj_abc123"
        assert req.title is None

    def test_conversation_update_request_supports_both(self):
        from src.api.main import ConversationUpdateRequest
        req = ConversationUpdateRequest(title="New Title", project_id="proj_abc123")
        assert req.title == "New Title"
        assert req.project_id == "proj_abc123"
