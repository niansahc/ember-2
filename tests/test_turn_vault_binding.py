"""
tests/test_turn_vault_binding.py

A turn belongs to the vault that was active when it started (issue #144).
Moving the turn onto worker threads made a vault swap reachable while a turn
is in flight (it could not land while the turn held the event loop), so the
turn's blocking steps now run under a binding to the turn's vault.

Each scenario swaps the active vault from inside the turn, then records which
vault each later step resolves. Positive control for every scenario: the same
turn with the binding helpers replaced by unbound callers resolves the swapped
vault, so the assertions cannot pass on a harness that cannot see a misroute.

Also here: two simultaneous turns on one session leave both exchanges stored.
"""

from __future__ import annotations

import time
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    _make_vault_tree,
    run_bound_inline,
    run_in_threads,
    synthetic_packet,
    vault_records,
)

SESSION_ID = "sess_test_binding_001"
REPLY = "A plain reply with nothing unusual in it at all."


@pytest.fixture
def vaults(tmp_path, monkeypatch, stub_write_embeddings):
    """(vault_a, vault_b); a is the active override when the turn starts."""
    from src.core import config

    vault_a, vault_b = tmp_path / "vault_a", tmp_path / "vault_b"
    _make_vault_tree(vault_a)
    _make_vault_tree(vault_b)
    config.set_vault_path_override(str(vault_a), "test")
    monkeypatch.setattr(
        "src.memory.exchange.spawn_vault_bound_thread", run_bound_inline, raising=False,
    )
    return vault_a, vault_b


@pytest.fixture
def client(vaults):
    with patch("src.api.main.get_ember_api_key", return_value=None):
        from src.api.main import app
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


@pytest.fixture
def unbound():
    """The binding helpers replaced by callers that bind nothing."""
    with patch("src.api.openai_adapter._run_bound",
               new=lambda vault, fn, *a, **k: fn(*a, **k)), \
         patch("src.api.openai_adapter._bound_iter", new=lambda vault, it: it):
        yield


def _enter_stubs(stack, extra=()):
    """Stub retrieval, generation, grounding, the coaching filter, onboarding
    and the extractors, so no model is called."""
    from src.api import openai_adapter as oa

    stack.enter_context(patch.object(
        oa.context_service, "build_context", return_value=synthetic_packet()))
    stack.enter_context(patch.object(
        oa.llm_adapter, "generate_response_iter",
        side_effect=lambda *a, **k: iter([REPLY])))
    stack.enter_context(patch.object(
        oa.llm_adapter, "generate_response", return_value=REPLY))
    stack.enter_context(patch(
        "src.safety.grounding_check.run_grounding_check",
        new=AsyncMock(return_value=(True, None))))
    stack.enter_context(patch(
        "src.llm.coaching_filter.filter_coaching_frame",
        side_effect=lambda text, *_a, **_k: text))
    onb = stack.enter_context(patch("src.api.openai_adapter.onboarding_service"))
    onb.is_active.return_value = False
    for name in ("_background_state_extraction",
                 "_background_topic_decline_resolution",
                 "_detect_and_write_commitment", "_detect_task_in_response",
                 "_background_deviation_detection"):
        stack.enter_context(patch(f"src.api.openai_adapter.{name}"))
    for p in extra:
        stack.enter_context(p)


def _raw_post(client, *, stream):
    return client.post(
        "/v1/chat/completions",
        json={"model": "ember-2",
              "messages": [{"role": "user", "content": "hello there"}],
              "stream": stream, "vault_enabled": True},
        headers={"X-Session-ID": SESSION_ID},
    )


def _post(client, *, stream, extra=()):
    with ExitStack() as stack:
        _enter_stubs(stack, extra)
        return _raw_post(client, stream=stream)


def _turns(vault, role):
    return [r for r in vault_records(vault, "conversation")
            if r["metadata"].get("session_id") == SESSION_ID
            and r["metadata"].get("role") == role]


def _swap_to(vault_b):
    from src.core import config
    config.set_vault_path_override(str(vault_b), "test")


def _streaming_scenario(client, vault_b):
    """Swap while generation runs (the first, empty content frame is already
    on the wire), then let the body's later steps run. Returns {step: path}
    for every later step that resolves the active vault."""
    from src.api import openai_adapter as oa
    from src.core.config import get_private_vault_path

    seen: dict[str, str] = {}

    def generate(*_a, **_k):
        _swap_to(vault_b)
        seen["generation"] = str(get_private_vault_path())
        yield REPLY

    def coaching(text, *_a, **_k):
        seen["coaching_filter"] = str(get_private_vault_path())
        return text

    def hedge(*_a, **_k):
        seen["commit_pending_hedge"] = str(get_private_vault_path())

    def extractors(*_a, **_k):
        seen["post_stream_cleanup"] = str(get_private_vault_path())

    resp = _post(client, stream=True, extra=(
        patch.object(oa.llm_adapter, "generate_response_iter", side_effect=generate),
        patch("src.llm.coaching_filter.filter_coaching_frame", side_effect=coaching),
        patch.object(oa.llm_adapter, "commit_pending_hedge", side_effect=hedge),
        patch("src.api.openai_adapter._spawn_post_exchange_extractors",
              side_effect=extractors),
    ))
    assert resp.status_code == 200
    assert "[DONE]" in resp.text
    return seen


class TestSwapDuringStreaming:

    def test_every_later_step_stays_in_the_turn_vault(self, client, vaults):
        vault_a, vault_b = vaults
        seen = _streaming_scenario(client, vault_b)

        assert set(seen) == {
            "generation", "coaching_filter", "commit_pending_hedge",
            "post_stream_cleanup",
        }
        assert all(path == str(vault_a) for path in seen.values()), seen

    def test_the_stored_exchange_lands_in_the_original_vault(self, client, vaults):
        vault_a, vault_b = vaults
        _streaming_scenario(client, vault_b)

        assert len(_turns(vault_a, "user")) == 1
        assert len(_turns(vault_a, "assistant")) == 1
        assert _turns(vault_b, "user") == []
        assert _turns(vault_b, "assistant") == []

    def test_positive_control_unbound_steps_resolve_the_swapped_vault(
        self, client, vaults, unbound,
    ):
        _, vault_b = vaults
        seen = _streaming_scenario(client, vault_b)

        assert seen["generation"] == str(vault_b)
        assert seen["coaching_filter"] == str(vault_b)
        assert seen["commit_pending_hedge"] == str(vault_b)
        assert seen["post_stream_cleanup"] == str(vault_b)


class TestSwapBeforeGeneration:

    def _scenario(self, client, vault_b, *, stream):
        from src.api import openai_adapter as oa
        from src.core.config import get_private_vault_path

        seen: dict[str, str] = {}

        def build(*_a, **_k):
            _swap_to(vault_b)
            seen["build_context"] = str(get_private_vault_path())
            return synthetic_packet()

        resp = _post(client, stream=stream, extra=(
            patch.object(oa.context_service, "build_context", side_effect=build),
        ))
        assert resp.status_code == 200
        return seen

    @pytest.mark.parametrize("stream", [True, False])
    def test_context_build_stays_in_the_turn_vault(self, client, vaults, stream):
        vault_a, vault_b = vaults
        seen = self._scenario(client, vault_b, stream=stream)

        assert seen["build_context"] == str(vault_a)
        assert len(_turns(vault_a, "assistant")) == 1
        assert _turns(vault_b, "assistant") == []

    def test_positive_control_unbound_build_resolves_the_swapped_vault(
        self, client, vaults, unbound,
    ):
        _, vault_b = vaults
        seen = self._scenario(client, vault_b, stream=True)
        assert seen["build_context"] == str(vault_b)


class TestOverlappingTurnsOnOneSession:

    def test_both_exchanges_are_stored(self, client, vaults):
        """Two at once on one session run one after the other
        (src/api/turn_lock.py) and store two user and two assistant turns,
        with no error."""
        from src.api import openai_adapter as oa

        vault_a, _ = vaults

        def slow_generate(*_a, **_k):
            time.sleep(0.5)
            yield REPLY

        # Stubs are process-global, so they are entered once, here, never
        # per thread (nested patches unwinding out of order would leak).
        with ExitStack() as stack:
            _enter_stubs(stack, extra=(
                patch.object(oa.llm_adapter, "generate_response_iter",
                             side_effect=slow_generate),
            ))
            statuses, errors = run_in_threads(
                lambda _i: _raw_post(client, stream=True).status_code, 2)

        assert errors == []
        assert statuses == [200, 200]
        assert len(_turns(vault_a, "user")) == 2
        assert len(_turns(vault_a, "assistant")) == 2
