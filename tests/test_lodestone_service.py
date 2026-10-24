"""
Tests for LodestoneService — lodestone living layer read/write (ADR-017).
"""

import json
import os
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


class TestUpdateIsAppendOnly:
    """Recalling Too Well Phase 1, item 6: update() must never mutate the
    original file in place (CLAUDE.md Rule 3), despite the docstring at
    the top of lodestone_service.py already claiming append-only."""

    def test_update_writes_a_new_file(self, vault_dir):
        lodestone_dir = vault_dir / "memory" / "lodestone"
        before = set(lodestone_dir.glob("*.json"))
        _write_record(vault_dir, "rec-1", confirmed=False)
        after_write = set(lodestone_dir.glob("*.json")) - before

        update("rec-1", {"confirmed": True})
        after_update = set(lodestone_dir.glob("*.json")) - before

        assert len(after_update) == 2, (
            "update() must add a new file, not rewrite the one write() created"
        )
        assert after_write.issubset(after_update)

    def test_original_file_is_never_mutated(self, vault_dir):
        import json

        lodestone_dir = vault_dir / "memory" / "lodestone"
        _write_record(vault_dir, "rec-1", confirmed=False, value="original value")
        original_path = lodestone_dir / "rec-1.json"
        original_bytes_before = original_path.read_bytes()

        update("rec-1", {"confirmed": True})

        assert original_path.read_bytes() == original_bytes_before
        original_record = json.loads(original_path.read_text(encoding="utf-8"))
        assert original_record["confirmed"] is False, (
            "the original physical file must still show the pre-update state"
        )

    def test_id_is_preserved_across_update(self, vault_dir):
        _write_record(vault_dir, "rec-1", confirmed=False)
        result = update("rec-1", {"confirmed": True})
        assert result["id"] == "rec-1"

    def test_read_active_reports_one_logical_record_after_update(self, vault_dir):
        """Two physical files now exist for this id (old + new version),
        but read_active() must resolve to exactly one logical record."""
        _write_record(vault_dir, "proposed-1", confirmed=False)
        update("proposed-1", {"confirmed": True})
        active = read_active()
        assert len(active) == 1
        assert active[0]["id"] == "proposed-1"
        assert active[0]["confirmed"] is True

    def test_sequential_updates_each_build_on_the_latest_version(self, vault_dir):
        """A second update must see the first update's changes, not the
        original file -- proves update() reads via the deduped view."""
        _write_record(vault_dir, "rec-1", confirmed=True)
        update("rec-1", {"user_note": "first note"})
        result = update("rec-1", {"flagged_as_noise": True})

        assert result["metadata"]["user_note"] == "first note", (
            "second update must preserve the first update's field, not the "
            "original record's"
        )
        assert result["metadata"]["flagged_as_noise"] is True

        # And the resolved view reflects only the latest version.
        all_records = read_all()
        matching = [r for r in all_records if r["id"] == "rec-1"]
        assert len(matching) == 1
        assert matching[0]["metadata"]["user_note"] == "first note"
        assert matching[0]["metadata"]["flagged_as_noise"] is True


def _set_mtime(path, stamp):
    """Pin a file's mtime so ordering cannot depend on filesystem timestamp
    granularity, vault copies or restores."""
    os.utime(path, (stamp, stamp))


class TestLatestVersionDoesNotDependOnMtime:
    """A tied or reset mtime (coarse timestamps, vault copy or restore)
    must not resurrect an older version."""

    def test_tied_mtimes_resolve_to_the_latest_version(self, vault_dir):
        lodestone_dir = vault_dir / "memory" / "lodestone"
        created = write("honesty", "character", confirmed=False)
        update(created["id"], {"user_note": "first note"})
        update(created["id"], {"confirmed": True})

        for path in lodestone_dir.glob("*.json"):
            _set_mtime(path, 1_700_000_000)

        current = [r for r in read_all() if r["id"] == created["id"]]
        assert len(current) == 1
        assert current[0]["confirmed"] is True
        assert current[0]["metadata"]["user_note"] == "first note"

    def test_inverted_mtimes_still_resolve_to_the_latest_version(self, vault_dir):
        lodestone_dir = vault_dir / "memory" / "lodestone"
        created = write("honesty", "character", confirmed=False)
        update(created["id"], {"user_note": "first note"})
        latest = update(created["id"], {"confirmed": True})

        # Newest version gets the oldest mtime, as after a copy that
        # touches files in an arbitrary order.
        newest_first = sorted(lodestone_dir.glob("*.json"), reverse=True)
        for i, path in enumerate(newest_first):
            _set_mtime(path, 1_700_000_000 + i)

        current = [r for r in read_all() if r["id"] == created["id"]]
        assert len(current) == 1
        assert current[0]["version"] == latest["version"]
        assert current[0]["confirmed"] is True

    def test_listing_is_newest_first_by_the_winning_file(self, vault_dir):
        """read_all() lists each id at its winning version's mtime, not at
        the mtime of an older version that happens to be newer on disk."""
        lodestone_dir = vault_dir / "memory" / "lodestone"
        a = write("honesty", "character")
        b = write("patience", "character")
        update(a["id"], {"user_note": "edited"})

        a0, b0, a1 = sorted(lodestone_dir.glob("*.json"))
        _set_mtime(a0, 1_700_000_300)
        _set_mtime(b0, 1_700_000_200)
        _set_mtime(a1, 1_700_000_100)

        listed = read_all()
        assert [r["id"] for r in listed] == [b["id"], a["id"]]
        assert listed[1]["version"] == 1

    def test_versions_increase_by_one_per_update(self, vault_dir):
        created = write("honesty", "character")
        first = update(created["id"], {"user_note": "a"})
        second = update(created["id"], {"user_note": "b"})
        assert (created["version"], first["version"], second["version"]) == (0, 1, 2)

    def test_control_unversioned_legacy_files_still_order_by_mtime(self, vault_dir):
        """Positive control: records written before the version field exist
        keep the old mtime ordering, so the version path above is what
        decides in the tests that pass, not a lucky mtime."""
        lodestone_dir = vault_dir / "memory" / "lodestone"
        newer = _write_record(vault_dir, "rec-1", value="new text")
        # Same logical id, two physical files, no version field on either.
        (lodestone_dir / "rec-1-older.json").write_text(
            json.dumps(dict(newer, value="old text")), encoding="utf-8"
        )
        _set_mtime(lodestone_dir / "rec-1.json", 1_700_000_100)
        _set_mtime(lodestone_dir / "rec-1-older.json", 1_700_000_000)

        assert [r["value"] for r in read_all()] == ["new text"]
