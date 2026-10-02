"""
tests/test_restore_unarchived_conversations.py

Tests for the one script in this repo that moves canonical records between two
live memory type directories.

Everything here runs against a synthetic vault under tmp_path. Nothing touches
the real one, and nothing asserts against a production count: the production
number is passed in as `--expect` at the call site, and what is tested here is
that the assertion REFUSES when it does not match.

Two properties get the most attention, because they are the ones whose absence
would be discovered too late.

The selector is the absence of four markers, so each marker gets its own test.
A selector that stopped reading one of them would still pass a single
all-markers-present fixture, and would move records that were archived on
purpose. Each exclusion is therefore paired with the positive control that the
same record IS selected once the marker is removed.

And the revert is a round-trip, asserted on bytes rather than on counts. A
manifest that restores the wrong content is worse than no manifest, because it
is believed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.restore_unarchived_conversations import (
    collect,
    is_unmarked_conversation,
    main,
    revert,
    write_manifest,
)


def _write(directory: Path, name: str, record: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def _record(record_id: str, **overrides) -> dict:
    record = {
        "id": record_id,
        "timestamp": record_id,
        "type": "conversation",
        "text": "a synthetic turn with enough body to clear the content floors",
        "source": "chat",
        "tags": ["conversation"],
        "metadata": {"role": "user", "content_kind": "user_content"},
    }
    record.update(overrides)
    return record


def _vault(tmp_path: Path) -> Path:
    (tmp_path / "memory" / "archive").mkdir(parents=True)
    (tmp_path / "memory" / "conversation").mkdir(parents=True)
    return tmp_path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# The selector: one test per marker, each with its positive control.
# ---------------------------------------------------------------------------

def test_an_unmarked_conversation_record_is_selected():
    """The positive control for all four exclusions below.

    Without this, a selector that returned False unconditionally would pass
    every exclusion test in this file.
    """
    assert is_unmarked_conversation(_record("r1"))


def test_the_test_marker_excludes():
    record = _record("r1")
    record["metadata"]["test"] = True
    assert not is_unmarked_conversation(record)


def test_the_deleted_marker_excludes():
    record = _record("r1")
    record["metadata"]["deleted"] = True
    assert not is_unmarked_conversation(record)


def test_the_cleanup_script_source_excludes():
    assert not is_unmarked_conversation(_record("r1", source="cleanup_script"))


def test_the_test_cleanup_tag_excludes():
    assert not is_unmarked_conversation(
        _record("r1", tags=["conversation", "test_cleanup"])
    )


def test_a_non_conversation_type_is_not_selected():
    """archive/ holds other types too; only conversation records move."""
    assert not is_unmarked_conversation(_record("r1", type="journal"))


def test_a_record_with_no_metadata_is_still_selectable():
    """Absence of metadata is absence of a marker, not a reason to skip."""
    record = _record("r1")
    del record["metadata"]
    assert is_unmarked_conversation(record)


# ---------------------------------------------------------------------------
# Collection, and what it refuses.
# ---------------------------------------------------------------------------

def test_collect_selects_only_the_unmarked_and_restores_the_filename(tmp_path):
    vault = _vault(tmp_path)
    archive = vault / "memory" / "archive"
    # The sweep's own naming: the stem is not the id.
    _write(archive, "conversation__2026-04-01T10-00-00.json",
           _record("2026-04-01T10-00-00"))
    _write(archive, "uat_cleanup_conversation_2026-04-02T10-00-00.json",
           _record("2026-04-02T10-00-00"))
    marked = _record("2026-04-03T10-00-00")
    marked["metadata"]["test"] = True
    _write(archive, "conversation__2026-04-03T10-00-00.json", marked)

    candidates, tally = collect(vault)

    assert len(candidates) == 2
    assert tally["files_scanned"] == 3
    assert tally["marked_or_other_type"] == 1
    assert [c.dest_path.name for c in candidates] == [
        "2026-04-01T10-00-00.json", "2026-04-02T10-00-00.json"
    ], "the destination name must be <id>.json, not the archived stem"


def test_a_record_with_no_id_is_counted_not_guessed(tmp_path):
    """The stem is not a fallback id here, because the sweep prefixed it."""
    vault = _vault(tmp_path)
    record = _record("r1")
    del record["id"]
    _write(vault / "memory" / "archive", "conversation__r1.json", record)

    candidates, tally = collect(vault)
    assert candidates == []
    assert tally["unreadable"] == 1


def test_a_destination_collision_refuses_before_moving_anything(tmp_path, capsys):
    vault = _vault(tmp_path)
    _write(vault / "memory" / "archive", "conversation__r1.json", _record("r1"))
    _write(vault / "memory" / "conversation", "r1.json", _record("r1"))

    code = main_with(["--vault", str(vault), "--confirm",
                      "--manifest", str(tmp_path / "out" / "m.jsonl")])
    assert code == 2
    assert "REFUSED" in capsys.readouterr().out
    assert (vault / "memory" / "archive" / "conversation__r1.json").exists(), (
        "the source file moved despite the refusal"
    )


def test_the_expected_count_refuses_when_the_vault_differs(tmp_path, capsys):
    """The count is a post-condition. A drifted vault must stop the run."""
    vault = _vault(tmp_path)
    _write(vault / "memory" / "archive", "conversation__r1.json", _record("r1"))

    code = main_with(["--vault", str(vault), "--expect", "64", "--confirm",
                      "--manifest", str(tmp_path / "out" / "m.jsonl")])
    assert code == 2
    out = capsys.readouterr().out
    assert "expected 64" in out and "select 1" in out
    assert (vault / "memory" / "archive" / "conversation__r1.json").exists()


def test_a_real_run_requires_a_manifest(tmp_path, capsys):
    vault = _vault(tmp_path)
    _write(vault / "memory" / "archive", "conversation__r1.json", _record("r1"))

    code = main_with(["--vault", str(vault), "--expect", "1", "--confirm"])
    assert code == 2
    assert "--manifest is required" in capsys.readouterr().out
    assert (vault / "memory" / "archive" / "conversation__r1.json").exists()


def test_a_manifest_inside_the_repository_is_refused(tmp_path, capsys):
    """The manifest carries record ids and paths, so it stays outside the tree."""
    vault = _vault(tmp_path)
    _write(vault / "memory" / "archive", "conversation__r1.json", _record("r1"))
    inside = Path(__file__).resolve().parents[1] / "manifest_should_be_refused.jsonl"

    code = main_with(["--vault", str(vault), "--expect", "1", "--confirm",
                      "--manifest", str(inside)])
    assert code == 2
    assert "REFUSED" in capsys.readouterr().out
    assert not inside.exists()
    assert (vault / "memory" / "archive" / "conversation__r1.json").exists()


def test_the_dry_run_moves_nothing_and_is_the_default(tmp_path, capsys):
    vault = _vault(tmp_path)
    source = _write(vault / "memory" / "archive", "conversation__r1.json",
                    _record("r1"))

    code = main_with(["--vault", str(vault)])
    assert code == 0
    assert "dry run: nothing moved" in capsys.readouterr().out
    assert source.exists()
    assert list((vault / "memory" / "conversation").glob("*.json")) == []


# ---------------------------------------------------------------------------
# The manifest and the revert round-trip.
# ---------------------------------------------------------------------------

def test_the_manifest_is_complete_before_the_first_move(tmp_path):
    """Written and fsynced up front, so an interrupted run is recoverable.

    Asserted by writing the manifest without moving anything and reading it
    back: if it were produced from the results of the moves, this would be
    empty.
    """
    vault = _vault(tmp_path)
    _write(vault / "memory" / "archive", "conversation__r1.json", _record("r1"))
    _write(vault / "memory" / "archive", "conversation__r2.json", _record("r2"))
    candidates, _ = collect(vault)

    manifest = tmp_path / "out" / "m.jsonl"
    write_manifest(manifest, candidates)

    entries = [json.loads(line) for line in
               manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(entries) == 2
    assert all(Path(e["source_path"]).exists() for e in entries), (
        "the manifest describes moves that have not happened yet"
    )
    assert all({"record_id", "source_path", "dest_path", "sha256"} <= e.keys()
               for e in entries)


def test_move_then_revert_restores_the_vault_byte_for_byte(tmp_path, capsys):
    """The reversibility requirement, asserted on bytes rather than counts."""
    vault = _vault(tmp_path)
    archive = vault / "memory" / "archive"
    names = ("conversation__r1.json", "conversation__r2.json",
             "uat_cleanup_conversation_r3.json")
    for index, name in enumerate(names, start=1):
        _write(archive, name, _record(f"r{index}"))
    # One marked record that must not move, and must not come back changed.
    marked = _record("r9")
    marked["metadata"]["test"] = True
    _write(archive, "conversation__r9.json", marked)

    before = {p.name: _sha(p) for p in sorted(archive.glob("*.json"))}

    manifest = tmp_path / "out" / "m.jsonl"
    assert main_with(["--vault", str(vault), "--expect", "3", "--confirm",
                      "--manifest", str(manifest)]) == 0
    assert len(list((vault / "memory" / "conversation").glob("*.json"))) == 3
    assert len(list(archive.glob("*.json"))) == 1, "the marked record moved"

    assert revert(manifest) == 3

    after = {p.name: _sha(p) for p in sorted(archive.glob("*.json"))}
    assert after == before, "the revert did not restore the vault exactly"
    assert list((vault / "memory" / "conversation").glob("*.json")) == []


def test_revert_refuses_on_a_content_mismatch(tmp_path, capsys):
    vault = _vault(tmp_path)
    _write(vault / "memory" / "archive", "conversation__r1.json", _record("r1"))
    manifest = tmp_path / "out" / "m.jsonl"
    assert main_with(["--vault", str(vault), "--expect", "1", "--confirm",
                      "--manifest", str(manifest)]) == 0

    moved = vault / "memory" / "conversation" / "r1.json"
    moved.write_text(json.dumps(_record("r1", text="edited since the move")),
                     encoding="utf-8")

    assert revert(manifest) == -1
    assert "SHA MISMATCH" in capsys.readouterr().out
    assert moved.exists(), "a refused revert moved the file anyway"


def test_revert_is_idempotent(tmp_path):
    """Running it twice is not an error, so a partial run can be finished."""
    vault = _vault(tmp_path)
    _write(vault / "memory" / "archive", "conversation__r1.json", _record("r1"))
    manifest = tmp_path / "out" / "m.jsonl"
    main_with(["--vault", str(vault), "--expect", "1", "--confirm",
               "--manifest", str(manifest)])

    assert revert(manifest) == 1
    assert revert(manifest) == 0


# ---------------------------------------------------------------------------

def main_with(argv: list[str]) -> int:
    """Call main() with argv, the way the CLI would."""
    import sys

    saved = sys.argv
    sys.argv = ["restore_unarchived_conversations.py", *argv]
    try:
        return main()
    finally:
        sys.argv = saved
