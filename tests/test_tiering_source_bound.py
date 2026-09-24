"""
tests/test_tiering_source_bound.py

Integration coverage for ADR-015 amendment (PR #180), implementation step 2:
source-bounding wired into TieringService.run() (reflection) and
lodestone_synthesis.synthesize_lodestone_candidates() (lodestone).

TieringService.run() has no prior end-to-end test anywhere in the suite --
every existing SQLite-level tiering test (tests/test_tiering.py) uses its
own isolated tmp_path database via SqliteVectorStore directly, never the
shared conftest session vault. That is deliberate and followed here too:
run() reassigns EVERY record in the vault it is pointed at, and the
conftest vault is shared and cumulative across the whole test session --
calling run() against it would recompute tiers for records other tests
wrote and never asked to have touched. set_vault_path_override points
TieringService at a private tmp_path vault instead; conftest's
restore_process_state (autouse) restores the override afterward.

The lodestone tests are the exception: lodestone_synthesis writes through
the real vault-JSON path (no SqliteVectorStore-level shortcut exists for
it), so they run against the shared conftest vault but control their
candidate set via a mocked memory_service.read() -- see
TestLodestoneChainedDerivation's docstring.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from unittest.mock import patch

import pytest

from src.core.config import set_vault_path_override
from src.retrieval.sqlite_vector_store import SqliteVectorStore
from src.tiering.tiering_service import TieringService


@pytest.fixture
def isolated_vault(tmp_path):
    """A private vault, not the shared conftest one, for TieringService.run()
    tests. Yields (vault_path, memory_db_path)."""
    vault = tmp_path / "vault"
    (vault / "embeddings").mkdir(parents=True)
    set_vault_path_override(str(vault), "test-tiering-source-bound")
    yield vault, vault / "embeddings" / "memory.db"


def _today_stamp() -> str:
    """Now, in the vault's hyphenated format.

    These tests need their records to heat-score hot on recency alone, so
    that the source bound is observably what pulls a reflection down
    rather than age doing it quietly. A literal date does that on the day
    it is written and then expires: the same test reads as a bound failure
    once the date drifts out of the hot band, which is what happened here.
    """
    return datetime.now().strftime("%Y-%m-%dT%H-%M-%S")


def _insert(db_path, record_id: str, memory_type: str, metadata: dict | None = None) -> None:
    store = SqliteVectorStore(db_path)
    try:
        store.insert({
            "id": record_id,
            "text": f"content for {record_id}",
            "embedding": [0.1] * 768,
            "source": "test",
            "memory_type": memory_type,
            "created_at": _today_stamp(),
            "metadata": metadata or {},
        })
    finally:
        store.close()


def _set_tier(db_path, record_id: str, tier: str) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("UPDATE vectors SET tier = ? WHERE id = ?", (tier, record_id))
        conn.commit()
    finally:
        conn.close()


def _get_tier(db_path, record_id: str) -> str | None:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT tier FROM vectors WHERE id = ?", (record_id,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


class TestReflectionBoundedByColdSources:
    def test_reflection_with_cold_source_goes_cold_regardless_of_own_heat(self, isolated_vault):
        """created_at is today, so the reflection would otherwise heat-score
        hot by recency alone. The bound must override that."""
        vault, db_path = isolated_vault
        _insert(db_path, "source-1", "journal")
        _set_tier(db_path, "source-1", "cold")
        _insert(db_path, "reflection-1", "reflection", {"source_record_ids": ["source-1"]})

        TieringService().run()

        assert _get_tier(db_path, "reflection-1") == "cold"

    def test_reflection_with_warm_source_is_capped_at_warm(self, isolated_vault):
        vault, db_path = isolated_vault
        _insert(db_path, "source-1", "journal")
        _set_tier(db_path, "source-1", "warm")
        _insert(db_path, "reflection-1", "reflection", {"source_record_ids": ["source-1"]})

        TieringService().run()

        assert _get_tier(db_path, "reflection-1") == "warm"

    def test_reflection_with_all_hot_sources_is_unaffected(self, isolated_vault):
        """Non-vacuousness: the bound must not silently become a no-op --
        confirm it leaves an already-correct hot tier alone."""
        vault, db_path = isolated_vault
        _insert(db_path, "source-1", "journal")
        _set_tier(db_path, "source-1", "hot")
        _insert(db_path, "reflection-1", "reflection", {"source_record_ids": ["source-1"]})

        TieringService().run()

        assert _get_tier(db_path, "reflection-1") == "hot"


class TestReflectionLegacyFloor:
    def test_no_source_record_ids_floors_at_cold(self, isolated_vault):
        vault, db_path = isolated_vault
        _insert(db_path, "reflection-1", "reflection", {})

        TieringService().run()

        assert _get_tier(db_path, "reflection-1") == "cold"

    def test_empty_source_record_ids_floors_at_cold(self, isolated_vault):
        vault, db_path = isolated_vault
        _insert(db_path, "reflection-1", "reflection", {"source_record_ids": []})

        TieringService().run()

        assert _get_tier(db_path, "reflection-1") == "cold"


class TestReflectionPartialResolution:
    def test_bounds_against_the_resolvable_source_not_the_missing_one(self, isolated_vault):
        vault, db_path = isolated_vault
        _insert(db_path, "source-1", "journal")
        _set_tier(db_path, "source-1", "warm")
        _insert(
            db_path,
            "reflection-1",
            "reflection",
            {"source_record_ids": ["source-1", "deleted-nonexistent-id"]},
        )

        TieringService().run()

        assert _get_tier(db_path, "reflection-1") == "warm"


class TestReflectionChainedDerivation:
    """A monthly reflection sourcing a daily reflection -- the same-type
    chained case. Bound is computed against the immediate source's tier
    snapshot at the start of THIS run; the daily reflection's own tier
    must already reflect a prior bound for the chain to be correct end to
    end, which is the convergence property described in
    src/tiering/source_bound.py and tiering_service.py's module docstring."""

    def test_monthly_reflection_bounded_by_already_settled_daily_reflection(self, isolated_vault):
        vault, db_path = isolated_vault
        _insert(db_path, "journal-1", "journal")
        _set_tier(db_path, "journal-1", "cold")
        # Daily reflection, already settled cold from a prior night's run
        # (simulated directly rather than via a second run() call).
        _insert(db_path, "daily-1", "reflection", {"source_record_ids": ["journal-1"]})
        _set_tier(db_path, "daily-1", "cold")
        # Monthly reflection sources the daily one.
        _insert(db_path, "monthly-1", "reflection", {"source_record_ids": ["daily-1"]})

        TieringService().run()

        assert _get_tier(db_path, "monthly-1") == "cold"


class TestLodestoneChainedDerivation:
    """A lodestone is synthesized from reflections, which are themselves
    derived from conversations/journal -- the chained-derivation case named
    in the task.

    _recent_reflections() reads ALL reflection-type records in the vault
    within a 30-day window, with no id filter -- unlike session_id
    filtering elsewhere in this rollout, there is nothing to scope it to
    "just this test's records" by. The conftest-provided vault is
    session-scoped and shared, and other test files write real reflection
    records into it, so a real memory_service.read() here would pull in
    reflections this test never wrote and break exact source_record_ids
    assertions.

    So: memory_service.read is mocked to return EXACTLY the records this
    test controls (same as the existing test_lodestone_synthesis.py
    pattern), but -- unlike that file's fully synthetic dicts -- each id
    is backed by a REAL write_memory() call against the shared conftest
    vault, so it resolves in _tier_index_for_ids() against a real,
    controllable tier.
    """

    @pytest.fixture(autouse=True)
    def stub_embeddings(self, monkeypatch):
        monkeypatch.setattr("src.memory.write_memory.embed_text", lambda _t: [0.0] * 768)

    def _stub_chat(self, responses):
        iterator = iter(responses)
        def _side_effect(**kwargs):
            return {"message": {"content": next(iterator)}}
        return _side_effect

    def _synthesis_responses(self):
        return [
            "the user keeps returning to direct conversation over comfort",
            "character",
            (
                "VALUE: I would rather lose ease than skip a hard conversation\n"
                "EVIDENCE:\n"
                "- declined to soften feedback in three sessions\n"
                "- noted resistance to performative pleasantness\n"
                "- chose a hard conversation over a comfortable one this month"
            ),
        ]

    def _write_and_tier_reflections(self, tier: str, label: str) -> list[dict]:
        """Real write_memory() writes (so ids resolve in SQLite), returned
        as the dict shape _recent_reflections()/_format_reflection_block()
        expect, for a mocked memory_service.read()."""
        from src.memory.write_memory import write_memory

        vault = None
        records = []
        for i in range(6):
            text = f"{label} reflection number {i} discussing directness over comfort."
            path = write_memory(text=text, memory_type="reflection", source="test")
            assert path is not None
            record_id = path.stem
            db_path = _memory_db_path()
            _set_tier(db_path, record_id, tier)
            records.append({
                "id": record_id,
                "type": "reflection",
                "text": text,
                "timestamp": record_id,
                "metadata": {"cadence": "weekly"},
            })
        return records

    def test_lodestone_from_cold_reflections_is_bounded_to_cold(self):
        from unittest.mock import MagicMock

        from src.reflection.lodestone_synthesis import synthesize_lodestone_candidates

        records = self._write_and_tier_reflections("cold", "Cold-sourced")
        svc = MagicMock()
        svc.read.return_value = records

        with patch(
            "src.reflection.lodestone_synthesis.ollama.chat",
            side_effect=self._stub_chat(self._synthesis_responses()),
        ):
            result = synthesize_lodestone_candidates(memory_service=svc)

        assert result is not None
        assert set(result["metadata"]["source_record_ids"]) == {r["id"] for r in records}
        assert result["metadata"]["tier"] == "cold"

    def test_lodestone_from_hot_reflections_is_not_capped_below_hot(self):
        from unittest.mock import MagicMock

        from src.reflection.lodestone_synthesis import synthesize_lodestone_candidates

        records = self._write_and_tier_reflections("hot", "Hot-sourced")
        svc = MagicMock()
        svc.read.return_value = records

        with patch(
            "src.reflection.lodestone_synthesis.ollama.chat",
            side_effect=self._stub_chat(self._synthesis_responses()),
        ):
            result = synthesize_lodestone_candidates(memory_service=svc)

        assert result is not None
        assert result["metadata"]["tier"] == "hot"

    def test_lodestone_with_no_resolvable_sources_floors_at_cold(self):
        """Fully synthetic reflection dicts with ids that were never
        actually written to SQLite (the existing test_lodestone_synthesis
        .py pattern) -- none resolve, so the legacy floor applies."""
        from datetime import datetime, timedelta
        from unittest.mock import MagicMock

        from src.reflection.lodestone_synthesis import synthesize_lodestone_candidates

        def _unresolvable_reflection(i):
            ts = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%dT%H-%M-%S-%f")
            return {
                "id": ts,
                "type": "reflection",
                "text": f"synthetic unresolvable reflection {i} about directness",
                "timestamp": ts,
                "metadata": {"cadence": "weekly"},
            }

        svc = MagicMock()
        svc.read.return_value = [_unresolvable_reflection(i) for i in range(6)]

        with patch(
            "src.reflection.lodestone_synthesis.ollama.chat",
            side_effect=self._stub_chat(self._synthesis_responses()),
        ):
            result = synthesize_lodestone_candidates(memory_service=svc)

        assert result is not None
        assert result["metadata"]["tier"] == "cold"


def _memory_db_path():
    from src.core.config import get_private_vault_path

    return get_private_vault_path() / "embeddings" / "memory.db"
