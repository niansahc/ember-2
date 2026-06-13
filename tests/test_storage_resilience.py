"""
tests/test_storage_resilience.py

Regression tests for A3: a single corrupt vault JSON file must not crash a
collection read. Before A3, read_memories called storage.read_json (bare
json.load) so one unreadable file raised out of the whole read path and took
down the chat request.

Uses the conftest session vault override; the corrupt/valid fixtures are
written into the test vault and cleaned up afterward so other tests are
unaffected. Synthetic data only (vault privacy rule).
"""

import json

from src.core.config import get_private_vault_path
from src.memory.read_memory import read_memories


def _journal_record(text: str) -> str:
    return json.dumps(
        {
            "id": "2030-01-01T00-00-00",
            "timestamp": "2030-01-01T00-00-00",
            "type": "journal",
            "text": text,
            "source": "test",
            "tags": ["journal"],
            "metadata": {},
        }
    )


def test_read_memories_skips_corrupt_file():
    jdir = get_private_vault_path() / "memory" / "journal"
    jdir.mkdir(parents=True, exist_ok=True)
    ok = jdir / "2030-01-01T00-00-00_a3ok.json"
    bad = jdir / "2030-01-01T00-00-01_a3corrupt.json"
    ok.write_text(_journal_record("a3 valid record"), encoding="utf-8")
    bad.write_text("{ this is not valid json", encoding="utf-8")
    try:
        records = read_memories(memory_type="journal", limit=100)
        texts = [r.get("text") for r in records if isinstance(r, dict)]
        assert "a3 valid record" in texts
    finally:
        ok.unlink(missing_ok=True)
        bad.unlink(missing_ok=True)
