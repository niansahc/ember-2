"""
src/api/turn_lock.py

One chat turn at a time per session. Other sessions and other endpoints stay
concurrent.

Turns run on worker threads so the event loop stays free, which also removed
the serialization the blocked loop used to give. Several turn steps assume it:
reading then resolving a pending confirmation, checking then creating a session
record, the conversation buffer's hedge marks.

The lock is an asyncio.Lock, taken and released only on the event loop, so a
waiting turn holds no worker thread. It is held for the whole turn: a streaming
turn keeps it until the response has finished, post-stream cleanup and any
on_abort included, so the next turn cannot read state the previous one is still
restoring.

Lock order: this lock comes first, before any buffer, pending or store lock.
Nothing that holds one of those waits on this one, because worker threads and
background threads never touch it and nothing inside a turn starts another
turn.

The wait is bounded. Main generation has no timeout, so an unbounded wait would
hang the conversation behind a wedged turn with no error.
"""

from __future__ import annotations

import asyncio
import logging
import time

import anyio
from fastapi.responses import StreamingResponse

logger = logging.getLogger("ember.openai_adapter")

# Seconds. Under the 180 s client limit, so the caller gets an answer.
TURN_WAIT_TIMEOUT_S = 150


class TurnBusy(Exception):
    """The session's previous turn did not finish within the wait limit."""


class _Entry:
    __slots__ = ("lock", "holders")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.holders = 0


class TurnLease:
    """The right to run a session's turn. Release is synchronous, so it works
    inside a cancelled scope, and safe to repeat."""

    def __init__(self, locks: "TurnLocks", session_id: str, entry: _Entry) -> None:
        self._locks = locks
        self._session_id = session_id
        self._entry = entry
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._entry.lock.release()
        self._locks._forget(self._session_id, self._entry)


class TurnLocks:
    """One lock per live session. An entry exists only while a turn holds or
    waits for it, so the store cannot grow with the session count. Used only on
    the event loop, so it needs no lock of its own."""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}

    async def acquire(self, session_id: str) -> TurnLease:
        entry = self._entries.setdefault(session_id, _Entry())
        entry.holders += 1
        waited = entry.lock.locked()
        start = time.perf_counter()
        try:
            await asyncio.wait_for(entry.lock.acquire(), TURN_WAIT_TIMEOUT_S)
        except TimeoutError:
            self._forget(session_id, entry)
            logger.warning("[TURN_LOCK] wait timed out ms=%d", _ms_since(start))
            raise TurnBusy from None
        except BaseException:
            self._forget(session_id, entry)
            raise
        if waited:
            logger.info("[TURN_LOCK] waited ms=%d", _ms_since(start))
        return TurnLease(self, session_id, entry)

    def _forget(self, session_id: str, entry: _Entry) -> None:
        entry.holders -= 1
        if entry.holders == 0:
            self._entries.pop(session_id, None)


def _ms_since(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


async def _close_body(body) -> None:
    """Close the response body now, so guard_sse's on_abort runs here rather
    than whenever the abandoned generator is finalized."""
    close = getattr(body, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except Exception as exc:  # noqa: BLE001
        logger.error("[TURN_LOCK] body close failed: %s", type(exc).__name__)


class TurnStreamingResponse(StreamingResponse):
    """A StreamingResponse that ends its turn: when it stops, for any reason,
    the body is closed and then the lease is released. Here rather than in the
    body, because a body that never started has no finally to run."""

    def __init__(self, inner: StreamingResponse, lease: TurnLease) -> None:
        self.__dict__.update(inner.__dict__)
        self._lease = lease

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                with anyio.CancelScope(shield=True):
                    await _close_body(self.body_iterator)
            finally:
                self._lease.release()
