"""
tests/test_delivery_accounting.py

Issue #227: retrieval stats must record what the model saw.

The packet is a candidate set and the prompt renders a slice of it. ADR-015
heat is meant to record delivery, so writing it against the packet credits
records the model never received. The write is not a small over-count
either: one write sets last_retrieved_at to now, which forces recency to
1.0 and heat to at least 0.625, clearing the 0.5 hot threshold outright.
One appearance in a candidate set was a guaranteed promotion to hot.

So the write is deferred: build_context arms a recorder, the prompt
builder reports what it rendered, the adapter commits once the prompt is
final. Three things have to hold, and each has a way of failing quietly:

1. Nothing renders, nothing is recorded. A packet that never reached a
   model is not a delivery, and the old code could not tell the
   difference.
2. The last render wins. prompt_guardrail rebuilds the same packet up to
   seven times as it drops sections; committing the first would credit
   records a later pass removed.
3. Committing twice does not count twice. The constitutional review path
   builds a second prompt for the same turn.

Fixtures are synthetic (CLAUDE.md Vault Privacy Rule).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.context.models import ContextItem, ContextPacket
from src.context.service import ContextService

QUERY = "what have i been reading about lately"
EMBED_DIM = 768


def _item(store_id: str, memory_type: str = "conversation") -> ContextItem:
    return ContextItem(
        id=store_id,
        content=f"synthetic record {store_id}",
        source="test",
        item_type=memory_type,
        memory_type=memory_type,
        store_id=store_id,
    )


def _packet(memory=(), reflections=()) -> ContextPacket:
    return ContextPacket(
        user_message=QUERY,
        memory_items=list(memory),
        reflection_items=list(reflections),
    )


# ---------------------------------------------------------------------------
# The packet's own accounting
# ---------------------------------------------------------------------------

class TestPacketAccounting:
    def test_an_unarmed_packet_records_nothing(self):
        """read_only arms no recorder, so there is nothing to reach."""
        packet = _packet([_item("a")])
        packet.begin_render()
        packet.record_rendered(packet.memory_items)
        assert packet.commit_delivery() == 0

    def test_an_armed_packet_commits_what_was_rendered(self):
        seen = []
        packet = _packet([_item("a"), _item("b")])
        packet.arm_delivery_recorder(seen.extend)
        packet.begin_render()
        packet.record_rendered(packet.memory_items[:1])

        assert packet.commit_delivery() == 1
        assert [i.store_id for i in seen] == ["a"]

    def test_arming_without_rendering_writes_nothing(self):
        """The turn that never reached a model."""
        calls = []
        packet = _packet([_item("a")])
        packet.arm_delivery_recorder(calls.append)
        assert packet.commit_delivery() == 0
        assert calls == []

    def test_the_last_render_wins(self):
        """The cascade-trim case, which an accumulating record would fail."""
        seen = []
        packet = _packet([_item("a"), _item("b"), _item("c")])
        packet.arm_delivery_recorder(seen.extend)

        packet.begin_render()
        packet.record_rendered(packet.memory_items)       # pre-trim build
        packet.begin_render()
        packet.record_rendered(packet.memory_items[:1])   # after a trim pass

        assert packet.commit_delivery() == 1
        assert [i.store_id for i in seen] == ["a"]

    def test_committing_twice_records_once(self):
        """The review path builds a second prompt for the same turn."""
        calls = []
        packet = _packet([_item("a")])
        packet.arm_delivery_recorder(calls.append)
        packet.begin_render()
        packet.record_rendered(packet.memory_items)

        assert packet.commit_delivery() == 1
        assert packet.commit_delivery() == 0
        assert len(calls) == 1

    def test_both_channels_are_recorded(self):
        seen = []
        packet = _packet([_item("m")], [_item("r", "reflection")])
        packet.arm_delivery_recorder(seen.extend)
        packet.begin_render()
        packet.record_rendered(packet.memory_items)
        packet.record_rendered(packet.reflection_items)

        assert packet.commit_delivery() == 2
        assert sorted(i.store_id for i in seen) == ["m", "r"]


# ---------------------------------------------------------------------------
# build_context arms rather than writes
# ---------------------------------------------------------------------------

@pytest.fixture
def seeded_vault():
    from src.memory.write_memory import write_memory

    vector = [0.1] * EMBED_DIM
    rows = [
        ("today i was reading about retrieval scoring and it clarified a lot",
         "conversation", {"role": "user", "content_kind": "experience"}),
        ("a second conversation turn about the same reading, different day",
         "conversation", {"role": "user", "content_kind": "experience"}),
        ("a third turn about reading, later again", "conversation",
         {"role": "user", "content_kind": "experience"}),
        ("a fourth turn about reading, later still", "conversation",
         {"role": "user", "content_kind": "experience"}),
        ("a fifth turn about reading that should fall outside the slice",
         "conversation", {"role": "user", "content_kind": "experience"}),
        ("a sixth turn about reading, also outside the slice", "conversation",
         {"role": "user", "content_kind": "experience"}),
        ("the user prefers dense technical reading over summaries", "profile",
         {"content_kind": "user_content"}),
    ]
    with patch("src.memory.write_memory.embed_text", return_value=vector):
        for text, memory_type, metadata in rows:
            write_memory(text=text, memory_type=memory_type, source="chat",
                         metadata=metadata)
    yield vector


class TestBuildContext:
    def test_build_context_no_longer_writes_by_itself(self, seeded_vault):
        """The defect, stated as a test.

        A build that writes on its own cannot distinguish a delivery from a
        candidate set, because at that point the slice has not happened.
        """
        service = ContextService()
        with patch("src.retrieval.semantic_search.embed_text",
                   return_value=seeded_vault):
            with patch.object(service, "_update_retrieval_stats") as stats:
                service.build_context(QUERY)
        stats.assert_not_called()

    def test_build_context_arms_the_recorder(self, seeded_vault):
        service = ContextService()
        with patch("src.retrieval.semantic_search.embed_text",
                   return_value=seeded_vault):
            packet = service.build_context(QUERY)
        assert packet._delivery_recorder is not None

    def test_read_only_arms_nothing(self, seeded_vault):
        """#206 stays structural: no writer exists to be reached."""
        service = ContextService()
        with patch("src.retrieval.semantic_search.embed_text",
                   return_value=seeded_vault):
            packet = service.build_context(QUERY, read_only=True)
        assert packet._delivery_recorder is None
        packet.begin_render()
        packet.record_rendered(packet.memory_items)
        assert packet.commit_delivery() == 0


# ---------------------------------------------------------------------------
# The prompt builder reports its own slice
# ---------------------------------------------------------------------------

class TestPromptBuilderRecordsTheSlice:
    def _build(self, packet):
        from src.llm.prompt_builder import PromptBuilder

        PromptBuilder().build_prompt(packet)

    def test_the_memory_channel_records_the_rendered_slice_only(self):
        """Six non-profile candidates, four rendered."""
        packet = _packet([_item(f"c{i}") for i in range(6)])
        self._build(packet)
        assert [i.store_id for i in packet.delivered_items] == [
            "c0", "c1", "c2", "c3"
        ]

    def test_profile_items_are_all_rendered_and_all_recorded(self):
        packet = _packet(
            [_item("p1", "profile"), _item("p2", "profile")]
            + [_item(f"c{i}") for i in range(6)]
        )
        self._build(packet)
        recorded = [i.store_id for i in packet.delivered_items]
        assert recorded == ["p1", "p2", "c0", "c1", "c2", "c3"]

    def test_the_reflection_channel_records_one(self):
        packet = _packet(
            [_item("c0")],
            [_item(f"r{i}", "reflection") for i in range(3)],
        )
        self._build(packet)
        recorded = [i.store_id for i in packet.delivered_items]
        assert "r0" in recorded
        assert "r1" not in recorded and "r2" not in recorded

    def test_rebuilding_does_not_accumulate(self):
        packet = _packet([_item(f"c{i}") for i in range(6)])
        self._build(packet)
        self._build(packet)
        assert len(packet.delivered_items) == 4

    def test_an_empty_packet_records_nothing(self):
        packet = _packet()
        self._build(packet)
        assert packet.delivered_items == []


# ---------------------------------------------------------------------------
# The cascade-trim path, on the real guardrail
# ---------------------------------------------------------------------------

class TestGuardrailClone:
    """prompt_guardrail clones the packet and rebuilds it as it trims.

    Two ways that breaks the accounting, both silent: the clone loses the
    recorder and the turn records nothing, or the pre-trim build is what
    commits and records more than the model received.
    """

    def _armed(self, count: int):
        seen = []
        packet = _packet([_item(f"c{i}") for i in range(count)])
        packet.arm_delivery_recorder(seen.extend)
        return packet, seen

    def test_the_clone_carries_the_recorder(self):
        from src.llm.prompt_guardrail import _clone_packet

        packet, seen = self._armed(2)
        clone = _clone_packet(packet)
        clone.begin_render()
        clone.record_rendered(clone.memory_items)

        assert clone.commit_delivery() == 2
        assert [i.store_id for i in seen] == ["c0", "c1"]

    def test_the_clone_commits_independently_of_the_original(self):
        """The adapter reassigns context_packet to the returned clone, so
        the original is left armed and uncommitted rather than double
        counting."""
        from src.llm.prompt_guardrail import _clone_packet

        packet, seen = self._armed(1)
        clone = _clone_packet(packet)
        clone.begin_render()
        clone.record_rendered(clone.memory_items)
        clone.commit_delivery()

        assert len(seen) == 1
        assert packet.commit_delivery() == 0
        assert len(seen) == 1

    def test_trim_to_fit_leaves_the_returned_packet_committable(self):
        from src.llm.prompt_guardrail import trim_to_fit

        packet, seen = self._armed(6)

        class _Builder:
            def build_prompt(self, working_packet, **kwargs):
                working_packet.begin_render()
                nonprofile = [
                    i for i in working_packet.memory_items
                    if i.memory_type != "profile"
                ][:4]
                working_packet.record_rendered(nonprofile)
                return "PROMPT"

        prompt, returned, telemetry = trim_to_fit(
            packet=packet,
            model="qwen3:8b",
            num_ctx=32768,
            builder=_Builder(),
            build_kwargs={},
        )

        assert returned.commit_delivery() == 4
        assert [i.store_id for i in seen] == ["c0", "c1", "c2", "c3"]


# ---------------------------------------------------------------------------
# End to end against the store, both channels
# ---------------------------------------------------------------------------

class TestAgainstTheStore:
    """The invariant, asserted against real rows rather than a mock.

    Both channels in one test because the failure that matters is a record
    being promoted without being rendered, and that can happen on either.
    """

    def _stats(self):
        import sqlite3
        from src.core.config import get_private_vault_path

        db = get_private_vault_path() / "embeddings" / "memory.db"
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return {
                row[0]: (row[1], row[2])
                for row in conn.execute(
                    "SELECT id, last_retrieved_at, retrieval_count FROM vectors"
                )
            }
        finally:
            conn.close()

    def _write(self, text, memory_type):
        from src.memory.write_memory import write_memory

        import pathlib

        with patch("src.memory.write_memory.embed_text",
                   return_value=[0.1] * EMBED_DIM):
            written = write_memory(text=text, memory_type=memory_type,
                                   source="chat", metadata={"role": "user"})
        # write_memory returns the canonical file path; the store id is the
        # record id, which is that file's stem.
        return pathlib.Path(str(written)).stem

    def test_only_rendered_records_take_a_stats_write(self):
        from src.llm.prompt_builder import PromptBuilder

        # Six non-profile candidates, of which the prompt renders four, and
        # three reflection candidates, of which it renders one.
        memory_ids = [
            self._write(f"synthetic delivery record number {n} about indexing",
                        "conversation")
            for n in range(6)
        ]
        reflection_ids = [
            self._write(f"synthetic synthesis number {n} over the week",
                        "reflection")
            for n in range(3)
        ]

        packet = _packet(
            [_item(i) for i in memory_ids],
            [_item(i, "reflection") for i in reflection_ids],
        )
        packet.arm_delivery_recorder(ContextService()._update_retrieval_stats)

        before = self._stats()
        PromptBuilder().build_prompt(packet)
        committed = packet.commit_delivery()

        after = self._stats()
        changed = {i for i in after if before.get(i) != after[i]}
        rendered = {i.store_id for i in packet.delivered_items}

        assert committed == 5, "four memory records and one reflection"
        assert changed == rendered

        # And the ones the model never saw are untouched, which is the
        # defect stated positively.
        not_rendered = (set(memory_ids) | set(reflection_ids)) - rendered
        assert len(not_rendered) == 4
        assert not (changed & not_rendered)
