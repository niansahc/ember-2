"""
tests/test_rebuild_tiers.py

Coverage for the tier rebuild tool.

The tool's destructive step discards delivery history that cannot be
repaired, so the parts worth pinning are the ones that decide whether it
runs at all and exactly what it touches:

  * clearing is opt-in, and reports how many rows it actually changed
  * clearing touches the delivery columns and nothing else
  * a real run refuses without a snapshot destination outside the repo

Fixtures are synthetic (CLAUDE.md Vault Privacy Rule).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import rebuild_tiers  # noqa: E402

SCHEMA = """
CREATE TABLE vectors (
    id TEXT PRIMARY KEY,
    text TEXT,
    memory_type TEXT,
    created_at TEXT,
    tier TEXT,
    heat_score REAL,
    last_retrieved_at TEXT,
    frequency_score REAL
)
"""


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "memory.db"
    conn = sqlite3.connect(path)
    conn.execute(SCHEMA)
    conn.executemany(
        "INSERT INTO vectors (id, text, memory_type, created_at, tier, "
        "heat_score, last_retrieved_at, frequency_score) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            # delivered records, contaminated or not -- indistinguishable
            ("a", "synthetic a", "conversation", "2026-09-01T00-00-00",
             "hot", 0.9, "2026-09-25T00-00-00", 3.0),
            ("b", "synthetic b", "conversation", "2026-09-01T00-00-00",
             "hot", 0.7, "2026-09-24T00-00-00", 1.0),
            # never delivered: hot on creation recency alone
            ("c", "synthetic c", "conversation", "2026-09-24T00-00-00",
             "hot", 0.6, None, 0.0),
            ("d", "synthetic d", "conversation", "2025-01-01T00-00-00",
             "cold", 0.01, None, 0.0),
            ("e", "synthetic e", "reflection", "2025-01-01T00-00-00",
             "cold", 0.02, None, None),
        ],
    )
    conn.commit()
    conn.close()
    return path


class TestReporting:
    def test_distribution_counts_by_tier(self, store):
        assert rebuild_tiers.distribution(store) == {"hot": 3, "cold": 2}

    def test_a_missing_store_is_empty_rather_than_an_error(self, tmp_path):
        assert rebuild_tiers.distribution(tmp_path / "absent.db") == {}

    def test_delivery_signal_counts_separate_the_two_columns(self, store):
        counts = rebuild_tiers.delivery_signal_counts(store)
        assert counts == {"rows": 5, "with_last_retrieved": 2,
                          "with_frequency": 2}

    def test_tier_by_id_is_a_full_map(self, store):
        assert rebuild_tiers.tier_by_id(store) == {
            "a": "hot", "b": "hot", "c": "hot", "d": "cold", "e": "cold"
        }


class TestResetIsNarrow:
    def test_it_reports_only_the_rows_it_changed(self, store):
        """Two rows carry a signal; three do not and must not be counted."""
        assert rebuild_tiers.reset_delivery_signal(store) == 2

    def test_it_clears_both_delivery_columns(self, store):
        rebuild_tiers.reset_delivery_signal(store)
        conn = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT count(*) FROM vectors WHERE last_retrieved_at IS NOT NULL "
                "OR COALESCE(frequency_score, 0) != 0").fetchone()[0]
        finally:
            conn.close()
        assert rows == 0

    def test_it_leaves_everything_else_alone(self, store):
        """created_at in particular: recency falls back to it once the
        delivery signal is gone, so damaging it would silently change
        every heat score."""
        def snapshot():
            conn = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
            try:
                return {r[0]: (r[1], r[2], r[3])
                        for r in conn.execute(
                            "SELECT id, text, memory_type, created_at FROM vectors")}
            finally:
                conn.close()

        before = snapshot()
        rebuild_tiers.reset_delivery_signal(store)
        assert snapshot() == before

    def test_running_it_twice_changes_nothing_the_second_time(self, store):
        assert rebuild_tiers.reset_delivery_signal(store) == 2
        assert rebuild_tiers.reset_delivery_signal(store) == 0


class TestItRefusesWithoutASafeSnapshot:
    def _run(self, monkeypatch, argv):
        monkeypatch.setattr(sys, "argv", ["rebuild_tiers.py"] + argv)
        return rebuild_tiers.main()

    def test_a_real_run_requires_an_artefacts_directory(self, monkeypatch):
        assert self._run(monkeypatch, []) == 2

    def test_an_artefacts_directory_inside_the_repository_is_refused(
        self, monkeypatch
    ):
        """The snapshot of a personal-vault store must not land in the tree."""
        assert self._run(
            monkeypatch,
            ["--artefacts", str(REPO_ROOT / "logs" / "tier_rebuild")],
        ) == 2

    def test_dry_run_needs_nothing_and_writes_nothing(self, monkeypatch):
        called = []
        monkeypatch.setattr(rebuild_tiers, "reset_delivery_signal",
                            lambda path: called.append(path))
        assert self._run(monkeypatch, ["--dry-run"]) == 0
        assert called == []
