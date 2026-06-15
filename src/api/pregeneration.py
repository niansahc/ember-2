"""
src/api/pregeneration.py

Terminal pre-generation routing mechanism for /v1/chat/completions (ADR-041,
issue #93 PR b).

This module is the generic dispatch machinery only - it carries no domain
knowledge and imports nothing from openai_adapter, so it can never form an
import cycle with it. The concrete interceptors (empty / override / onboarding)
live in openai_adapter alongside their dependencies; this module just holds the
context type, the terminal-reply type, and the ordered-chain runner.

Contract - "enrichment-independent terminal interceptor" (ADR-041):
  An interceptor is a callable (RouterContext) -> TerminalReply | None. It may
  only read the RouterContext; it must not mutate it or any value the
  enrichment pipeline or generation handler later reads. Any side effects must
  be fully encapsulated behind a service that needs no enrichment-resolved
  inputs (this is why onboarding qualifies but the clarification short-circuit,
  which writes adapter-level conversation turns keyed by session/project, does
  not - the latter is deferred to PR c).

The RouterContext and TerminalReply dataclasses are frozen so the "must not
mutate the context" half of the contract is enforced structurally rather than
by convention.

Deviation from issue #93's stated signature (documented in ADR-041): the issue
described interceptors as (ctx) -> Optional[Response]. We instead return
TerminalReply DATA and let the single caller translate it into a response via
early_return_response. That gives the A1 stream-vs-JSON invariant exactly one
funnel (one early_return_response call site, not three) and keeps this module
free of any dependency on the response builders.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence


@dataclass(frozen=True)
class RouterContext:
    """Read-only inputs a terminal interceptor is allowed to see.

    Deliberately minimal - NOT the full GenerationContext (that arrives in
    PR c). It carries only what the empty / override / onboarding decisions
    need plus the completion id minted once per request so every terminal
    reply and its log line share one id.
    """

    latest_user_message: str
    stream: bool
    image_parts: list
    completion_id: str


@dataclass(frozen=True)
class TerminalReply:
    """A decision to terminate the request with a canned reply.

    Interceptors return this instead of a response object so the stream-vs-JSON
    decision stays in a single downstream funnel (early_return_response). The
    label identifies the originating interceptor in the early-return log line.
    """

    text: str
    label: str


# An interceptor inspects the context and either claims the request (returns a
# TerminalReply) or passes (returns None).
Interceptor = Callable[[RouterContext], Optional[TerminalReply]]


class PreGenerationRouter:
    """Ordered chain of terminal interceptors; the first to claim wins.

    run() walks the interceptors in declared order and returns the first
    non-None TerminalReply, short-circuiting the rest. Declared order IS the
    precedence, so it must mirror the historical top-to-bottom order of the
    short-circuits it replaces.
    """

    def __init__(self, interceptors: Sequence[Interceptor]):
        self._interceptors = tuple(interceptors)

    def run(self, ctx: RouterContext) -> Optional[TerminalReply]:
        for interceptor in self._interceptors:
            reply = interceptor(ctx)
            if reply is not None:
                return reply
        return None
