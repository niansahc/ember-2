"""
src/llm/post_gen_pipeline.py

Unified post-generation validator pipeline.

Runs five validators in a fixed order against a completed response:

  1. source allowlist (strip fabricated citations)
  2. web search refusal (deterministic fallback when model ignores web results)
  3. vision refusal (substitute when vision fired but model refused)
  4. ask-first (the code's search offer replaces the draft on an ask-first
     exchange)
  5. URL validator (B-MEM-005: strip fabricated https?:// URLs against a
     per-turn allowlist; runs last so it sees the final returned text)

Followed by an empty-response guard that fills zero-byte replies with a
fallback so the streaming path never emits a blank message to the client
.

Then, when this turn's web search was unavailable (every engine blocked, the
request failed, every hit was rejected as irrelevant, or AI-document
quarantine removed every hit), a fixed disclosure line is prepended so the
user knows the answer did not come from a search. Skipped for refusals, for substituted
replies (the confirm-path retry offer already says search failed), and for
the empty-guard fallback.

The ordering is deliberate:
  - Source stripping first so downstream validators see the cleaned text.
  - Web search refusal before vision/ask-first: if the model had web
    results and still refused, the deterministic fallback is the right
    answer regardless of vision or ask-first state.
  - Vision before ask-first: a vision refusal is a direct failure to use
    the <vision_context> section, so it wins over any ask-first logic.
  - Empty guard before URL validator: ensures URL validation runs against
    whatever text is actually returned, including any empty-fallback text.
  - URL validator last: substitutions earlier in the pipeline can introduce
    or remove URLs, so validating the final text is the only correct
    position. URLs in substituted snippet text come from web_items by
    construction and pass the allowlist cleanly.

Callers should log the returned `substitutions` list so eval can track
intervention rates.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import NamedTuple

from src.llm.ask_first_validator import (
    REPEAT_SEARCH_OFFER,
    RETRY_OFFER_RESPONSE,
    SCRIPTED_ASK_FIRST_RESPONSE,
    remove_unbacked_offers,
)
from src.llm.refusal import is_refusal_response
from src.tools.web_search import NO_REPEAT_OFFER_REASONS
from src.llm.source_validator import (
    extract_web_domains,
    validate_and_strip_sources,
)
from src.llm.vision_refusal_validator import validate_vision_response
from src.llm.web_search_refusal_validator import validate_web_search_response
from src.safety.url_validator import (
    build_url_allowlist,
    validate_and_strip_urls,
)

logger = logging.getLogger(__name__)

# Deterministic, not left to the model: an 8B model told "search was
# unavailable" still sometimes answers as if it had searched.
WEB_SEARCH_UNAVAILABLE_DISCLOSURE = (
    "Web search isn't working right now, so this answer is from what I "
    "already know and may be out of date."
)

_EMPTY_FALLBACK = (
    "I had trouble generating a response to that. Try rephrasing, or let me "
    "know what you're actually trying to figure out."
)


class CodeOffer(NamedTuple):
    """A search offer Ember's code made on this exchange (ADR-048): the only
    kind of offer the adapter writes a pending confirmation for. kind is
    "ask_first" or "repeat_search"; text is the offer as the user sees it."""

    kind: str
    text: str


@dataclass
class PostGenResult:
    reply: str
    stripped_sources: list[str]
    web_refusal_substituted: bool
    vision_substituted: bool
    ask_first_substituted: bool
    empty_fallback_fired: bool
    stripped_urls: list[dict] = field(default_factory=list)
    kept_urls: list[str] = field(default_factory=list)
    # The code's search offer on this exchange, if it made one.
    code_offer: CodeOffer | None = None


def run_post_gen_pipeline(
    reply: str,
    *,
    intent_class: str,
    web_search_autonomous: bool,
    used_web_search: bool,
    used_vault: bool,
    used_vision: bool,
    web_items: list | None = None,
    vault_sources: list | None = None,
    vision_description: str | None = None,
    confirmation_search_failed: bool = False,
    explicit_search_request: bool = False,
    ask_first_active: bool = False,
    user_message: str | None = None,
    memory_items: list | None = None,
    state_items: list | None = None,
    web_search_status: str | None = None,
    web_search_reason: str | None = None,
) -> PostGenResult:
    """Run source -> vision -> ask-first -> empty-guard against a full reply.

    ask_first_active is the adapter's ask-first decision for this exchange.
    The result's code_offer names the search offer the code made, if any:
    the adapter writes a pending confirmation for it and for nothing else.
    """

    # Judged on the reply as generated, before any substitution below.
    is_refusal = is_refusal_response(reply)
    stripped_sources: list[str] = []
    web_refusal_substituted = False
    vision_substituted = False
    ask_first_substituted = False
    empty_fallback_fired = False

    allowlist: list[str] = []
    if web_items:
        allowlist.extend(extract_web_domains(web_items))
    if vault_sources:
        for entry in vault_sources:
            if isinstance(entry, dict):
                vid = entry.get("id")
                if isinstance(vid, str) and vid:
                    allowlist.append(vid)

    reply, stripped = validate_and_strip_sources(
        reply,
        allowed_sources=allowlist,
        used_web=used_web_search,
        used_vault=used_vault,
        used_vision=used_vision,
    )
    if stripped:
        stripped_sources = stripped
        logger.info("[POSTGEN] stripped fabricated sources: %s", stripped)

    # Web search refusal: if web results were in context but the model
    # still generated "I don't have real-time data" / "check CNN", build
    # a response directly from the snippets. This is the deterministic
    # fallback when prefix injection didn't fully prevent the RLHF refusal.
    reply, web_refusal_substituted = validate_web_search_response(
        reply, web_items=web_items
    )
    if web_refusal_substituted:
        logger.info("[POSTGEN] web search refusal substituted with snippet response")

    reply, vision_substituted = validate_vision_response(
        reply, used_vision=used_vision, vision_description=vision_description
    )
    if vision_substituted:
        logger.info("[POSTGEN] vision refusal substituted")

    # The code's offer on an ask-first exchange (ADR-048): the reply is
    # always SCRIPTED_ASK_FIRST_RESPONSE, whatever the model drafted, since
    # its own offer wording could leave the user unsure that the search goes
    # to the web. A refusal is kept as written and gets no offer (CLAUDE.md
    # implementation standard 2).
    code_offer: CodeOffer | None = None
    if ask_first_active and intent_class == "web_search":
        if is_refusal:
            logger.info("[POSTGEN] refusal kept on an ask-first exchange: no offer")
        else:
            reply = SCRIPTED_ASK_FIRST_RESPONSE
            ask_first_substituted = True
            code_offer = CodeOffer("ask_first", SCRIPTED_ASK_FIRST_RESPONSE)
            logger.info("[POSTGEN] ask-first response substituted")

    # Search failure on a confirmation exchange: substitute the repeat-search
    # offer instead of letting the model narrate or re-offer from scratch.
    # The adapter writes a pending confirmation for it, so the user's next
    # "yes" re-runs the search. A refusal is kept and gets no offer.
    if confirmation_search_failed:
        if is_refusal:
            logger.info("[POSTGEN] refusal kept on a failed confirm search: no offer")
        else:
            reply = RETRY_OFFER_RESPONSE
            ask_first_substituted = True
            code_offer = CodeOffer("repeat_search", RETRY_OFFER_RESPONSE)
            logger.warning("[POSTGEN] confirmation search failed - retry offer substituted")

    # The model never offers a search (ADR-048): on an exchange where the
    # code made no offer, nothing would honour a "yes" to one, so every
    # offer sentence the model wrote is removed. A refusal is never rewritten.
    if code_offer is None and not is_refusal:
        reply, removed = remove_unbacked_offers(reply)
        if removed:
            logger.info("[POSTGEN] removed %d unbacked search offer(s)", removed)

    if not reply or not reply.strip():
        reply = _EMPTY_FALLBACK
        empty_fallback_fired = True
        logger.warning("[POSTGEN] empty-response guard fired")

    if (
        web_search_status == "unavailable"
        and not is_refusal
        and not ask_first_substituted
        and not empty_fallback_fired
    ):
        reply = f"{WEB_SEARCH_UNAVAILABLE_DISCLOSURE}\n\n{reply}"
        logger.info("[POSTGEN] web search unavailable - disclosure line prepended")

    # Repeat-search offer (ADR-048): a search ran on this exchange and failed
    # for a reason a repeat could fix, so the code offers to repeat it. A
    # confirmation exchange already got its substituted offer above, and a
    # refusal gets none.
    if web_search_status == "unavailable" and code_offer is None and not is_refusal:
        if web_search_reason in NO_REPEAT_OFFER_REASONS:
            logger.info("[POSTGEN] no repeat-search offer: reason=%s repeats on retry",
                        web_search_reason)
        else:
            reply = f"{reply}\n\n{REPEAT_SEARCH_OFFER}"
            code_offer = CodeOffer("repeat_search", REPEAT_SEARCH_OFFER)
            logger.info("[POSTGEN] repeat-search offer added (reason=%s)",
                        web_search_reason)

    stripped_urls: list[dict] = []
    kept_urls: list[str] = []
    try:
        url_allowlist = build_url_allowlist(
            web_items=web_items,
            memory_items=memory_items,
            state_items=state_items,
            user_message=user_message,
        )
        reply, stripped_urls, kept_urls = validate_and_strip_urls(
            reply, url_allowlist
        )
        if stripped_urls:
            logger.info(
                "[POSTGEN] stripped fabricated urls (%d stripped, %d kept): %s",
                len(stripped_urls),
                len(kept_urls),
                stripped_urls,
            )
    except Exception as exc:
        logger.warning(
            "[POSTGEN] url_validator failed: %s", type(exc).__name__
        )
        stripped_urls = []
        kept_urls = []

    return PostGenResult(
        reply=reply,
        stripped_sources=stripped_sources,
        web_refusal_substituted=web_refusal_substituted,
        vision_substituted=vision_substituted,
        ask_first_substituted=ask_first_substituted,
        empty_fallback_fired=empty_fallback_fired,
        stripped_urls=stripped_urls,
        kept_urls=kept_urls,
        code_offer=code_offer,
    )
