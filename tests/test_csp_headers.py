"""Tests for Content Security Policy headers on API responses.

Every response must carry a CSP header that constrains the UI to self-hosted
assets. The middleware is applied at the app level so every route inherits it.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    """API client with no API key gate so any route returns a real response."""
    with patch("src.api.main.get_ember_api_key", return_value=None), \
         patch("src.core.config.get_private_vault_path", return_value=tmp_path), \
         patch("src.core.preferences.get_private_vault_path", return_value=tmp_path):
        from src.api.main import app
        yield TestClient(app)


def test_csp_header_present_on_root(client):
    response = client.get("/")
    assert "Content-Security-Policy" in response.headers


def test_csp_header_contains_required_directives(client):
    response = client.get("/")
    csp = response.headers["Content-Security-Policy"]

    # Each directive listed in the security policy should be present.
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "style-src 'self' 'unsafe-inline'" in csp
    assert "img-src 'self' data: blob:" in csp
    assert "font-src 'self'" in csp
    assert "connect-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp


def test_csp_header_present_on_api_route(client):
    """CSP must also fire on JSON API routes, not just the static root."""
    response = client.get("/v1/preferences")
    assert "Content-Security-Policy" in response.headers


def test_csp_header_present_on_validation_error(client):
    """CSP must apply to error responses too — validation failures from the
    framework still flow through middleware on the way out."""
    # PATCH /v1/preferences with a non-dict body triggers 422 validation error.
    response = client.patch("/v1/preferences", json="not-a-dict")
    assert response.status_code in (400, 422)
    assert "Content-Security-Policy" in response.headers
