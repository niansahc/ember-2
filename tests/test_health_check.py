"""
tests/test_health_check.py

Tests for the API health check endpoint.
GET / serves the UI when ui/ exists, so use GET /api/health for JSON checks.
"""

from fastapi.testclient import TestClient
from src.api.main import app

client = TestClient(app)


def test_root_returns_200():
    response = client.get("/")
    assert response.status_code == 200


def test_api_health_returns_200():
    response = client.get("/api/health")
    assert response.status_code == 200


def test_api_health_contains_message():
    response = client.get("/api/health")
    data = response.json()
    assert "message" in data
    assert data["message"] == "Ember-2 API is running"


def test_api_health_contains_model():
    response = client.get("/api/health")
    data = response.json()
    assert "model" in data
    assert isinstance(data["model"], str)
    assert len(data["model"]) > 0
