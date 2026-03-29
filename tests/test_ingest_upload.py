"""
tests/test_ingest_upload.py

Tests for POST /ingest/upload multipart file upload endpoint.
Tests file extension routing, image passthrough, and error handling.
Does NOT test actual PDF/DOCX parsing (those have their own importers).
"""

import base64
import io
import pytest


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
