"""tests/eval/test_grounding_verification.py

Synthetic eval for the grounding verification layer (ADR-019).

Runs 10 (response, retrieved_context) pairs through run_grounding_check
with the underlying httpx call mocked to return a deterministic YES/NO.
Five pairs contain a clear fabrication (specific fact, date, name, or
number not in the context) and must produce grounded=False. Five are
cleanly grounded and must produce grounded=True with claims=None.

The mock target is httpx.AsyncClient as used inside
src/safety/grounding_check.py (line 87). Wrapping it as an async
context manager with .post returning an object exposing .json() is the
shape the function under test expects.

This module is marked @pytest.mark.eval so it is excluded from the
default pytest tests/ -q suite. Run explicitly with:
    pytest tests/eval/test_grounding_verification.py -v -m eval
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest

from src.safety.grounding_check import run_grounding_check


pytestmark = pytest.mark.eval


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _make_async_client_mock(answer_content: str):
    """Build a callable that returns an httpx.AsyncClient-shaped mock.

    The function under test does:
        async with httpx.AsyncClient(timeout=30.0) as client:
            result = await client.post(url, json=...)
        answer = result.json()["message"]["content"].strip()

    The mock implements the async-context-manager protocol and returns
    a stub client whose .post coroutine resolves to a stub response
    whose .json() returns the canned payload.
    """

    response_stub = MagicMock()
    response_stub.json.return_value = {"message": {"content": answer_content}}

    async def _post_stub(*_args, **_kwargs):
        return response_stub

    client_stub = MagicMock()
    client_stub.post = _post_stub

    @asynccontextmanager
    async def _client_factory(*_args, **_kwargs):
        yield client_stub

    # httpx.AsyncClient(timeout=30.0) constructs an instance and is then
    # used as `async with`. Returning the asynccontextmanager from the
    # constructor call gives the with-block the right protocol.
    def _async_client_constructor(*args, **kwargs):
        return _client_factory(*args, **kwargs)

    return _async_client_constructor


# ---------------------------------------------------------------------------
# Fabrication pairs: response contains specific claims absent from context.
# ---------------------------------------------------------------------------


_FABRICATION_PAIRS = [
    # day + event type fabricated
    (
        "User mentioned they have a meeting on Thursday.",
        "You have a board presentation scheduled for Friday at 2pm.",
    ),
    # programming language + timeline fabricated
    (
        "User is working on a Python migration script.",
        "The Ruby migration you started last month is almost done.",
    ),
    # frequency fabricated
    (
        "User said they went to the gym yesterday.",
        "You've been going to the gym every day for the past two weeks.",
    ),
    # named third party + relationship fabricated
    (
        "User mentioned they had coffee with a colleague.",
        "Your manager Sarah agreed to your promotion proposal during the coffee chat.",
    ),
    # specific number + outcome fabricated
    (
        "User has been thinking about a side project.",
        "Your side project earned $4,200 in revenue last quarter.",
    ),
]


# ---------------------------------------------------------------------------
# Clean grounding pairs: response is supported by the context.
# ---------------------------------------------------------------------------


_CLEAN_PAIRS = [
    (
        "User is focused on shipping v0.18.0.",
        "Sounds like v0.18.0 is the priority right now.",
    ),
    (
        "User mentioned feeling tired after a long week.",
        "That sounds like a draining week.",
    ),
    (
        "User said they finished the deployment yesterday.",
        "Glad to hear the deployment landed.",
    ),
    (
        "User is debugging a slow query in the retrieval pipeline.",
        "A slow query in retrieval can really stall the loop.",
    ),
    (
        "User has been reading about local LLM quantization.",
        "Local LLM quantization is a deep area to dig into.",
    ),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("context, response", _FABRICATION_PAIRS)
def test_fabrication_flagged_as_ungrounded(context, response):
    """Each fabrication pair must produce grounded=False with the
    judge's YES verdict surfaced in the claims string."""
    canned_yes = "YES: response contains claims not present in context."
    factory = _make_async_client_mock(canned_yes)
    with patch("src.safety.grounding_check.httpx.AsyncClient", side_effect=factory):
        grounded, claims = asyncio.run(run_grounding_check(response, context))
    assert grounded is False
    assert claims is not None
    assert claims.upper().startswith("YES")


@pytest.mark.parametrize("context, response", _CLEAN_PAIRS)
def test_clean_response_marked_grounded(context, response):
    """Each clean pair must produce grounded=True with no claims string."""
    canned_no = "NO"
    factory = _make_async_client_mock(canned_no)
    with patch("src.safety.grounding_check.httpx.AsyncClient", side_effect=factory):
        grounded, claims = asyncio.run(run_grounding_check(response, context))
    assert grounded is True
    assert claims is None
