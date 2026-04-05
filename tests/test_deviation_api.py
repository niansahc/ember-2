"""
Tests for deviation API endpoints (ADR-026).
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest


def _make_deviation_record(vault_dir, record_id, pattern_class="caretaking_language",
                           confirmed=False):
    """Write a deviation record directly to vault for testing."""
    dev_dir = vault_dir / "memory" / "deviation"
    dev_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "id": record_id,
        "timestamp": record_id,
        "type": "deviation",
        "text": f"[deviation:{pattern_class}] test response",
        "source": "deviation_detector",
        "tags": ["deviation", pattern_class],
        "metadata": {
            "friction_context": "test user message",
            "pattern_class": pattern_class,
            "deviation_chosen": "test response",
            "reason": None,
            "value_aligned": False,
            "outcome_signal": "neutral",
            "entropy_score": 0.3,
            "second_pass_result": "YES",
            "user_edited": False,
            "user_note": None,
            "flagged_as_noise": False,
            "confirmed": confirmed,
        },
    }
    file_path = dev_dir / f"{record_id}.json"
    file_path.write_text(json.dumps(record), encoding="utf-8")
    return record


@pytest.fixture
def vault_dir(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    dev_dir = vault / "memory" / "deviation"
    dev_dir.mkdir(parents=True)
    with patch("src.core.config.get_private_vault_path", return_value=vault):
        yield vault


class TestGetDeviations:
    def test_returns_all_records(self, vault_dir):
        from src.api.main import _read_deviation_records
        _make_deviation_record(vault_dir, "d1", confirmed=False)
        _make_deviation_record(vault_dir, "d2", confirmed=True)
        records = _read_deviation_records()
        assert len(records) == 2

    def test_filter_by_confirmed(self, vault_dir):
        from src.api.main import _read_deviation_records
        _make_deviation_record(vault_dir, "d1", confirmed=False)
        _make_deviation_record(vault_dir, "d2", confirmed=True)
        confirmed = _read_deviation_records(confirmed=True)
        assert len(confirmed) == 1
        assert confirmed[0]["metadata"]["confirmed"] is True

    def test_filter_by_pattern_class(self, vault_dir):
        from src.api.main import _read_deviation_records
        _make_deviation_record(vault_dir, "d1", pattern_class="caretaking_language")
        _make_deviation_record(vault_dir, "d2", pattern_class="closing_question")
        records = _read_deviation_records(pattern_class="closing_question")
        assert len(records) == 1
        assert records[0]["metadata"]["pattern_class"] == "closing_question"

    def test_limit_respected(self, vault_dir):
        from src.api.main import _read_deviation_records
        for i in range(10):
            _make_deviation_record(vault_dir, f"d{i:03d}")
        records = _read_deviation_records(limit=3)
        assert len(records) == 3

    def test_empty_vault_returns_empty(self, vault_dir):
        from src.api.main import _read_deviation_records
        records = _read_deviation_records()
        assert len(records) == 0

    def test_skips_non_deviation_records(self, vault_dir):
        from src.api.main import _read_deviation_records
        dev_dir = vault_dir / "memory" / "deviation"
        bad = {"id": "bad", "type": "conversation", "text": "wrong type"}
        (dev_dir / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
        records = _read_deviation_records()
        assert len(records) == 0


class TestUpdateDeviation:
    def test_confirm_record(self, vault_dir):
        from src.api.main import _update_deviation_record
        _make_deviation_record(vault_dir, "d1", confirmed=False)
        result = _update_deviation_record("d1", {"confirmed": True})
        assert result is not None
        assert result["metadata"]["confirmed"] is True
        assert result["metadata"]["user_edited"] is True

    def test_add_reason(self, vault_dir):
        from src.api.main import _update_deviation_record
        _make_deviation_record(vault_dir, "d1")
        result = _update_deviation_record("d1", {"reason": "accuracy matters more"})
        assert result["metadata"]["reason"] == "accuracy matters more"

    def test_flag_as_noise(self, vault_dir):
        from src.api.main import _update_deviation_record
        _make_deviation_record(vault_dir, "d1")
        result = _update_deviation_record("d1", {"flagged_as_noise": True})
        assert result["metadata"]["flagged_as_noise"] is True

    def test_set_value_aligned(self, vault_dir):
        from src.api.main import _update_deviation_record
        _make_deviation_record(vault_dir, "d1")
        result = _update_deviation_record("d1", {"value_aligned": True})
        assert result["metadata"]["value_aligned"] is True

    def test_add_user_note(self, vault_dir):
        from src.api.main import _update_deviation_record
        _make_deviation_record(vault_dir, "d1")
        result = _update_deviation_record("d1", {"user_note": "this was real"})
        assert result["metadata"]["user_note"] == "this was real"

    def test_nonexistent_returns_none(self, vault_dir):
        from src.api.main import _update_deviation_record
        result = _update_deviation_record("missing", {"confirmed": True})
        assert result is None

    def test_multiple_updates_at_once(self, vault_dir):
        from src.api.main import _update_deviation_record
        _make_deviation_record(vault_dir, "d1")
        result = _update_deviation_record("d1", {
            "confirmed": True,
            "reason": "directness over comfort",
            "value_aligned": True,
        })
        assert result["metadata"]["confirmed"] is True
        assert result["metadata"]["reason"] == "directness over comfort"
        assert result["metadata"]["value_aligned"] is True
