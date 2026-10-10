"""
tests/test_api_auth_default_deny.py

The API key gate is default-deny (ultrareview #281). Before this change the
middleware only guarded a prefix list, so /provider-key*, /tiering/run,
/docs, /openapi.json and /redoc were reachable with no key, and the legacy
unauthenticated POST /chat wrote to the vault. Public paths are an explicit
allowlist plus GET/HEAD requests only the SPA catch-all answers.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

KEY = "test-api-key-0123456789"
WRONG_KEY = "wrong-key-0123456789"


@pytest.fixture
def client(tmp_path: Path):
    """App with a configured key and a fixture UI dir behind the catch-all."""
    import src.api.main as main_module

    ui_dir = tmp_path / "ui"
    (ui_dir / "assets").mkdir(parents=True)
    (ui_dir / "index.html").write_text("<html>fixture</html>", encoding="utf-8")
    (ui_dir / "manifest.json").write_text('{"name": "fixture"}', encoding="utf-8")
    (ui_dir / "assets" / "app.js").write_text("// fixture", encoding="utf-8")

    with patch.object(main_module, "_UI_DIR", ui_dir), \
         patch.object(main_module, "_cached_index_html", None), \
         patch.object(main_module, "_cached_index_mtime", 0.0), \
         patch("src.api.main.get_ember_api_key", return_value=KEY):
        yield TestClient(main_module.app)


# --- Denied without a key -------------------------------------------------

@pytest.mark.parametrize(
    "method, path",
    [
        ("POST", "/provider-key"),
        ("GET", "/provider-key/anthropic"),
        ("DELETE", "/provider-key/anthropic"),
        ("POST", "/tiering/run"),
        ("GET", "/openapi.json"),
        ("GET", "/docs"),
        ("GET", "/redoc"),
        ("GET", "/v1/models"),
        ("POST", "/ingest/upload"),
        ("POST", "/v1/chat/completions"),
    ],
)
def test_requires_key(client: TestClient, method: str, path: str):
    response = client.request(method, path)
    assert response.status_code == 401, (method, path, response.status_code)
    assert response.json() == {"detail": "Invalid or missing API key"}


def test_get_on_post_only_route_is_not_treated_as_ui(client: TestClient):
    """A path that matches a route with the wrong method is still an API
    request: 401, not index.html."""
    response = client.get("/tiering/run")
    assert response.status_code == 401


def test_unknown_post_is_denied(client: TestClient):
    """Only GET/HEAD fall through to the UI; a POST to nowhere is denied."""
    response = client.post("/no/such/route")
    assert response.status_code == 401


def test_legacy_chat_route_is_gone(client: TestClient):
    """POST /chat used to answer 200 with no key and write a conversation
    record. The router no longer exists: with a valid key the path falls to
    the GET-only SPA catch-all (405), or 404 where that is not registered.
    Either way nothing is written."""
    from src.core.config import get_private_vault_path

    conversation_dir = get_private_vault_path() / "memory" / "conversation"
    before = sorted(conversation_dir.rglob("*")) if conversation_dir.exists() else []

    response = client.post("/chat", json={"message": "hello"}, headers={"X-API-Key": KEY})

    assert response.status_code in (404, 405), response.status_code
    after = sorted(conversation_dir.rglob("*")) if conversation_dir.exists() else []
    assert after == before


def test_legacy_chat_module_removed():
    assert not (Path(__file__).resolve().parents[1] / "src" / "api" / "chat.py").exists()


# --- Public without a key -------------------------------------------------

def test_root_is_public(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert "fixture" in response.text


def test_health_is_public(client: TestClient):
    response = client.get("/api/health")
    assert response.status_code == 200


def test_assets_prefix_is_public(client: TestClient):
    # The /assets mount is bound at import to the real ui/ tree (absent in
    # CI), so the status depends on the environment; the gate is what is
    # under test and it must not answer 401.
    response = client.get("/assets/app.js")
    assert response.status_code != 401


def test_root_level_ui_file_is_public(client: TestClient):
    response = client.get("/manifest.json")
    assert response.status_code == 200
    assert response.json() == {"name": "fixture"}


def test_spa_deep_link_is_public(client: TestClient):
    response = client.get("/projects/some-id")
    assert response.status_code == 200
    assert "fixture" in response.text


def test_pin_status_is_public(client: TestClient):
    response = client.get("/v1/security/pin/status")
    assert response.status_code != 401


def test_pin_verify_reaches_handler(client: TestClient):
    response = client.post("/v1/security/pin/verify", json={})
    assert response.status_code != 401


# --- Positive controls: the key works --------------------------------------

def test_valid_key_via_x_api_key(client: TestClient):
    response = client.get("/v1/models", headers={"X-API-Key": KEY})
    assert response.status_code != 401


def test_valid_key_via_bearer(client: TestClient):
    response = client.get("/v1/models", headers={"Authorization": f"Bearer {KEY}"})
    assert response.status_code != 401


def test_wrong_key_is_denied(client: TestClient):
    response = client.get("/v1/models", headers={"X-API-Key": WRONG_KEY})
    assert response.status_code == 401


def test_public_paths_are_the_documented_set():
    """Adding a public path is a deliberate act; this pins the list."""
    from src.api.main import PUBLIC_PATHS, PUBLIC_PREFIXES

    assert PUBLIC_PATHS == frozenset({
        "/",
        "/api/health",
        "/v1/security/pin/verify",
        "/v1/security/pin/status",
    })
    assert PUBLIC_PREFIXES == ("/assets/",)
