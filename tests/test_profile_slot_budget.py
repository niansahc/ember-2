"""
tests/test_profile_slot_budget.py

Profile records are not charged against the memory retrieval window.

Before this change, ContextService subtracted the profile count from
memory_limit, so three guaranteed profile records took three of the four to
six non-profile slots. On the reflective policy, whose limit is 4, that left
one.

The subtraction reserved no prompt space. prompt_builder.py:978-979
partitions profile out again at render time and caps non-profile at [:4]
independently, so profile's space in the prompt was never in question. The
subtraction only starved the other channel before it reached a layer that was
going to separate them anyway -- and profile was the only always-on layer
billed to the retrieval window, while nature, lodestone, state, tasks and
reflections each carry their own budget.

The guarantee itself is untouched: profile items are still partitioned out
first, still prepended, still bypass the limit cutoff. No gate, no threshold,
no ordering was introduced.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.context.models import ContextItem
from src.context.policies import ContextPolicy
from src.context.service import ContextService


def _item(item_id: str, memory_type: str, score: float) -> ContextItem:
    return ContextItem(
        id=item_id,
        store_id=item_id,
        content=f"record {item_id} with enough body to clear the content floors",
        source="chat",
        item_type=memory_type,
        memory_type=memory_type,
        score=score,
        timestamp="2026-09-01T12-00-00",
        tags=[],
        metadata={"raw_score": score, "role": "user"},
    )


def _packet(policy: ContextPolicy, n_profile: int, n_other: int):
    """Run build_context with retrieval stubbed to a known candidate set."""
    profile = [_item(f"p{i}", "profile", 0.9 - i * 0.01) for i in range(n_profile)]
    other = [_item(f"o{i}", "conversation", 0.8 - i * 0.01) for i in range(n_other)]

    service = ContextService()
    with patch("src.context.service.classify_query", return_value=policy), \
         patch.object(service.retriever, "retrieve",
                      return_value=([], [], profile + other, [], None)):
        return service.build_context("a query", read_only=True)


def _counts(packet):
    prof = [i for i in packet.memory_items if i.memory_type == "profile"]
    other = [i for i in packet.memory_items if i.memory_type != "profile"]
    return len(prof), len(other)


# ---------------------------------------------------------------------------
# The change
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("policy_name,limit", [
    ("reflective", 4),
    ("recent", 5),
    ("default", 6),
])
def test_profile_does_not_reduce_the_non_profile_allocation(policy_name, limit):
    policy = ContextPolicy(name=policy_name)
    with_profile = _packet(policy, n_profile=3, n_other=10)
    without_profile = _packet(policy, n_profile=0, n_other=10)

    _, other_with = _counts(with_profile)
    _, other_without = _counts(without_profile)

    assert other_with == other_without == limit, (
        "the non-profile channel must get the full memory_limit whether or "
        "not profile records are present"
    )


def test_reflective_gets_its_whole_window():
    # The policy the subtraction hurt most: limit 4, previously 1 slot left.
    packet = _packet(ContextPolicy(name="reflective"), n_profile=3, n_other=10)
    assert _counts(packet) == (3, 4)


def test_the_guarantee_is_unchanged():
    """Profile still bypasses the limit cutoff entirely.

    Five profile records against a limit of 4 all survive -- the partition
    prepends them before the limit is applied to anything.
    """
    packet = _packet(ContextPolicy(name="reflective"), n_profile=5, n_other=10)
    prof, other = _counts(packet)
    assert prof == 5, "profile must not be truncated by memory_limit"
    assert other == 4


def test_profile_items_still_come_first():
    packet = _packet(ContextPolicy(name="default"), n_profile=3, n_other=10)
    types = [i.memory_type for i in packet.memory_items]
    assert types[:3] == ["profile"] * 3


def test_no_profile_records_is_unaffected():
    packet = _packet(ContextPolicy(name="default"), n_profile=0, n_other=10)
    assert _counts(packet) == (0, 6)


def test_fewer_candidates_than_the_limit_is_not_padded():
    packet = _packet(ContextPolicy(name="default"), n_profile=3, n_other=2)
    assert _counts(packet) == (3, 2)


# ---------------------------------------------------------------------------
# Why it matters beyond the count
# ---------------------------------------------------------------------------

def test_diversity_selection_gets_a_usable_limit_on_reflective():
    """The subtraction did not just shrink the window, it killed a mechanism.

    _select_diverse_memory round-robins across conversation / ingested /
    other. At a limit of 1 it takes one item from the first group and
    terminates, so on the one policy that sets diversity=True the other
    groups were unreachable. This asserts the limit it now receives is large
    enough for the round-robin to reach a second group; the round-robin's own
    behaviour is a separate defect and is filed separately.
    """
    profile = [_item(f"p{i}", "profile", 0.9) for i in range(3)]
    other = (
        [_item(f"c{i}", "conversation", 0.8 - i * 0.01) for i in range(6)]
        + [_item("j0", "journal", 0.5)]
    )

    service = ContextService()
    seen = {}
    real = service._select_diverse_memory

    def _spy(items, limit):
        seen["limit"] = limit
        return real(items, limit)

    policy = ContextPolicy(name="reflective", diversity=True)
    with patch("src.context.service.classify_query", return_value=policy), \
         patch.object(service.retriever, "retrieve",
                      return_value=([], [], profile + other, [], None)), \
         patch.object(service, "_select_diverse_memory", side_effect=_spy):
        service.build_context("a query", read_only=True)

    assert seen["limit"] == 4, (
        "diversity selection received the profile-reduced limit; the "
        "round-robin cannot reach a second memory type below 2"
    )
