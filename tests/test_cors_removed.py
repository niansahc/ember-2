"""
tests/test_cors_removed.py

The API carries no CORS headers. The UI is same-origin in every supported
deployment (Vite proxy in dev, FastAPI-served ui/ in prod, same-origin
reverse proxy remote), and the previous wildcard allow-origin with
credentials let any site drive the API from a browser (ultrareview #281).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import ui_client

CORS_HEADERS = (
    "access-control-allow-origin",
    "access-control-allow-credentials",
    "access-control-allow-methods",
    "access-control-allow-headers",
)
FOREIGN_ORIGIN = {"Origin": "https://evil.example"}


@pytest.fixture
def client(ui_tree: Path):
    with ui_client(ui_tree) as c:
        yield c


def _assert_no_cors(response):
    for header in CORS_HEADERS:
        assert header not in response.headers, f"{header} present: {response.headers[header]}"


def test_cors_middleware_not_installed():
    from fastapi.middleware.cors import CORSMiddleware

    from src.api.main import app

    assert all(m.cls is not CORSMiddleware for m in app.user_middleware)


def test_root_with_foreign_origin_has_no_cors_headers(client: TestClient):
    response = client.get("/", headers=FOREIGN_ORIGIN)
    assert response.status_code == 200
    _assert_no_cors(response)
    # Positive control: the header check is reading real headers.
    assert "Content-Security-Policy" in response.headers


def test_traversal_attempt_with_foreign_origin_has_no_cors_headers(client: TestClient):
    response = client.get("/%2e%2e/outside/secret.txt", headers=FOREIGN_ORIGIN)
    assert response.status_code == 200
    assert b"SENTINEL" not in response.content
    _assert_no_cors(response)
    assert "Content-Security-Policy" in response.headers


def test_preflight_has_no_cors_headers(client: TestClient):
    response = client.options(
        "/v1/models",
        headers={
            **FOREIGN_ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-api-key",
        },
    )
    _assert_no_cors(response)
    assert "Content-Security-Policy" in response.headers
