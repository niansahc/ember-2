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

Closing the connection also aborts a model load in progress (Ollama 0.40.13:
"client connection closed before llama-server finished loading, aborting
load"), so a timeout shorter than a cold load means that caller can never
warm its model. Each timeout below outlasts a cold load on supported
hardware (see below); on slower hardware a timeout can still cut a load
short. Stage 3 has its own client timeout for that reason: its budget caps
the turn and is shorter than a load.

Supported hardware, which these budgets assume: a GPU Ollama can use with at
least 8 GB of VRAM, so the default model (qwen3:8b, about 5.2 GB) runs
entirely on the GPU. CPU-only and partial offload are not supported.
Measured on an 8 GiB card (RTX 3060 Ti, qwen3:8b): about 70 tok/s, and a
cold call of 5.6 s of which 3.4 s was the load. The budgets assume a floor of
SUPPORTED_MIN_TOKENS_PER_S and a cold load of at most SUPPORTED_MAX_COLD_LOAD_S,
about a fifth of the measured rate and three times the measured load, for
thermal limits, other GPU work or a slower disk. A capped call's budget is at
least cap / floor + load (tests/test_aux_call_timeouts.py checks it).

Not covered, by decision: the main answer generation (adapter.py) and the
embedding calls (shared with ingest and write paths). The grounding check and
revision pass (src/safety/grounding_check.py) speak raw async HTTP and carry
their own timeouts.

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

# The floor the budgets below assume (module docstring).
SUPPORTED_MIN_TOKENS_PER_S = 15
SUPPORTED_MAX_COLD_LOAD_S = 10

# Seconds. "cap" is the call's num_predict; at the floor, cap / 15 + 10.
AUX_CHECK_TIMEOUT_S = 15      # yes/no checks; cap 10: about 11 s
AUX_REWRITE_TIMEOUT_S = 45    # coaching and identity rewrites; cap 500: about 43 s
# Uncapped, thinking off: the review verdict JSON and the buffer summary.
# Chosen, not derived: room for about 750 tokens at the floor after a load.
AUX_REVIEW_TIMEOUT_S = 60     # constitutional review call
AUX_SUMMARY_TIMEOUT_S = 60    # buffer compression summary
# VL preprocessor; cap 300: about 30 s. The rest is for swapping the vision
# model in, which loads a second model (ADR-032).
AUX_VISION_TIMEOUT_S = 120
# Onboarding format: uncapped and thinking on, measured at 143-181 tokens.
# Budgeted for twice that, 360 tokens: 24 s at the floor plus a 10 s load is
# 34 s. It is the first call of an install, so it is often cold.
AUX_FORMAT_TIMEOUT_S = 45
# Lodestone value inference: thinking off, cap 100: about 17 s.
AUX_LODESTONE_TIMEOUT_S = 30
# Stage 3 client timeout, longer than its budget: the classifier's budget wait
# caps the turn, and the abandoned call stays connected so a load can finish.
# Uncapped JSON label, thinking off. Three times the 10 s load allowance.
AUX_STAGE3_LOAD_TIMEOUT_S = 30


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
        response = _client_for_timeout(timeout_s).chat(**chat_kwargs)
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
