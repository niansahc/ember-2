"""
tests/test_prompt_builder_web_section.py

Tests rendering of the <web_search_results> prompt section after the
freshness signal was added. The section now emits a 'published: <age>'
line per item using _format_relative_age, with 'unknown' for items
whose web_search did not surface a publishedDate.

No existing test covered _build_web_search_section; this file fills
that gap.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.context.models import ContextPacket
from src.llm.prompt_builder import PromptBuilder, _format_relative_age


# Fixed reference 'now' for deterministic age rendering across tests.
_NOW = datetime(2026, 5, 3, 14, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# _format_relative_age boundaries
# ---------------------------------------------------------------------------

def test_relative_age_unknown_for_none():
    assert _format_relative_age(None, now=_NOW) == "unknown"


def test_relative_age_unknown_for_empty_string():
    assert _format_relative_age("", now=_NOW) == "unknown"


def test_relative_age_unknown_for_unparseable():
    assert _format_relative_age("not a date", now=_NOW) == "unknown"


def test_relative_age_minutes():
    iso = (_NOW - timedelta(minutes=15)).isoformat()
    assert _format_relative_age(iso, now=_NOW) == "15 minutes ago"


def test_relative_age_singular_minute():
    iso = (_NOW - timedelta(minutes=1)).isoformat()
    assert _format_relative_age(iso, now=_NOW) == "1 minute ago"


def test_relative_age_hours():
    iso = (_NOW - timedelta(hours=3)).isoformat()
    assert _format_relative_age(iso, now=_NOW) == "3 hours ago"


def test_relative_age_days():
    iso = (_NOW - timedelta(days=5)).isoformat()
    assert _format_relative_age(iso, now=_NOW) == "5 days ago"


def test_relative_age_months():
    iso = (_NOW - timedelta(days=90)).isoformat()
    assert _format_relative_age(iso, now=_NOW) == "3 months ago"


def test_relative_age_years():
    iso = (_NOW - timedelta(days=400)).isoformat()
    assert _format_relative_age(iso, now=_NOW) == "1 year ago"


def test_relative_age_naive_iso_assumed_utc():
    """ISO string without timezone should be treated as UTC, not raise."""
    iso = "2026-05-03T11:00:00"  # 3 hours before _NOW
    assert _format_relative_age(iso, now=_NOW) == "3 hours ago"


# ---------------------------------------------------------------------------
# _build_web_search_section rendering
# ---------------------------------------------------------------------------

def _bare_builder():
    """Construct PromptBuilder without invoking __init__ — the web
    section method does not depend on system_prompt or other init state."""
    return PromptBuilder.__new__(PromptBuilder)


def test_section_empty_when_no_web_items():
    builder = _bare_builder()
    packet = ContextPacket(user_message="hello", web_items=[])
    assert builder._build_web_search_section(packet) == ""


def test_section_renders_dated_item_with_relative_age():
    builder = _bare_builder()
    iso = (_NOW - timedelta(hours=3)).isoformat()
    packet = ContextPacket(
        user_message="latest news",
        web_items=[{
            "title": "Article",
            "url": "https://example.com/a",
            "snippet": "snippet text",
            "published_at": iso,
        }],
    )
    with patch("src.llm.prompt_builder.datetime") as mock_dt:
        mock_dt.now.return_value = _NOW
        mock_dt.fromisoformat = datetime.fromisoformat
        section = builder._build_web_search_section(packet)
    assert "published: 3 hours ago" in section
    assert "Article" in section
    assert "https://example.com/a" in section
    assert "snippet text" in section


def test_section_renders_undated_item_as_unknown():
    builder = _bare_builder()
    packet = ContextPacket(
        user_message="evergreen",
        web_items=[{
            "title": "Wikipedia entry",
            "url": "https://example.com/wiki",
            "snippet": "snippet text",
            "published_at": None,
        }],
    )
    section = builder._build_web_search_section(packet)
    assert "published: unknown" in section


def test_section_handles_missing_published_at_key():
    """Backward compat: existing callers that don't set published_at
    (e.g. legacy test fixtures) render as unknown, not crash."""
    builder = _bare_builder()
    packet = ContextPacket(
        user_message="legacy",
        web_items=[{
            "title": "Legacy fixture",
            "url": "https://example.com/legacy",
            "snippet": "snippet text",
        }],
    )
    section = builder._build_web_search_section(packet)
    assert "published: unknown" in section


def test_section_renders_mixed_dated_and_undated():
    builder = _bare_builder()
    iso = (_NOW - timedelta(hours=2)).isoformat()
    packet = ContextPacket(
        user_message="mixed",
        web_items=[
            {"title": "Dated", "url": "https://example.com/d",
             "snippet": "s", "published_at": iso},
            {"title": "Undated", "url": "https://example.com/u",
             "snippet": "s", "published_at": None},
        ],
    )
    with patch("src.llm.prompt_builder.datetime") as mock_dt:
        mock_dt.now.return_value = _NOW
        mock_dt.fromisoformat = datetime.fromisoformat
        section = builder._build_web_search_section(packet)
    assert "published: 2 hours ago" in section
    assert "published: unknown" in section
