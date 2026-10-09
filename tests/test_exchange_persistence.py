"""
tests/test_exchange_persistence.py

End-to-end exchange persistence through POST /v1/chat/completions (ADR-047,
plan v3). Terms follow CONTEXT.md: an exchange is a user turn plus everything
Ember does in response; it ends in exactly one assistant turn or exactly one
exchange outcome.

Each test runs against a fresh temporary vault (set as the runtime override,
the same mechanism conftest uses for the session vault), with real record
writes. Generation, retrieval, classification and embedding are stubbed so no
model is called; index jobs run synchronously so memory.db assertions are
deterministic. The post-exchange extractors are stubbed unless a test is about
them. Every absence assertion has a positive control in this file.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.conftest import synthetic_packet

SESSION_ID = "sess_test_exchange_001"
USER_TEXT = "hello there"
PLAIN_REPLY = "A plain reply with nothing unusual in it at all."
CODE_FENCE_REPLY = "Run this:\n```python\nprint('hi')\n```"
OFFER_REPLY = "That is not in your vault yet. Want me to search the web for it?"
IMAGE_URL = "data:image/png;base64,iVBORw0KGgo="

EXTRACTORS = (
    "_background_state_extraction",
    "_background_topic_decline_resolution",
    "_detect_and_write_commitment",
    "_detect_task_in_response",
    "_background_deviation_detection",
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path, monkeypatch):
    """A fresh vault for this test, set as the runtime override."""
    from src.core import config

    for sub in ("memory/conversation", "memory/session", "memory/state", "embeddings"):
        (tmp_path / sub).mkdir(parents=True)
    previous = (config._vault_path_override, config._vault_label)
    config.set_vault_path_override(str(tmp_path), "test")
    monkeypatch.setattr("src.memory.write_memory.embed_text", lambda _t: [0.0] * 768)

    def _sync_spawn(target, args=(), vault=None, name=None):
        with config.vault_binding(vault):
            target(*args)

    # Exists only once the recorder does; raising=False keeps the fixture
    # usable when these tests are run against the code before the change.
    monkeypatch.setattr(
        "src.memory.exchange.spawn_vault_bound_thread", _sync_spawn, raising=False,
    )
    yield tmp_path
    if previous[0] is None:
        config.clear_vault_path_override()
    else:
        config.set_vault_path_override(*previous)


@pytest.fixture
def client(vault):
    with patch("src.api.main.get_ember_api_key", return_value=None):
        from src.api.main import app
        yield TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="module")
def casual_policy():
    """The real policy for a plain greeting (Stage 1, no model call)."""
    from src.context.policies import classify_query

    return classify_query(USER_TEXT)


def _chat(
    client,
    content=USER_TEXT,
    *,
    policy=None,
    stream: bool = True,
    fast: bool = False,
    reply: str = PLAIN_REPLY,
    gen_error: BaseException | None = None,
    vault_enabled: bool = True,
    stub_extractors: bool = True,
    extra=(),
):
    """POST one chat message to the test conversation and return the response."""
    from src.api import openai_adapter as oa

    def _iter(*_a, **_k):
        if gen_error is not None:
            raise gen_error
        return iter([reply])

    def _stream(*_a, **_k):
        if gen_error is not None:
            raise gen_error
        yield reply

    def _full(*_a, **_k):
        if gen_error is not None:
            raise gen_error
        return reply

    with ExitStack() as stack:
        stack.enter_context(patch.object(
            oa.context_service, "build_context", return_value=synthetic_packet(),
        ))
        stack.enter_context(patch.object(oa.llm_adapter, "generate_response_iter", side_effect=_iter))
        stack.enter_context(patch.object(oa.llm_adapter, "generate_response_stream", side_effect=_stream))
        stack.enter_context(patch.object(oa.llm_adapter, "generate_response", side_effect=_full))
        stack.enter_context(patch(
            "src.safety.grounding_check.run_grounding_check", return_value=(True, None),
        ))
        # Identity: the coaching filter's semantic check would call a model.
        stack.enter_context(patch(
            "src.llm.coaching_filter.filter_coaching_frame",
            side_effect=lambda text, *_a, **_k: text,
        ))
        if policy is not None:
            stack.enter_context(patch("src.context.policies.classify_query", return_value=policy))
        onb = stack.enter_context(patch("src.api.openai_adapter.onboarding_service"))
        onb.is_active.return_value = False
        if stub_extractors:
            for name in EXTRACTORS:
                stack.enter_context(patch(f"src.api.openai_adapter.{name}"))
        if fast:
            stack.enter_context(patch.object(oa, "_STREAM_ALWAYS_GROUNDED", False))
        for p in extra:
            stack.enter_context(p)
        return client.post(
            "/v1/chat/completions",
            json={
                "model": "ember-2",
                "messages": [{"role": "user", "content": content}],
                "stream": stream,
                "vault_enabled": vault_enabled,
            },
            headers={"X-Session-ID": SESSION_ID},
        )


def _records(vault, memory_type: str) -> list[dict]:
    folder = vault / "memory" / memory_type
    if not folder.exists():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]


def _turns(vault, role: str | None = None) -> list[dict]:
    turns = [r for r in _records(vault, "conversation")
             if r["metadata"].get("session_id") == SESSION_ID]
    if role:
        turns = [t for t in turns if t["metadata"].get("role") == role]
    return turns


def _outcomes(vault) -> list[dict]:
    return [r for r in _records(vault, "system_event")
            if r["metadata"].get("kind") == "exchange_outcome"]


def _row_ids(vault) -> set[str]:
    db = vault / "embeddings" / "memory.db"
    if not db.exists():
        return set()
    conn = sqlite3.connect(str(db))
    try:
        return {r[0] for r in conn.execute("SELECT id FROM vectors")}
    finally:
        conn.close()


def _history(client) -> list[dict]:
    resp = client.get(f"/v1/conversations/{SESSION_ID}")
    assert resp.status_code == 200, resp.status_code
    return resp.json()["turns"]


def _sse_events(text: str) -> list:
    out: list = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[len("data:"):].strip()
            out.append("[DONE]" if payload == "[DONE]" else json.loads(payload))
    return out


def _chunk_ids(text: str) -> set[str]:
    return {e["id"] for e in _sse_events(text) if isinstance(e, dict) and "choices" in e}


# ---------------------------------------------------------------------------
# Code fences and bracket starts reach history (v3 section 7, "Code fences")
# ---------------------------------------------------------------------------

def test_code_fence_reply_is_in_history(client, vault, casual_policy):
    _chat(client, policy=casual_policy, reply=CODE_FENCE_REPLY)
    history = _history(client)
    assert [t["role"] for t in history] == ["user", "assistant"]
    assert history[1]["content"] == CODE_FENCE_REPLY


def test_user_message_starting_with_a_brace_is_in_history(client, vault, casual_policy):
    message = '{"note": "a pasted snippet"}'
    _chat(client, message, policy=casual_policy)
    assert [t["content"] for t in _history(client) if t["role"] == "user"] == [message]


def test_code_fence_reply_has_no_index_row(client, vault, casual_policy):
    _chat(client, policy=casual_policy, reply=CODE_FENCE_REPLY)
    [reply] = _turns(vault, "assistant")
    assert reply["id"] not in _row_ids(vault)


def test_plain_reply_has_an_index_row(client, vault, casual_policy):
    """Positive control for the code-fence absence above."""
    _chat(client, policy=casual_policy)
    [reply] = _turns(vault, "assistant")
    assert reply["id"] in _row_ids(vault)


# ---------------------------------------------------------------------------
# The user turn is never lost (v3 section 7, "Lost user turn", decision A)
# ---------------------------------------------------------------------------

def test_generation_failure_keeps_the_user_turn_and_records_failed(client, vault, casual_policy):
    resp = _chat(client, policy=casual_policy, gen_error=RuntimeError("model server down"))
    assert resp.status_code == 200
    [turn] = _turns(vault)
    assert turn["metadata"]["role"] == "user"
    assert turn["text"] == USER_TEXT
    [outcome] = _outcomes(vault)
    assert outcome["metadata"]["outcome"] == "failed"
    assert outcome["metadata"]["reason"] == "RuntimeError"


def test_success_writes_the_assistant_turn(client, vault, casual_policy):
    """Positive control: no assistant turn on failure, one on success."""
    _chat(client, policy=casual_policy)
    assert [t["text"] for t in _turns(vault, "assistant")] == [PLAIN_REPLY]
    assert _outcomes(vault) == []


def test_build_context_failure_keeps_an_indexed_user_turn(client, vault, casual_policy):
    from src.api import openai_adapter as oa

    resp = _chat(client, policy=casual_policy, extra=(
        patch.object(oa.context_service, "build_context", side_effect=RuntimeError("boom")),
    ))
    assert resp.status_code == 500
    [turn] = _turns(vault)
    assert turn["text"] == USER_TEXT
    assert turn["id"] in _row_ids(vault)
    assert [o["metadata"]["outcome"] for o in _outcomes(vault)] == ["failed"]


def _prefix_task_note(gen_ctx, work):
    work.message = f'[System: tasks created - "water the plants"] {work.message}'


def test_task_prefixed_turn_stores_the_sent_text(client, vault, casual_policy):
    from src.api import openai_adapter as oa

    message = "create a task to water the plants"
    with patch.object(oa.context_service, "build_context", return_value=synthetic_packet()) as bc:
        _chat(client, message, policy=casual_policy, extra=(
            patch("src.api.openai_adapter._apply_tasks", side_effect=_prefix_task_note),
            patch.object(oa.context_service, "build_context", bc),
        ))
    assert [t["text"] for t in _turns(vault, "user")] == [message]
    # Positive control: the model's input does carry Ember's note.
    assert bc.call_args.args[0].startswith("[System: tasks created")


def test_confirmed_yes_turn_stores_yes(client, vault, casual_policy):
    pending = ({"confirmed": True, "action": "web_search", "query": "the original question"}, [])
    _chat(client, "yes", policy=casual_policy, extra=(
        patch("src.api.openai_adapter._check_pending_confirmation", return_value=pending),
        patch("src.tools.web_search.web_search", return_value=[]),
    ))
    assert [t["text"] for t in _turns(vault, "user")] == ["yes"]


def test_vault_off_writes_no_conversation_record_turn_or_outcome(client, vault, casual_policy):
    _chat(client, policy=casual_policy, vault_enabled=False, gen_error=RuntimeError("x"))
    assert _records(vault, "session") == []
    assert _turns(vault) == []
    assert _outcomes(vault) == []


def test_vault_on_writes_conversation_record_turn_and_outcome(client, vault, casual_policy):
    """Positive control for the vault-off absence above."""
    _chat(client, policy=casual_policy, gen_error=RuntimeError("x"))
    assert len(_records(vault, "session")) == 1
    assert len(_turns(vault, "user")) == 1
    assert len(_outcomes(vault)) == 1


def test_clarification_stores_the_user_turn_once_and_its_reply(client, vault):
    """No policy stub: 'google please' is a bare marker (Stage 0, no model)."""
    from src.context.policies import SCRIPTED_CLARIFICATION_RESPONSE

    _chat(client, "google please", stream=False)
    assert [t["text"] for t in _turns(vault, "user")] == ["google please"]
    [reply] = _turns(vault, "assistant")
    assert reply["text"] == SCRIPTED_CLARIFICATION_RESPONSE
    assert reply["metadata"]["awaiting_search_content"] is True
    assert reply["metadata"]["exchange_id"] == _turns(vault, "user")[0]["metadata"]["exchange_id"]


# ---------------------------------------------------------------------------
# Writes happen before [DONE] (v3 section 7, "[DONE] before writes")
# ---------------------------------------------------------------------------

def test_both_turns_exist_when_done_is_sent(client, vault, casual_policy):
    from src.api.sse import sse_done as real_done

    seen: list[list[str]] = []

    def _done():
        seen.append([t["metadata"]["role"] for t in _turns(vault)])
        return real_done()

    _chat(client, policy=casual_policy, extra=(
        patch("src.api.openai_adapter.sse_done", side_effect=_done),
    ))
    assert seen == [["user", "assistant"]]


def test_order_is_user_turn_generation_reply_done(client, vault, casual_policy):
    from src.api import openai_adapter as oa
    from src.api.sse import sse_done as real_done
    from src.memory import write_memory as wm

    events: list[str] = []
    real_write = wm.storage.write_json

    def _write(path, data):
        if data.get("type") == "conversation":
            events.append(f"write:{data['metadata'].get('role')}")
        return real_write(path, data)

    def _iter(*_a, **_k):
        events.append("generate")
        return iter([PLAIN_REPLY])

    def _done():
        events.append("done")
        return real_done()

    _chat(client, policy=casual_policy, extra=(
        patch.object(wm.storage, "write_json", side_effect=_write),
        patch.object(oa.llm_adapter, "generate_response_iter", side_effect=_iter),
        patch("src.api.openai_adapter.sse_done", side_effect=_done),
    ))
    assert events == ["write:user", "generate", "write:assistant", "done"]


# ---------------------------------------------------------------------------
# Cleanup failures (v3 section 7, "Cleanup exception")
# ---------------------------------------------------------------------------

def test_embedding_failure_during_indexing_keeps_both_turns(client, vault, casual_policy, monkeypatch):
    def _boom(_t):
        raise ConnectionError("embedding server down")

    monkeypatch.setattr("src.memory.write_memory.embed_text", _boom)
    _chat(client, policy=casual_policy)
    assert [t["role"] for t in _history(client)] == ["user", "assistant"]


def test_commitment_start_failure_does_not_skip_the_other_extractors(client, vault, casual_policy):
    from src.api import openai_adapter as oa

    started: list[str] = []

    def _spawn(target, args=(), vault=None, name=None):
        if target is oa._detect_and_write_commitment:
            raise RuntimeError("thread start failed")
        started.append(target.__name__)

    _chat(client, policy=casual_policy, stub_extractors=False, extra=(
        patch("src.api.openai_adapter.spawn_vault_bound_thread", side_effect=_spawn),
    ))
    assert "_detect_task_in_response" in started
    assert "_background_state_extraction" in started  # control: extractors started


def test_outcome_record_is_not_in_history(client, vault, casual_policy):
    _chat(client, policy=casual_policy, gen_error=RuntimeError("x"))
    assert [t["role"] for t in _history(client)] == ["user"]


def test_outcome_record_file_exists(client, vault, casual_policy):
    """Positive control for the history absence above."""
    _chat(client, policy=casual_policy, gen_error=RuntimeError("x"))
    assert len(_outcomes(vault)) == 1


# ---------------------------------------------------------------------------
# Decision D: the pending confirmation is written before [DONE]
# ---------------------------------------------------------------------------

def _pending(vault) -> list:
    """pending_confirmation records in the test vault, read the way the
    confirmation path reads them."""
    from src.state.state_service import StateService

    return StateService().read_by_category("pending_confirmation")


@pytest.mark.parametrize("fast", [False, True], ids=["grounded", "fast"])
def test_pending_confirmation_exists_when_done_is_sent(client, vault, casual_policy, fast):
    from src.api.sse import sse_done as real_done

    seen: list[int] = []

    def _done():
        seen.append(len(_pending(vault)))
        return real_done()

    _chat(client, policy=casual_policy, fast=fast, reply=OFFER_REPLY, extra=(
        patch("src.api.openai_adapter.sse_done", side_effect=_done),
    ))
    assert seen == [1]


def test_no_pending_confirmation_without_an_offer(client, vault, casual_policy):
    _chat(client, policy=casual_policy)
    assert _pending(vault) == []


def test_no_pending_confirmation_with_the_vault_off(client, vault, casual_policy):
    _chat(client, policy=casual_policy, reply=OFFER_REPLY, vault_enabled=False)
    assert _pending(vault) == []


def test_offer_with_the_vault_on_writes_one_pending_confirmation(client, vault, casual_policy):
    """Positive control for both pending-confirmation absences above."""
    _chat(client, policy=casual_policy, reply=OFFER_REPLY)
    assert len(_pending(vault)) == 1


# ---------------------------------------------------------------------------
# Q1: the response id is the exchange id
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fast", [False, True], ids=["grounded", "fast"])
def test_stream_response_id_is_the_exchange_id(client, vault, casual_policy, fast):
    resp = _chat(client, policy=casual_policy, fast=fast)
    ids = {t["metadata"].get("exchange_id") for t in _turns(vault)}
    assert len(ids) == 1
    assert _chunk_ids(resp.text) == ids


def test_non_stream_response_id_is_the_exchange_id(client, vault, casual_policy):
    resp = _chat(client, policy=casual_policy, stream=False)
    ids = {t["metadata"].get("exchange_id") for t in _turns(vault)}
    assert ids == {resp.json()["id"]}


# ---------------------------------------------------------------------------
# Q3: every ending leaves exactly one assistant turn or one outcome
# ---------------------------------------------------------------------------

def _ending(name, client, casual_policy):
    from src.api import openai_adapter as oa

    if name == "success_grounded":
        return _chat(client, policy=casual_policy)
    if name == "success_fast":
        return _chat(client, policy=casual_policy, fast=True)
    if name == "success_non_stream":
        return _chat(client, policy=casual_policy, stream=False)
    if name == "clarification":
        return _chat(client, "google please")
    if name == "generation_fails_in_stream":
        return _chat(client, policy=casual_policy, gen_error=RuntimeError("x"))
    if name == "generation_fails_non_stream":
        return _chat(client, policy=casual_policy, stream=False, gen_error=RuntimeError("x"))
    if name == "raise_before_stream":
        return _chat(client, policy=casual_policy, extra=(
            patch.object(oa.context_service, "build_context", side_effect=RuntimeError("x")),
        ))
    if name == "vision_unavailable":
        return _chat(client, _image_content(""), policy=casual_policy, extra=(
            patch.object(oa.vision_service, "analyze", return_value=None),
        ))
    raise AssertionError(name)


ENDINGS = (
    "success_grounded", "success_fast", "success_non_stream", "clarification",
    "generation_fails_in_stream", "generation_fails_non_stream",
    "raise_before_stream", "vision_unavailable",
)
FAILING_ENDINGS = {
    "generation_fails_in_stream", "generation_fails_non_stream",
    "raise_before_stream", "vision_unavailable",
}


@pytest.mark.parametrize("ending", ENDINGS)
def test_every_ending_leaves_one_reply_or_one_outcome(client, vault, casual_policy, ending):
    _ending(ending, client, casual_policy)
    users = _turns(vault, "user")
    assert len(users) == 1
    exchange_id = users[0]["metadata"].get("exchange_id")
    assert exchange_id
    replies = [t for t in _turns(vault, "assistant") if t["metadata"].get("exchange_id") == exchange_id]
    outcomes = [o for o in _outcomes(vault) if o["metadata"].get("exchange_id") == exchange_id]
    assert len(replies) + len(outcomes) == 1
    # Positive control within the parametrization: success never has an
    # outcome, and every failing ending does.
    assert bool(outcomes) is (ending in FAILING_ENDINGS)


# ---------------------------------------------------------------------------
# Q4, write side: an image-only message stores empty text plus the count
# ---------------------------------------------------------------------------

def _image_content(text: str) -> list[dict]:
    return [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": IMAGE_URL}},
    ]


def test_image_only_user_turn_stores_empty_text_and_image_count(client, vault, casual_policy):
    from src.api import openai_adapter as oa

    _chat(client, _image_content(""), policy=casual_policy, extra=(
        patch.object(oa.vision_service, "analyze", return_value="A red square on white."),
    ))
    [turn] = _turns(vault, "user")
    assert turn["text"] == ""
    assert turn["metadata"]["image_count"] == 1


def test_image_with_text_stores_the_text(client, vault, casual_policy):
    """Positive control: the placeholder only ever replaces empty text."""
    from src.api import openai_adapter as oa

    _chat(client, _image_content("what is this?"), policy=casual_policy, extra=(
        patch.object(oa.vision_service, "analyze", return_value="A red square on white."),
    ))
    [turn] = _turns(vault, "user")
    assert turn["text"] == "what is this?"


def test_vision_unavailable_stores_no_canned_reply(client, vault, casual_policy):
    from src.api import openai_adapter as oa
    from src.llm.vision_service import VISION_UNAVAILABLE_RESPONSE

    resp = _chat(client, _image_content(""), policy=casual_policy, extra=(
        patch.object(oa.vision_service, "analyze", return_value=None),
    ))
    assert VISION_UNAVAILABLE_RESPONSE in resp.text  # control: the client got it
    assert _turns(vault, "assistant") == []
    [outcome] = _outcomes(vault)
    assert outcome["metadata"]["reason"] == "vision_unavailable"


def test_user_turn_is_indexed_only_after_retrieval(client, vault, casual_policy):
    """The exchange's own retrieval cannot return its user turn (ADR-047):
    the user turn has no index row while build_context runs, and has one
    once the exchange is done."""
    from src.api import openai_adapter as oa

    rows_during_retrieval: list[set[str]] = []

    def _build(*_a, **_k):
        rows_during_retrieval.append(_row_ids(vault))
        return synthetic_packet()

    _chat(client, policy=casual_policy, extra=(
        patch.object(oa.context_service, "build_context", side_effect=_build),
    ))
    [turn] = _turns(vault, "user")
    assert rows_during_retrieval == [set()]
    assert turn["id"] in _row_ids(vault)


# ---------------------------------------------------------------------------
# Q10: a storage failure sends storage_failed, exactly once
# ---------------------------------------------------------------------------

STORAGE_CAUSES = pytest.mark.parametrize(
    "cause",
    ["oserror", "vault_write_blocked"],
)


def _cause(name: str) -> BaseException:
    from src.core.config import VaultWriteBlocked

    return OSError("disk full") if name == "oserror" else VaultWriteBlocked("unverified swap")


def _fail_writes_for(role: str, exc: BaseException):
    """Make the vault refuse the record with this metadata role, only."""
    from src.memory import write_memory as wm

    real = wm.write_canonical_record

    def _write(*args, **kwargs):
        if (kwargs.get("metadata") or {}).get("role") == role:
            raise exc
        return real(*args, **kwargs)

    return patch("src.memory.exchange.write_canonical_record", side_effect=_write)


def _errors(text: str) -> list[dict]:
    return [e for e in _sse_events(text) if isinstance(e, dict) and e.get("type") == "error"]


def _assert_storage_failed_once(resp):
    from src.api.sse import STORAGE_FAILED_CODE, STORAGE_FAILED_MESSAGE

    assert resp.status_code == 200
    events = _sse_events(resp.text)
    assert _errors(resp.text) == [
        {"type": "error", "code": STORAGE_FAILED_CODE, "message": STORAGE_FAILED_MESSAGE}
    ]
    assert events[-2]["choices"][0]["finish_reason"] == "stop"
    assert events[-1] == "[DONE]"
    assert events.count("[DONE]") == 1


@STORAGE_CAUSES
def test_user_record_write_failure_sends_storage_failed_once(client, vault, casual_policy, cause):
    from src.api import openai_adapter as oa

    with patch.object(oa.llm_adapter, "generate_response_iter") as gen:
        resp = _chat(client, policy=casual_policy, extra=(
            _fail_writes_for("user", _cause(cause)),
            patch.object(oa.llm_adapter, "generate_response_iter", gen),
        ))
    _assert_storage_failed_once(resp)
    gen.assert_not_called()  # nothing is generated for an exchange Ember cannot save
    assert _turns(vault) == []


@STORAGE_CAUSES
def test_reply_write_failure_sends_storage_failed_once(client, vault, casual_policy, cause):
    resp = _chat(client, policy=casual_policy, extra=(_fail_writes_for("assistant", _cause(cause)),))
    _assert_storage_failed_once(resp)
    assert PLAIN_REPLY not in resp.text  # the reply is not sent
    [outcome] = _outcomes(vault)
    assert outcome["metadata"]["outcome"] == "failed"
    assert outcome["metadata"]["reason"] == type(_cause(cause)).__name__


def test_conversation_record_write_failure_sends_storage_failed_once(client, vault, casual_policy):
    resp = _chat(client, policy=casual_policy, extra=(
        patch("src.memory.exchange.create_session", side_effect=OSError("disk full")),
    ))
    _assert_storage_failed_once(resp)
    assert len(_turns(vault, "user")) == 1
    assert [o["metadata"]["reason"] for o in _outcomes(vault)] == ["OSError"]


def test_generation_failure_still_sends_generation_failed(client, vault, casual_policy):
    """Positive control: only storage failures use storage_failed."""
    from src.api.sse import GENERATION_FAILED_CODE

    resp = _chat(client, policy=casual_policy, gen_error=RuntimeError("model server down"))
    assert [e["code"] for e in _errors(resp.text)] == [GENERATION_FAILED_CODE]


def test_clarification_reply_write_failure_sends_storage_failed_once(client, vault):
    """The clarification reply is stored by the handler before the stream
    starts, so its failure takes the handler's storage branch."""
    resp = _chat(client, "google please", extra=(
        _fail_writes_for("assistant", OSError("disk full")),
    ))
    _assert_storage_failed_once(resp)
    assert [o["metadata"]["reason"] for o in _outcomes(vault)] == ["OSError"]


# ---------------------------------------------------------------------------
# History: newest turns, retries once, image_count (v3 "Newest turns", Q4, Q5)
# ---------------------------------------------------------------------------

def _write_turns(count: int) -> list[str]:
    """Write `count` alternating user/assistant turns; return their texts."""
    from src.memory.write_memory import write_canonical_record

    texts = []
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        text = f"turn number {i:03d}"
        write_canonical_record(
            text=text, memory_type="conversation", source="chat",
            metadata={"role": role, "session_id": SESSION_ID},
        )
        texts.append(text)
    return texts


def test_history_returns_the_newest_turns_in_order(vault):
    from src.memory.session import get_turns

    texts = _write_turns(205)
    turns = get_turns(SESSION_ID, limit=200)
    assert [t["text"] for t in turns] == texts[-200:]
    assert turns[-1]["text"] == "turn number 204"


def test_history_limit_at_or_above_the_count_returns_every_turn(vault):
    """Positive control: nothing is dropped when everything fits."""
    from src.memory.session import get_turns

    texts = _write_turns(6)
    assert [t["text"] for t in get_turns(SESSION_ID, limit=6)] == texts
    assert [t["text"] for t in get_turns(SESSION_ID, limit=200)] == texts


def test_failure_then_retry_shows_the_message_once(client, vault, casual_policy):
    _chat(client, policy=casual_policy, gen_error=RuntimeError("model server down"))
    _chat(client, policy=casual_policy)
    history = _history(client)
    assert [(t["role"], t["content"]) for t in history] == [
        ("user", USER_TEXT), ("assistant", PLAIN_REPLY),
    ]
    # The vault keeps both exchanges.
    assert len(_turns(vault, "user")) == 2
    assert len(_outcomes(vault)) == 1


def test_failure_then_a_different_message_shows_both(client, vault, casual_policy):
    """Positive control: only an identical repeat is a retry."""
    _chat(client, policy=casual_policy, gen_error=RuntimeError("model server down"))
    _chat(client, "something else entirely", policy=casual_policy)
    assert [(t["role"], t["content"]) for t in _history(client)] == [
        ("user", USER_TEXT),
        ("user", "something else entirely"),
        ("assistant", PLAIN_REPLY),
    ]


def test_retry_collapse_runs_before_the_limit(vault):
    """`limit` counts the turns the user sees, not the stored records."""
    from src.memory.session import get_turns
    from src.memory.write_memory import write_canonical_record

    for role, text in [("user", "first question"), ("assistant", "first answer"),
                       ("user", "retried question"), ("user", "retried question"),
                       ("assistant", "second answer")]:
        write_canonical_record(
            text=text, memory_type="conversation", source="chat",
            metadata={"role": role, "session_id": SESSION_ID},
        )
    assert [t["text"] for t in get_turns(SESSION_ID, limit=3)] == [
        "first answer", "retried question", "second answer",
    ]


def test_history_returns_the_image_count(client, vault, casual_policy):
    from src.api import openai_adapter as oa

    _chat(client, _image_content(""), policy=casual_policy, extra=(
        patch.object(oa.vision_service, "analyze", return_value="A red square on white."),
    ))
    user, reply = _history(client)
    assert user["image_count"] == 1
    assert user["content"] == ""
    # Control: a turn without images reports 0, not a missing field.
    assert reply["image_count"] == 0


def test_repeats_with_different_image_counts_are_not_retries(vault):
    """Same (empty) text, different attachments: two different messages."""
    from src.memory.session import get_turns
    from src.memory.write_memory import write_canonical_record

    for role, count in [("user", 1), ("user", 2), ("assistant", 0)]:
        meta = {"role": role, "session_id": SESSION_ID}
        if count:
            meta["image_count"] = count
        write_canonical_record(
            text="" if role == "user" else "Two photos of the same plant.",
            memory_type="conversation", source="chat", metadata=meta,
        )
    assert [t["metadata"].get("image_count") for t in get_turns(SESSION_ID)] == [1, 2, None]
