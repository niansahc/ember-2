"""
tests/test_vault_citation.py

Tests for vault citation signal — X-Ember-Vault-Used header and
vault_sources SSE event emitted when vault records are retrieved.
"""

from src.api.openai_adapter import _build_vault_sources, _format_source_date
from src.context.models import ContextItem


def _make_item(
    memory_type: str = "conversation",
    timestamp: str = "2026-04-10T14-30-00",
    content: str = "A test memory record with enough content to pass filters.",
    score: float = 0.6,
) -> ContextItem:
    return ContextItem(
        id=f"test-{hash(content) % 10000}",
        content=content,
        source=memory_type,
        item_type=memory_type,
        memory_type=memory_type,
        score=score,
        timestamp=timestamp,
    )


class TestBuildVaultSources:

    def test_returns_entries_for_non_profile_items(self):
        """Non-profile memory items produce vault source entries."""
        from src.context.models import ContextPacket
        packet = ContextPacket(
            user_message="test",
            memory_items=[
                _make_item("conversation", "2026-03-15T10-00-00"),
                _make_item("journal", "2026-02-03T08-00-00"),
            ],
        )
        sources = _build_vault_sources(packet)
        assert len(sources) == 2
        assert sources[0]["type"] == "conversation"
        assert sources[1]["type"] == "journal"

    def test_excludes_profile_items(self):
        """Profile items are always injected and should not be cited."""
        from src.context.models import ContextPacket
        packet = ContextPacket(
            user_message="test",
            memory_items=[
                _make_item("profile", "2026-04-01T12-00-00"),
                _make_item("conversation", "2026-04-01T12-00-00"),
            ],
        )
        sources = _build_vault_sources(packet)
        assert len(sources) == 1
        assert sources[0]["type"] == "conversation"

    def test_empty_when_no_items(self):
        from src.context.models import ContextPacket
        packet = ContextPacket(user_message="test")
        sources = _build_vault_sources(packet)
        assert sources == []

    def test_includes_reflection_items(self):
        from src.context.models import ContextPacket
        packet = ContextPacket(
            user_message="test",
            memory_items=[],
            reflection_items=[
                _make_item("reflection", "2026-04-05T09-00-00"),
            ],
        )
        sources = _build_vault_sources(packet)
        assert len(sources) == 1
        assert sources[0]["type"] == "reflection"

    def test_summary_contains_type_and_date(self):
        from src.context.models import ContextPacket
        packet = ContextPacket(
            user_message="test",
            memory_items=[
                _make_item("conversation", "2026-03-15T10-00-00"),
            ],
        )
        sources = _build_vault_sources(packet)
        summary = sources[0]["summary"]
        assert "conversation" in summary
        assert "March" in summary
        assert "15" in summary

    def test_source_has_required_fields(self):
        from src.context.models import ContextPacket
        packet = ContextPacket(
            user_message="test",
            memory_items=[
                _make_item("journal", "2026-02-03T08-00-00"),
            ],
        )
        sources = _build_vault_sources(packet)
        entry = sources[0]
        assert "type" in entry
        assert "timestamp" in entry
        assert "summary" in entry


class TestFormatSourceDate:

    def test_formats_standard_timestamp(self):
        result = _format_source_date("2026-03-15T10-00-00")
        assert "March" in result
        assert "15" in result

    def test_empty_on_empty_input(self):
        assert _format_source_date("") == ""
        assert _format_source_date(None) == ""

    def test_handles_malformed_timestamp(self):
        assert _format_source_date("not-a-date") == ""


# ---------------------------------------------------------------------------
# Web search authority instruction
# ---------------------------------------------------------------------------

class TestWebSearchAuthorityInstruction:
    """The authority rules must tell the model that web search results
    are current, authoritative, and should be cited with URLs."""

    def test_authority_rules_contain_web_search_authority(self):
        from src.llm.prompt_builder import _render_authority_rules
        rules = _render_authority_rules(is_conversational=False)
        assert "live data" in rules
        assert "current as of today" in rules
        assert "Cite specific URLs" in rules
        assert "Do not discount" in rules

    def test_authority_rules_no_longer_hedge_web_results(self):
        from src.llm.prompt_builder import _render_authority_rules
        rules = _render_authority_rules(is_conversational=False)
        assert "unverified" not in rules
        # The old instruction said "hedge with 'according to web results'"
        assert "according to web results" not in rules


# ---------------------------------------------------------------------------
# Vault citation suppression during web search
# ---------------------------------------------------------------------------

class TestVaultCitationSuppressionDuringWebSearch:
    """When web search is the primary source and the only vault items
    are profile records, the vault_sources signal should be suppressed
    to avoid showing 'Source: Vault' on web search responses."""

    def test_profile_only_vault_suppressed_during_web_search(self):
        """Profile-only vault sources are suppressed when web search
        is active — the UI should show 'Source: Web Search' not 'Vault'."""
        from src.context.models import ContextPacket

        packet = ContextPacket(
            user_message="What is the current price of Bitcoin?",
            memory_items=[
                _make_item("profile", "2026-01-01T00-00-00"),
            ],
            web_items=[{"title": "BTC Price", "url": "https://example.com", "snippet": "$60k"}],
        )
        vault_sources = _build_vault_sources(packet)
        # Profile items are excluded by _build_vault_sources already
        assert len(vault_sources) == 0

    def test_non_profile_vault_preserved_during_web_search(self):
        """When actual vault records (non-profile) are retrieved alongside
        web search, vault_sources should still fire — the response
        genuinely draws on both sources."""
        from src.context.models import ContextPacket

        packet = ContextPacket(
            user_message="What did I say about Bitcoin last week?",
            memory_items=[
                _make_item("profile", "2026-01-01T00-00-00"),
                _make_item("conversation", "2026-04-05T10-00-00"),
            ],
            web_items=[{"title": "BTC Price", "url": "https://example.com", "snippet": "$60k"}],
        )
        vault_sources = _build_vault_sources(packet)
        # conversation record should produce a vault source
        assert len(vault_sources) == 1
        assert vault_sources[0]["type"] == "conversation"
