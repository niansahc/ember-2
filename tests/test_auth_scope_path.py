"""
tests/test_auth_scope_path.py

Security middleware reads the path from request.scope, never request.url.
request.url is rebuilt from the Host header, so a crafted Host could make
the URL path look like a public route (CVE-2026-48710, Starlette < 1.3.1).
"""
from __future__ import annotations

import inspect

import pytest
from fastapi.testclient import TestClient

from tests.conftest import ui_client

KEY = "test-api-key-0123456789"
BAD_HOST = {"Host": "127.0.0.1:8000/api/health?x="}


@pytest.fixture
def client(ui_tree):
    with ui_client(ui_tree, api_key=KEY, base_url="http://127.0.0.1:8000") as c:
        yield c


def test_security_middleware_reads_scope_path_not_url_path():
    import src.api.main as main_module

    for func in (main_module.api_key_auth, main_module.audit_log):
        source = inspect.getsource(func)
        assert "request.url" not in source, func.__name__
    assert 'request.scope["path"]' in inspect.getsource(main_module._scope_path)


def test_bad_host_cannot_reach_keyed_route_without_key(client: TestClient):
    response = client.get("/v1/models", headers=BAD_HOST)
    assert response.status_code != 200
    assert "data" not in response.text


def test_bad_host_with_valid_key_still_hits_the_real_path(client: TestClient):
    """Positive control: the request line path is what gets served."""
    response = client.get("/api/health", headers={**BAD_HOST, "X-API-Key": KEY})
    assert response.status_code in (200, 421)
    response = client.get("/v1/models", headers={"X-API-Key": KEY})
    assert response.status_code != 401


def test_starlette_floor_installed():
    import starlette

    major, minor, patch, *_ = (int(x) for x in starlette.__version__.split(".")[:3])
    assert (major, minor, patch) >= (1, 3, 1)
