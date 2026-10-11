"""
tests/test_meta_markers_shared.py

write_memory.should_skip_memory() and semantic_search.should_exclude_result()
used to carry separate copies of the meta-marker list (ultrareview #280,
item 6). Both now read src.retrieval.markers.META_MARKERS; every marker in
it must be rejected on both the write side and the retrieval side.

The control sentence is the same text with the marker removed, which both
filters accept, so a rejection is caused by the marker and not by length,
JSON prefix or code-fence rules.
"""

from __future__ import annotations

import pytest

from src.memory.write_memory import should_skip_memory
from src.retrieval.markers import META_MARKERS
from src.retrieval.semantic_search import normalize_text, should_exclude_result

PREFIX = "plain synthetic sentence written for the shared marker filter test"
SUFFIX = "followed by a little more ordinary text"


def _with_marker(marker: str) -> str:
    return f"{PREFIX} {marker} {SUFFIX}"


def _without_marker() -> str:
    return f"{PREFIX} {SUFFIX}"


@pytest.mark.parametrize("marker", META_MARKERS)
def test_marker_rejected_on_write_side(marker):
    assert should_skip_memory(_with_marker(marker), memory_type="journal")


@pytest.mark.parametrize("marker", META_MARKERS)
def test_marker_rejected_on_retrieval_side(marker):
    assert should_exclude_result(normalize_text(_with_marker(marker)))


def test_control_same_text_without_marker_accepted_by_both():
    text = _without_marker()
    assert not should_skip_memory(text, memory_type="journal")
    assert not should_exclude_result(normalize_text(text))
