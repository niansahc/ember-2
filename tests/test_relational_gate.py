"""
tests/test_relational_gate.py

Tests for the relational intensity amplification gate — suppression of
lodestone relational records when relational constitutional triggers
are active. See flourishing_over_preference open design item (now
implemented).
"""

from unittest.mock import patch

from src.context.models import ContextPacket
from src.llm.prompt_builder import PromptBuilder


MOCK_LODESTONE_RECORDS = [
    {"value": "Cares about honest communication in close relationships", "taxonomy_category": "relational"},
    {"value": "Values building things that outlast their creator", "taxonomy_category": "directional"},
    {"value": "Finds meaning in reciprocal vulnerability", "taxonomy_category": "relational"},
    {"value": "Oriented toward craft over credential", "taxonomy_category": "character"},
]


class TestRelationalAmplificationGate:

    def test_relational_records_suppressed_when_flag_is_true(self):
        """When suppress_relational_lodestone=True, lodestone records with
        taxonomy_category='relational' are filtered out. Non-relational
        records still inject."""
        pb = PromptBuilder()
        packet = ContextPacket(user_message="I've been struggling with this.")

        with patch("src.context.lodestone_resolver.resolve", return_value=MOCK_LODESTONE_RECORDS):
            prompt = pb.build_prompt(packet, suppress_relational_lodestone=True)

        # Relational records should NOT appear
        assert "honest communication in close relationships" not in prompt
        assert "reciprocal vulnerability" not in prompt
        # Non-relational records SHOULD appear
        assert "outlast their creator" in prompt
        assert "craft over credential" in prompt

    def test_all_records_inject_when_flag_is_false(self):
        """When suppress_relational_lodestone=False (default), all lodestone
        records inject normally including relational ones."""
        pb = PromptBuilder()
        packet = ContextPacket(user_message="Tell me about my values.")

        with patch("src.context.lodestone_resolver.resolve", return_value=MOCK_LODESTONE_RECORDS):
            prompt = pb.build_prompt(packet, suppress_relational_lodestone=False)

        # All records should appear
        assert "honest communication in close relationships" in prompt
        assert "outlast their creator" in prompt
        assert "reciprocal vulnerability" in prompt
        assert "craft over credential" in prompt
