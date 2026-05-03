"""
src/llm/source_validator.py

Deterministic post-generation validator for fabricated source citations.

Problem: at 8B scale, the model has learned the "(source: X)" format as a
confidence-signaling pattern from training data and deploys it regardless
of whether X is a grounded source. Observed across UAT in five unrelated
domains: city stats, current time, vision tools, financial data, Nobel
Prize. Citations included real-sounding domains, invented tool names, and
multilingual fabrication.

Fix: after generation, scan the response for (source: X) patterns, verify
X against a per-turn allowlist built from actual context (web domains
returned by tool, vault item IDs in the context packet, vision flag),
and strip anything not on the allowlist. Log stripped citations so eval
can track fabrication rate.
"""

from __future__ import annotations

import re

from src.safety.url_validator import extract_host

# Patterns: prefer the parenthesised form, but also catch trailing bare
# "source: X" at the end of a sentence. Both case-insensitive.
_PAREN_SOURCE_PATTERN = re.compile(
    r"\s*\(source:\s*([^)]+?)\s*\)",
    re.IGNORECASE,
)
_TRAILING_SOURCE_PATTERN = re.compile(
    r"\s+source:\s*([^\s\.,;]+(?:\.[^\s\.,;]+)*)\s*(?=[\.\!\?]|$)",
    re.IGNORECASE,
)


def extract_web_domains(web_items: list) -> list[str]:
    """Pull hostnames from web search result items.

    Each item may carry its source under different keys depending on the
    tool implementation — handle url, source, domain, or link defensively.
    Returns a deduplicated list of lowercase hostnames.
    """
    if not web_items:
        return []
    domains: list[str] = []
    seen: set[str] = set()
    for item in web_items:
        if not isinstance(item, dict):
            continue
        for key in ("url", "source", "link", "domain"):
            value = item.get(key)
            if not value or not isinstance(value, str):
                continue
            host = _hostname_of(value)
            if host and host not in seen:
                seen.add(host)
                domains.append(host)
            break
    return domains


def _hostname_of(value: str) -> str:
    """Return a lowercase hostname from a URL or a bare domain string."""
    return extract_host(value)


def _citation_is_allowed(
    citation: str,
    allowed_sources: list[str],
    used_web: bool,
    used_vault: bool,
    used_vision: bool,
) -> bool:
    """Decide whether a captured citation value should be preserved."""
    value = citation.strip().lower()
    if not value:
        return False

    # Strip any trailing qualifier (e.g. "low confidence — based on...") so
    # the primary citation token is what gets matched.
    primary = value.split(",", 1)[0].strip()

    # Literal tokens permitted only when the corresponding source fired.
    if used_web and primary in {"web_search_results", "web_search", "web"}:
        return True
    if used_vault and primary in {"vault_memory", "vault", "memory"}:
        return True
    if used_vision and primary in {
        "from the image",
        "image",
        "vision",
        "vision_context",
    }:
        return True

    # Normalise potential host forms for comparison against web domains.
    host_primary = _hostname_of(primary) or primary
    for allowed in allowed_sources:
        if not allowed:
            continue
        allowed_lower = allowed.strip().lower()
        if not allowed_lower:
            continue
        if allowed_lower == primary:
            return True
        if allowed_lower == host_primary:
            return True
        # Treat the allowlist entry as a host and check suffix equality
        # (covers subdomain vs. root-domain mentions).
        host_allowed = _hostname_of(allowed_lower) or allowed_lower
        if host_primary and (
            host_primary == host_allowed
            or host_primary.endswith("." + host_allowed)
            or host_allowed.endswith("." + host_primary)
        ):
            return True
    return False


def validate_and_strip_sources(
    response: str,
    allowed_sources: list[str],
    used_web: bool,
    used_vault: bool,
    used_vision: bool,
) -> tuple[str, list[str]]:
    """Strip fabricated source citations from a completed response.

    Returns (cleaned_response, stripped_citations). The caller should log
    the stripped list so eval can track fabrication rate over time.
    """
    if not response:
        return response, []

    stripped: list[str] = []

    def _replace_paren(match: re.Match) -> str:
        captured = match.group(1)
        if _citation_is_allowed(
            captured, allowed_sources, used_web, used_vault, used_vision
        ):
            return match.group(0)
        stripped.append(captured.strip())
        return ""

    def _replace_trailing(match: re.Match) -> str:
        captured = match.group(1)
        if _citation_is_allowed(
            captured, allowed_sources, used_web, used_vault, used_vision
        ):
            return match.group(0)
        stripped.append(captured.strip())
        return ""

    cleaned = _PAREN_SOURCE_PATTERN.sub(_replace_paren, response)
    cleaned = _TRAILING_SOURCE_PATTERN.sub(_replace_trailing, cleaned)
    # Collapse any double-spaces introduced by stripping a mid-sentence
    # parenthetical.
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([\.\!\?,;])", r"\1", cleaned)
    return cleaned.rstrip(), stripped
