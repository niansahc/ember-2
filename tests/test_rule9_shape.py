"""
tests/test_rule9_shape.py

Regression guard for CLAUDE.md Rule #9 ("Do not use the word 'shape' in
any output -- code comments, prompts, ADRs, prose, or conversation").

Scope: the five files surfaced by the 2026-06-06 architecture review.
A future PR can broaden the lint to the whole tree. Keeping scope narrow
here so a non-related "shapefile" reference in unrelated code does not
land as a side effect of this fix.

The check is word-boundary, case-insensitive, ASCII-only.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files that the architecture review named. Paths are repo-relative.
RULE9_TARGET_FILES = (
    "src/context/policies.py",
    "src/llm/intent_classifier.py",
    "src/safety/url_validator.py",
    "prompts/ember_system_prompt.txt",
    "docs/adr/ADR-016-nature-layer.md",
)

# Word-boundary, case-insensitive. Catches "shape", "Shape", "SHAPES",
# "reshape", "shaped", "shaping". CLAUDE.md Rule #9 is absolute.
_SHAPE_PATTERN = re.compile(r"\bshape\w*\b", re.IGNORECASE)


@pytest.mark.parametrize("relpath", RULE9_TARGET_FILES)
def test_no_shape_word_in_rule9_target(relpath: str) -> None:
    path = REPO_ROOT / relpath
    assert path.exists(), f"target file missing: {relpath}"
    text = path.read_text(encoding="utf-8")
    matches = _SHAPE_PATTERN.findall(text)
    assert not matches, (
        f"{relpath} contains forbidden 'shape' usages: {matches!r} "
        "(CLAUDE.md Rule #9)"
    )
