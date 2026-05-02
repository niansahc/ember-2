"""
Tests for src/safety/url_validator.py (B-MEM-005 v0.17.2 follow-up).

Synthetic fixtures only per the Vault Privacy Rule. No real proper names,
no real vault content, no production URLs.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.safety.url_validator import (
    build_url_allowlist,
    extract_urls,
    is_url_allowed,
    validate_and_strip_urls,
)


def _allowlist(*urls: str) -> set[tuple[str, str]]:
    """Helper: build an allowlist set from a list of full URLs."""
    return build_url_allowlist(web_items=[{"url": u} for u in urls])


# ----------------------------------------------------------------------
# Pass-through cases (1-16)
# ----------------------------------------------------------------------


def test_01_empty_reply_unchanged():
    cleaned, stripped, kept = validate_and_strip_urls("", _allowlist())
    assert cleaned == ""
    assert stripped == []
    assert kept == []


def test_02_no_urls_in_reply_unchanged():
    reply = "Just a normal sentence with no links at all."
    cleaned, stripped, kept = validate_and_strip_urls(reply, _allowlist())
    assert cleaned == reply
    assert stripped == []
    assert kept == []


def test_03_url_from_web_items_kept():
    allowlist = build_url_allowlist(
        web_items=[{"url": "https://example.test/docs"}]
    )
    reply = "Found it at https://example.test/docs"
    cleaned, stripped, kept = validate_and_strip_urls(reply, allowlist)
    assert cleaned == reply
    assert stripped == []
    assert kept == ["https://example.test/docs"]


def test_04_url_from_memory_item_content_kept():
    memory = [SimpleNamespace(content="Earlier note: see https://example.test/x")]
    allowlist = build_url_allowlist(memory_items=memory)
    reply = "The link is https://example.test/x"
    cleaned, stripped, kept = validate_and_strip_urls(reply, allowlist)
    assert cleaned == reply
    assert stripped == []
    assert kept == ["https://example.test/x"]


def test_05_url_from_state_item_text_kept():
    state = [SimpleNamespace(text="Active project tracker: https://example.test/proj")]
    allowlist = build_url_allowlist(state_items=state)
    reply = "Tracker is at https://example.test/proj"
    cleaned, stripped, kept = validate_and_strip_urls(reply, allowlist)
    assert cleaned == reply
    assert stripped == []
    assert kept == ["https://example.test/proj"]


def test_06_url_from_user_message_kept():
    user_msg = "Look at https://example.test/page please"
    allowlist = build_url_allowlist(user_message=user_msg)
    reply = "Sure: https://example.test/page"
    cleaned, stripped, kept = validate_and_strip_urls(reply, allowlist)
    assert cleaned == reply
    assert stripped == []
    assert kept == ["https://example.test/page"]


def test_07_path_prefix_match_kept():
    allowlist = _allowlist("https://example.test/sdk")
    reply = "See https://example.test/sdk/blob/main/README.md"
    cleaned, stripped, kept = validate_and_strip_urls(reply, allowlist)
    assert cleaned == reply
    assert stripped == []
    assert "https://example.test/sdk/blob/main/README.md" in kept


def test_08_localhost_implicit_allow():
    reply = "API runs at http://localhost:8000/v1/chat"
    cleaned, stripped, kept = validate_and_strip_urls(reply, _allowlist())
    assert cleaned == reply
    assert stripped == []
    assert "http://localhost:8000/v1/chat" in kept


def test_09_loopback_ipv4_implicit_allow():
    reply = "Try http://127.0.0.1:9000/ping"
    cleaned, stripped, kept = validate_and_strip_urls(reply, _allowlist())
    assert cleaned == reply
    assert stripped == []
    assert "http://127.0.0.1:9000/ping" in kept


def test_10_private_ipv4_implicit_allow():
    reply = "Router admin: http://192.168.1.1/admin"
    cleaned, stripped, kept = validate_and_strip_urls(reply, _allowlist())
    assert cleaned == reply
    assert stripped == []
    assert "http://192.168.1.1/admin" in kept


def test_11_url_inside_fenced_code_block_skipped():
    reply = (
        "Here is an example:\n"
        "```bash\n"
        "curl https://fake.example/never-allowed\n"
        "```\n"
        "End."
    )
    cleaned, stripped, kept = validate_and_strip_urls(reply, _allowlist())
    assert cleaned == reply
    assert stripped == []
    assert kept == []


def test_12_url_inside_inline_backticks_skipped():
    reply = "Use `https://fake.example/x` as a placeholder."
    cleaned, stripped, kept = validate_and_strip_urls(reply, _allowlist())
    assert cleaned == reply
    assert stripped == []
    assert kept == []


def test_13_web_refusal_substituted_text_passes_through():
    """Defensive ordering test: URLs from web_items survive when the
    response text was built from those same web_items (simulating
    validate_web_search_response substitution)."""
    web_items = [
        {"url": "https://example.test/article-a", "title": "A", "snippet": "..."},
        {"url": "https://example.test/article-b", "title": "B", "snippet": "..."},
    ]
    allowlist = build_url_allowlist(web_items=web_items)
    reply = (
        "Based on what I found:\n"
        "- A: https://example.test/article-a\n"
        "- B: https://example.test/article-b"
    )
    cleaned, stripped, kept = validate_and_strip_urls(reply, allowlist)
    assert cleaned == reply
    assert stripped == []
    assert set(kept) == {
        "https://example.test/article-a",
        "https://example.test/article-b",
    }


def test_14_scheme_variation_canonicalizes():
    allowlist = _allowlist("https://example.test/foo")
    reply = "Or http://example.test/foo"
    cleaned, stripped, kept = validate_and_strip_urls(reply, allowlist)
    assert cleaned == reply
    assert stripped == []
    assert "http://example.test/foo" in kept


def test_15_www_variation_canonicalizes():
    allowlist = _allowlist("https://example.test/foo")
    reply = "Mirror at https://www.example.test/foo"
    cleaned, stripped, kept = validate_and_strip_urls(reply, allowlist)
    assert cleaned == reply
    assert stripped == []
    assert "https://www.example.test/foo" in kept


def test_16_trailing_slash_canonicalizes():
    allowlist = _allowlist("https://example.test/foo/")
    reply = "See https://example.test/foo"
    cleaned, stripped, kept = validate_and_strip_urls(reply, allowlist)
    assert cleaned == reply
    assert stripped == []
    assert "https://example.test/foo" in kept


# ----------------------------------------------------------------------
# Strip cases (17-23)
# ----------------------------------------------------------------------


def test_17_disallowed_bare_url_stripped_with_placeholder():
    reply = "Try https://fake.example/repo for details."
    cleaned, stripped, kept = validate_and_strip_urls(reply, _allowlist())
    assert cleaned == "Try [unverified link] for details."
    assert stripped == [{"url": "https://fake.example/repo", "form": "bare"}]
    assert kept == []


def test_18_disallowed_markdown_link_becomes_bare_label():
    reply = "Check the [docs](https://fake.example/docs) here."
    cleaned, stripped, kept = validate_and_strip_urls(reply, _allowlist())
    assert cleaned == "Check the docs here."
    assert stripped == [{"url": "https://fake.example/docs", "form": "markdown"}]
    assert kept == []


def test_19_disallowed_autolink_stripped_with_placeholder():
    reply = "Reference: <https://fake.example/article>"
    cleaned, stripped, kept = validate_and_strip_urls(reply, _allowlist())
    assert cleaned == "Reference: [unverified link]"
    assert stripped == [{"url": "https://fake.example/article", "form": "bare"}]
    assert kept == []


def test_20_trailing_punctuation_preserved_outside_replacement():
    reply = "Visit https://fake.example/x."
    cleaned, stripped, kept = validate_and_strip_urls(reply, _allowlist())
    assert cleaned == "Visit [unverified link]."
    assert stripped == [{"url": "https://fake.example/x", "form": "bare"}]
    assert kept == []


def test_21_path_prefix_sibling_collision_stripped():
    """Boundary check: allowlist /sdk does not permit /sdk-malicious."""
    allowlist = _allowlist("https://github.example/org/sdk")
    reply = "Try https://github.example/org/sdk-malicious for the bad one."
    cleaned, stripped, kept = validate_and_strip_urls(reply, allowlist)
    assert "[unverified link]" in cleaned
    assert "sdk-malicious" not in cleaned
    assert any(s["url"] == "https://github.example/org/sdk-malicious" for s in stripped)
    assert kept == []


def test_22_mixed_allowed_and_disallowed():
    allowlist = _allowlist("https://example.test/good")
    reply = (
        "Allowed: https://example.test/good\n"
        "Bad: https://fake.example/bad"
    )
    cleaned, stripped, kept = validate_and_strip_urls(reply, allowlist)
    assert "https://example.test/good" in cleaned
    assert "fake.example" not in cleaned
    assert "[unverified link]" in cleaned
    assert any(s["url"] == "https://fake.example/bad" for s in stripped)
    assert "https://example.test/good" in kept


def test_23_multiple_disallowed_bare_urls_all_stripped():
    reply = (
        "Three bad links:\n"
        "1. https://fake.example/a\n"
        "2. https://fake.example/b\n"
        "3. https://fake.example/c"
    )
    cleaned, stripped, kept = validate_and_strip_urls(reply, _allowlist())
    assert cleaned.count("[unverified link]") == 3
    assert len(stripped) == 3
    assert {s["url"] for s in stripped} == {
        "https://fake.example/a",
        "https://fake.example/b",
        "https://fake.example/c",
    }
    assert all(s["form"] == "bare" for s in stripped)
    assert kept == []


# ----------------------------------------------------------------------
# Failure mode (24-26)
# ----------------------------------------------------------------------


def test_24_none_reply_returns_empty():
    cleaned, stripped, kept = validate_and_strip_urls(None, _allowlist())  # type: ignore[arg-type]
    assert cleaned is None
    assert stripped == []
    assert kept == []


def test_25_memory_item_with_nonstring_content_handled():
    """Vault item shape variation: a non-string content field must not
    crash allowlist building."""
    memory = [
        SimpleNamespace(content=None),
        SimpleNamespace(content=123),
        SimpleNamespace(content="A real one with https://example.test/ok"),
    ]
    allowlist = build_url_allowlist(memory_items=memory)
    assert ("example.test", "/ok") in allowlist
    reply = "Link: https://example.test/ok"
    cleaned, _, kept = validate_and_strip_urls(reply, allowlist)
    assert cleaned == reply
    assert "https://example.test/ok" in kept


def test_26_user_message_none_uses_other_sources():
    web_items = [{"url": "https://example.test/from-web"}]
    allowlist = build_url_allowlist(web_items=web_items, user_message=None)
    reply = "Got it: https://example.test/from-web"
    cleaned, stripped, kept = validate_and_strip_urls(reply, allowlist)
    assert cleaned == reply
    assert stripped == []
    assert kept == ["https://example.test/from-web"]


# ----------------------------------------------------------------------
# Direct unit tests on helpers (extra coverage)
# ----------------------------------------------------------------------


def test_extract_urls_strips_trailing_punctuation():
    text = "Three: https://a.test, https://b.test! and https://c.test."
    urls = extract_urls(text)
    assert urls == ["https://a.test", "https://b.test", "https://c.test"]


def test_extract_urls_preserves_balanced_parens_in_path():
    text = "Wikipedia-style: https://wiki.test/Foo_(bar) end."
    urls = extract_urls(text)
    assert urls == ["https://wiki.test/Foo_(bar)"]


def test_is_url_allowed_empty_path_in_allowlist_permits_any_path():
    allowlist = build_url_allowlist(web_items=[{"url": "https://example.test"}])
    assert is_url_allowed("https://example.test/anything", allowlist)
    assert is_url_allowed("https://example.test/deep/path", allowlist)


def test_is_url_allowed_rejects_different_host():
    allowlist = _allowlist("https://example.test/x")
    assert not is_url_allowed("https://other.test/x", allowlist)


def test_build_url_allowlist_dedupes_across_sources():
    web = [{"url": "https://example.test/x"}]
    memory = [SimpleNamespace(content="Mentioned https://example.test/x earlier.")]
    allowlist = build_url_allowlist(web_items=web, memory_items=memory)
    assert allowlist == {("example.test", "/x")}
