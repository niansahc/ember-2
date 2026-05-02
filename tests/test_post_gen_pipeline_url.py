"""
End-to-end integration test for the URL validator wired into
run_post_gen_pipeline (B-MEM-005 v0.17.2 follow-up).

Synthetic fixtures only per the Vault Privacy Rule.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.llm.post_gen_pipeline import run_post_gen_pipeline


def test_27_end_to_end_mixed_allowed_disallowed_urls():
    """Mixed allowed and disallowed URLs flow through run_post_gen_pipeline.
    The final reply has the disallowed URL replaced; PostGenResult.stripped_urls
    and kept_urls are populated correctly.
    """
    web_items = [
        {
            "url": "https://example.test/article",
            "title": "Article",
            "snippet": "An article snippet.",
        }
    ]
    memory_items = [
        SimpleNamespace(content="Earlier I bookmarked https://example.test/note")
    ]
    state_items = [
        SimpleNamespace(text="Active project: https://example.test/project")
    ]
    user_message = "What about https://example.test/from-user?"

    reply = (
        "From web: https://example.test/article\n"
        "From vault: https://example.test/note\n"
        "From state: https://example.test/project\n"
        "From your message: https://example.test/from-user\n"
        "Fabricated: https://fake.example/invented-repo"
    )

    result = run_post_gen_pipeline(
        reply,
        intent_class="general",
        web_search_autonomous=True,
        used_web_search=True,
        used_vault=True,
        used_vision=False,
        web_items=web_items,
        vault_sources=None,
        user_message=user_message,
        memory_items=memory_items,
        state_items=state_items,
    )

    assert "https://example.test/article" in result.reply
    assert "https://example.test/note" in result.reply
    assert "https://example.test/project" in result.reply
    assert "https://example.test/from-user" in result.reply
    assert "fake.example" not in result.reply
    assert "[unverified link]" in result.reply

    assert len(result.stripped_urls) == 1
    assert result.stripped_urls[0]["url"] == "https://fake.example/invented-repo"
    assert result.stripped_urls[0]["form"] == "bare"

    assert set(result.kept_urls) == {
        "https://example.test/article",
        "https://example.test/note",
        "https://example.test/project",
        "https://example.test/from-user",
    }


def test_27b_no_urls_anywhere_keeps_telemetry_empty():
    """Sanity: a clean response with no URLs and no allowlist sources gets
    empty stripped_urls and kept_urls (regression guard against the new
    fields accidentally collecting noise)."""
    result = run_post_gen_pipeline(
        "Just a normal response with no links.",
        intent_class="general",
        web_search_autonomous=True,
        used_web_search=False,
        used_vault=False,
        used_vision=False,
    )
    assert result.stripped_urls == []
    assert result.kept_urls == []
    assert result.reply == "Just a normal response with no links."


def test_27c_empty_fallback_response_passes_through_url_validator():
    """An empty model reply triggers the empty-fallback guard, then the URL
    validator runs against the fallback string. Fallback contains no URLs,
    so telemetry stays empty."""
    result = run_post_gen_pipeline(
        "",
        intent_class="general",
        web_search_autonomous=True,
        used_web_search=False,
        used_vault=False,
        used_vision=False,
    )
    assert result.empty_fallback_fired is True
    assert result.stripped_urls == []
    assert result.kept_urls == []
