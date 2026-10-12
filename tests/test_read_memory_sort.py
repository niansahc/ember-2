"""
tests/test_read_memory_sort.py

read_memories() returns records newest first by their timestamp field.
_memory_sort_key() only understood ISO 8601 and epoch values, so a record in
the vault's canonical hyphenated form (YYYY-MM-DDTHH-MM-SS[-ffffff]) got key
0.0: hyphenated records sorted after every ISO record, and among themselves
fell back to reverse filename order. The key now parses through
src.core.timestamps.parse_vault_timestamp.

Each test writes synthetic records into its own tmp vault.
"""

from __future__ import annotations

import json

import pytest

import src.memory.read_memory as read_memory
from src.core.config import vault_binding

# Filenames deliberately disagree with timestamp order, so a sort that
# falls back to filename order gives a different answer.
RECORDS = [
    ("a.json", "newest_hyphen", "2026-03-03T10-00-00"),
    ("b.json", "oldest_iso", "2026-01-01T10:00:00"),
    ("c.json", "middle_hyphen_us", "2026-02-02T10-00-00-000000"),
    ("d.json", "second_iso_z", "2026-02-20T10:00:00Z"),
]


@pytest.fixture
def journal_vault(tmp_path):
    folder = tmp_path / "memory" / "journal"
    folder.mkdir(parents=True)
    for filename, record_id, timestamp in RECORDS:
        (folder / filename).write_text(json.dumps({
            "id": record_id, "timestamp": timestamp, "type": "journal",
            "text": f"Synthetic journal record {record_id}.", "source": "test",
            "tags": [], "metadata": {},
        }), encoding="utf-8")
    with vault_binding(tmp_path):
        yield tmp_path


def test_mixed_hyphenated_and_iso_records_come_back_newest_first(journal_vault):
    records = read_memory.read_memories(memory_type="journal", limit=None)

    assert [r["id"] for r in records] == [
        "newest_hyphen", "second_iso_z", "middle_hyphen_us", "oldest_iso",
    ]


def test_limit_keeps_the_newest_by_timestamp(journal_vault):
    records = read_memory.read_memories(memory_type="journal", limit=2)

    assert [r["id"] for r in records] == ["newest_hyphen", "second_iso_z"]


@pytest.mark.parametrize("record, expected", [
    ({"timestamp": "2026-01-01T00-00-00"}, 1767225600.0),
    ({"timestamp": "2026-01-01T00-00-00-500000"}, 1767225600.5),
    ({"timestamp": "2026-01-01T00:00:00Z"}, 1767225600.0),
    ({"timestamp": "1767225600.25"}, 1767225600.25),
    ({"timestamp": 1767225600}, 1767225600.0),
    ({"timestamp": 1767225600.25}, 1767225600.25),
    ({"timestamp": "not a timestamp"}, 0.0),
    ({}, 0.0),
])
def test_sort_key(record, expected):
    assert read_memory._memory_sort_key(record) == expected
