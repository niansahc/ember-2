"""
tests/test_exchange_recorder.py

Unit tests for ExchangeRecorder (src/memory/exchange.py, ADR-047, plan v3 Q7).

One recorder per exchange. It writes the user turn and the conversation record,
then finishes the exchange exactly once: with the assistant turn
(record_reply) or with an exchange outcome (record_outcome). The first finish
wins; a second finish writes nothing. Terms follow CONTEXT.md.

Every test drives the recorder against a real temporary vault (no filesystem
mocks). Embedding is stubbed and index jobs run synchronously so the memory.db
assertions are deterministic. Every absence assertion has a positive control.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading

import pytest

from src.core.config import VaultWriteBlocked, vault_binding

EXCHANGE_ID = "chatcmpl-test-exchange-001"
SESSION_ID = "sess_test_recorder_001"
USER_TEXT = "What did we decide about the retrieval pipeline last week?"
REPLY_TEXT = "You decided to keep the composition frame and drop the term count."


@pytest.fixture
def spawned(monkeypatch):
    """Run index jobs synchronously and record each spawn's thread name."""
    names: list[str] = []

    def _sync_spawn(target, args=(), vault=None, name=None):
        names.append(name)
        with vault_binding(vault):
            target(*args)

    monkeypatch.setattr("src.memory.exchange.spawn_vault_bound_thread", _sync_spawn)
    return names


@pytest.fixture
def vault(tmp_path, monkeypatch, spawned):
    for sub in ("memory", "embeddings"):
        (tmp_path / sub).mkdir()
    monkeypatch.setattr(
        "src.memory.write_memory.embed_text", lambda _t: [0.0] * 768,
    )
    with vault_binding(tmp_path):
        yield tmp_path


def _recorder(vault, **kwargs):
    from src.memory.exchange import ExchangeRecorder

    kwargs.setdefault("project_id", None)
    kwargs.setdefault("enabled", True)
    return ExchangeRecorder(EXCHANGE_ID, SESSION_ID, vault=vault, **kwargs)


def _records(vault, memory_type: str) -> list[dict]:
    folder = vault / "memory" / memory_type
    if not folder.exists():
        return []
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(folder.glob("*.json"))
    ]


def _row_ids(vault) -> set[str]:
    db = vault / "embeddings" / "memory.db"
    if not db.exists():
        return set()
    conn = sqlite3.connect(str(db))
    try:
        return {r[0] for r in conn.execute("SELECT id FROM vectors")}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The user turn and the conversation record
# ---------------------------------------------------------------------------

def test_user_turn_stores_the_sent_text_with_the_exchange_id(vault):
    rec = _recorder(vault, project_id="proj_test_001")
    assert rec.record_user_turn(USER_TEXT) is True

    [turn] = _records(vault, "conversation")
    assert turn["text"] == USER_TEXT
    assert turn["metadata"]["role"] == "user"
    assert turn["metadata"]["content_kind"] == "user_content"
    assert turn["metadata"]["session_id"] == SESSION_ID
    assert turn["metadata"]["exchange_id"] == EXCHANGE_ID
    assert turn["metadata"]["project_id"] == "proj_test_001"
    assert "image_count" not in turn["metadata"]


def test_first_user_turn_creates_the_conversation_record_titled_from_it(vault):
    _recorder(vault).record_user_turn(USER_TEXT)

    [conv] = _records(vault, "session")
    assert conv["metadata"]["session_id"] == SESSION_ID
    assert conv["text"] == "What did we decide about the retrieval pipeline..."


def test_user_record_is_written_before_the_conversation_record(vault, monkeypatch):
    seen: list[int] = []
    from src.memory import exchange

    real_create = exchange.create_session

    def _create(session_id, title, **kw):
        seen.append(len(_records(vault, "conversation")))
        return real_create(session_id, title, **kw)

    monkeypatch.setattr(exchange, "create_session", _create)
    _recorder(vault).record_user_turn(USER_TEXT)
    assert seen == [1]


def test_existing_conversation_gets_no_second_conversation_record(vault):
    _recorder(vault).record_user_turn(USER_TEXT)
    _recorder(vault).record_user_turn("A follow-up question about the same topic.")
    assert len(_records(vault, "session")) == 1
    assert len(_records(vault, "conversation")) == 2


def test_image_only_user_turn_stores_empty_text_and_the_image_count(vault):
    from src.memory.exchange import DEFAULT_CONVERSATION_TITLE

    assert _recorder(vault).record_user_turn("", image_count=2) is True
    [turn] = _records(vault, "conversation")
    assert turn["text"] == ""
    assert turn["metadata"]["image_count"] == 2
    [conv] = _records(vault, "session")
    assert conv["text"] == DEFAULT_CONVERSATION_TITLE


def test_empty_text_without_images_writes_nothing(vault):
    assert _recorder(vault).record_user_turn("   ") is False
    assert _records(vault, "conversation") == []
    assert _records(vault, "session") == []


def test_disabled_recorder_writes_nothing(vault):
    rec = _recorder(vault, enabled=False)
    rec.record_user_turn(USER_TEXT)
    rec.record_reply(REPLY_TEXT)
    rec.record_outcome("failed", "RuntimeError")
    for memory_type in ("conversation", "session", "system_event"):
        assert _records(vault, memory_type) == []


def test_enabled_recorder_writes_turns_and_conversation_record(vault):
    """Positive control for the disabled recorder."""
    rec = _recorder(vault)
    rec.record_user_turn(USER_TEXT)
    rec.record_reply(REPLY_TEXT)
    assert len(_records(vault, "conversation")) == 2
    assert len(_records(vault, "session")) == 1


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------

def test_user_turn_is_not_indexed_until_ensure_user_indexed(vault):
    rec = _recorder(vault)
    rec.record_user_turn(USER_TEXT)
    [turn] = _records(vault, "conversation")
    assert _row_ids(vault) == set()
    rec.ensure_user_indexed()
    assert _row_ids(vault) == {turn["id"]}


def test_ensure_user_indexed_schedules_one_index_job(vault, spawned):
    rec = _recorder(vault)
    rec.record_user_turn(USER_TEXT)
    rec.ensure_user_indexed()
    rec.ensure_user_indexed()
    assert spawned.count("exchange-index") == 1


def test_reply_is_indexed_when_recorded(vault):
    rec = _recorder(vault)
    rec.record_user_turn(USER_TEXT)
    rec.record_reply(REPLY_TEXT)
    reply = [r for r in _records(vault, "conversation") if r["metadata"]["role"] == "assistant"]
    assert _row_ids(vault) == {reply[0]["id"]}


def test_code_fence_reply_is_stored_but_not_indexed(vault):
    rec = _recorder(vault)
    rec.record_user_turn(USER_TEXT)
    rec.record_reply("Try this:\n```python\nprint('hi')\n```")
    assert len(_records(vault, "conversation")) == 2
    assert _row_ids(vault) == set()


def test_index_failure_is_logged_and_the_record_stays(vault, monkeypatch, caplog):
    def _boom(_t):
        raise ConnectionError("embedding server down")

    monkeypatch.setattr("src.memory.write_memory.embed_text", _boom)
    rec = _recorder(vault)
    rec.record_user_turn(USER_TEXT)
    with caplog.at_level(logging.WARNING, logger="ember.exchange"):
        rec.ensure_user_indexed()
    assert len(_records(vault, "conversation")) == 1
    assert "[EXCHANGE] index failed" in caplog.text
    assert "embedding server down" not in caplog.text


# ---------------------------------------------------------------------------
# Finishing the exchange: first finish wins
# ---------------------------------------------------------------------------

def test_reply_then_outcome_writes_no_outcome(vault, caplog):
    rec = _recorder(vault)
    rec.record_user_turn(USER_TEXT)
    assert rec.record_reply(REPLY_TEXT) is True
    with caplog.at_level(logging.WARNING, logger="ember.exchange"):
        assert rec.record_outcome("interrupted", None) is False
    assert _records(vault, "system_event") == []
    assert "[EXCHANGE] already finished" in caplog.text


def test_outcome_then_reply_writes_no_reply(vault):
    rec = _recorder(vault)
    rec.record_user_turn(USER_TEXT)
    assert rec.record_outcome("failed", "RuntimeError") is True
    assert rec.record_reply(REPLY_TEXT) is False
    roles = [r["metadata"]["role"] for r in _records(vault, "conversation")]
    assert roles == ["user"]


def test_first_finish_writes_the_outcome_record(vault):
    """Positive control for the two second-finish tests above."""
    rec = _recorder(vault)
    rec.record_user_turn(USER_TEXT)
    rec.record_outcome("failed", "RuntimeError")
    [outcome] = _records(vault, "system_event")
    assert outcome["metadata"] == {
        "kind": "exchange_outcome",
        "session_id": SESSION_ID,
        "exchange_id": EXCHANGE_ID,
        "outcome": "failed",
        "reason": "RuntimeError",
    }


def test_outcome_record_is_never_indexed(vault):
    rec = _recorder(vault)
    rec.record_user_turn(USER_TEXT)
    rec.ensure_user_indexed()
    rec.record_outcome("interrupted", None)
    [turn] = _records(vault, "conversation")
    assert _row_ids(vault) == {turn["id"]}


def test_no_outcome_without_a_stored_user_turn(vault):
    rec = _recorder(vault)
    assert rec.record_outcome("failed", "RuntimeError") is False
    assert _records(vault, "system_event") == []


def test_concurrent_finishes_store_exactly_one(vault):
    rec = _recorder(vault)
    rec.record_user_turn(USER_TEXT)
    barrier = threading.Barrier(2)
    results: list[bool] = []

    def _reply():
        with vault_binding(vault):
            barrier.wait()
            results.append(rec.record_reply(REPLY_TEXT))

    def _outcome():
        with vault_binding(vault):
            barrier.wait()
            results.append(rec.record_outcome("interrupted", None))

    threads = [threading.Thread(target=_reply), threading.Thread(target=_outcome)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert sorted(results) == [False, True]
    replies = [r for r in _records(vault, "conversation") if r["metadata"]["role"] == "assistant"]
    assert len(replies) + len(_records(vault, "system_event")) == 1


def test_record_failure_indexes_the_user_turn_and_records_failed(vault):
    rec = _recorder(vault)
    rec.record_user_turn(USER_TEXT)
    rec.record_failure(RuntimeError("model server down"))
    [turn] = _records(vault, "conversation")
    assert _row_ids(vault) == {turn["id"]}
    [outcome] = _records(vault, "system_event")
    assert outcome["metadata"]["outcome"] == "failed"
    assert outcome["metadata"]["reason"] == "RuntimeError"


# ---------------------------------------------------------------------------
# Storage failures
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cause", [OSError("disk full"), VaultWriteBlocked("unverified swap")])
def test_user_record_write_failure_raises_exchange_storage_error(vault, monkeypatch, cause):
    from src.memory import exchange

    def _fail(**_kw):
        raise cause

    monkeypatch.setattr(exchange, "write_canonical_record", _fail)
    rec = _recorder(vault)
    with pytest.raises(exchange.ExchangeStorageError) as err:
        rec.record_user_turn(USER_TEXT)
    assert err.value.cause_type == type(cause).__name__
    assert rec.user_turn_stored is False


def test_conversation_record_failure_keeps_the_stored_user_turn(vault, monkeypatch):
    from src.memory import exchange

    def _fail(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(exchange, "create_session", _fail)
    rec = _recorder(vault)
    with pytest.raises(exchange.ExchangeStorageError):
        rec.record_user_turn(USER_TEXT)
    assert rec.user_turn_stored is True
    assert len(_records(vault, "conversation")) == 1


def test_failed_reply_write_leaves_the_exchange_open_for_an_outcome(vault, monkeypatch):
    from src.memory import exchange

    rec = _recorder(vault)
    rec.record_user_turn(USER_TEXT)
    real_write = exchange.write_canonical_record

    def _fail(**_kw):
        raise OSError("disk full")

    monkeypatch.setattr(exchange, "write_canonical_record", _fail)
    with pytest.raises(exchange.ExchangeStorageError) as err:
        rec.record_reply(REPLY_TEXT)
    assert err.value.step == "reply"
    monkeypatch.setattr(exchange, "write_canonical_record", real_write)
    assert rec.record_outcome("failed", err.value.cause_type) is True
    assert len(_records(vault, "system_event")) == 1
