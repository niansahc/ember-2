"""
tests/test_retrieval_confidence.py

Tests for retrieval confidence metadata injection into the vault_memory
section of the prompt. Verifies that the model receives score and age
metadata to calibrate certainty on vault-retrieved claims.
"""

import itertools
from unittest.mock import patch
from datetime import datetime, timedelta

from src.context.models import ContextItem, ContextPacket
from src.llm.prompt_builder import PromptBuilder

# N3: monotonic counter avoids hash-collision-on-identical-content that the
# prior `hash(content) % 10000` scheme produced. Two _make_item calls with
# the same content text now produce distinct IDs, so was_hedged() can't
# silently misfire when fixture data is intentionally identical.
_make_item_counter = itertools.count()


def _make_item(
    content: str,
    score: float = 0.6,
    memory_type: str = "conversation",
    days_ago: int = 0,
    role: str = "user",
) -> ContextItem:
    ts = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H-%M-%S")
    return ContextItem(
        id=f"test-{next(_make_item_counter)}",
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


# ---------------------------------------------------------------------------
# B-MEM-003/004/005: stale-hedge fixes (v0.17.1)
# ---------------------------------------------------------------------------


class TestHedgeRepetitionFixes:
    """Three-part fix: literal-example removal, profile-exclusion rule,
    follow-up confidence-block suppression."""

    def test_authority_rules_no_longer_contains_literal_3_days_ago(self):
        """B-MEM-003/004 regression: literal example was being copied
        verbatim onto stable profile facts."""
        from src.llm.prompt_builder import _render_authority_rules
        rendered = _render_authority_rules(is_conversational=False)
        assert "3 days ago" not in rendered

    def test_authority_rules_use_abstract_age_placeholder(self):
        """The abstract <N> placeholder tells the model to substitute the real age."""
        from src.llm.prompt_builder import _render_authority_rules
        rendered = _render_authority_rules(is_conversational=False)
        assert "<N>" in rendered

    def test_authority_rules_profile_exclusion_present_when_has_profile(self):
        from src.llm.prompt_builder import (
            _AUTHORITY_RULES_PROFILE_HEDGE_EXCLUSION,
            _render_authority_rules,
        )
        rendered = _render_authority_rules(
            is_conversational=False, has_profile=True
        )
        assert _AUTHORITY_RULES_PROFILE_HEDGE_EXCLUSION in rendered

    def test_authority_rules_profile_exclusion_absent_when_no_profile(self):
        """Avoid prompt bloat when profile exclusion isn't relevant."""
        from src.llm.prompt_builder import (
            _AUTHORITY_RULES_PROFILE_HEDGE_EXCLUSION,
            _render_authority_rules,
        )
        rendered = _render_authority_rules(
            is_conversational=False, has_profile=False
        )
        assert _AUTHORITY_RULES_PROFILE_HEDGE_EXCLUSION not in rendered

    def test_build_prompt_passes_has_profile_when_profile_in_packet(self):
        """Integration: build_prompt resolves has_profile from packet contents."""
        from src.llm.prompt_builder import _AUTHORITY_RULES_PROFILE_HEDGE_EXCLUSION
        pb = PromptBuilder()
        packet = ContextPacket(
            user_message="Who am I?",
            memory_items=[
                ContextItem(
                    id="p1", content="profile content", source="profile",
                    item_type="profile", memory_type="profile", score=0.9,
                ),
            ],
        )
        prompt = pb.build_prompt(packet)
        assert _AUTHORITY_RULES_PROFILE_HEDGE_EXCLUSION in prompt


class TestConfidenceBlockFollowupSuppression:
    """B-MEM-005: confidence block must be suppressed on follow-up turns
    when every retrieved record was hedged in a prior turn."""

    def test_confidence_block_emitted_first_turn_stages_pending_hedge(self):
        """S1: prompt-build time stages records as pending (not committed).
        Commit happens after the coaching filter in openai_adapter — so
        failed LLM calls don't leave spurious marks."""
        pb = PromptBuilder()
        packet = ContextPacket(
            user_message="what was that?",
            memory_items=[_make_item("recall", score=0.4, days_ago=20)],
        )
        section = pb._build_context_section(packet, is_conversational=False)
        assert "[Retrieval confidence:]" in section
        record_id = packet.memory_items[0].id
        # Before commit: record is pending, not hedged
        assert record_id in pb.conversation_buffer.pending_hedge_record_ids
        assert pb.conversation_buffer.was_hedged(record_id) is False

    def test_commit_pending_hedge_promotes_to_lru(self):
        """S1: commit_pending_hedge() moves staged records into the LRU."""
        pb = PromptBuilder()
        packet = ContextPacket(
            user_message="what was that?",
            memory_items=[_make_item("recall", score=0.4, days_ago=20)],
        )
        pb._build_context_section(packet, is_conversational=False)
        record_id = packet.memory_items[0].id

        pb.conversation_buffer.commit_pending_hedge()

        assert pb.conversation_buffer.was_hedged(record_id) is True
        # Pending list cleared after commit
        assert pb.conversation_buffer.pending_hedge_record_ids == []

    def test_uncommitted_pending_does_not_suppress_followup(self):
        """S1 regression: if the LLM call fails (no commit), the next turn
        with the same record should still emit the confidence block — the
        record was never actually hedged from the user's perspective."""
        pb = PromptBuilder()
        item = _make_item("recall", score=0.4, days_ago=20)

        # Turn 1: build prompt but never commit (simulates failed response)
        pb._build_context_section(
            ContextPacket(user_message="q1", memory_items=[item]),
            is_conversational=False,
        )

        # Turn 2: same record retrieved — block should still emit
        section2 = pb._build_context_section(
            ContextPacket(user_message="q2", memory_items=[item]),
            is_conversational=False,
        )
        assert "[Retrieval confidence:]" in section2

    def test_confidence_block_suppressed_when_all_previously_hedged(self):
        pb = PromptBuilder()
        item = _make_item("recall", score=0.4, days_ago=20)
        pb.conversation_buffer.mark_hedge_emitted([item.id])

        packet = ContextPacket(user_message="follow-up", memory_items=[item])
        section = pb._build_context_section(packet, is_conversational=False)
        assert "[Retrieval confidence:]" not in section

    def test_confidence_block_emitted_when_new_record_in_followup(self):
        """If retrieval brings in a new record alongside previously-hedged ones,
        the confidence block remains useful for the new record."""
        pb = PromptBuilder()
        old = _make_item("recall", score=0.4, days_ago=20)
        pb.conversation_buffer.mark_hedge_emitted([old.id])

        # N3: monotonic counter in _make_item guarantees distinct IDs even
        # when content is identical, so no manual id patch is needed here.
        new = _make_item("new context", score=0.4, days_ago=20)

        packet = ContextPacket(
            user_message="follow-up with new info", memory_items=[old, new]
        )
        section = pb._build_context_section(packet, is_conversational=False)
        assert "[Retrieval confidence:]" in section


class TestHedgedRecordIdsLRU:
    """ConversationBuffer.hedged_record_ids LRU bounds."""

    def test_lru_capped_at_max(self):
        from src.context.conversation_buffer import (
            _HEDGED_RECORD_IDS_MAX,
            ConversationBuffer,
        )
        buf = ConversationBuffer()
        ids = [f"r{i}" for i in range(_HEDGED_RECORD_IDS_MAX + 10)]
        buf.mark_hedge_emitted(ids)

        assert len(buf.hedged_record_ids) == _HEDGED_RECORD_IDS_MAX
        # Oldest evicted, newest retained
        assert buf.was_hedged("r0") is False
        assert buf.was_hedged(ids[-1]) is True

    def test_was_hedged_touches_lru_position(self):
        from src.context.conversation_buffer import (
            _HEDGED_RECORD_IDS_MAX,
            ConversationBuffer,
        )
        buf = ConversationBuffer()
        buf.mark_hedge_emitted([f"r{i}" for i in range(_HEDGED_RECORD_IDS_MAX)])
        # Touch r0 — move to most recent
        assert buf.was_hedged("r0") is True
        # Insert one new — should evict r1 (now oldest), not r0
        buf.mark_hedge_emitted(["r_new"])
        assert buf.was_hedged("r0") is True
        assert buf.was_hedged("r1") is False

    def test_mark_skips_empty_ids(self):
        from src.context.conversation_buffer import ConversationBuffer
        buf = ConversationBuffer()
        buf.mark_hedge_emitted(["", "valid", ""])
        assert len(buf.hedged_record_ids) == 1
        assert buf.was_hedged("valid") is True

    def test_was_hedged_unknown_id_returns_false(self):
        from src.context.conversation_buffer import ConversationBuffer
        buf = ConversationBuffer()
        assert buf.was_hedged("never-seen") is False
        assert buf.was_hedged("") is False
