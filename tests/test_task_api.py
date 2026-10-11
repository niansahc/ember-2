"""
tests/test_task_api.py

Integration tests for task API endpoints.
"""

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    """Create a test client with a temporary vault."""
    with patch("src.core.config.get_private_vault_path", return_value=tmp_path), \
         patch("src.tasks.task_service.get_private_vault_path", return_value=tmp_path), \
         patch("src.api.main.get_ember_api_key", return_value=None):
        from src.api.main import app
        yield TestClient(app)


class TestCreateTask:
    """POST /v1/tasks"""

    def test_create_task_default_status(self, client):
        resp = client.post("/v1/tasks", json={"title": "Fix the bug"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "created"
        assert data["title"] == "Fix the bug"
        assert data["task_status"] == "active"
        assert data["id"]

    def test_create_task_custom_status(self, client):
        resp = client.post("/v1/tasks", json={"title": "Maybe later", "status": "proposed"})
        assert resp.status_code == 200
        assert resp.json()["task_status"] == "proposed"

    def test_create_task_with_project(self, client):
        resp = client.post("/v1/tasks", json={
            "title": "Project task",
            "project_id": "proj-1",
        })
        assert resp.status_code == 200

    def test_create_task_invalid_status(self, client):
        resp = client.post("/v1/tasks", json={"title": "Bad", "status": "blocked"})
        assert resp.status_code == 400
        assert "Invalid status" in resp.json()["detail"]

    def test_create_task_missing_title(self, client):
        resp = client.post("/v1/tasks", json={})
        assert resp.status_code == 422  # Pydantic validation


class TestListTasks:
    """GET /v1/tasks"""

    def test_list_tasks_empty(self, client):
        resp = client.get("/v1/tasks")
        assert resp.status_code == 200
        assert resp.json()["tasks"] == []

    def test_list_tasks_returns_created(self, client):
        client.post("/v1/tasks", json={"title": "Task 1"})
        client.post("/v1/tasks", json={"title": "Task 2"})

        resp = client.get("/v1/tasks")
        assert resp.status_code == 200
        tasks = resp.json()["tasks"]
        assert len(tasks) == 2

    def test_list_tasks_filter_by_status(self, client):
        client.post("/v1/tasks", json={"title": "Active", "status": "active"})
        client.post("/v1/tasks", json={"title": "Done", "status": "done"})

        resp = client.get("/v1/tasks", params={"status": "active"})
        tasks = resp.json()["tasks"]
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Active"

    def test_list_tasks_filter_by_project(self, client):
        client.post("/v1/tasks", json={"title": "Proj", "project_id": "p1"})
        client.post("/v1/tasks", json={"title": "General"})

        resp = client.get("/v1/tasks", params={"project_id": "p1"})
        tasks = resp.json()["tasks"]
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Proj"

    def test_list_tasks_invalid_status_filter(self, client):
        resp = client.get("/v1/tasks", params={"status": "blocked"})
        assert resp.status_code == 400


class TestGetTask:
    """GET /v1/tasks/{id}"""

    def test_get_existing_task(self, client):
        create_resp = client.post("/v1/tasks", json={"title": "Find me"})
        task_id = create_resp.json()["id"]

        resp = client.get(f"/v1/tasks/{task_id}")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Find me"

    def test_get_nonexistent_task(self, client):
        resp = client.get("/v1/tasks/nonexistent")
        assert resp.status_code == 404


class TestUpdateTaskStatus:
    """PATCH /v1/tasks/{id}"""

    def test_update_status(self, client):
        create_resp = client.post("/v1/tasks", json={"title": "Do it"})
        task_id = create_resp.json()["id"]

        resp = client.patch(f"/v1/tasks/{task_id}", json={"status": "done"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"
        assert data["task_status"] == "done"
        assert data["previous_status"] == "active"

        # Verify the latest record reflects the update
        get_resp = client.get(f"/v1/tasks/{task_id}")
        assert get_resp.json()["status"] == "done"

    def test_update_invalid_status(self, client):
        create_resp = client.post("/v1/tasks", json={"title": "Test"})
        task_id = create_resp.json()["id"]

        resp = client.patch(f"/v1/tasks/{task_id}", json={"status": "blocked"})
        assert resp.status_code == 400

    def test_update_nonexistent_task(self, client):
        resp = client.patch("/v1/tasks/nonexistent", json={"status": "done"})
        assert resp.status_code == 404


# Far-future so the record carrying it is the task's latest.
SENTINEL_TIMESTAMP = "2099-01-01T00-00-00-000001"


def _patch_task_timestamp_source(monkeypatch):
    """Route task_service.next_timestamp() to a sentinel. Applied after the
    task is created so the create record keeps a real timestamp."""
    monkeypatch.setattr("src.tasks.task_service.next_timestamp", lambda: SENTINEL_TIMESTAMP)


class TestDeleteTask:
    """DELETE /v1/tasks/{id}"""

    def test_delete_cancels_task(self, client):
        task_id = client.post("/v1/tasks", json={"title": "Drop it"}).json()["id"]

        resp = client.delete(f"/v1/tasks/{task_id}")
        assert resp.status_code == 200
        assert resp.json() == {"status": "cancelled", "id": task_id}
        assert client.get(f"/v1/tasks/{task_id}").json()["status"] == "cancelled"

        again = client.delete(f"/v1/tasks/{task_id}")
        assert again.json() == {"status": "already_cancelled", "id": task_id}

    def test_delete_nonexistent_task(self, client):
        assert client.delete("/v1/tasks/nonexistent").status_code == 404

    def test_delete_timestamp_comes_from_task_timestamp_source(self, client, monkeypatch):
        """The cancel record is named {timestamp}_{slug}.json like every
        other record for the task. A raw datetime.now() could repeat the
        timestamp of an update written in the same clock tick, and
        TaskService.write() would then silently drop the cancel record.
        next_timestamp() never repeats a value."""
        task_id = client.post("/v1/tasks", json={"title": "Cancel me"}).json()["id"]
        _patch_task_timestamp_source(monkeypatch)

        client.delete(f"/v1/tasks/{task_id}")

        latest = client.get(f"/v1/tasks/{task_id}").json()
        assert latest["timestamp"] == SENTINEL_TIMESTAMP
        assert latest["status"] == "cancelled"

    def test_control_update_timestamp_comes_from_task_timestamp_source(self, client, monkeypatch):
        """Positive control: PATCH already uses next_timestamp(), so the same
        patch shows up in its record. This proves the patch takes effect at
        call time and the delete test fails only on the delete path."""
        task_id = client.post("/v1/tasks", json={"title": "Finish me"}).json()["id"]
        _patch_task_timestamp_source(monkeypatch)

        client.patch(f"/v1/tasks/{task_id}", json={"status": "done"})

        latest = client.get(f"/v1/tasks/{task_id}").json()
        assert latest["timestamp"] == SENTINEL_TIMESTAMP
        assert latest["status"] == "done"
