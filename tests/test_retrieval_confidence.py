"""
tests/test_retrieval_confidence.py

Tests for retrieval confidence metadata injection into the vault_memory
section of the prompt. Verifies that the model receives score and age
metadata to calibrate certainty on vault-retrieved claims.
"""

from unittest.mock import patch
from datetime import datetime, timedelta

from src.context.models import ContextItem, ContextPacket
from src.llm.prompt_builder import PromptBuilder


def _make_item(
    content: str,
    score: float = 0.6,
    memory_type: str = "conversation",
    days_ago: int = 0,
    role: str = "user",
) -> ContextItem:
    ts = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H-%M-%S")
    return ContextItem(
        id=f"test-{hash(content) % 10000}",
        content=content,
        source=memory_type,
        item_type=memory_type,
        memory_type=memory_type,
        score=score,
        timestamp=ts,
        metadata={"role": role},
    )


class TestRetrievalConfidenceBlock:
    """The vault_memory section should include retrieval confidence metadata
    when non-profile items are present."""

    def test_confidence_block_present_with_items(self):
        pb = PromptBuilder()
        packet = ContextPacket(
            user_message="What did I say about the project?",
            memory_items=[
                _make_item("Discussed the architecture rewrite", score=0.72, days_ago=2),
                _make_item("Mentioned switching to Rust", score=0.55, days_ago=5),
            ],
        )
        prompt = pb.build_prompt(packet)
        assert "[Retrieval confidence:]" in prompt
        assert "scores:" in prompt
        assert "avg=" in prompt

    def test_high_confidence_label(self):
        pb = PromptBuilder()
        items = [
            _make_item("Recent high-scoring record", score=0.75, days_ago=1),
            _make_item("Another recent record", score=0.65, days_ago=3),
        ]
        block = pb._build_retrieval_confidence(items)
        assert "high" in block

    def test_moderate_confidence_label(self):
        pb = PromptBuilder()
        items = [
            _make_item("Moderately scored record", score=0.45, days_ago=15),
            _make_item("Another moderate record", score=0.40, days_ago=20),
        ]
        block = pb._build_retrieval_confidence(items)
        assert "moderate" in block

    def test_low_confidence_label(self):
        pb = PromptBuilder()
        items = [
            _make_item("Old low-scoring record", score=0.30, days_ago=60),
            _make_item("Another old record", score=0.28, days_ago=90),
        ]
        block = pb._build_retrieval_confidence(items)
        assert "low" in block

    def test_no_confidence_block_for_empty_items(self):
        pb = PromptBuilder()
        block = pb._build_retrieval_confidence([])
        assert block == ""

    def test_oldest_record_age_shown(self):
        pb = PromptBuilder()
        items = [
            _make_item("Recent", score=0.6, days_ago=2),
            _make_item("Older", score=0.5, days_ago=14),
        ]
        block = pb._build_retrieval_confidence(items)
        assert "14 days ago" in block

    def test_authority_rules_reference_confidence(self):
        """Authority rules should tell the model to check retrieval confidence."""
        from src.llm.prompt_builder import AUTHORITY_RULES
        assert "Retrieval confidence" in AUTHORITY_RULES
        # v0.16.0-dev: authority rules now reference "confidence" and "low"
        # as separate concepts, not the literal "low-confidence"/"low score"
        # compound phrases. Check for the concept, not the old phrasing.
        lowered = AUTHORITY_RULES.lower()
        assert "confidence is low" in lowered or "moderate or low" in lowered

    def test_profile_only_items_have_no_confidence_block(self):
        """Profile items are not scored for confidence — they are always
        injected. The confidence block only appears for non-profile items."""
        pb = PromptBuilder()
        packet = ContextPacket(
            user_message="Who am I?",
            memory_items=[
                ContextItem(
                    id="p1", content="A developer who likes Rust and coffee.",
                    source="profile", item_type="profile",
                    memory_type="profile", score=0.5,
                ),
            ],
        )
        prompt = pb.build_prompt(packet)
        # The authority rules mention "Retrieval confidence" as an instruction,
        # but the actual confidence DATA block (with "scores: min=") should
        # not appear when there are only profile items.
        assert "scores: min=" not in prompt
