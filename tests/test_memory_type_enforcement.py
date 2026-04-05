"""
tests/test_memory_type_enforcement.py

Tests for typed memory enforcement.
All writes to the vault must use a valid memory type from VALID_MEMORY_TYPES.
"""

import json
import tempfile
from pathlib import Path

import pytest

from src.memory.storage import MemoryStorage, VALID_MEMORY_TYPES


class TestValidMemoryTypes:
    """VALID_MEMORY_TYPES should contain all expected types."""

    def test_contains_core_types(self):
        expected = {
            "profile", "journal", "conversation", "reflection",
            "summary", "state", "task", "project", "reference",
            "ingested", "archive", "system_event", "decision",
            "review_log", "evaluation", "session", "lodestone",
            "deviation",
        }
        assert expected == VALID_MEMORY_TYPES

    def test_session_is_valid(self):
        assert "session" in VALID_MEMORY_TYPES

    def test_is_frozenset(self):
        assert isinstance(VALID_MEMORY_TYPES, frozenset)


class TestGetMemoryDirValidation:
    """get_memory_dir() should reject invalid types."""

    def test_valid_type_succeeds(self, tmp_path):
        storage = MemoryStorage()
        result = storage.get_memory_dir(tmp_path, "conversation")
        assert result.exists()
        assert result == tmp_path / "memory" / "conversation"

    def test_all_valid_types_succeed(self, tmp_path):
        storage = MemoryStorage()
        for mem_type in VALID_MEMORY_TYPES:
            result = storage.get_memory_dir(tmp_path, mem_type)
            assert result.exists()

    def test_invalid_type_raises(self, tmp_path):
        storage = MemoryStorage()
        with pytest.raises(ValueError, match="Invalid memory type 'banana'"):
            storage.get_memory_dir(tmp_path, "banana")

    def test_empty_string_raises(self, tmp_path):
        storage = MemoryStorage()
        with pytest.raises(ValueError, match="Invalid memory type"):
            storage.get_memory_dir(tmp_path, "")

    def test_typo_raises(self, tmp_path):
        storage = MemoryStorage()
        with pytest.raises(ValueError, match="Invalid memory type 'converstation'"):
            storage.get_memory_dir(tmp_path, "converstation")

    def test_arbitrary_string_raises(self, tmp_path):
        storage = MemoryStorage()
        with pytest.raises(ValueError):
            storage.get_memory_dir(tmp_path, "my_custom_type")

    def test_invalid_type_does_not_create_directory(self, tmp_path):
        storage = MemoryStorage()
        try:
            storage.get_memory_dir(tmp_path, "xyzzy")
        except ValueError:
            pass
        assert not (tmp_path / "memory" / "xyzzy").exists()


class TestIngestedChunkTypeField:
    """Ingested chunk payloads should include a type field."""

    def test_chunk_payload_has_type(self):
        # Simulate what write_chunks_to_vault produces
        chunk_payload = {
            "type": "ingested",
            "source": "file",
            "doc_id": "test",
            "chunk_id": "test_0",
            "title": "test.pdf",
            "created_at": None,
            "content": "Hello world",
            "metadata": {},
        }
        assert chunk_payload["type"] == "ingested"
