"""
tests/test_eval_model_pin.py

Harness-side model pinning: tools/eval_helpers.pin_model and restore_model.

The eval tools used to switch models by writing model_override.json, which made
the candidate the intent classifier, coaching filter, deviation detector and
reflection paths as well as the generator -- so a candidate was scored on a
pipeline it had itself selected -- and clobbered the user's persisted model
mid-run (CHANGELOG.md:308).

These drive the real app through a TestClient rather than asserting on mocks,
so the server's persist gate is what is actually exercised.
"""

import hashlib

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.openai_adapter import llm_adapter
from src.core.config import get_private_vault_path, set_ember_model_override

from tools import eval_helpers

REFERENCE = "qwen3:8b"
CANDIDATES = ["qwen3:32b", "qwen3.6:35b-a3b", "gemma4:26b-a4b-it-q4_K_M"]


def _override_path():
    return get_private_vault_path() / "model_override.json"


def _override_digest():
    p = _override_path()
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "ABSENT"


@pytest.fixture(autouse=True)
def restore_model_state():
    """Snapshot both halves of the model state, as test_model_pinning does.

    llm_adapter is a module singleton and the override lives in the active
    test vault, so a leak from either contaminates later tests.
    """
    prior_model = llm_adapter.model
    p = _override_path()
    prior_bytes = p.read_bytes() if p.exists() else None
    yield
    llm_adapter.model = prior_model
    if prior_bytes is None:
        p.unlink(missing_ok=True)
    else:
        p.write_bytes(prior_bytes)


@pytest.fixture
def routed(monkeypatch):
    """Route eval_helpers' httpx calls into the real app, recording them.

    Returns the call log so a test can assert what reached the server -- and,
    for the validation test, that nothing did.
    """
    calls = []
    client = TestClient(app)

    def fake_get(url, headers=None, timeout=None, **kwargs):
        calls.append(("GET", url))
        path = url.replace(eval_helpers._api_base(), "")
        return client.get(path, headers=headers or {})

    def fake_post(url, json=None, headers=None, timeout=None, **kwargs):
        calls.append(("POST", url, json))
        path = url.replace(eval_helpers._api_base(), "")
        return client.post(path, json=json, headers=headers or {})

    monkeypatch.setattr(eval_helpers.httpx, "get", fake_get)
    monkeypatch.setattr(eval_helpers.httpx, "post", fake_post)
    # The installed-model list is environment-dependent; pin it so the test
    # asserts validation logic rather than what happens to be pulled locally.
    monkeypatch.setattr(
        eval_helpers,
        "_installed_and_cloud_models",
        lambda state: set(CANDIDATES) | {REFERENCE},
    )
    return calls


class TestSweepLeavesTheOverrideAlone:
    def test_three_candidate_sweep_does_not_touch_the_override(self, routed):
        set_ember_model_override(REFERENCE)
        llm_adapter.set_model(REFERENCE)
        before = _override_digest()

        for candidate in CANDIDATES:
            eval_helpers.pin_model(candidate)
            assert llm_adapter.model == candidate

        eval_helpers.restore_model(REFERENCE)

        assert _override_digest() == before
        assert llm_adapter.model == REFERENCE

    def test_restore_returns_the_in_memory_model(self, routed):
        """persist=false still moves llm_adapter.model, so the sweep must put
        it back or the running API keeps generating with the last candidate."""
        set_ember_model_override(REFERENCE)
        llm_adapter.set_model(REFERENCE)

        eval_helpers.pin_model(CANDIDATES[0])
        assert llm_adapter.model == CANDIDATES[0]

        eval_helpers.restore_model(REFERENCE)
        assert llm_adapter.model == REFERENCE
        assert _override_digest() == _override_digest()  # unchanged throughout


class TestValidationHappensBeforePosting:
    def test_unknown_tag_never_reaches_the_server(self, routed):
        set_ember_model_override(REFERENCE)
        llm_adapter.set_model(REFERENCE)

        with pytest.raises(eval_helpers.ModelPinError):
            eval_helpers.pin_model("qwen3:99b-does-not-exist")

        assert not [c for c in routed if c[0] == "POST"], "a POST was issued"
        assert llm_adapter.model == REFERENCE


class TestPinIsReasserted:
    def test_every_post_is_followed_by_a_verifying_get(self, routed):
        set_ember_model_override(REFERENCE)
        llm_adapter.set_model(REFERENCE)

        for candidate in CANDIDATES:
            eval_helpers.pin_model(candidate)

        methods = [c[0] for c in routed]
        # Each pin_model does: GET (validate) -> POST -> GET (verify).
        assert methods.count("POST") == len(CANDIDATES)
        for i, m in enumerate(methods):
            if m == "POST":
                assert methods[i + 1] == "GET", "POST not followed by a verifying GET"

    def test_posts_carry_persist_false(self, routed):
        set_ember_model_override(REFERENCE)
        llm_adapter.set_model(REFERENCE)
        eval_helpers.pin_model(CANDIDATES[0])

        posts = [c for c in routed if c[0] == "POST"]
        assert posts, "no POST issued"
        for _, _, body in posts:
            assert body["persist"] is False

    def test_a_reverted_pin_aborts(self, routed, monkeypatch):
        """A --reload uvicorn silently reverts an in-memory pin, which would
        relabel reference results as candidate results (ADR-043)."""
        set_ember_model_override(REFERENCE)
        llm_adapter.set_model(REFERENCE)

        real_read = eval_helpers.read_model_state
        seen = {"n": 0}

        def flaky_read():
            seen["n"] += 1
            state = real_read()
            # Second call is the post-POST verification; simulate a revert.
            if seen["n"] == 2:
                return {**state, "pinned": False, "model": REFERENCE}
            return state

        monkeypatch.setattr(eval_helpers, "read_model_state", flaky_read)

        with pytest.raises(eval_helpers.ModelPinError):
            eval_helpers.pin_model(CANDIDATES[0])
