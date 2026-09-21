"""
tests/test_rule9_shape.py

Regression guard for CLAUDE.md Rule #9 ("Do not use the word 'shape' in
any output -- code comments, prompts, ADRs, prose, or conversation").

Scope: files that have been cleaned, one entry per file. Deliberately not
the whole tree.

The rule as written in CLAUDE.md is absolute, but the word has a second,
legitimate sense the rule is not aimed at: the STRUCTURE of a thing, as in
"the SSE event shape" (ADR-040), "the dict shape a mock returns", or "known
command shapes". `docs/BUILDING_EMBER.md` states the target precisely --
do not use "shape" to mean "influence", "determine", or "configure". A
tree-wide lint would fail on the structural sense, on this file, on
CLAUDE.md's own statement of the rule, and on "reward shaping" where it
names a technique from the literature. So the guard is a list, and a file
joins it once its influence-sense uses are gone.

Added 2026-09-19, after a sweep found six more: a printed diagnostic line,
a user-facing onboarding question, two code comments, an ADR, and a
research note.

The check is word-boundary, case-insensitive, ASCII-only.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Paths are repo-relative.
RULE9_TARGET_FILES = (
    # Named by the 2026-06-06 architecture review.
    "src/context/policies.py",
    "src/llm/intent_classifier.py",
    "src/safety/url_validator.py",
    "prompts/ember_system_prompt.txt",
    "docs/adr/ADR-016-nature-layer.md",
    # Found by the 2026-09-19 sweep. The first two are the ones that
    # reached a person: a line printed by a diagnostic tool, and a
    # question the onboarding flow asks the user.
    "tools/tiering_distribution_report.py",
    "src/onboarding/steps.py",
    "scripts/seed_identity_template.py",
    "scripts/rebuild_indexes.py",
    "docs/adr/ADR-013-deviation-memory.md",
    "docs/research/memory-trust-gap-followup.md",
    # Written under the rule rather than cleaned after the fact. Listed so
    # it stays that way.
    "docs/adr/ADR-044-retrieval-score-composition-contract.md",
    "docs/adr/ADR-045-supersession-via-write-time-linking.md",
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
