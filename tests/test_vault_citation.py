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
