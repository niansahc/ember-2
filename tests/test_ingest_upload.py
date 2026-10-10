"""
tests/test_ingest_upload.py

Tests for POST /ingest/upload multipart file upload endpoint.
Tests file extension routing, image passthrough, and error handling.
Does NOT test actual PDF/DOCX parsing (those have their own importers).
"""

import base64
import io
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.conftest import _make_vault_tree


class TestUploadRouting:
    """Test that file extensions are correctly categorized."""

    def test_image_extensions_recognized(self):
        from src.api.routes.ingest import IMAGE_EXTENSIONS
        for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
            assert ext in IMAGE_EXTENSIONS, f"{ext} should be an image extension"

    def test_document_extensions_recognized(self):
        from src.api.routes.ingest import DOCUMENT_EXTENSIONS
        for ext in [".pdf", ".docx", ".csv", ".xlsx"]:
            assert ext in DOCUMENT_EXTENSIONS, f"{ext} should be a document extension"

    def test_document_type_mapping(self):
        from src.api.routes.ingest import DOCUMENT_EXTENSIONS
        assert DOCUMENT_EXTENSIONS[".pdf"] == "pdf"
        assert DOCUMENT_EXTENSIONS[".docx"] == "docx"
        assert DOCUMENT_EXTENSIONS[".csv"] == "csv"
        assert DOCUMENT_EXTENSIONS[".xlsx"] == "csv"

    def test_mime_map_coverage(self):
        from src.api.routes.ingest import MIME_MAP, IMAGE_EXTENSIONS
        for ext in IMAGE_EXTENSIONS:
            assert ext in MIME_MAP, f"{ext} should have a MIME type mapping"

    def test_unsupported_extension_not_in_maps(self):
        from src.api.routes.ingest import DOCUMENT_EXTENSIONS, IMAGE_EXTENSIONS
        assert ".txt" in DOCUMENT_EXTENSIONS  # txt is now supported
        assert ".exe" not in DOCUMENT_EXTENSIONS
        assert ".exe" not in IMAGE_EXTENSIONS


class TestImagePassthrough:
    """Test that image data is correctly base64 encoded."""

    def test_base64_roundtrip(self):
        """Verify base64 encode/decode preserves image bytes."""
        original = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # fake PNG header
        encoded = base64.b64encode(original).decode("ascii")
        decoded = base64.b64decode(encoded)
        assert decoded == original

    def test_mime_type_for_jpeg(self):
        from src.api.routes.ingest import MIME_MAP
        assert MIME_MAP[".jpg"] == "image/jpeg"
        assert MIME_MAP[".jpeg"] == "image/jpeg"

    def test_mime_type_for_png(self):
        from src.api.routes.ingest import MIME_MAP
        assert MIME_MAP[".png"] == "image/png"

    def test_mime_type_for_gif(self):
        from src.api.routes.ingest import MIME_MAP
        assert MIME_MAP[".gif"] == "image/gif"

    def test_mime_type_for_webp(self):
        from src.api.routes.ingest import MIME_MAP
        assert MIME_MAP[".webp"] == "image/webp"


class TestSessionImportFix:
    """Verify session.py uses get_private_vault_path() not a constant."""

    def test_session_uses_function_not_constant(self):
        import inspect
        from src.memory import session
        source = inspect.getsource(session._session_dir)
        assert "get_private_vault_path()" in source
        assert "PRIVATE_VAULT_PATH" not in source

    def test_conversation_dir_uses_function(self):
        import inspect
        from src.memory import session
        source = inspect.getsource(session._conversation_dir)
        assert "get_private_vault_path()" in source


# ---------------------------------------------------------------------------
# Filename sanitization (ultrareview #281)
#
# The upload filename was joined onto vault/imports/uploads/ unsanitized, so
# a crafted multipart filename could write outside the uploads directory.
# A filename is now one safe path segment or the request is rejected before
# any disk write.
# ---------------------------------------------------------------------------

BACKSLASH = chr(92)
NUL = chr(0)


def _tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


@pytest.fixture
def upload_vault(tmp_path: Path):
    """A throwaway vault for the upload route, with the imports tree present."""
    vault = tmp_path / "vault"
    _make_vault_tree(vault)
    return vault


@pytest.fixture
def upload_client(upload_vault: Path):
    with patch("src.api.routes.ingest.get_private_vault_path", return_value=upload_vault), \
         patch("src.api.main.get_ember_api_key", return_value=None):
        from src.api.main import app
        yield TestClient(app)


class TestSafeUploadName:
    def test_plain_basename_passes(self):
        from src.api.routes.ingest import _safe_upload_name
        assert _safe_upload_name("notes.txt") == "notes.txt"
        assert _safe_upload_name("  report.pdf ") == "report.pdf"
        assert _safe_upload_name("my file (1).docx") == "my file (1).docx"

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            "",
            "   ",
            ".",
            "..",
            "../../escaped.pdf",
            "/outside/escaped.pdf",
            BACKSLASH.join(["..", "..", "escaped.pdf"]),
            "C:" + BACKSLASH + "outside" + BACKSLASH + "escaped.pdf",
            "sub/escaped.pdf",
            "C:notes.pdf",
            "escaped" + NUL + ".pdf",
        ],
    )
    def test_unsafe_names_raise_400(self, raw):
        from fastapi import HTTPException
        from src.api.routes.ingest import _safe_upload_name
        with pytest.raises(HTTPException) as exc_info:
            _safe_upload_name(raw)
        assert exc_info.value.status_code == 400

    def test_upload_target_rejects_escape(self, tmp_path: Path):
        """Second layer: even a name that slipped past the first check must
        resolve directly inside the uploads directory."""
        from fastapi import HTTPException
        from src.api.routes.ingest import _upload_target
        uploads = tmp_path / "uploads"
        uploads.mkdir()
        assert _upload_target(uploads, "ok.txt") == uploads / "ok.txt"
        with pytest.raises(HTTPException) as exc_info:
            _upload_target(uploads, "../escaped.txt")
        assert exc_info.value.status_code == 400


class TestUploadRouteTraversal:
    # A drive-letter form (C:\outside\escaped.pdf) is not listed here: the
    # multipart parser already reduces it to its basename before the route
    # runs, so the route sees a plain name. The raw form is covered by the
    # _safe_upload_name unit test above.
    @pytest.mark.parametrize(
        "filename",
        [
            "../../escaped.pdf",
            BACKSLASH.join(["..", "..", "escaped.pdf"]),
            "/outside/escaped.pdf",
            ".",
            "..",
            "../../escaped.png",
        ],
    )
    def test_traversal_filename_is_rejected_and_writes_nothing(
        self, upload_client: TestClient, upload_vault: Path, filename: str
    ):
        before = _tree(upload_vault.parent)
        response = upload_client.post(
            "/ingest/upload",
            files={"file": (filename, b"%PDF-1.4 fixture bytes", "application/octet-stream")},
        )
        assert 400 <= response.status_code < 500, (filename, response.status_code, response.text)
        uploads_dir = upload_vault / "imports" / "uploads"
        assert not uploads_dir.exists() or _tree(uploads_dir) == []
        assert _tree(upload_vault.parent) == before, "a file appeared on disk"

    def test_empty_filename_is_rejected(self, upload_client: TestClient, upload_vault: Path):
        before = _tree(upload_vault.parent)
        response = upload_client.post(
            "/ingest/upload",
            files={"file": ("", b"fixture", "text/plain")},
        )
        assert 400 <= response.status_code < 500, response.status_code
        assert _tree(upload_vault.parent) == before

    def test_plain_document_is_saved_under_uploads(self, upload_client: TestClient, upload_vault: Path):
        """Positive control: a normal basename is written to imports/uploads/
        and handed to the ingestion pipeline, so the rejections above are not
        passing because the route is broken."""
        from src.ingest.importers.files import load_text_file

        docs_seen = {}

        def fake_pipeline(docs):
            docs_seen["docs"] = docs
            return ["chunk-a", "chunk-b"]

        with patch("src.api.routes.ingest.run_ingestion_pipeline", side_effect=fake_pipeline), \
             patch("src.api.routes.ingest.write_chunks_to_vault") as write_chunks:
            response = upload_client.post(
                "/ingest/upload",
                files={"file": ("notes.txt", b"fixture note text", "text/plain")},
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "ingested"
        assert body["filename"] == "notes.txt"
        assert body["chunks"] == 2
        saved = upload_vault / "imports" / "uploads" / "notes.txt"
        assert saved.read_bytes() == b"fixture note text"
        assert docs_seen["docs"] == load_text_file(str(saved))
        write_chunks.assert_called_once()

    def test_image_passthrough_echoes_sanitized_name(self, upload_client: TestClient, upload_vault: Path):
        response = upload_client.post(
            "/ingest/upload",
            files={"file": (" photo.png ", b"\x89PNG fixture", "image/png")},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "image"
        assert response.json()["filename"] == "photo.png"
        assert not (upload_vault / "imports" / "uploads").exists()
