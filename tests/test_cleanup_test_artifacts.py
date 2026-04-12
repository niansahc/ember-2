"""
tests/test_cleanup_test_artifacts.py

Tests for the test artifact cleanup script. Uses synthetic fixture
data only — no real vault content per Vault Privacy Rule.
"""

import json
from pathlib import Path

import pytest

import sys
REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cleanup_test_artifacts import (
    _is_april_2026,
    _matches_eval_source,
    _matches_eval_content,
    scan_vault,
    archive_records,
    EVAL_QUESTIONS,
)


def _write_record(vault: Path, subdir: str, filename: str, record: dict) -> Path:
    """Helper: write a synthetic record to the vault."""
    target_dir = vault / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / filename
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return path


class TestApril2026Filter:

    def test_april_2026_matches(self):
        assert _is_april_2026("2026-04-11T14-00-00") is True
        assert _is_april_2026("2026-04-01T00-00-00") is True

    def test_march_2026_does_not_match(self):
        assert _is_april_2026("2026-03-31T23-59-59") is False

    def test_may_2026_does_not_match(self):
        assert _is_april_2026("2026-05-01T00-00-00") is False

    def test_empty_timestamp(self):
        assert _is_april_2026("") is False
        assert _is_april_2026(None) is False


class TestSourceMatching:

    def test_source_contains_test(self):
        assert _matches_eval_source({"source": "test_eval"}) is True

    def test_source_contains_eval(self):
        assert _matches_eval_source({"source": "eval_harness"}) is True

    def test_tags_contain_test(self):
        assert _matches_eval_source({"source": "chat", "tags": ["test"]}) is True

    def test_metadata_test_flag(self):
        assert _matches_eval_source({"source": "chat", "metadata": {"test": True}}) is True

    def test_normal_source_no_match(self):
        assert _matches_eval_source({"source": "chat", "tags": ["conversation"]}) is False


class TestContentMatching:

    def test_eval_question_in_text(self):
        assert _matches_eval_content({"text": "What do you know about me?"}) is True

    def test_partial_match(self):
        assert _matches_eval_content({"text": "User asked: how are you today"}) is True

    def test_no_match(self):
        assert _matches_eval_content({"text": "The quick brown fox jumps over the lazy dog"}) is False

    def test_empty_text(self):
        assert _matches_eval_content({"text": ""}) is False
        assert _matches_eval_content({}) is False

    def test_eval_questions_list_is_populated(self):
        assert len(EVAL_QUESTIONS) >= 15


class TestScanVault:

    def test_finds_matching_conversation_record(self, tmp_path):
        vault = tmp_path / "vault"
        _write_record(vault, "memory/conversation", "2026-04-11T14-00-00.json", {
            "id": "2026-04-11T14-00-00",
            "timestamp": "2026-04-11T14-00-00",
            "type": "conversation",
            "text": "What do you know about me?",
            "source": "chat",
            "tags": ["test"],
        })
        matches = scan_vault(vault)
        assert len(matches) == 1

    def test_skips_non_april_records(self, tmp_path):
        vault = tmp_path / "vault"
        _write_record(vault, "memory/conversation", "2026-03-15T10-00-00.json", {
            "id": "2026-03-15T10-00-00",
            "timestamp": "2026-03-15T10-00-00",
            "type": "conversation",
            "text": "What do you know about me?",
            "source": "test",
            "tags": ["test"],
        })
        matches = scan_vault(vault)
        assert len(matches) == 0

    def test_skips_non_matching_records(self, tmp_path):
        vault = tmp_path / "vault"
        _write_record(vault, "memory/conversation", "2026-04-11T14-00-00.json", {
            "id": "2026-04-11T14-00-00",
            "timestamp": "2026-04-11T14-00-00",
            "type": "conversation",
            "text": "A completely normal conversation about gardening.",
            "source": "chat",
            "tags": ["conversation"],
        })
        matches = scan_vault(vault)
        assert len(matches) == 0

    def test_finds_records_across_subdirs(self, tmp_path):
        vault = tmp_path / "vault"
        _write_record(vault, "memory/conversation", "2026-04-11T14-00-00.json", {
            "id": "conv-1",
            "timestamp": "2026-04-11T14-00-00",
            "type": "conversation",
            "text": "How are you?",
            "source": "eval",
            "tags": [],
        })
        _write_record(vault, "memory/state", "2026-04-11T14-00-01_open_loop.json", {
            "id": "state-1",
            "timestamp": "2026-04-11T14-00-01",
            "type": "state",
            "text": "Test extraction",
            "source": "test",
            "tags": ["test"],
        })
        matches = scan_vault(vault)
        assert len(matches) == 2


class TestArchiveRecords:

    def test_moves_files_to_archive(self, tmp_path):
        vault = tmp_path / "vault"
        path = _write_record(vault, "memory/conversation", "2026-04-11T14-00-00.json", {
            "id": "2026-04-11T14-00-00",
            "timestamp": "2026-04-11T14-00-00",
            "type": "conversation",
            "text": "test record",
            "source": "test",
        })
        record = json.loads(path.read_text(encoding="utf-8"))
        matches = [(path, record)]

        moved = archive_records(vault, matches)
        assert moved == 1
        assert not path.exists()

        archive_dir = vault / "memory" / "archive"
        archived = list(archive_dir.glob("*.json"))
        assert len(archived) == 1

    def test_archive_does_not_overwrite(self, tmp_path):
        vault = tmp_path / "vault"
        archive_dir = vault / "memory" / "archive"
        archive_dir.mkdir(parents=True)

        # Create a record and a pre-existing archive file with the same name
        path = _write_record(vault, "memory/conversation", "test.json", {
            "id": "t1", "timestamp": "2026-04-11T14-00-00",
            "type": "conversation", "text": "test", "source": "test",
        })
        record = json.loads(path.read_text(encoding="utf-8"))
        # Pre-populate archive with same filename
        (archive_dir / "conversation__test.json").write_text("{}", encoding="utf-8")

        moved = archive_records(vault, [(path, record)])
        assert moved == 1

        archived = list(archive_dir.glob("*.json"))
        assert len(archived) == 2  # original + new with counter suffix
