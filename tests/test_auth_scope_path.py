"""
tests/test_auth_scope_path.py

Security middleware reads the path from request.scope, never request.url.
request.url is rebuilt from the Host header, so a crafted Host could make
the URL path look like a public route (CVE-2026-48710, Starlette < 1.3.1).
"""
from __future__ import annotations

import inspect
from unittest.mock import patch

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
    """Two layers: the host allowlist answers 421 first; with that layer
    opened the key gate reads the scope path and answers 401."""
    import src.api.main as main_module

    response = client.get("/v1/models", headers=BAD_HOST)
    assert response.status_code == 421

    with patch.object(main_module, "_allowed_hosts", return_value=frozenset({BAD_HOST["Host"]})):
        response = client.get("/v1/models", headers=BAD_HOST)
    assert response.status_code == 401


def test_valid_key_and_host_reach_the_route(client: TestClient):
    """Positive control for the gate the bad-host test exercises."""
    response = client.get("/v1/models", headers={"X-API-Key": KEY})
    assert response.status_code != 401


def test_starlette_floor_installed():
    import starlette

    major, minor, patch, *_ = (int(x) for x in starlette.__version__.split(".")[:3])
    assert (major, minor, patch) >= (1, 3, 1)
