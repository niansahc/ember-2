"""
src/llm/post_gen_pipeline.py

Unified post-generation validator pipeline.

Runs five validators in a fixed order against a completed response:

  1. source allowlist (strip fabricated citations)
  2. web search refusal (deterministic fallback when model ignores web results)
  3. vision refusal (substitute when vision fired but model refused)
  4. ask-first (substitute when web_search intent skipped the confirmation)
  5. URL validator (B-MEM-005: strip fabricated https?:// URLs against a
     per-turn allowlist; runs last so it sees the final returned text)

Followed by an empty-response guard that fills zero-byte replies with a
fallback so the streaming path never emits a blank message to the client
.

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

from src.llm.ask_first_validator import validate_ask_first_response
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

_EMPTY_FALLBACK = (
    "I had trouble generating a response to that. Try rephrasing, or let me "
    "know what you're actually trying to figure out."
)


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
) -> PostGenResult:
    """Run source → vision → ask-first → empty-guard against a full reply.

    ask_first_mode is computed internally as (intent == web_search AND
    web_search_autonomous is False). Callers pass the raw signals and this
    function applies the routing rule.
    """

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

    ask_first_mode = ask_first_active
    reply, ask_first_substituted = validate_ask_first_response(
        reply, intent_class=intent_class, ask_first_mode=ask_first_mode
    )
    if ask_first_substituted:
        logger.info("[POSTGEN] ask-first response substituted")

    # Search failure on confirmation turn — substitute with a retry offer
    # instead of letting the model narrate or re-offer from scratch.
    if confirmation_search_failed:
        reply = (
            "I tried searching but hit an error. Want me to try again?"
        )
        ask_first_substituted = True
        logger.warning("[POSTGEN] confirmation search failed - retry offer substituted")

    if not reply or not reply.strip():
        reply = _EMPTY_FALLBACK
        empty_fallback_fired = True
        logger.warning("[POSTGEN] empty-response guard fired")

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
    )
