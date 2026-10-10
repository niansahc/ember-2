"""
tests/test_ui_catch_all.py

The SPA catch-all (`GET /{path:path}`) must never serve a file from outside
the UI build directory. Before this fix, `_UI_DIR / path` was joined and
served whenever `is_file()` was true, so a decoded `..` segment or an
absolute path read arbitrary files off disk (ultrareview finding on #281).

The catch-all is registered unconditionally, so these tests run in CI where
ui/ does not exist. _UI_DIR is patched to a fixture tree.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    UI_FIXTURE_FAVICON as FAVICON_BYTES,
    UI_FIXTURE_INDEX as INDEX_BODY,
    UI_FIXTURE_SECRET as SECRET_BYTES,
    ui_client,
)


@pytest.fixture
def client(ui_tree: Path):
    with ui_client(ui_tree) as c:
        yield c


def _traversal_paths(ui_tree: Path) -> list[str]:
    """Request paths that all name <tmp>/outside/secret.txt from the UI dir.
    The last is the absolute form: "/C:/.../secret.txt" on Windows,
    "//tmp/.../secret.txt" on POSIX."""
    secret = ui_tree.parent / "outside" / "secret.txt"
    return [
        "/%2e%2e/outside/secret.txt",
        "/..%2foutside/secret.txt",
        "/%2e%2e%2foutside/secret.txt",
        "/" + secret.as_posix(),
    ]


def test_sentinel_is_readable_from_disk(ui_tree: Path):
    """Positive control: the file the traversal targets really exists and is
    reachable by a plain relative join, which is what the old code did."""
    secret = ui_tree.parent / "outside" / "secret.txt"
    assert secret.read_bytes() == SECRET_BYTES
    assert (ui_tree / ".." / "outside" / "secret.txt").is_file()


def test_traversal_paths_return_index_not_sentinel(client: TestClient, ui_tree: Path):
    for request_path in _traversal_paths(ui_tree):
        response = client.get(request_path)
        assert response.status_code == 200, request_path
        assert SECRET_BYTES not in response.content, request_path
        assert response.text == INDEX_BODY, request_path


def test_root_level_ui_file_is_served_byte_for_byte(client: TestClient):
    """Positive control for the serving path: a real file inside the UI dir
    still comes back exactly, so the traversal tests are not passing because
    file serving is broken."""
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.content == FAVICON_BYTES


def test_unknown_spa_route_returns_index(client: TestClient):
    response = client.get("/some/deep/link")
    assert response.status_code == 200
    assert response.text == INDEX_BODY


def test_headless_install_returns_404(tmp_path: Path):
    """No index.html: the catch-all answers 404 like an unregistered route."""
    import src.api.main as main_module

    empty_ui = tmp_path / "no-ui"
    with patch.object(main_module, "_UI_DIR", empty_ui), \
         patch("src.api.main.get_ember_api_key", return_value=None):
        response = TestClient(main_module.app).get("/anything")
    assert response.status_code == 404


class TestResolveUiFile:
    """Unit layer under the route: decoded inputs, independent of HTTP parsing."""

    def test_file_inside_dir_resolves(self, ui_tree: Path):
        import src.api.main as main_module

        with patch.object(main_module, "_UI_DIR", ui_tree):
            assert main_module._resolve_ui_file("favicon.ico") == (ui_tree / "favicon.ico").resolve()
            assert main_module._resolve_ui_file("assets/app.js") == (ui_tree / "assets" / "app.js").resolve()

    def test_parent_segment_is_rejected(self, ui_tree: Path):
        import src.api.main as main_module

        with patch.object(main_module, "_UI_DIR", ui_tree):
            assert main_module._resolve_ui_file("../outside/secret.txt") is None
            assert main_module._resolve_ui_file("assets/../../outside/secret.txt") is None

    def test_absolute_path_is_rejected(self, ui_tree: Path):
        import src.api.main as main_module

        secret = ui_tree.parent / "outside" / "secret.txt"
        assert secret.is_file()
        with patch.object(main_module, "_UI_DIR", ui_tree):
            assert main_module._resolve_ui_file(str(secret)) is None
            assert main_module._resolve_ui_file(secret.as_posix()) is None

    def test_directory_and_missing_file_return_none(self, ui_tree: Path):
        import src.api.main as main_module

        with patch.object(main_module, "_UI_DIR", ui_tree):
            assert main_module._resolve_ui_file("assets") is None
            assert main_module._resolve_ui_file("") is None
            assert main_module._resolve_ui_file("nope.txt") is None

    def test_nul_byte_returns_none(self, ui_tree: Path):
        import src.api.main as main_module

        with patch.object(main_module, "_UI_DIR", ui_tree):
            assert main_module._resolve_ui_file("favicon.ico\x00") is None
