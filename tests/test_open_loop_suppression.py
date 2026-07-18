"""
tests/test_open_loop_suppression.py

Regression test for B-STATE-001 (issue #102): read-time suppression of a
resolved open_loop.

resolve_open_loops_by_topic() writes an append-only resolution tombstone
carrying metadata.original_id pointing at the open_loop it resolves. The
tombstone is skipped by the resolver (it carries metadata.resolved=True), but
the ORIGINAL open_loop record keeps metadata.resolved=False, so before this
fix it kept surfacing in StateResolver.get_current_state() after resolution.

The fix wires StateService.resolved_ids() (which honors both legacy
metadata.resolved and original_id tombstones) into the resolver's open_loop
path, so the original is suppressed at read time.

This is a read-side test only; the write side already writes the tombstone
correctly (covered by test_bug009_topic_fixation.py).

All tests use tmp_path; no real vault is touched.
"""

from src.state.state_resolver import StateResolver
from src.state.state_service import StateService


def test_resolved_open_loop_not_surfaced_by_resolver(tmp_path):
    svc = StateService(vault_path=tmp_path)
    resolver = StateResolver(service=svc)

    original = StateService.make_record(
        state_type="open_loop",
        text="Follow up on the budget review",
        source="test",
    )
    svc.write(original)

    # Sanity: the open_loop surfaces as current state before it is resolved.
    before = [i for i in resolver.get_current_state() if i.category == "open_loop"]
    assert any("budget" in i.text.lower() for i in before)

    # Resolve it by topic — writes an append-only tombstone with original_id.
    assert svc.resolve_open_loops_by_topic("the budget") == 1

    # After resolution the original open_loop must no longer surface.
    after = [i for i in resolver.get_current_state() if i.category == "open_loop"]
    assert not any("budget" in i.text.lower() for i in after)


def test_unrelated_open_loop_still_surfaces_after_resolution(tmp_path):
    """Suppressing one resolved loop must not suppress an unrelated active one."""
    svc = StateService(vault_path=tmp_path)
    resolver = StateResolver(service=svc)

    svc.write(
        StateService.make_record(
            state_type="open_loop",
            text="Follow up on the budget review",
            source="test",
        )
    )
    svc.write(
        StateService.make_record(
            state_type="open_loop",
            text="Schedule the dentist appointment",
            source="test",
        )
    )

    assert svc.resolve_open_loops_by_topic("the budget") == 1

    after = [i for i in resolver.get_current_state() if i.category == "open_loop"]
    assert not any("budget" in i.text.lower() for i in after)
    assert any("dentist" in i.text.lower() for i in after)
