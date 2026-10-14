"""
tests/test_searxng_probe_stub.py

GET /api/health reports Docker status by requesting SearXNG
(src.api.main._check_docker_status). In tests that is a real network call:
~2-5 s per health request depending on whether SearXNG is up, and the result
depends on the machine. tests/conftest.py stubs the probe for every test
unless the test is marked `docker_probe`.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import src.api.main as main


@pytest.fixture
def httpx_get_calls(monkeypatch):
    """Record every httpx.get call; answer each with a 200."""
    calls: list[tuple] = []

    def fake_get(url, *args, **kwargs):
        calls.append((url, kwargs))
        return MagicMock(status_code=200)

    monkeypatch.setattr("httpx.get", fake_get)
    return calls


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "get_ember_api_key", lambda: None)
    return TestClient(main.app)


def test_health_makes_no_searxng_request_by_default(client, httpx_get_calls):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert httpx_get_calls == []


@pytest.mark.docker_probe
def test_control_docker_probe_marker_reaches_searxng(client, httpx_get_calls):
    """Positive control: with the marker the real probe runs, so the empty
    list above means the stub held, not a probe that never fires."""
    response = client.get("/api/health")

    assert response.status_code == 200
    assert len(httpx_get_calls) == 1
    assert ":8888" in httpx_get_calls[0][0]
