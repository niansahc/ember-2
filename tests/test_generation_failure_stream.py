"""
tests/test_generation_failure_stream.py

A generation failure inside the SSE generator must reach the client as a typed
error frame (ADR-040 v3), not as HTTP 200 with an empty body. Headers are
flushed before the generator runs, so the frame is the only channel.

Every absence assertion here ("no vault_sources frame", "no vault header")
has a positive control in the same file: the identical request with a
successful generation, where the thing does occur.

Both streaming branches are covered. Production routes every turn through the
buffer-then-stream path (`_STREAM_ALWAYS_GROUNDED` is True); the fast-streaming
tests flip that module constant so the raw branch runs.
"""

import json
import logging
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.sse import GENERATION_FAILED_CODE, GENERATION_FAILED_MESSAGE
from src.llm.adapter import StatusSignal
from tests.conftest import synthetic_packet

MARKER = "EXC-MARKER-7f3a"


def _events(text: str) -> list:
    out: list = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        out.append("[DONE]" if payload == "[DONE]" else json.loads(payload))
    return out


def _types(events: list) -> list:
    return [e.get("type") for e in events if isinstance(e, dict) and "type" in e]


@pytest.fixture
def client():
    with patch("src.api.main.get_ember_api_key", return_value=None):
        from src.api.main import app
        yield TestClient(app)


def _post_chat(client, *, stream: bool = True, packet=None, extra_patches=(),
               test_session: bool = True):
    """POST one chat turn with side-effect writers stubbed.

    Each caller passes only the patches that differ: the generation stubs,
    the ollama stubs, or the branch flag. test_session=False drops the
    X-Test-Session header so the post-[DONE] extractor step runs; the exchange
    recorder stays stubbed either way, so nothing reaches the vault.
    """
    from src.api import openai_adapter as oa

    with ExitStack() as stack:
        stack.enter_context(patch.object(
            oa.context_service, "build_context",
            return_value=packet if packet is not None else synthetic_packet(),
        ))
        for p in extra_patches:
            stack.enter_context(p)
        for name in ("ExchangeRecorder", "_background_state_extraction",
                     "_detect_and_write_commitment", "_detect_task_in_response",
                     "_background_topic_decline_resolution"):
            stack.enter_context(patch(f"src.api.openai_adapter.{name}"))
        onb = stack.enter_context(patch("src.api.openai_adapter.onboarding_service"))
        onb.is_active.return_value = False
        return client.post(
            "/v1/chat/completions",
            json={
                "model": "ember-2",
                "messages": [{"role": "user", "content": "hello there"}],
                "stream": stream,
            },
            headers={"X-Test-Session": "true"} if test_session else {},
        )


def _post(client, *, stream: bool, iter_side_effect=None, iter_items=None,
          packet=None, extra=()):
    """POST a chat turn with generation stubbed; returns the response.

    `extra` patches are entered after the defaults, so they override them.
    """
    from src.api import openai_adapter as oa

    return _post_chat(client, stream=stream, packet=packet, extra_patches=(
        patch.object(
            oa.llm_adapter, "generate_response_iter",
            side_effect=iter_side_effect, return_value=iter(iter_items or []),
        ),
        patch.object(oa.llm_adapter, "generate_response", return_value="A grounded reply."),
        patch("src.safety.grounding_check.run_grounding_check", return_value=(True, None)),
        *extra,
    ))


def _raise(*_a, **_k):
    raise RuntimeError(MARKER)


def test_failure_emits_error_frame_then_stop_and_done(client):
    resp = _post(client, stream=True, iter_side_effect=_raise)
    events = _events(resp.text)

    assert resp.status_code == 200  # headers flushed before the body ran
    errors = [e for e in events if isinstance(e, dict) and e.get("type") == "error"]
    assert errors == [
        {"type": "error", "code": GENERATION_FAILED_CODE, "message": GENERATION_FAILED_MESSAGE}
    ]
    stop = events[-2]
    assert stop["choices"][0]["finish_reason"] == "stop"
    assert events[-1] == "[DONE]"
    assert events.index(errors[0]) < len(events) - 2


def test_failure_emits_no_sources_frames(client):
    resp = _post(client, stream=True, iter_side_effect=_raise)
    types = _types(_events(resp.text))
    assert "vault_sources" not in types
    assert "sources" not in types


def test_success_control_emits_vault_sources_and_no_error(client):
    resp = _post(client, stream=True, iter_items=["A grounded reply."])
    types = _types(_events(resp.text))
    assert "vault_sources" in types
    assert "error" not in types


def test_exception_text_never_reaches_the_wire(client):
    resp = _post(client, stream=True, iter_side_effect=_raise)
    assert MARKER not in resp.text


def test_exception_is_logged_with_type_only_in_message(client, caplog):
    with caplog.at_level(logging.ERROR):
        _post(client, stream=True, iter_side_effect=_raise)
    records = [r for r in caplog.records if r.getMessage().startswith("[GENERATION] failed")]
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert records[0].getMessage() == "[GENERATION] failed: RuntimeError"
    assert records[0].exc_info is not None


def test_failure_after_status_frame_keeps_status_then_errors(client):
    def _status_then_raise(*_a, **_k):
        yield StatusSignal("review_pending")
        raise RuntimeError(MARKER)

    resp = _post(client, stream=True, iter_side_effect=_status_then_raise)
    types = _types(_events(resp.text))
    assert "status" in types
    assert types.index("status") < types.index("error")
    assert "vault_sources" not in types


def test_streaming_response_has_no_vault_header(client):
    resp = _post(client, stream=True, iter_items=["A grounded reply."])
    assert "x-ember-vault-used" not in resp.headers


def test_non_streaming_control_still_sets_vault_header(client):
    resp = _post(client, stream=False)
    assert resp.status_code == 200
    assert resp.headers.get("x-ember-vault-used") == "true"


# ---------------------------------------------------------------------------
# End to end: real adapter, real prompt build, unreachable generation host.
# Auxiliary callers stay on the (mocked) local ollama module, matching the
# EMBER_GENERATION_OLLAMA_HOST split in src/core/config.py.
# ---------------------------------------------------------------------------

UNREACHABLE_HOST = "127.0.0.1:1"


def _local_chat(**kwargs):
    if kwargs.get("stream"):
        return iter([{"message": {"content": "A plain reply."}}])
    return {"message": {"content": "A plain reply."}}


def _post_real_adapter(client, committed: list):
    packet = synthetic_packet()
    packet.arm_delivery_recorder(lambda items: committed.append(list(items)))
    return _post_chat(client, packet=packet, extra_patches=(
        patch("ollama.chat", side_effect=_local_chat),
        patch("ollama.embed", return_value={"embeddings": [[0.0] * 768]}),
        patch("src.safety.grounding_check.run_grounding_check", return_value=(True, None)),
    ))


def test_unreachable_generation_host_surfaces_error_and_skips_commit(client, monkeypatch, caplog):
    monkeypatch.setenv("EMBER_GENERATION_OLLAMA_HOST", UNREACHABLE_HOST)
    committed: list = []
    with caplog.at_level(logging.ERROR):
        resp = _post_real_adapter(client, committed)
    events = _events(resp.text)
    types = _types(events)

    assert resp.status_code == 200
    assert "error" in types
    assert "vault_sources" not in types
    assert "x-ember-vault-used" not in resp.headers
    assert events[-1] == "[DONE]"
    assert committed == []
    assert any(r.getMessage().startswith("[GENERATION] failed") for r in caplog.records)


def test_reachable_generation_control_delivers_and_commits(client, monkeypatch):
    monkeypatch.delenv("EMBER_GENERATION_OLLAMA_HOST", raising=False)
    committed: list = []
    resp = _post_real_adapter(client, committed)
    types = _types(_events(resp.text))

    assert "error" not in types
    assert "vault_sources" in types
    assert len(committed) == 1


# ---------------------------------------------------------------------------
# Fast streaming branch. Production keeps _STREAM_ALWAYS_GROUNDED True, which
# makes this branch unreachable; the tests flip the module constant so the
# branch runs.
# ---------------------------------------------------------------------------

def _post_fast(client, stream_fn, extra=(), test_session: bool = True):
    from src.api import openai_adapter as oa

    return _post_chat(client, test_session=test_session, extra_patches=(
        patch.object(oa, "_STREAM_ALWAYS_GROUNDED", False),
        patch.object(oa.llm_adapter, "generate_response_stream", side_effect=stream_fn),
        *extra,
    ))


def _content_frames(events: list) -> list:
    return [
        e["choices"][0]["delta"]["content"]
        for e in events
        if isinstance(e, dict) and e.get("choices") and "content" in e["choices"][0]["delta"]
    ]


def test_fast_path_failure_before_tokens_emits_error(client):
    def _boom(*_a, **_k):
        raise RuntimeError(MARKER)
        yield  # pragma: no cover - makes this a generator

    resp = _post_fast(client, _boom)
    events = _events(resp.text)
    types = _types(events)

    assert resp.status_code == 200
    assert types.count("error") == 1
    assert "vault_sources" not in types
    assert [c for c in _content_frames(events) if c] == []
    assert events[-2]["choices"][0]["finish_reason"] == "stop"
    assert events[-1] == "[DONE]"
    assert MARKER not in resp.text


def test_fast_path_failure_after_two_tokens_keeps_tokens_before_error(client):
    def _two_then_boom(*_a, **_k):
        yield "first "
        yield "second "
        raise RuntimeError(MARKER)

    resp = _post_fast(client, _two_then_boom)
    events = _events(resp.text)
    types = _types(events)

    assert [c for c in _content_frames(events) if c] == ["first ", "second "]
    error_pos = next(i for i, e in enumerate(events) if isinstance(e, dict) and e.get("type") == "error")
    last_token_pos = max(
        i for i, e in enumerate(events)
        if isinstance(e, dict) and e.get("choices") and e["choices"][0]["delta"].get("content")
    )
    assert last_token_pos < error_pos
    assert "vault_sources" not in types
    assert events[-1] == "[DONE]"
    assert MARKER not in resp.text


def test_fast_path_success_control_has_no_error_and_sends_vault_sources(client):
    def _ok(*_a, **_k):
        yield "first "
        yield "second"

    resp = _post_fast(client, _ok)
    events = _events(resp.text)
    types = _types(events)

    assert [c for c in _content_frames(events) if c] == ["first ", "second"]
    assert "error" not in types
    assert "vault_sources" in types
    assert events[-1] == "[DONE]"


# ---------------------------------------------------------------------------
# guard_sse: a raise in any stage of the body, not only generation, ends the
# stream with the error frame, one stop chunk and one [DONE]. Each stage test
# patches the stage to raise; the no-exception controls run the same request
# with nothing raising.
# ---------------------------------------------------------------------------

def _boom_exc():
    return RuntimeError(MARKER)


def _assert_failed_once(resp):
    events = _events(resp.text)
    errors = [e for e in events if isinstance(e, dict) and e.get("type") == "error"]
    stops = [
        e for e in events
        if isinstance(e, dict) and e.get("choices")
        and e["choices"][0].get("finish_reason") == "stop"
    ]
    assert resp.status_code == 200
    assert errors == [
        {"type": "error", "code": GENERATION_FAILED_CODE, "message": GENERATION_FAILED_MESSAGE}
    ]
    assert len(stops) == 1
    assert events.count("[DONE]") == 1
    assert events[-3:] == [errors[0], stops[0], "[DONE]"]
    assert MARKER not in resp.text
    return events


def _assert_completed_once(resp):
    events = _events(resp.text)
    assert resp.status_code == 200
    assert "error" not in _types(events)
    assert events.count("[DONE]") == 1
    assert events[-1] == "[DONE]"
    return events


def _web_packet():
    packet = synthetic_packet()
    packet.web_items = [{"title": "A synthetic page", "url": "https://example.com/a"}]
    return packet


OK = ["A grounded reply."]

# Stage name -> (packet factory or None, patches that make the stage raise).
# Built lazily per test: a patch object cannot be entered twice.
GROUNDED_STAGES = {
    "grounding": (None, lambda: (
        patch("src.safety.grounding_check.run_grounding_check", side_effect=_boom_exc()),
    )),
    "revision": (None, lambda: (
        patch("src.safety.grounding_check.run_grounding_check", return_value=(False, "claim")),
        patch("src.safety.grounding_check.run_revision_pass", side_effect=_boom_exc()),
    )),
    "coaching": (None, lambda: (
        patch("src.llm.coaching_filter.filter_coaching_frame", side_effect=_boom_exc()),
    )),
    "post_gen": (None, lambda: (
        patch("src.llm.post_gen_pipeline.run_post_gen_pipeline", side_effect=_boom_exc()),
    )),
    "web_sources_emission": (_web_packet, lambda: (
        patch("src.api.openai_adapter.sse_sources", side_effect=_boom_exc()),
    )),
    "vault_sources_emission": (None, lambda: (
        patch("src.api.openai_adapter.sse_vault_sources", side_effect=_boom_exc()),
    )),
}


@pytest.mark.parametrize("stage", sorted(GROUNDED_STAGES))
def test_grounded_stage_raise_emits_terminal_frames_once(client, caplog, stage):
    packet_fn, patches = GROUNDED_STAGES[stage]
    packet = packet_fn() if packet_fn else None
    with caplog.at_level(logging.ERROR):
        resp = _post(client, stream=True, iter_items=OK, packet=packet, extra=patches())
    _assert_failed_once(resp)
    failed = [r.getMessage() for r in caplog.records
              if r.getMessage().startswith("[GENERATION] failed")]
    assert failed == ["[GENERATION] failed: RuntimeError"]


def test_grounded_generation_raise_emits_terminal_frames_once(client):
    _assert_failed_once(_post(client, stream=True, iter_side_effect=_raise))


def test_grounded_no_exception_control(client):
    events = _assert_completed_once(_post(client, stream=True, iter_items=OK))
    assert "vault_sources" in _types(events)


def test_grounded_web_sources_control_emits_sources(client):
    # Control for the web_sources_emission stage: the frame is reached.
    events = _assert_completed_once(
        _post(client, stream=True, iter_items=OK, packet=_web_packet())
    )
    assert "sources" in _types(events)


def test_review_stage_raise_emits_terminal_frames_once(client, monkeypatch):
    """Real adapter generate_response_iter; trigger forced; review raises."""
    from unittest.mock import MagicMock

    from src.api import openai_adapter as oa

    monkeypatch.delenv("EMBER_GENERATION_OLLAMA_HOST", raising=False)
    trigger = MagicMock(triggered=True, triggered_by=["test_signal"])
    review = MagicMock(side_effect=_boom_exc())
    resp = _post_chat(client, extra_patches=(
        patch("ollama.chat", side_effect=_local_chat),
        patch("ollama.embed", return_value={"embeddings": [[0.0] * 768]}),
        patch("src.safety.grounding_check.run_grounding_check", return_value=(True, None)),
        patch.object(oa.llm_adapter.policy_service, "evaluate_trigger", return_value=trigger),
        patch.object(oa.llm_adapter.policy_service, "get_active_principles", return_value=[]),
        patch.object(oa.llm_adapter.review_service, "review", review),
    ))
    events = _assert_failed_once(resp)
    assert review.called  # control: the review stage was reached
    assert "status" in _types(events)  # review_pending went out before the raise


# Post-[DONE] work is only the extractor step now: the turns and the pending
# confirmation are stored before [DONE] (ADR-047). A raise in that step must
# stay log-only. The Phase B builders are stubbed because these two requests
# drop X-Test-Session so the extractor step runs.
_NON_TEST_STUBS = (
    "_apply_confirmation", "_apply_tasks", "_apply_timers",
)


def _post_terminal_raise():
    return (
        patch(
            "src.api.openai_adapter._spawn_post_exchange_extractors",
            side_effect=_boom_exc(),
        ),
        *(patch(f"src.api.openai_adapter.{name}") for name in _NON_TEST_STUBS),
    )


def test_post_terminal_raise_adds_no_frames(client, caplog):
    """_post_stream_cleanup runs after [DONE]; a raise there is log-only."""
    from src.api import openai_adapter as oa

    with caplog.at_level(logging.ERROR):
        resp = _post_chat(client, test_session=False, extra_patches=(
            patch.object(oa.llm_adapter, "generate_response_iter", return_value=iter(OK)),
            patch("src.safety.grounding_check.run_grounding_check", return_value=(True, None)),
            *_post_terminal_raise(),
        ))
    _assert_completed_once(resp)
    messages = [r.getMessage() for r in caplog.records]
    assert "[SSE] post-terminal failure: RuntimeError" in messages  # control: it raised
    assert not any(m.startswith("[GENERATION] failed") for m in messages)


def test_fast_path_vault_sources_raise_emits_terminal_frames_once(client):
    def _ok(*_a, **_k):
        yield "first "

    resp = _post_fast(client, _ok, extra=(
        patch("src.api.openai_adapter.sse_vault_sources", side_effect=_boom_exc()),
    ))
    events = _assert_failed_once(resp)
    assert [c for c in _content_frames(events) if c] == ["first "]


def test_fast_path_post_terminal_raise_adds_no_frames(client, caplog):
    def _ok(*_a, **_k):
        yield "first "

    with caplog.at_level(logging.ERROR):
        resp = _post_fast(client, _ok, test_session=False, extra=_post_terminal_raise())
    _assert_completed_once(resp)
    messages = [r.getMessage() for r in caplog.records]
    assert "[SSE] post-terminal failure: RuntimeError" in messages


def test_fast_path_coaching_raise_now_fails_before_done(client):
    """The fast path finishes the stored copy (coaching, post-gen, the reply
    write) before [DONE] now (ADR-047), so a raise there is a failed exchange:
    the streamed tokens stay and the error frame follows them."""
    def _ok(*_a, **_k):
        yield "first "

    resp = _post_fast(client, _ok, extra=(
        patch("src.llm.coaching_filter.filter_coaching_frame", side_effect=_boom_exc()),
    ))
    events = _assert_failed_once(resp)
    assert [c for c in _content_frames(events) if c] == ["first "]


def test_fast_path_no_exception_control(client):
    def _ok(*_a, **_k):
        yield "first "

    events = _assert_completed_once(_post_fast(client, _ok))
    assert "vault_sources" in _types(events)
