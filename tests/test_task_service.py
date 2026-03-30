"""
tests/test_task_service.py

Tests for TaskService (vault I/O for task records).
"""

import json
import pytest

from src.tasks.models import VALID_TASK_STATUSES, TaskRecord
from src.tasks.task_service import TaskService


def _make_record(
    title: str = "Test task",
    status: str = "active",
    source: str = "test",
    project_id: str | None = None,
    **kwargs,
) -> TaskRecord:
    """Helper to create a TaskRecord via the factory."""
    return TaskService.make_record(
        title=title,
        status=status,
        source=source,
        project_id=project_id,
        **kwargs,
    )


class TestTaskServiceWrite:
    """Tests for writing task records to the vault."""

    def test_write_creates_json_file(self, tmp_path):
        service = TaskService(vault_path=tmp_path)
        record = _make_record()
        path = service.write(record)

        assert path.exists()
        assert path.suffix == ".json"

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["title"] == "Test task"
        assert data["type"] == "task"
        assert data["status"] == "active"

    def test_write_is_append_only(self, tmp_path):
        service = TaskService(vault_path=tmp_path)
        record = _make_record()
        path1 = service.write(record)

        # Writing the same record again should skip (file already exists)
        with pytest.warns(match="already exists"):
            path2 = service.write(record)

        assert path1 == path2

    def test_write_creates_directory(self, tmp_path):
        vault = tmp_path / "nested" / "vault"
        service = TaskService(vault_path=vault)
        record = _make_record()
        path = service.write(record)
        assert path.exists()


class TestTaskServiceRead:
    """Tests for reading task records from the vault."""

    def test_read_all_returns_newest_first(self, tmp_path):
        service = TaskService(vault_path=tmp_path)

        r1 = TaskRecord(
            id="2026-03-30T10-00-00-000000",
            timestamp="2026-03-30T10-00-00-000000",
            type="task",
            title="First",
            status="active",
            text="First task",
            source="test",
        )
        r2 = TaskRecord(
            id="2026-03-30T11-00-00-000000",
            timestamp="2026-03-30T11-00-00-000000",
            type="task",
            title="Second",
            status="active",
            text="Second task",
            source="test",
        )

        service.write(r1)
        service.write(r2)

        records = service.read_all()
        assert len(records) == 2
        assert records[0].title == "Second"
        assert records[1].title == "First"

    def test_read_all_empty_vault(self, tmp_path):
        service = TaskService(vault_path=tmp_path)
        assert service.read_all() == []

    def test_read_all_skips_corrupted_json(self, tmp_path):
        service = TaskService(vault_path=tmp_path)
        record = _make_record()
        service.write(record)

        # Write a corrupted file
        task_dir = tmp_path / "memory" / "task"
        bad_file = task_dir / "2026-01-01T00-00-00_bad.json"
        bad_file.write_text("not json!", encoding="utf-8")

        with pytest.warns(match="unreadable"):
            records = service.read_all()

        assert len(records) == 1

    def test_read_all_skips_invalid_type(self, tmp_path):
        service = TaskService(vault_path=tmp_path)
        task_dir = tmp_path / "memory" / "task"
        task_dir.mkdir(parents=True, exist_ok=True)

        bad_data = {
            "id": "t1", "timestamp": "t1", "type": "state",
            "title": "Wrong type", "status": "active",
            "text": "Bad", "source": "test",
        }
        (task_dir / "bad_type.json").write_text(
            json.dumps(bad_data), encoding="utf-8"
        )

        with pytest.warns(match="expected 'task'"):
            records = service.read_all()

        assert len(records) == 0

    def test_read_all_skips_missing_fields(self, tmp_path):
        service = TaskService(vault_path=tmp_path)
        task_dir = tmp_path / "memory" / "task"
        task_dir.mkdir(parents=True, exist_ok=True)

        incomplete = {"id": "t1", "type": "task"}
        (task_dir / "incomplete.json").write_text(
            json.dumps(incomplete), encoding="utf-8"
        )

        with pytest.warns(match="missing required"):
            records = service.read_all()

        assert len(records) == 0


class TestTaskServiceReadByStatus:
    """Tests for status-filtered reads."""

    def test_read_by_status(self, tmp_path):
        service = TaskService(vault_path=tmp_path)

        service.write(_make_record(title="Active 1", status="active"))
        service.write(_make_record(title="Done 1", status="done"))
        service.write(_make_record(title="Active 2", status="active"))

        active = service.read_by_status("active")
        assert len(active) == 2
        assert all(r.status == "active" for r in active)

        done = service.read_by_status("done")
        assert len(done) == 1
        assert done[0].title == "Done 1"

    def test_read_by_status_invalid_raises(self, tmp_path):
        service = TaskService(vault_path=tmp_path)
        with pytest.raises(ValueError, match="Unknown task status"):
            service.read_by_status("blocked")


class TestTaskServiceReadByProject:
    """Tests for project-filtered reads."""

    def test_read_by_project(self, tmp_path):
        service = TaskService(vault_path=tmp_path)

        service.write(_make_record(title="Project task", project_id="proj-1"))
        service.write(_make_record(title="General task"))
        service.write(_make_record(title="Other project", project_id="proj-2"))

        proj1 = service.read_by_project("proj-1")
        assert len(proj1) == 1
        assert proj1[0].title == "Project task"

    def test_read_by_project_no_matches(self, tmp_path):
        service = TaskService(vault_path=tmp_path)
        service.write(_make_record(title="General"))
        assert service.read_by_project("nonexistent") == []


class TestTaskServiceReadActive:
    """Tests for active task reads (proposed + active)."""

    def test_read_active(self, tmp_path):
        service = TaskService(vault_path=tmp_path)

        service.write(_make_record(title="Active", status="active"))
        service.write(_make_record(title="Proposed", status="proposed"))
        service.write(_make_record(title="Done", status="done"))
        service.write(_make_record(title="Cancelled", status="cancelled"))

        active = service.read_active()
        assert len(active) == 2
        titles = {r.title for r in active}
        assert titles == {"Active", "Proposed"}


class TestTaskServiceReadById:
    """Tests for single-task lookup by ID."""

    def test_read_by_id_found(self, tmp_path):
        service = TaskService(vault_path=tmp_path)
        record = _make_record(title="Find me")
        service.write(record)

        found = service.read_by_id(record.id)
        assert found is not None
        assert found.title == "Find me"

    def test_read_by_id_not_found(self, tmp_path):
        service = TaskService(vault_path=tmp_path)
        assert service.read_by_id("nonexistent") is None


class TestTaskServiceMakeRecord:
    """Tests for the convenience factory."""

    def test_make_record_defaults(self):
        record = TaskService.make_record(title="Quick task")
        assert record.type == "task"
        assert record.status == "active"
        assert record.source == "user_input"
        assert record.text == "Quick task"  # defaults to title
        assert record.project_id is None
        assert record.tags == []

    def test_make_record_custom_fields(self):
        record = TaskService.make_record(
            title="Custom",
            status="proposed",
            source="task_detector",
            project_id="proj-1",
            text="Detailed description",
            tags=["urgent"],
            metadata={"session_id": "sess-1"},
        )
        assert record.status == "proposed"
        assert record.source == "task_detector"
        assert record.project_id == "proj-1"
        assert record.text == "Detailed description"
        assert record.tags == ["urgent"]
        assert record.metadata["session_id"] == "sess-1"

    def test_make_record_has_microsecond_id(self):
        record = TaskService.make_record(title="Precision")
        # Microsecond format: YYYY-MM-DDTHH-MM-SS-ffffff (6 digits appended to seconds)
        # The %f format appends microseconds directly after seconds with a hyphen
        assert len(record.id) > len("2026-03-30T14-30-00")
        assert record.id == record.timestamp
