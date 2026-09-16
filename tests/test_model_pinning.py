"""
tests/test_model_pinning.py

POST /model persist gating, and the GET /model pin report.

A model sweep used to put the candidate in every role at once: POST /model
wrote model_override.json, and get_ember_model() reads that file on every call,
so the candidate became the Stage-3 intent classifier, the coaching-filter
rewriter, the deviation detector and the reflection paths as well as the
generator. The candidate was then both the system under test and the router
choosing its own test path.

persist=False pins by omission -- the candidate reaches llm_adapter.model and
nothing else, so every call-time role keeps resolving the reference model.
persist defaults True, so every existing caller is unaffected.

These are the first HTTP-level tests for either endpoint.
"""

import pytest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api.main import app
from src.api.openai_adapter import llm_adapter
from src.core.config import get_ember_model, get_private_vault_path

REFERENCE = "qwen3:8b"
CANDIDATE = "qwen3.6:35b-a3b"


@pytest.fixture(autouse=True)
def restore_model_state():
    """Snapshot and restore both halves of the model state.

    llm_adapter is a module singleton and model_override.json lives in the
    active (test) vault, so a leak from either would contaminate every later
    test in the session.
    """
    prior_model = llm_adapter.model
    override = get_private_vault_path() / "model_override.json"
    prior_override = override.read_bytes() if override.exists() else None

    yield

    llm_adapter.model = prior_model
    if prior_override is None:
        override.unlink(missing_ok=True)
    else:
        override.write_bytes(prior_override)


@pytest.fixture
def client():
    with patch("src.api.main.get_ember_api_key", return_value=None):
        yield TestClient(app)


def _set_reference():
    """Put a known reference model in the persisted override."""
    from src.core.config import set_ember_model_override

    set_ember_model_override(REFERENCE)
    llm_adapter.set_model(REFERENCE)


class TestPersistDefaultsToTodaysBehaviour:
    """The deployment boundary rests on this: a PC-only install never sends
    persist, and must behave exactly as it does today."""

    def test_omitting_persist_writes_the_override(self, client):
        _set_reference()
        resp = client.post("/model", json={"model": CANDIDATE})
        assert resp.status_code == 200
        assert llm_adapter.model == CANDIDATE
        assert get_ember_model() == CANDIDATE

    def test_persist_true_writes_the_override(self, client):
        _set_reference()
        resp = client.post("/model", json={"model": CANDIDATE, "persist": True})
        assert resp.status_code == 200
        assert llm_adapter.model == CANDIDATE
        assert get_ember_model() == CANDIDATE


class TestPersistFalsePinsByOmission:
    """persist=False reaches generation and nothing else."""

    def test_candidate_reaches_generation_only(self, client):
        _set_reference()
        resp = client.post("/model", json={"model": CANDIDATE, "persist": False})
        assert resp.status_code == 200
        # Generation follows the candidate...
        assert llm_adapter.model == CANDIDATE
        # ...while every call-time role still resolves the reference.
        assert get_ember_model() == REFERENCE

    def test_override_file_is_untouched(self, client):
        _set_reference()
        override = get_private_vault_path() / "model_override.json"
        before = override.read_bytes()

        client.post("/model", json={"model": CANDIDATE, "persist": False})

        assert override.read_bytes() == before


class TestGetModelReportsThePin:
    def test_reports_divergence_after_a_pinned_swap(self, client):
        _set_reference()
        client.post("/model", json={"model": CANDIDATE, "persist": False})

        data = client.get("/model").json()
        assert data["model"] == CANDIDATE
        assert data["reference_model"] == REFERENCE
        assert data["pinned"] is True

    def test_not_pinned_when_the_two_agree(self, client):
        _set_reference()
        client.post("/model", json={"model": CANDIDATE, "persist": True})

        data = client.get("/model").json()
        assert data["model"] == CANDIDATE
        assert data["reference_model"] == CANDIDATE
        assert data["pinned"] is False

    def test_existing_keys_are_preserved(self, client):
        """Additive only -- the UI reads model/available/cloud."""
        data = client.get("/model").json()
        for key in ("model", "available", "cloud"):
            assert key in data


def test_post_model_does_not_touch_the_conversation_buffer(client):
    """ConversationBuffer no longer tracks a context window at all (issue
    #155 removed set_context_window() and the attribute it wrote, since
    the real budget was always LLMAdapter._get_num_ctx). This asserts the
    buffer's observable state is untouched by a model swap.
    """
    _set_reference()
    buffer = llm_adapter.prompt_builder.conversation_buffer
    before = list(buffer.buffer)

    client.post("/model", json={"model": CANDIDATE, "persist": False})

    assert buffer.buffer == before
