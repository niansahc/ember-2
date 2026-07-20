"""
src/api/pregeneration.py

Terminal pre-generation routing mechanism for /v1/chat/completions
(ADR-041 PR b; ADR-042 PR c).

This module holds the generic dispatch machinery plus the per-request context
types it dispatches over. It carries no domain LOGIC and imports nothing from
openai_adapter at runtime, so it can never form an import cycle with it. The
concrete interceptors (empty / override / onboarding, and the enrichment-
dependent clarification) live in openai_adapter alongside their dependencies;
this module holds only the context types, the terminal-reply type, and the
ordered-chain runner.

Two router stages exist (ADR-042):
  1. Pre-enrichment: PreGenerationRouter[RouterContext] runs the enrichment-
     INDEPENDENT terminals (empty / override / onboarding) before any session /
     project / vault resolution.
  2. Post-enrichment: PreGenerationRouter[GenerationContext] runs the
     enrichment-DEPENDENT terminals (clarification) after the Phase A value
     builders have resolved session_id / project_id / is_test / vault_enabled,
     and before the Phase B message-mutating prep builders run.

Contract - terminal interceptor:
  An interceptor is a callable (C) -> TerminalReply | None over its context
  type C. It may only READ the context; it must not mutate it. Side effects are
  allowed only when they need no value the later pipeline reads back on the same
  turn (onboarding writes onboarding state; clarification writes conversation
  turns keyed by the already-resolved session/project - both terminal, so
  nothing downstream consumes their output on that turn).

The context dataclasses (RouterContext, GenerationContext) are frozen so the
"must not mutate the context" half of the contract is enforced structurally.
GenerationWork - the mutable per-request carrier for the evolving message and
prep-derived values - is deliberately NOT frozen: it is the Phase B builders'
working state, never an interceptor input.

Deviation from issue #93's stated signature (documented in ADR-041):
interceptors return TerminalReply DATA, not a Response. The single caller
translates it via early_return_response, giving the A1 stream-vs-JSON invariant
exactly one funnel and keeping this module free of any response-builder import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Generic, Optional, Sequence, TypeVar

if TYPE_CHECKING:
    # Typing-only import: the memoized query policy carried on GenerationContext.
    # Imported under TYPE_CHECKING so this module stays runtime-import-light and
    # domain-logic-free.
    from src.context.policies import ContextPolicy


@dataclass(frozen=True)
class RouterContext:
    """Read-only inputs an enrichment-INDEPENDENT terminal interceptor may see.

    Deliberately minimal: only what empty / override / onboarding need, plus the
    completion id minted once per request so every terminal reply and its log
    line share one id. GenerationContext (below) is the post-enrichment superset.
    """

    latest_user_message: str
    stream: bool
    image_parts: list
    completion_id: str


@dataclass(frozen=True)
class GenerationContext:
    """Read-only, enrichment-resolved per-request identity/routing values.

    Built by the Phase A value builders once the RouterContext-stage terminals
    have passed. Frozen: these values are resolved once and never change, so an
    enrichment-dependent interceptor (clarification) reading them cannot have its
    inputs mutated out from under it by the later Phase B prep builders.

    completion_id and stream are carried forward from the request's RouterContext
    so a single id spans the pre-router terminals, the clarification terminal,
    and the final generation response. raw_user_message is the clean pre-prefix
    snapshot of the user message taken at the Phase A->B boundary (the value
    _write_pending_confirmation stores as the web-search query); the mutating
    message itself lives on GenerationWork, not here.
    """

    session_id: str
    project_id: Optional[str]
    project_name: Optional[str]
    is_test: bool
    vault_enabled: bool
    skip_vault: bool
    completion_id: str
    stream: bool
    policy: "ContextPolicy"
    raw_user_message: str


@dataclass
class GenerationWork:
    """Mutable per-request working state for the Phase B generation-prep builders.

    Holds the evolving user message - rewritten at several prep sites
    (confirmation override, task / timer system-prefixes, thin-vault offer) - and
    the values those builders derive for the generation handler. NOT frozen and
    NOT an interceptor input: only the Phase B builders and the generation
    handler touch it.
    """

    message: str


@dataclass(frozen=True)
class TerminalReply:
    """A decision to terminate the request with a canned reply.

    Interceptors return this instead of a response object so the stream-vs-JSON
    decision stays in a single downstream funnel (early_return_response). The
    label identifies the originating interceptor in the early-return log line.
    """

    text: str
    label: str


# Context type an interceptor dispatches over (RouterContext or GenerationContext).
C = TypeVar("C")

# An interceptor inspects its context and either claims the request (returns a
# TerminalReply) or passes (returns None).
Interceptor = Callable[[C], Optional[TerminalReply]]


class PreGenerationRouter(Generic[C]):
    """Ordered chain of terminal interceptors over a context type C; first claim wins.

    Generic over C so one mechanism serves both the pre-enrichment RouterContext
    stage and the post-enrichment GenerationContext stage (ADR-042). run() walks
    the interceptors in declared order and returns the first non-None
    TerminalReply, short-circuiting the rest. Declared order IS the precedence,
    so it must mirror the historical top-to-bottom order of the short-circuits it
    replaces.
    """

    def __init__(self, interceptors: Sequence[Interceptor[C]]):
        self._interceptors = tuple(interceptors)

    def run(self, ctx: C) -> Optional[TerminalReply]:
        for interceptor in self._interceptors:
            reply = interceptor(ctx)
            if reply is not None:
                return reply
        return None
