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

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

INDEX_BODY = "<html><body>fixture-index</body></html>"
FAVICON_BYTES = b"\x00\x00\x01\x00fixture-favicon"
SECRET_BYTES = b"SENTINEL-OUTSIDE-UI-DIR"


@pytest.fixture
def ui_tree(tmp_path: Path):
    """A fixture UI dir with a sibling directory that must stay unreachable."""
    ui_dir = tmp_path / "ui"
    (ui_dir / "assets").mkdir(parents=True)
    (ui_dir / "index.html").write_text(INDEX_BODY, encoding="utf-8")
    (ui_dir / "favicon.ico").write_bytes(FAVICON_BYTES)
    (ui_dir / "assets" / "app.js").write_text("console.log('fixture');", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(SECRET_BYTES)
    return ui_dir


@pytest.fixture
def client(ui_tree: Path):
    import src.api.main as main_module

    with patch.object(main_module, "_UI_DIR", ui_tree), \
         patch.object(main_module, "_cached_index_html", None), \
         patch.object(main_module, "_cached_index_mtime", 0.0), \
         patch("src.api.main.get_ember_api_key", return_value=None):
        yield TestClient(main_module.app)


def _traversal_paths(ui_tree: Path) -> list[str]:
    """Request paths that all name <tmp>/outside/secret.txt from the UI dir."""
    secret = ui_tree.parent / "outside" / "secret.txt"
    if os.name == "nt":
        absolute = "/" + secret.as_posix()  # "/C:/.../outside/secret.txt"
    else:
        absolute = "/" + secret.as_posix()  # "//tmp/.../outside/secret.txt"
    return [
        "/%2e%2e/outside/secret.txt",
        "/..%2foutside/secret.txt",
        "/%2e%2e%2foutside/secret.txt",
        absolute,
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
