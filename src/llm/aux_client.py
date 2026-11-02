"""
src/llm/aux_client.py

One place for the local auxiliary Ollama calls that sit on the request path
(coaching filter, constitutional review, intent classifier Stage 3, buffer
summary, vision preprocessor, onboarding format, lodestone inference).

The module-level `ollama.chat` uses a client with no timeout, so a wedged
local runner holds the calling thread forever. aux_chat uses a client with a
real httpx timeout instead: when it fires the connection closes, Ollama
cancels the task and frees its single slot, and the caller's existing
fail-open handling runs.

Not covered, by decision: the main answer generation (adapter.py) and the
embedding calls (shared with ingest and write paths).

Each call logs one line, labels and numbers only, never prompt or reply text:
    [TAG] step ms=412
    [TAG] step outcome=timeout ms=15003
    [TAG] step outcome=error type=ConnectError ms=3
"""

import functools
import logging
import time

import httpx
import ollama

logger = logging.getLogger("ember.aux")

# Seconds. Intent classifier Stage 3 uses its own budget,
# get_intent_classifier_timeout_ms() / 1000, passed by its caller.
AUX_CHECK_TIMEOUT_S = 15      # yes/no checks (coaching semantic identity check)
AUX_REWRITE_TIMEOUT_S = 45    # coaching rewrite, identity rewrite
AUX_REVIEW_TIMEOUT_S = 60     # constitutional review call
AUX_SUMMARY_TIMEOUT_S = 60    # buffer compression summary
AUX_VISION_TIMEOUT_S = 120    # VL preprocessor (cold model load on 8 GiB)
AUX_FORMAT_TIMEOUT_S = 30     # onboarding answer format, lodestone inference


@functools.lru_cache(maxsize=None)
def _client_for_timeout(timeout_s: float) -> ollama.Client:
    """One client per timeout value, cached for the process.

    Default host, resolved from OLLAMA_HOST at construction exactly as the
    module-level client resolves it, so these calls reach the same local
    instance they reached before.
    """
    return ollama.Client(timeout=timeout_s)


def aux_chat(*, tag: str, step: str, timeout_s: float, **chat_kwargs):
    """ollama chat with a client timeout and a timing log line.

    chat_kwargs pass through untouched (model, messages, options, think,
    format), so each caller's request is identical to the one it made through
    `ollama.chat`. Exceptions propagate unchanged after the log line; the
    caller's fail-open handling is the policy.
    """
    start = time.perf_counter()
    try:
        response = _client_for_timeout(float(timeout_s)).chat(**chat_kwargs)
    except Exception as exc:
        ms = int((time.perf_counter() - start) * 1000)
        if isinstance(exc, httpx.TimeoutException):
            outcome = "outcome=timeout"
        else:
            outcome = f"outcome=error type={type(exc).__name__}"
        logger.warning("[%s] %s %s ms=%d", tag, step, outcome, ms)
        raise
    ms = int((time.perf_counter() - start) * 1000)
    logger.info("[%s] %s ms=%d", tag, step, ms)
    return response
