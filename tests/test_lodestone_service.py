"""
Tests for LodestoneService — lodestone living layer read/write (ADR-017).
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.memory import lodestone_service
from src.memory.lodestone_service import (
    MAX_ACTIVE_RECORDS,
    read_active,
    read_all,
    read_proposed,
    update,
    write,
)


@pytest.fixture
def vault_dir(tmp_path):
    """Set up a temporary vault with lodestone directory."""
    vault = tmp_path / "vault"
    vault.mkdir()
    lodestone_dir = vault / "memory" / "lodestone"
    lodestone_dir.mkdir(parents=True)
    with patch("src.memory.lodestone_service.get_private_vault_path", return_value=vault):
        yield vault


def _write_record(vault_dir, record_id, confirmed=True, value="test value",
                  taxonomy="character"):
    """Helper to write a lodestone record directly."""
    lodestone_dir = vault_dir / "memory" / "lodestone"
    record = {
        "id": record_id,
        "timestamp": record_id,
        "type": "lodestone",
        "value": value,
        "acquisition_path": "explicit",
        "source": "conversation",
        "supporting_evidence": "",
        "recurrence_count": 1,
        "confirmed": confirmed,
        "conflict_resolution": False,
        "metadata": {
            "user_note": None,
            "taxonomy_category": taxonomy,
            "flagged_as_noise": False,
        },
    }
    file_path = lodestone_dir / f"{record_id}.json"
    file_path.write_text(json.dumps(record), encoding="utf-8")
    return record


class TestWrite:
    def test_write_creates_record(self, vault_dir):
        rec = write("honesty matters", "character")
        assert rec["type"] == "lodestone"
        assert rec["value"] == "honesty matters"
        assert rec["confirmed"] is True
        assert rec["metadata"]["taxonomy_category"] == "character"

    def test_write_proposed(self, vault_dir):
        rec = write("curiosity", "ground", confirmed=False, source="reflection_synthesis")
        assert rec["confirmed"] is False
        assert rec["source"] == "reflection_synthesis"

    def test_write_file_exists(self, vault_dir):
        rec = write("test", "character")
        lodestone_dir = vault_dir / "memory" / "lodestone"
        files = list(lodestone_dir.glob("*.json"))
        assert len(files) == 1

    def test_write_cap_enforced(self, vault_dir):
        for i in range(MAX_ACTIVE_RECORDS):
            _write_record(vault_dir, f"rec-{i:03d}", confirmed=True)
        with pytest.raises(ValueError, match="cap reached"):
            write("one too many", "character", confirmed=True)

    def test_write_proposed_bypasses_cap(self, vault_dir):
        for i in range(MAX_ACTIVE_RECORDS):
            _write_record(vault_dir, f"rec-{i:03d}", confirmed=True)
        rec = write("proposed is fine", "character", confirmed=False)
        assert rec["confirmed"] is False


class TestRead:
    def test_read_active_returns_confirmed_only(self, vault_dir):
        _write_record(vault_dir, "confirmed-1", confirmed=True)
        _write_record(vault_dir, "proposed-1", confirmed=False)
        active = read_active()
        assert len(active) == 1
        assert active[0]["id"] == "confirmed-1"

    def test_read_proposed_returns_unconfirmed_only(self, vault_dir):
        _write_record(vault_dir, "confirmed-1", confirmed=True)
        _write_record(vault_dir, "proposed-1", confirmed=False)
        proposed = read_proposed()
        assert len(proposed) == 1
        assert proposed[0]["id"] == "proposed-1"

    def test_read_all_returns_both(self, vault_dir):
        _write_record(vault_dir, "confirmed-1", confirmed=True)
        _write_record(vault_dir, "proposed-1", confirmed=False)
        all_recs = read_all()
        assert len(all_recs) == 2

    def test_read_empty_vault(self, vault_dir):
        assert read_active() == []
        assert read_proposed() == []
        assert read_all() == []

    def test_skips_non_lodestone_records(self, vault_dir):
        lodestone_dir = vault_dir / "memory" / "lodestone"
        bad_record = {"id": "bad", "type": "conversation", "text": "wrong type"}
        (lodestone_dir / "bad.json").write_text(json.dumps(bad_record), encoding="utf-8")
        assert read_all() == []

    def test_skips_malformed_json(self, vault_dir):
        lodestone_dir = vault_dir / "memory" / "lodestone"
        (lodestone_dir / "broken.json").write_text("{invalid json", encoding="utf-8")
        assert read_all() == []


class TestUpdate:
    def test_confirm_proposed_record(self, vault_dir):
        _write_record(vault_dir, "proposed-1", confirmed=False)
        result = update("proposed-1", {"confirmed": True})
        assert result is not None
        assert result["confirmed"] is True
        # Verify persisted
        active = read_active()
        assert len(active) == 1

    def test_add_user_note(self, vault_dir):
        _write_record(vault_dir, "rec-1", confirmed=True)
        result = update("rec-1", {"user_note": "this one matters to me"})
        assert result["metadata"]["user_note"] == "this one matters to me"

    def test_flag_as_noise(self, vault_dir):
        _write_record(vault_dir, "rec-1", confirmed=False)
        result = update("rec-1", {"flagged_as_noise": True})
        assert result["metadata"]["flagged_as_noise"] is True

    def test_update_nonexistent_returns_none(self, vault_dir):
        result = update("does-not-exist", {"confirmed": True})
        assert result is None

    def test_confirm_respects_cap(self, vault_dir):
        for i in range(MAX_ACTIVE_RECORDS):
            _write_record(vault_dir, f"active-{i:03d}", confirmed=True)
        _write_record(vault_dir, "proposed-1", confirmed=False)
        with pytest.raises(ValueError, match="cap reached"):
            update("proposed-1", {"confirmed": True})
