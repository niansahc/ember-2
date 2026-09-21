"""
tests/test_eval_fixture_guard.py

Guard against eval fixtures being indexed into a non-test vault (#211).

Twelve records written by tests/eval/seeder.py were found in the live
personal vault's index. They had been silently unreachable through a
dimension-mismatch path; the #211 rebuild gave them real embeddings and made
them genuinely retrievable, at which point synthetic test content was
indistinguishable from real recollection in someone's personal memory.

Two properties, and the second is the one that keeps this from rotting:

  1. Eval fixtures index into the configured test vault and nowhere else.
  2. The guard FAILS CLOSED. An unset, unresolvable, or simply different
     VAULT_PATH_TEST means "do not index". Getting this wrong one way puts
     synthetic records in personal memory silently; the other way makes an
     eval visibly fail to find its own corpus. Only one of those is
     recoverable in a minute.

The canonical JSON files are deliberately untouched by all of this. A record
that exists in the vault is a fact about the vault, append-only owns it, and
the defect was never that the files exist -- it was that the index read them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.memory.eval_fixtures import (
    is_eval_fixture,
    is_test_vault,
    may_index_eval_fixture,
    should_index_record,
)


# ---------------------------------------------------------------------------
# Recognising a fixture
# ---------------------------------------------------------------------------

def test_source_marks_a_fixture():
    assert is_eval_fixture("eval_seed") is True


def test_metadata_flag_marks_a_fixture():
    # Caught even if the source field is lost or rewritten.
    assert is_eval_fixture("api", {"eval_seed": True}) is True


def test_ordinary_records_are_not_fixtures():
    assert is_eval_fixture("chat") is False
    assert is_eval_fixture("api", {"role": "user"}) is False
    assert is_eval_fixture(None, None) is False


def test_falsy_metadata_flag_is_not_a_fixture():
    assert is_eval_fixture("api", {"eval_seed": False}) is False


# ---------------------------------------------------------------------------
# Identifying the test vault
# ---------------------------------------------------------------------------

def test_configured_test_vault_is_recognised(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_PATH_TEST", str(tmp_path))
    assert is_test_vault(tmp_path) is True


def test_recognised_through_a_non_normalised_path(tmp_path, monkeypatch):
    # Same vault reached by a different spelling must still be the test vault,
    # or the guard blocks the eval it is supposed to permit.
    monkeypatch.setenv("VAULT_PATH_TEST", str(tmp_path))
    assert is_test_vault(str(tmp_path) + "/./") is True


def test_a_different_vault_is_not_the_test_vault(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_PATH_TEST", str(tmp_path / "test"))
    assert is_test_vault(tmp_path / "live") is False


def test_unset_test_vault_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("VAULT_PATH_TEST", raising=False)
    assert is_test_vault(tmp_path) is False
    assert may_index_eval_fixture(tmp_path) is False


def test_empty_test_vault_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_PATH_TEST", "")
    assert is_test_vault(tmp_path) is False


def test_unknown_destination_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_PATH_TEST", str(tmp_path))
    assert is_test_vault(None) is False
    assert may_index_eval_fixture(None) is False


def test_env_is_read_per_call_not_cached(tmp_path, monkeypatch):
    # The vault can be swapped at runtime; a cached answer would outlive it.
    monkeypatch.setenv("VAULT_PATH_TEST", str(tmp_path))
    assert is_test_vault(tmp_path) is True
    monkeypatch.setenv("VAULT_PATH_TEST", str(tmp_path / "elsewhere"))
    assert is_test_vault(tmp_path) is False


# ---------------------------------------------------------------------------
# The question both write paths ask
# ---------------------------------------------------------------------------

def test_fixture_indexes_into_the_test_vault(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_PATH_TEST", str(tmp_path))
    assert should_index_record(tmp_path, "eval_seed") is True


def test_fixture_does_not_index_into_a_live_vault(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_PATH_TEST", str(tmp_path / "test"))
    assert should_index_record(tmp_path / "live", "eval_seed") is False
    assert should_index_record(tmp_path / "live", "api", {"eval_seed": True}) is False


def test_ordinary_records_index_into_any_vault(tmp_path, monkeypatch):
    # The guard must not become a general indexing gate.
    monkeypatch.delenv("VAULT_PATH_TEST", raising=False)
    assert should_index_record(tmp_path, "chat") is True
    assert should_index_record(tmp_path, "api", {"role": "user"}) is True
    assert should_index_record(None, "reflection_engine") is True


# ---------------------------------------------------------------------------
# The write path honours it
# ---------------------------------------------------------------------------

def test_write_memory_writes_the_file_but_skips_the_index(tmp_path, monkeypatch):
    """The canonical record is still written. Only indexing is refused."""
    from unittest.mock import patch

    import src.memory.write_memory as wm

    monkeypatch.setenv("VAULT_PATH_TEST", str(tmp_path / "somewhere-else"))
    monkeypatch.setattr(wm, "get_private_vault_path", lambda: tmp_path)

    with patch.object(wm, "embed_text") as embed, \
         patch.object(wm, "_get_write_memory_store") as store:
        result = wm.write_memory(
            text="a synthetic eval corpus record long enough to clear the floor",
            memory_type="journal",
            source="eval_seed",
            metadata={"eval_seed": True},
        )

    assert result is not None, "the canonical record must still be written"
    written = list((tmp_path / "memory" / "journal").glob("*.json"))
    assert len(written) == 1, "the vault file is the append-only record and stays"

    store.assert_not_called()
    embed.assert_not_called(), "a skipped fixture should not cost an embedding call"


def test_write_memory_indexes_a_fixture_in_the_test_vault(tmp_path, monkeypatch):
    from unittest.mock import patch

    import src.memory.write_memory as wm

    monkeypatch.setenv("VAULT_PATH_TEST", str(tmp_path))
    monkeypatch.setattr(wm, "get_private_vault_path", lambda: tmp_path)

    with patch.object(wm, "embed_text", return_value=[0.0] * 8), \
         patch.object(wm, "_get_write_memory_store") as store:
        wm.write_memory(
            text="a synthetic eval corpus record long enough to clear the floor",
            memory_type="journal",
            source="eval_seed",
            metadata={"eval_seed": True},
        )

    store.return_value.insert.assert_called_once()


def test_write_memory_still_indexes_ordinary_records(tmp_path, monkeypatch):
    from unittest.mock import patch

    import src.memory.write_memory as wm

    monkeypatch.delenv("VAULT_PATH_TEST", raising=False)
    monkeypatch.setattr(wm, "get_private_vault_path", lambda: tmp_path)

    with patch.object(wm, "embed_text", return_value=[0.0] * 8), \
         patch.object(wm, "_get_write_memory_store") as store:
        wm.write_memory(
            text="an ordinary journal entry long enough to clear the content floor",
            memory_type="journal",
            source="api",
        )

    store.return_value.insert.assert_called_once()


# ---------------------------------------------------------------------------
# The rebuild path honours it too
# ---------------------------------------------------------------------------

def test_rebuild_skips_fixtures_outside_the_test_vault(tmp_path, monkeypatch):
    """Otherwise a rebuild reintroduces exactly what the write path refuses."""
    import json

    from scripts.rebuild_indexes import collect_source_records

    monkeypatch.setenv("VAULT_PATH_TEST", str(tmp_path / "somewhere-else"))
    d = tmp_path / "memory" / "journal"
    d.mkdir(parents=True)
    for name, source in (("fixture", "eval_seed"), ("real", "api")):
        (d / f"2026-01-01T00-00-0{len(name)}.json").write_text(
            json.dumps({
                "id": f"id_{name}",
                "timestamp": "2026-01-01T00-00-00",
                "type": "journal",
                "text": f"a {name} journal record long enough to clear the floor",
                "source": source,
                "metadata": {"eval_seed": True} if source == "eval_seed" else {},
            }),
            encoding="utf-8",
        )

    ids = {r.canonical_id for r in collect_source_records(tmp_path)}
    assert ids == {"id_real"}


def test_rebuild_keeps_fixtures_in_the_test_vault(tmp_path, monkeypatch):
    import json

    from scripts.rebuild_indexes import collect_source_records

    monkeypatch.setenv("VAULT_PATH_TEST", str(tmp_path))
    d = tmp_path / "memory" / "journal"
    d.mkdir(parents=True)
    (d / "2026-01-01T00-00-01.json").write_text(
        json.dumps({
            "id": "id_fixture",
            "timestamp": "2026-01-01T00-00-01",
            "type": "journal",
            "text": "a fixture journal record long enough to clear the floor",
            "source": "eval_seed",
            "metadata": {"eval_seed": True},
        }),
        encoding="utf-8",
    )

    assert {r.canonical_id for r in collect_source_records(tmp_path)} == {"id_fixture"}
