"""
tests/test_rebuild_memory_db.py

Tests for the memory.db rebuild path in scripts/rebuild_indexes.py.

All fixtures are synthetic (CLAUDE.md Vault Privacy Rule) and every test
builds a throwaway vault under tmp_path, so nothing here can reach a real
vault regardless of how PRIVATE_VAULT_PATH is configured.

The invariant under test is not "the rebuild reproduces the old file". It
cannot: roughly 99% of a real memory.db carries ids minted by the retired
ingest pipeline, and those ids exist in no canonical record. The invariant
is that the rebuild contains exactly one row per eligible canonical record,
preserves store_id and delivery history wherever a row can be matched, and
never writes to the live database.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from scripts.rebuild_indexes import (
    IdCollisionError,
    collect_source_records,
    plan_memory_db_rebuild,
    rebuild_memory_db,
)
from src.retrieval.sqlite_vector_store import SqliteVectorStore

EMBED_DIM = 8


@pytest.fixture(autouse=True)
def stub_embeddings(monkeypatch):
    """Deterministic embeddings. The rebuild's correctness is about which
    rows exist and what ids they carry, not about vector values."""
    monkeypatch.setattr(
        "scripts.rebuild_indexes.embed_texts",
        lambda texts: [[float(len(t) % 7)] * EMBED_DIM for t in texts],
    )


def _write_native(vault: Path, memory_type: str, record_id: str, text: str, **extra) -> Path:
    directory = vault / "memory" / memory_type
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{record_id}.json"
    path.write_text(
        json.dumps({
            "id": record_id,
            "timestamp": record_id,
            "type": memory_type,
            "text": text,
            "source": extra.pop("source", "chat"),
            "tags": [],
            "metadata": extra.pop("metadata", {}),
            **extra,
        }),
        encoding="utf-8",
    )
    return path


def _write_ingested(vault: Path, chunk_id: str, text: str, source="chatgpt", role="user") -> Path:
    directory = vault / "memory" / "ingested"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{chunk_id}.json"
    metadata = {"role": role} if role is not None else {}
    path.write_text(
        json.dumps({
            "type": "ingested",
            "source": source,
            "doc_id": "doc_001",
            "chunk_id": chunk_id,
            "title": "t",
            "created_at": "2024-01-01T00-00-00",
            "content": text,
            "metadata": metadata,
        }),
        encoding="utf-8",
    )
    return path


def _seed_existing_db(vault: Path, rows: list[dict]) -> Path:
    db_path = vault / "embeddings" / "memory.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteVectorStore(db_path)
    for row in rows:
        store.insert({
            "id": row["id"],
            "text": row["text"],
            "embedding": [0.0] * EMBED_DIM,
            "source": row.get("source", "chat"),
            "memory_type": row.get("memory_type", "conversation"),
            "created_at": row.get("created_at", "2024-01-01T00-00-00"),
            "metadata": {},
        })
    # Delivery history is written after insert; SqliteVectorStore.insert does
    # not accept these columns, matching how they are set in production.
    conn = sqlite3.connect(str(db_path))
    for row in rows:
        conn.execute(
            "UPDATE vectors SET tier = ?, heat_score = ?, frequency_score = ?, "
            "last_retrieved_at = ?, quality = ? WHERE id = ?",
            (
                row.get("tier", "cold"),
                row.get("heat_score", 0.01),
                row.get("frequency_score", 0.0),
                row.get("last_retrieved_at"),
                row.get("quality"),
                row["id"],
            ),
        )
    conn.commit()
    conn.close()
    return db_path


def _rows(db_path: Path) -> dict[str, dict]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return {r["id"]: dict(r) for r in conn.execute("SELECT * FROM vectors")}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Source enumeration
# ---------------------------------------------------------------------------

def test_collects_all_four_native_types(tmp_path):
    for memory_type in ("conversation", "profile", "reflection", "journal"):
        _write_native(tmp_path, memory_type, f"2024-01-01T00-00-0{len(memory_type)}", f"text for {memory_type}")

    found = {r.memory_type for r in collect_source_records(tmp_path)}
    assert found == {"conversation", "profile", "reflection", "journal"}


def test_prior_substrate_chunks_become_conversation(tmp_path):
    _write_ingested(tmp_path, "chunk_user", "a user turn from the export", role="user")
    _write_ingested(tmp_path, "chunk_asst", "an assistant turn from the export", role="assistant")

    records = collect_source_records(tmp_path)
    assert {r.canonical_id for r in records} == {"chunk_user", "chunk_asst"}
    assert all(r.memory_type == "conversation" for r in records)


def test_non_chatgpt_ingested_is_excluded(tmp_path):
    _write_ingested(tmp_path, "chunk_doc", "a paragraph from a pdf", source="file", role="user")
    assert collect_source_records(tmp_path) == []


def test_unresolvable_role_is_excluded(tmp_path):
    # Matches the migration: a role that is not exactly user or assistant is
    # left alone rather than guessed at.
    _write_ingested(tmp_path, "chunk_sys", "a system turn", role="system")
    _write_ingested(tmp_path, "chunk_none", "a turn with no role", role=None)
    assert collect_source_records(tmp_path) == []


def test_empty_text_is_skipped(tmp_path):
    _write_native(tmp_path, "conversation", "2024-01-01T00-00-01", "   ")
    assert collect_source_records(tmp_path) == []


def test_ingested_text_is_read_from_content_field(tmp_path):
    _write_ingested(tmp_path, "chunk_a", "the chunk body")
    assert collect_source_records(tmp_path)[0].text == "the chunk body"


# ---------------------------------------------------------------------------
# Planning: match, add, orphan
# ---------------------------------------------------------------------------

def test_plan_matches_on_text_not_id(tmp_path):
    # The whole point: the indexed row carries an id that appears in no
    # canonical record, exactly as the retired pipeline left it.
    _write_ingested(tmp_path, "chunk_a", "a turn that is already indexed")
    _seed_existing_db(tmp_path, [{"id": "legacy_id_999", "text": "a turn that is already indexed"}])

    plan = plan_memory_db_rebuild(tmp_path, tmp_path / "embeddings" / "memory.db")
    assert len(plan.matched) == 1
    assert plan.matched[0][1]["id"] == "legacy_id_999"
    assert plan.added == []
    assert plan.orphans == []


def test_plan_normalizes_whitespace_and_case_when_matching(tmp_path):
    _write_ingested(tmp_path, "chunk_a", "A Turn   With  Odd Spacing")
    _seed_existing_db(tmp_path, [{"id": "legacy_1", "text": "a turn with odd spacing"}])

    plan = plan_memory_db_rebuild(tmp_path, tmp_path / "embeddings" / "memory.db")
    assert len(plan.matched) == 1


def test_plan_reports_vault_records_missing_from_index(tmp_path):
    _write_native(tmp_path, "profile", "2024-01-01T00-00-01", "a profile record never indexed")
    _seed_existing_db(tmp_path, [])

    plan = plan_memory_db_rebuild(tmp_path, tmp_path / "embeddings" / "memory.db")
    assert len(plan.added) == 1
    assert plan.added[0].memory_type == "profile"


def test_plan_reports_indexed_rows_with_no_canonical_record(tmp_path):
    _seed_existing_db(tmp_path, [{"id": "orphan_1", "text": "indexed but not in the vault"}])

    plan = plan_memory_db_rebuild(tmp_path, tmp_path / "embeddings" / "memory.db")
    assert len(plan.orphans) == 1
    assert plan.orphans[0]["id"] == "orphan_1"


def test_duplicate_text_pairs_deterministically(tmp_path):
    _write_ingested(tmp_path, "chunk_b", "same text")
    _write_ingested(tmp_path, "chunk_a", "same text")
    _seed_existing_db(tmp_path, [
        {"id": "legacy_2", "text": "same text"},
        {"id": "legacy_1", "text": "same text"},
    ])

    first = plan_memory_db_rebuild(tmp_path, tmp_path / "embeddings" / "memory.db")
    second = plan_memory_db_rebuild(tmp_path, tmp_path / "embeddings" / "memory.db")

    pairing = sorted((r.canonical_id, e["id"]) for r, e in first.matched)
    assert pairing == [("chunk_a", "legacy_1"), ("chunk_b", "legacy_2")]
    assert pairing == sorted((r.canonical_id, e["id"]) for r, e in second.matched)


def test_surplus_duplicates_split_between_added_and_orphaned(tmp_path):
    _write_ingested(tmp_path, "chunk_a", "same text")
    _write_ingested(tmp_path, "chunk_b", "same text")
    _seed_existing_db(tmp_path, [{"id": "legacy_1", "text": "same text"}])

    plan = plan_memory_db_rebuild(tmp_path, tmp_path / "embeddings" / "memory.db")
    assert len(plan.matched) == 1
    assert len(plan.added) == 1
    assert plan.orphans == []


# ---------------------------------------------------------------------------
# Rebuild output
# ---------------------------------------------------------------------------

def test_rebuild_preserves_store_id_and_delivery_history(tmp_path):
    _write_ingested(tmp_path, "chunk_a", "a turn that is already indexed")
    _seed_existing_db(tmp_path, [{
        "id": "legacy_id_999",
        "text": "a turn that is already indexed",
        # Deliberately not the schema default ('hot'), so the assertion
        # cannot pass on an unwritten column.
        "tier": "warm",
        "heat_score": 0.83,
        "frequency_score": 1.25,
        "last_retrieved_at": "2026-09-18T00-00-00",
        "quality": "suppressed",
    }])

    out = tmp_path / "embeddings" / "rebuilt.db"
    rebuild_memory_db(tmp_path, out_path=out)

    rows = _rows(out)
    assert set(rows) == {"legacy_id_999"}
    row = rows["legacy_id_999"]
    assert row["tier"] == "warm"
    assert row["heat_score"] == pytest.approx(0.83)
    assert row["frequency_score"] == pytest.approx(1.25)
    assert row["last_retrieved_at"] == "2026-09-18T00-00-00"
    assert row["quality"] == "suppressed"
    assert row["memory_type"] == "conversation"


def test_rebuild_mints_canonical_id_for_new_records(tmp_path):
    _write_native(tmp_path, "profile", "2024-01-01T00-00-01", "a profile record never indexed")
    _seed_existing_db(tmp_path, [])

    out = tmp_path / "embeddings" / "rebuilt.db"
    rebuild_memory_db(tmp_path, out_path=out)

    rows = _rows(out)
    assert set(rows) == {"2024-01-01T00-00-01"}
    assert rows["2024-01-01T00-00-01"]["memory_type"] == "profile"


def test_rebuild_row_count_equals_eligible_source_count(tmp_path):
    for i in range(3):
        _write_native(tmp_path, "conversation", f"2024-01-01T00-00-0{i}", f"native turn {i}")
    for i in range(4):
        _write_ingested(tmp_path, f"chunk_{i}", f"exported turn {i}")
    _write_ingested(tmp_path, "chunk_skip", "a pdf paragraph", source="file")
    _seed_existing_db(tmp_path, [])

    out = tmp_path / "embeddings" / "rebuilt.db"
    written = rebuild_memory_db(tmp_path, out_path=out)

    assert written == 7
    assert len(_rows(out)) == 7
    assert len(collect_source_records(tmp_path)) == 7


def test_rebuild_drops_orphans_by_default(tmp_path):
    _write_ingested(tmp_path, "chunk_a", "a turn in the vault")
    _seed_existing_db(tmp_path, [
        {"id": "legacy_1", "text": "a turn in the vault"},
        {"id": "orphan_1", "text": "indexed but not in the vault"},
    ])

    out = tmp_path / "embeddings" / "rebuilt.db"
    rebuild_memory_db(tmp_path, out_path=out)
    assert set(_rows(out)) == {"legacy_1"}


def test_rebuild_keeps_orphans_when_asked(tmp_path):
    _write_ingested(tmp_path, "chunk_a", "a turn in the vault")
    _seed_existing_db(tmp_path, [
        {"id": "legacy_1", "text": "a turn in the vault"},
        {"id": "orphan_1", "text": "indexed but not in the vault"},
    ])

    out = tmp_path / "embeddings" / "rebuilt.db"
    rebuild_memory_db(tmp_path, out_path=out, keep_orphans=True)
    assert set(_rows(out)) == {"legacy_1", "orphan_1"}


def test_rebuild_recomputes_authorship_from_source(tmp_path):
    _write_ingested(tmp_path, "chunk_user", "a user turn", role="user")
    _write_ingested(tmp_path, "chunk_asst", "an assistant turn", role="assistant")
    _seed_existing_db(tmp_path, [])

    out = tmp_path / "embeddings" / "rebuilt.db"
    rebuild_memory_db(tmp_path, out_path=out)

    rows = _rows(out)
    assert rows["chunk_user"]["authorship"] != rows["chunk_asst"]["authorship"]


def test_authorship_is_recomputed_not_carried(tmp_path):
    # authorship is a pure function of the source record, so a stale value on
    # the matched row must not survive the rebuild. This is what lets a
    # rebuild correct an authorship defect rather than perpetuate it.
    _write_ingested(tmp_path, "chunk_user", "a user turn", role="user")
    db_path = _seed_existing_db(tmp_path, [{"id": "legacy_1", "text": "a user turn"}])
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE vectors SET authorship = ? WHERE id = ?", ("third_party", "legacy_1"))
    conn.commit()
    conn.close()

    out = tmp_path / "embeddings" / "rebuilt.db"
    rebuild_memory_db(tmp_path, out_path=out)

    assert _rows(out)["legacy_1"]["authorship"] != "third_party"


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------

def test_dry_run_writes_nothing(tmp_path):
    _write_ingested(tmp_path, "chunk_a", "a turn in the vault")
    db_path = _seed_existing_db(tmp_path, [{"id": "legacy_1", "text": "a turn in the vault"}])
    before = db_path.read_bytes()

    planned = rebuild_memory_db(tmp_path, dry_run=True)

    assert planned == 1
    assert db_path.read_bytes() == before
    assert not (tmp_path / "embeddings" / "memory.db.rebuild").exists()


def test_rebuild_never_touches_the_live_database(tmp_path):
    _write_ingested(tmp_path, "chunk_a", "a turn in the vault")
    _write_native(tmp_path, "profile", "2024-01-01T00-00-01", "a new profile record")
    db_path = _seed_existing_db(tmp_path, [{"id": "legacy_1", "text": "a turn in the vault"}])
    before = db_path.read_bytes()

    rebuild_memory_db(tmp_path)

    assert db_path.read_bytes() == before
    assert (tmp_path / "embeddings" / "memory.db.rebuild").exists()


def test_rebuild_default_output_is_a_sibling_not_the_original(tmp_path):
    _write_ingested(tmp_path, "chunk_a", "a turn in the vault")
    _seed_existing_db(tmp_path, [])

    rebuild_memory_db(tmp_path)

    assert (tmp_path / "embeddings" / "memory.db.rebuild").exists()


def test_rebuild_with_no_existing_database_is_a_clean_build(tmp_path):
    _write_ingested(tmp_path, "chunk_a", "a turn in the vault")

    out = tmp_path / "embeddings" / "rebuilt.db"
    written = rebuild_memory_db(tmp_path, out_path=out)

    assert written == 1
    assert set(_rows(out)) == {"chunk_a"}


def test_shared_canonical_id_across_types_is_detected(tmp_path):
    # write_memory derives memory_id from the timestamp alone while the
    # directory carries the type, so a conversation and a reflection written
    # in the same second share an id and collapse into one row.
    shared = "2024-01-01T00-00-01"
    _write_native(tmp_path, "conversation", shared, "a conversation turn")
    _write_native(tmp_path, "reflection", shared, "a reflection body")

    plan = plan_memory_db_rebuild(tmp_path, tmp_path / "embeddings" / "memory.db")
    assert set(plan.collisions) == {shared}
    assert plan.records_lost_to_collision == 1


def test_rebuild_refuses_to_silently_drop_colliding_records(tmp_path):
    shared = "2024-01-01T00-00-01"
    _write_native(tmp_path, "conversation", shared, "a conversation turn")
    _write_native(tmp_path, "reflection", shared, "a reflection body")

    out = tmp_path / "embeddings" / "rebuilt.db"
    with pytest.raises(IdCollisionError):
        rebuild_memory_db(tmp_path, out_path=out)
    assert not out.exists()


def test_collisions_can_be_accepted_explicitly(tmp_path):
    shared = "2024-01-01T00-00-01"
    _write_native(tmp_path, "conversation", shared, "a conversation turn")
    _write_native(tmp_path, "reflection", shared, "a reflection body")

    out = tmp_path / "embeddings" / "rebuilt.db"
    rebuild_memory_db(tmp_path, out_path=out, allow_id_collisions=True)
    assert len(_rows(out)) == 1


def test_no_collision_when_ids_are_distinct(tmp_path):
    _write_native(tmp_path, "conversation", "2024-01-01T00-00-01", "a conversation turn")
    _write_native(tmp_path, "reflection", "2024-01-01T00-00-02", "a reflection body")

    plan = plan_memory_db_rebuild(tmp_path, tmp_path / "embeddings" / "memory.db")
    assert plan.collisions == {}
    assert plan.records_lost_to_collision == 0


def test_unreadable_record_does_not_abort_the_rebuild(tmp_path):
    _write_ingested(tmp_path, "chunk_ok", "a readable turn")
    bad = tmp_path / "memory" / "ingested" / "chunk_bad.json"
    bad.write_text("{ not json", encoding="utf-8")

    out = tmp_path / "embeddings" / "rebuilt.db"
    assert rebuild_memory_db(tmp_path, out_path=out) == 1
