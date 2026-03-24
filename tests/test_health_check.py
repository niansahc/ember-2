"""
tests/test_health_check.py

Tests for the GET / health check endpoint.
"""

from fastapi.testclient import TestClient
from src.api.main import app

client = TestClient(app)


def test_health_check_returns_200():
    response = client.get("/")
    assert response.status_code == 200


def test_health_check_contains_message():
    response = client.get("/")
    data = response.json()
    assert "message" in data
    assert data["message"] == "Ember-2 API is running"


def test_health_check_contains_model():
    response = client.get("/")
    data = response.json()
    assert "model" in data
    assert isinstance(data["model"], str)
    assert len(data["model"]) > 0
