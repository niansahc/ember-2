"""
src/safety/url_validator.py

Deterministic post-generation validator for fabricated URLs (B-MEM-005).

Problem: at 8B scale qwen3 invents plausible-but-fake URLs (often github.com
paths) when asked about projects, products, or topics with thin or absent
vault context. The instruction-level rule in prompt_builder is a partial
mitigation only; the model's URL-generation prior is too strong for prose
prohibition to fully suppress.

Fix: after generation, scan the response for https?:// URLs, compare each
against a per-turn allowlist built from web search results, retrieved vault
content, and the user's current message, and replace disallowed URLs with
[unverified link] (bare form) or convert disallowed [label](url) markdown
links to bare label text.

Pipeline position: final step of run_post_gen_pipeline, after the empty
guard, so it always sees the text the user will actually receive.

Out of scope (documented):
  - bare-domain mentions without scheme (github.com/foo)
  - schemes other than http/https (mailto:, file://, ftp://)
  - IDN / Unicode hostnames
  - URLs ending in unbalanced ) lose the trailing paren (Wikipedia-style
    paths with balanced parens are preserved)

Streaming caveat: this validator only protects the buffered/stored copy of
the response. The fast-streaming path (B-CON-002) emits raw token chunks to
the client before any post-gen validator runs. Same shape as every existing
validator in post_gen_pipeline; not a regression.

Degenerate case: if every URL in a response is disallowed, the user sees a
list of [unverified link] placeholders. No threshold escalation; revisit
only if eval shows this firing in real usage.

Failure mode: top-level entry is wrapped in try/except by the caller in
post_gen_pipeline (fail-open with WARN log). This module raises freely on
malformed input; the wrapper turns that into a no-op + log.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# URL chars: anything that is not whitespace, angle bracket, quote, or
# backtick. Trailing punctuation is stripped after the match.
_BARE_URL_PATTERN = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)

# Markdown link [label](url). Label may be empty. URL goes to the first
# whitespace or closing paren. Closing paren is optional so a malformed
# link like '[label](url' (model omitted ')') still collapses cleanly to
# bare label rather than leaving orphan '[' and '(' brackets after the
# bare-URL pass strips the URL alone.
_MARKDOWN_LINK_PATTERN = re.compile(
    r"\[([^\]]*)\]\((https?://[^\s)]+)\)?",
    re.IGNORECASE,
)

# Autolink <url>.
_AUTOLINK_PATTERN = re.compile(r"<(https?://[^>\s]+)>", re.IGNORECASE)

# Code spans that should not have URLs stripped.
_FENCED_CODE_PATTERN = re.compile(r"```[\s\S]*?```")
_INLINE_CODE_PATTERN = re.compile(r"`[^`]*`")

_TRAILING_PUNCT = ".,;:!?]>\"'"

_PLACEHOLDER = "[unverified link]"


def extract_urls(text: str) -> list[str]:
    """Return the https?:// URLs found in text, ignoring code blocks.

    Trailing punctuation (one char from .,;:!?]>"' or unbalanced )) is
    stripped from each result so the caller sees the actual URL.
    """
    if not text or not isinstance(text, str):
        return []
    code_spans = _find_code_spans(text)
    urls: list[str] = []
    for match in _BARE_URL_PATTERN.finditer(text):
        if _in_any_span(match.start(), code_spans):
            continue
        cleaned = _strip_trailing_punct(match.group(0))
        if cleaned:
            urls.append(cleaned)
    return urls


def build_url_allowlist(
    web_items: list | None = None,
    memory_items: list | None = None,
    state_items: list | None = None,
    user_message: str | None = None,
) -> set[tuple[str, str]]:
    """Build the per-turn URL allowlist as a set of (host, path) tuples.

    Sources:
      - web_items[i]["url"]                full URL from SearXNG
      - memory_items[i].content            scan for https?://
      - state_items[i].text                scan for https?://
      - user_message                       scan for https?:// in current turn
    """
    allowlist: set[tuple[str, str]] = set()

    if web_items:
        for item in web_items:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if isinstance(url, str) and url:
                canonical = _canonicalize_url(url)
                if canonical:
                    allowlist.add(canonical)

    if memory_items:
        for item in memory_items:
            content = getattr(item, "content", None)
            if isinstance(content, str):
                for url in extract_urls(content):
                    canonical = _canonicalize_url(url)
                    if canonical:
                        allowlist.add(canonical)

    if state_items:
        for item in state_items:
            text_value = getattr(item, "text", None)
            if isinstance(text_value, str):
                for url in extract_urls(text_value):
                    canonical = _canonicalize_url(url)
                    if canonical:
                        allowlist.add(canonical)

    if isinstance(user_message, str) and user_message:
        for url in extract_urls(user_message):
            canonical = _canonicalize_url(url)
            if canonical:
                allowlist.add(canonical)

    return allowlist


def is_url_allowed(url: str, allowlist: set[tuple[str, str]]) -> bool:
    """Bidirectional path-prefix match against the canonical allowlist.

    Match rule (host equality required first, then any of):
      - allowlist path is empty (any path on host)
      - emitted path equals allowlist path
      - emitted path is a child of allowlist path (emitted starts with
        allowlist_path + "/") -- e.g. allowlist /sdk permits /sdk/blob/main
      - allowlist path is a child of emitted path (allowlist starts with
        emitted_path + "/") -- e.g. allowlist /sdk/blob/main permits the
        repo root /sdk. This handles the case where SearXNG returned a
        deeper page URL but the model emitted the parent.
      - emitted path is empty (bare host root) and any URL on that host
        is in the allowlist -- the host root is in scope when any deeper
        URL on it is verified.

    Implicit-allow hosts (localhost, private/loopback IPv4 ranges,
    IPv6 ::1 and fe80::/10) always pass.
    """
    if not url:
        return False
    canonical = _canonicalize_url(url)
    if canonical is None:
        return False
    url_host, url_path = canonical

    if _is_implicit_allow(url_host):
        return True

    for allow_host, allow_path in allowlist:
        if url_host != allow_host:
            continue
        if allow_path == "":
            return True
        if url_path == "":
            return True
        if url_path == allow_path:
            return True
        if url_path.startswith(allow_path + "/"):
            return True
        if allow_path.startswith(url_path + "/"):
            return True
    return False


def validate_and_strip_urls(
    reply: str,
    allowlist: set[tuple[str, str]],
) -> tuple[str, list[dict], list[str]]:
    """Strip disallowed URLs from a completed response.

    Three passes in order: markdown links, autolinks, bare URLs. Each pass
    re-finds code-block spans on the current text since prior passes may
    have shortened it.

    Returns (cleaned_reply, stripped_urls, kept_urls).
      stripped_urls: list of {"url": str, "form": "bare" | "markdown"}
      kept_urls:     deduplicated list of URL strings that passed
    """
    if not reply or not isinstance(reply, str):
        return reply, [], []

    stripped: list[dict] = []
    kept: list[str] = []

    code_spans = _find_code_spans(reply)

    def md_repl(match: re.Match) -> str:
        if _in_any_span(match.start(), code_spans):
            return match.group(0)
        label, url = match.group(1), match.group(2)
        url_clean = _strip_trailing_punct(url)
        if is_url_allowed(url_clean, allowlist):
            kept.append(url_clean)
            return match.group(0)
        stripped.append({"url": url_clean, "form": "markdown"})
        return label

    reply = _MARKDOWN_LINK_PATTERN.sub(md_repl, reply)

    code_spans = _find_code_spans(reply)

    def auto_repl(match: re.Match) -> str:
        if _in_any_span(match.start(), code_spans):
            return match.group(0)
        url_clean = _strip_trailing_punct(match.group(1))
        if is_url_allowed(url_clean, allowlist):
            kept.append(url_clean)
            return match.group(0)
        stripped.append({"url": url_clean, "form": "bare"})
        return _PLACEHOLDER

    reply = _AUTOLINK_PATTERN.sub(auto_repl, reply)

    code_spans = _find_code_spans(reply)

    def bare_repl(match: re.Match) -> str:
        if _in_any_span(match.start(), code_spans):
            return match.group(0)
        full = match.group(0)
        url = _strip_trailing_punct(full)
        suffix = full[len(url):]
        if is_url_allowed(url, allowlist):
            kept.append(url)
            return full
        stripped.append({"url": url, "form": "bare"})
        return _PLACEHOLDER + suffix

    reply = _BARE_URL_PATTERN.sub(bare_repl, reply)

    seen: set[str] = set()
    deduped_kept: list[str] = []
    for url in kept:
        if url not in seen:
            seen.add(url)
            deduped_kept.append(url)

    return reply, stripped, deduped_kept


def extract_host(value: str) -> str:
    """Return lowercase hostname with www. stripped, or "" on parse failure.

    Accepts a full URL or a bare domain string (no scheme required). Used by
    both this module (URL canonicalisation) and source_validator (citation
    hostname matching). Centralised so attack-pattern coverage stays in sync.
    """
    if not value or not isinstance(value, str):
        return ""
    value = value.strip().lower()
    if not value:
        return ""
    if "://" not in value:
        value = "http://" + value
    try:
        host = urlparse(value).hostname or ""
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _canonicalize_url(url: str) -> tuple[str, str] | None:
    """Return (host, path) with host lowercased and www. stripped, path with
    a single trailing slash removed. Query and fragment are dropped. Returns
    None for non-http(s) or unparseable input.
    """
    if not url or not isinstance(url, str):
        return None
    try:
        parsed = urlparse(url.strip())
    except (ValueError, AttributeError):
        return None
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower()
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path or ""
    if path == "/":
        path = ""
    elif len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return host, path


def _is_implicit_allow(host: str) -> bool:
    """True for localhost, IPv4 loopback/private ranges, IPv6 loopback and
    link-local. These are zero-fabrication-risk and should never be stripped.
    """
    if not host:
        return False
    h = host.lower().strip()
    if h == "localhost":
        return True
    if h == "::1" or h.startswith("[::1]"):
        return True
    if h.startswith("fe80:") or h.startswith("[fe80:"):
        return True
    parts = h.split(".")
    if len(parts) == 4:
        try:
            octets = [int(p) for p in parts]
        except ValueError:
            return False
        if any(o < 0 or o > 255 for o in octets):
            return False
        if octets[0] == 127:
            return True
        if octets[0] == 10:
            return True
        if octets[0] == 192 and octets[1] == 168:
            return True
        if octets[0] == 172 and 16 <= octets[1] <= 31:
            return True
    return False


def _find_code_spans(text: str) -> list[tuple[int, int]]:
    """Return (start, end) ranges covering fenced and inline code in text."""
    spans: list[tuple[int, int]] = []
    for match in _FENCED_CODE_PATTERN.finditer(text):
        spans.append((match.start(), match.end()))
    for match in _INLINE_CODE_PATTERN.finditer(text):
        s, e = match.start(), match.end()
        if not _in_any_span(s, spans):
            spans.append((s, e))
    return spans


def _in_any_span(pos: int, spans: list[tuple[int, int]]) -> bool:
    for start, end in spans:
        if start <= pos < end:
            return True
    return False


def _strip_trailing_punct(url: str) -> str:
    """Strip a single trailing punctuation char that is sentence/syntax
    punctuation rather than part of the URL. Closing paren is only stripped
    when the URL contains no opening paren (so balanced parens like
    /Foo_(bar) are preserved)."""
    if not url:
        return url
    last = url[-1]
    if last == ")":
        if "(" not in url:
            return url[:-1]
        return url
    if last in _TRAILING_PUNCT:
        return url[:-1]
    return url
