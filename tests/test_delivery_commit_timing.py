"""
tests/test_delivery_commit_timing.py

ADR-015 retrieval stats (issue #227): the delivery commit fires only after
generation succeeds. A turn whose generation raised delivered nothing to the
model, so it must not credit the records. A stream that fails partway also
does not commit ("only after generation succeeds").

Each absence assertion ("recorder not called") is paired with a success
control where the same setup does call it.
"""

from unittest.mock import patch

import pytest

from src.context.models import ContextItem, ContextPacket
from src.llm.adapter import LLMAdapter


def _armed_packet():
    """A packet whose render is recorded and whose recorder is armed."""
    item = ContextItem(
        id="fixture-1",
        content="A synthetic fixture record with enough content to pass filters.",
        source="conversation",
        item_type="conversation",
        memory_type="conversation",
        score=0.6,
        timestamp="2026-03-15T10-00-00",
    )
    packet = ContextPacket(user_message="hello there", memory_items=[item])
    committed: list = []
    packet.arm_delivery_recorder(lambda items: committed.append(list(items)))
    packet.begin_render()
    packet.record_rendered([item])
    return packet, committed


@pytest.fixture
def adapter():
    a = LLMAdapter(model="qwen3:8b")

    def _fake_trim(packet, **_kwargs):
        return "system prompt", packet, {"sections_dropped": [], "overflow": False}

    with patch("src.llm.adapter.trim_to_fit", side_effect=_fake_trim), \
         patch.object(a, "_get_num_ctx", return_value=4096), \
         patch.object(a, "_maybe_compress_buffer"):
        yield a


def _drain(gen):
    return list(gen)


def test_sync_generation_failure_does_not_commit(adapter):
    packet, committed = _armed_packet()
    with patch.object(adapter, "_chat", side_effect=RuntimeError("down")):
        with pytest.raises(RuntimeError):
            _drain(adapter.generate_response_iter(packet))
    assert committed == []


def test_sync_generation_success_commits_once(adapter):
    packet, committed = _armed_packet()
    with patch.object(adapter, "_chat", return_value="A plain reply."):
        _drain(adapter.generate_response_iter(packet))
    assert len(committed) == 1
    assert [i.id for i in committed[0]] == ["fixture-1"]


def test_stream_generation_failure_does_not_commit(adapter):
    packet, committed = _armed_packet()

    def _boom(**_kwargs):
        raise RuntimeError("down")
        yield  # pragma: no cover - makes this a generator

    with patch.object(adapter, "_chat_stream", side_effect=_boom):
        with pytest.raises(RuntimeError):
            _drain(adapter.generate_response_stream(packet))
    assert committed == []


def test_stream_mid_stream_failure_does_not_commit(adapter):
    packet, committed = _armed_packet()

    def _partial(**_kwargs):
        yield "partial "
        raise RuntimeError("connection dropped")

    received = []
    with patch.object(adapter, "_chat_stream", side_effect=_partial):
        with pytest.raises(RuntimeError):
            for chunk in adapter.generate_response_stream(packet):
                received.append(chunk)
    assert received == ["partial "]  # control: tokens did flow before the failure
    assert committed == []


def test_stream_success_commits_once(adapter):
    packet, committed = _armed_packet()

    def _ok(**_kwargs):
        yield "A plain "
        yield "reply."

    with patch.object(adapter, "_chat_stream", side_effect=_ok):
        _drain(adapter.generate_response_stream(packet))
    assert len(committed) == 1
    assert [i.id for i in committed[0]] == ["fixture-1"]
