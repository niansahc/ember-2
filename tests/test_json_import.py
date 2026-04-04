"""
Tests for generic JSON import endpoint.

Covers: valid array, valid single object, missing text field,
malformed JSON, type defaults, optional fields, file upload path.
"""

from unittest.mock import patch, MagicMock

import pytest

from src.api.routes.ingest import _import_json_records


# ── Valid imports ───────────────────────────────────────────────────────


def test_valid_json_array_import():
    """Valid JSON array with text fields imports correctly."""
    records = [
        {"text": "First record with enough content to pass filters easily here"},
        {"text": "Second record also with enough content to pass the length filter"},
    ]

    with patch("src.api.routes.ingest.write_memory") as mock_write:
        mock_write.return_value = "/fake/path.json"
        result = _import_json_records(records)

    assert result["imported"] == 2
    assert result["errors"] == []
    assert mock_write.call_count == 2


def test_valid_single_object_import():
    """Single JSON object (not array) should be accepted."""
    record = {"text": "A single record with enough content to pass filters easily here"}

    with patch("src.api.routes.ingest.write_memory") as mock_write:
        mock_write.return_value = "/fake/path.json"
        result = _import_json_records(record)

    assert result["imported"] == 1
    assert result["errors"] == []


# ── Validation errors ──────────────────────────────────────────────────


def test_missing_text_field_returns_error():
    """Records without text field produce validation errors."""
    records = [
        {"source": "test", "tags": ["no-text"]},
        {"text": "Valid record with enough content to pass the write memory filters"},
    ]

    with patch("src.api.routes.ingest.write_memory") as mock_write:
        mock_write.return_value = "/fake/path.json"
        result = _import_json_records(records)

    assert result["imported"] == 1
    assert len(result["errors"]) == 1
    assert result["errors"][0]["index"] == 0
    assert "text" in result["errors"][0]["error"].lower()


def test_empty_text_field_returns_error():
    """Empty or whitespace-only text field should error."""
    records = [{"text": "   "}]

    with patch("src.api.routes.ingest.write_memory") as mock_write:
        result = _import_json_records(records)

    assert result["imported"] == 0
    assert len(result["errors"]) == 1


def test_malformed_record_returns_error():
    """Non-dict items in array produce errors."""
    records = [
        "just a string",
        {"text": "Valid record with enough content to pass the write memory filters"},
    ]

    with patch("src.api.routes.ingest.write_memory") as mock_write:
        mock_write.return_value = "/fake/path.json"
        result = _import_json_records(records)

    assert result["imported"] == 1
    assert len(result["errors"]) == 1
    assert "object" in result["errors"][0]["error"].lower()


def test_malformed_json_raises_422():
    """Non-list, non-dict input raises HTTPException."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        _import_json_records("not json at all")

    assert exc_info.value.status_code == 422


# ── Defaults and optional fields ────────────────────────────────────────


def test_type_defaults_to_ingested():
    """When type is not specified, defaults to 'ingested'."""
    records = [{"text": "Record without type specified but with enough content for filters"}]

    with patch("src.api.routes.ingest.write_memory") as mock_write:
        mock_write.return_value = "/fake/path.json"
        _import_json_records(records)

    call_kwargs = mock_write.call_args[1]
    assert call_kwargs["memory_type"] == "ingested"


def test_custom_type_passed_through():
    """When type is specified, it's passed to write_memory."""
    records = [{"text": "A journal entry with enough content for the filters", "type": "journal"}]

    with patch("src.api.routes.ingest.write_memory") as mock_write:
        mock_write.return_value = "/fake/path.json"
        _import_json_records(records)

    call_kwargs = mock_write.call_args[1]
    assert call_kwargs["memory_type"] == "journal"


def test_tags_and_source_optional():
    """Tags and source are optional and have sensible defaults."""
    records = [{"text": "Minimal record with only text field that passes the filters easily"}]

    with patch("src.api.routes.ingest.write_memory") as mock_write:
        mock_write.return_value = "/fake/path.json"
        _import_json_records(records)

    call_kwargs = mock_write.call_args[1]
    assert call_kwargs["source"] == "json_import"
    assert call_kwargs["tags"] == []


def test_tags_and_source_passed_through():
    """When provided, tags and source are passed to write_memory."""
    records = [{
        "text": "Record with all optional fields set for testing purposes here",
        "source": "my_export",
        "tags": ["imported", "test"],
    }]

    with patch("src.api.routes.ingest.write_memory") as mock_write:
        mock_write.return_value = "/fake/path.json"
        _import_json_records(records)

    call_kwargs = mock_write.call_args[1]
    assert call_kwargs["source"] == "my_export"
    assert call_kwargs["tags"] == ["imported", "test"]


def test_content_kind_passed_to_metadata():
    """content_kind should be passed through to metadata."""
    records = [{
        "text": "An experience record with content kind specified for testing",
        "content_kind": "experience",
    }]

    with patch("src.api.routes.ingest.write_memory") as mock_write:
        mock_write.return_value = "/fake/path.json"
        _import_json_records(records)

    call_kwargs = mock_write.call_args[1]
    assert call_kwargs["metadata"]["content_kind"] == "experience"


# ── File upload path ────────────────────────────────────────────────────


def test_json_extension_in_document_extensions():
    """Verify .json is registered as a supported upload extension."""
    from src.api.routes.ingest import DOCUMENT_EXTENSIONS
    assert ".json" in DOCUMENT_EXTENSIONS
    assert DOCUMENT_EXTENSIONS[".json"] == "json"
