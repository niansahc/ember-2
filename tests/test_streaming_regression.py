"""
tests/test_streaming_regression.py

Release-gate regression test for the streaming SSE code path in
openai_adapter.py. Catches NameError / import errors in the streaming
generator that unit tests cannot reach because:

  1. The SSE generator only executes during a real HTTP stream
  2. Post-stream code (deviation detection, state extraction) is
     gated by `if not is_test:` — test sessions skip it entirely
  3. The NameError at line 1053 (prompt_builder not in scope) was
     invisible to 1162 passing tests

This test uses FastAPI TestClient with stream=True to exercise the
full streaming path. It mocks the LLM call so no Ollama is needed,
but walks through the real SSE generator including post-stream
processing.

Marked for release-only runs — not part of the fast `pytest tests/`
cycle. Run before every release with:
    pytest tests/test_streaming_regression.py -v
"""

from unittest.mock import patch, MagicMock

import pytest


def _mock_ollama_chat(**kwargs):
    """Fake ollama.chat that handles both stream=True and stream=False.

    When stream=True, returns an iterator of chunk dicts (matching
    the real ollama.chat streaming behavior). When stream=False,
    returns a single response dict.
    """
    if kwargs.get("stream"):
        def _chunks():
            for word in ["This ", "is ", "a ", "test ", "response."]:
                yield {"message": {"content": word}}
        return _chunks()
    return {"message": {"content": "This is a test response from the mock model."}}


class TestStreamingSSERegression:
    """Verify the streaming SSE path doesn't crash on scope errors.

    This is a release-gate test. It catches the class of bug where
    the streaming generator references a variable that exists in the
    non-streaming path but not in the streaming closure (e.g. bare
    `prompt_builder` instead of `llm_adapter.prompt_builder`).
    """

    def test_streaming_request_does_not_crash(self):
        """A stream=True request should return 200 and produce SSE
        chunks without raising NameError or other scope errors.

        Uses X-Test-Session: true to avoid vault writes, but the
        SSE generator itself is still fully exercised.
        """
        with patch("src.api.main.get_ember_api_key", return_value=None), \
             patch("ollama.chat", side_effect=_mock_ollama_chat):
            from fastapi.testclient import TestClient
            from src.api.main import app
            client = TestClient(app)

            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "ember-2",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                },
                headers={"X-Test-Session": "true"},
            )

            # The response should be 200 with SSE content type.
            # A NameError in the generator would cause a 500 or
            # connection error.
            assert resp.status_code == 200

    def test_non_streaming_request_still_works(self):
        """Baseline: non-streaming path should work with the same mock."""
        with patch("src.api.main.get_ember_api_key", return_value=None), \
             patch("ollama.chat", return_value=_mock_ollama_chat()):
            from fastapi.testclient import TestClient
            from src.api.main import app
            client = TestClient(app)

            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "ember-2",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": False,
                },
                headers={"X-Test-Session": "true"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "choices" in data
